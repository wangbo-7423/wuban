"""长期记忆服务：在「对话历史 ⇆ MCP 知识图谱」之间做抽取与回灌。

三条链路：
1. **读（聊天前）**：把记忆图谱按「上下文工程」拆两层注入 system prompt——
   `get_persistent_digest()` 是 L0 常驻层（偏好/目标/误区，任何话题都用得上，每轮注入），
   `get_relevant_digest()` 是 L1 检索层（当前消息命中的已学概念 + 一步邻接关系，按需注入）。
   图谱随对话增长，全量注入会膨胀成噪音，`get_digest()` 全量视图只留给 API/调试；
2. **写（聊天后）**：`extract_and_write()` 用 GLM 从本轮对话抽取实体/关系，
   去重合并进图谱（BackgroundTasks 里跑，不拖慢回包）；
3. **重建**：`rebuild_from_history()` 清空后按批次重放全部历史消息，
   从零构建记忆（冷启动 / 修图用）。

抽取的数据模型与官方 server-memory 一致：
    entities: [{name, entityType, observations: [str]}]
    relations: [{from, to, relationType}]
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.agent import glm_client
from app.mcp import MemoryUnavailable, bridge
from app.models.conversation import Conversation, Message

logger = logging.getLogger(__name__)

# 官方 server 是自由 schema；我们约束一个受控词表，保证图谱可按类型分组展示
ENTITY_TYPES = ("概念", "误区", "偏好", "目标", "项目")

# 抽取上限：防止模型把一段对话抽成百科全书
_MAX_ENTITIES_PER_TURN = 8
_MAX_OBS_PER_ENTITY = 3
_MAX_RELATIONS_PER_TURN = 10
_NAME_MAX = 40
_OBS_MAX = 60

# L1 检索层预算：命中概念与邻接关系都要设上限，防止长尾话题注入过多
_MAX_L1_HITS = 5
_MAX_L1_RELATIONS = 8
# 每个概念最多 2 个别名（混合召回的第二通道）
_MAX_ALIASES = 2
_OBS_FULL = _MAX_OBS_PER_ENTITY + _MAX_ALIASES  # observations 总容量（含别名条目）

_ALIAS_PREFIX = "别名："  # 别名在图谱里的持久化约定：observations 中的「别名：X」条目

_EXTRACT_SYSTEM = """你是学习记忆抽取器。从一段「AI 伴学」对话里抽取值得长期记住的知识图谱信息。

只抽四类东西：
1. **概念**：学生学过/问过的学科概念（名字用标准中文术语全称，如「傅里叶变换」「PID 控制器」）；
2. **误区**：学生暴露出的具体理解错误或困惑（名字写成简短判断，如「混淆卷积与相关」）；
3. **偏好**：学生的学习偏好信号（如「偏好图示讲解」「喜欢先看例子」）；
4. **目标**：学生提到的学习目标/课程/考试（如「备考自动控制期末」）。

硬性规则：
- 没有值得记的就返回空数组，**不要硬凑**；寒暄、一次性细节、纯计算过程不抽；
- 实体名 ≤ 20 字；「概念」可带 0~2 个 aliases（英文缩写/口语叫法，如 FFT、傅氏变换、拉氏变换），其他类型不给；
- observations 每条 ≤ 40 字、只写事实（如「学生初次接触该概念」「已能独立完成积分计算」「与 X 混淆过，已纠正」）；
- entityType 只能是：概念 / 误区 / 偏好 / 目标；
- relations 只连本次抽取的实体或「已知实体」列表里出现过的名字，relationType 用短词（如 前置依赖 / 包含 / 应用于 / 相关 / 易混淆 / 属于目标）；
- 只输出 JSON，不要任何解释文字。"""

_EXTRACT_TMPL = """课程上下文：{course}

已知实体（可复用、可与之建关系）：{known}

<对话>
学生：{user_text}

导师：{assistant_text}
</对话>

输出 JSON：
{{"entities": [{{"name": "...", "entityType": "概念", "aliases": ["FFT"], "observations": ["..."]}}],
  "relations": [{{"from": "...", "to": "...", "relationType": "..."}}]}}"""


# ── 读路径 ───────────────────────────────────────────────


def get_graph(user_id: str) -> dict[str, Any]:
    """原始图谱（给 API / 前端可视化用）。不可用时返回空图 + available 标记。"""
    if not bridge.available():
        return {"available": False, "entities": [], "relations": [],
                "stats": {"entity_count": 0, "relation_count": 0}}
    try:
        g = bridge.read_graph(user_id)
    except MemoryUnavailable as e:
        logger.warning("read_graph 不可用: %s", e)
        return {"available": False, "entities": [], "relations": [],
                "stats": {"entity_count": 0, "relation_count": 0}}
    return {
        "available": True,
        "entities": g.get("entities", []),
        "relations": g.get("relations", []),
        "stats": {
            "entity_count": len(g.get("entities", [])),
            "relation_count": len(g.get("relations", [])),
        },
    }


def get_persistent_digest(user_id: str) -> str | None:
    """L0 常驻层：偏好 / 目标 / 误区，每轮固定注入（预算约 100~200 token）。

    这三类是「人格级」信息——任何话题都用得上，所以常驻；
    学过的概念是「话题级」信息，交给 L1 按需检索，不在这里堆。
    """
    g = _read_graph_safe(user_id)
    if not g:
        return None
    by_type: dict[str, list[dict]] = {}
    for e in g.get("entities", []):
        by_type.setdefault(e.get("entityType") or "概念", []).append(e)

    prefs = [e["name"] for e in by_type.get("偏好", [])][:5]
    goals = [e["name"] for e in by_type.get("目标", [])][:5]
    mis = by_type.get("误区", [])[:6]

    lines: list[str] = ["## 学生长期记忆（历史对话沉淀，常驻）"]
    if prefs:
        lines.append(f"- 偏好：{'、'.join(prefs)}")
    if goals:
        lines.append(f"- 目标：{'、'.join(goals)}")
    if mis:
        lines.append("- 已发现的误区（讲解时注意避开/主动纠正）：")
        for e in mis:
            # 抽取约定把「已纠正」写进 observations；有则标注，避免把旧误区当新误区反复纠
            fixed = "已纠正" in "".join(e.get("observations") or [])
            lines.append(f"  - {e['name']}" + ("（已纠正）" if fixed else ""))
    if len(lines) == 1:
        return None
    lines.append(
        "（使用要求：自然衔接——贴合偏好、延续目标、避开已纠过的误区；"
        "不要机械复述这份清单，也不要主动提「记忆」的存在。"
        "他学过哪些概念不在本层，需要时用 memory_search 工具查询。）"
    )
    return "\n".join(lines)


def get_relevant_digest(user_id: str, query: str) -> str | None:
    """L1 检索层：当前用户消息命中的已学概念 + 一步邻接关系，按需注入。

    混合召回（对齐课程 3.1.2.3 的双通道思路，轻量版）：
    - 通道一（关键词）：实体名归一化后作为子串出现在 query 里；
    - 通道二（别名）：抽取阶段让 GLM 记下英文缩写/口语叫法（FFT、拉氏变换…），
      存为「别名：X」observation，这里一并参与匹配——解决「学生说 FFT、
      图谱记的是傅里叶变换」的同义鸿沟。
    不做 query 短词 → 实体名的反向匹配，避免「积」命中「积分」这类高误报；
    更深的语义检索（embedding）留给 memory_search 工具兜底。无命中 → None。
    """
    text = _normalize_text(query)
    if not text:
        return None
    g = _read_graph_safe(user_id)
    if not g:
        return None
    concepts = [
        e for e in g.get("entities", [])
        if (e.get("entityType") or "概念") == "概念"
    ]

    def _match(e: dict) -> bool:
        if _normalize_text(e.get("name") or "") in text:
            return True
        return any(
            _normalize_text(o[len(_ALIAS_PREFIX):]) in text
            for o in (e.get("observations") or [])
            if o.startswith(_ALIAS_PREFIX)
        )

    hits = [e for e in concepts if _match(e)]
    if not hits:
        return None
    hits.sort(key=lambda e: -len(e.get("observations") or []))  # 交流多的优先
    hits = hits[:_MAX_L1_HITS]

    lines: list[str] = ["## 与当前问题相关的已学概念（按记忆图谱匹配）"]
    hit_names: set[str] = set()
    for e in hits:
        # 展示用 observations 剔除别名条目（别名是检索用的，不是讲给学生听的进展）
        obs = [o for o in (e.get("observations") or []) if not o.startswith(_ALIAS_PREFIX)]
        latest = obs[-1][:_OBS_MAX] if obs else ""
        n = len(obs)
        lines.append(
            f"- {e['name']}" + (f"（交流 {n} 次；{latest}）" if latest else "")
        )
        hit_names.add(e["name"])

    # 一步邻接关系：给学生知识结构做锚点（含未命中概念名，便于自然衔接前后置）
    rel_lines: list[str] = []
    seen_rel: set[tuple[str, str, str]] = set()
    for r in g.get("relations", []):
        src, dst = r.get("from") or "", r.get("to") or ""
        if src not in hit_names and dst not in hit_names:
            continue
        key = (src, dst, r.get("relationType") or "")
        if key in seen_rel:
            continue
        seen_rel.add(key)
        rel_lines.append(f"- {src} —{r.get('relationType')}→ {dst}")
        if len(rel_lines) >= _MAX_L1_RELATIONS:
            break
    if rel_lines:
        lines.append("### 相邻关系")
        lines.extend(rel_lines)
    return "\n".join(lines)


def get_digest(user_id: str) -> str | None:
    """全量记忆摘要（L0 + 全部概念/误区/关系）。图谱膨胀后不再逐轮注入，
    留给 API / 调试 / sanity 脚本做全貌查看。聊天链路请用上面两个分层函数。"""
    g = _read_graph_safe(user_id)
    if not g:
        return None
    entities = g.get("entities", [])
    relations = g.get("relations", [])
    if not entities:
        return None

    by_type: dict[str, list[dict]] = {}
    for e in entities:
        by_type.setdefault(e.get("entityType") or "概念", []).append(e)

    lines: list[str] = ["## 关于这位学生的长期记忆（历史对话沉淀的知识图谱）"]

    prefs = [e["name"] for e in by_type.get("偏好", [])][:5]
    goals = [e["name"] for e in by_type.get("目标", [])][:5]
    pref_goal = "；".join(
        part for part in (
            f"偏好：{'、'.join(prefs)}" if prefs else "",
            f"目标：{'、'.join(goals)}" if goals else "",
        ) if part
    )
    if pref_goal:
        lines.append(f"- {pref_goal}")

    concepts = sorted(
        by_type.get("概念", []), key=lambda e: -len(e.get("observations", []))
    )[:20]
    if concepts:
        lines.append("### 学过的概念（按交流频次）")
        for e in concepts:
            obs = e.get("observations", [])
            latest = obs[-1][:_OBS_MAX] if obs else ""
            n = len(obs)
            lines.append(f"- {e['name']}" + (f"（交流 {n} 次；{latest}）" if latest else f"（交流 {n} 次）"))

    mis = by_type.get("误区", [])[:6]
    if mis:
        lines.append("### 已发现的误区（讲解时注意避开/主动纠正）")
        for e in mis:
            lines.append(f"- {e['name']}")

    names = {e["name"] for e in concepts} | {e["name"] for e in mis}
    rel_lines = [
        f"- {r['from']} —{r['relationType']}→ {r['to']}"
        for r in relations if r.get("from") in names and r.get("to") in names
    ][:15]
    if rel_lines:
        lines.append("### 概念关系")
        lines.extend(rel_lines)

    lines.append(
        "（使用要求：自然衔接——延续他在学的概念、避开已纠过的误区、贴合偏好；"
        "不要机械复述这份清单，也不要主动提「记忆」的存在。）"
    )
    return "\n".join(lines)


def search_for_agent(user_id: str, query: str) -> dict[str, Any]:
    """给 GLM 的 memory_search 工具用：查相关实体 + 邻接关系。"""
    try:
        found = bridge.search_nodes(user_id, query)
    except MemoryUnavailable as e:
        return {"ok": False, "error": f"记忆服务不可用: {e}"}
    entities = found.get("entities", [])
    relations = found.get("relations", [])
    return {
        "ok": True,
        "query": query,
        "entities": [
            {
                "name": e.get("name"),
                "entityType": e.get("entityType"),
                "observations": (e.get("observations") or [])[:5],
            }
            for e in entities[:10]
        ],
        "relations": [
            f"{r.get('from')} —{r.get('relationType')}→ {r.get('to')}"
            for r in relations[:10]
        ],
    }


# ── 写路径 ───────────────────────────────────────────────


def extract_and_write(
    user_id: str, course_id: str, user_text: str, assistant_text: str
) -> dict[str, Any] | None:
    """单轮抽取：LLM 结构化抽取 → 去重合并进图谱。后台调用，失败只记日志。"""
    if not bridge.available():
        return None
    if not (user_text or "").strip() or not (assistant_text or "").strip():
        return None
    try:
        extraction = _extract(course_id, user_text, assistant_text, known=_known_names(user_id))
        if extraction is None:
            return None
        return apply_extraction(user_id, extraction)
    except MemoryUnavailable as e:
        logger.warning("记忆抽取降级（memory 不可用）: %s", e)
        return None
    except Exception:  # noqa: BLE001
        logger.exception("记忆抽取失败（不影响主链路）")
        return None


def apply_extraction(user_id: str, extraction: dict[str, Any]) -> dict[str, Any]:
    """把一次抽取结果去重合并进图谱：已有实体只补 observations，关系去重。"""
    entities = _validate_entities(extraction.get("entities"))
    relations = _validate_relations(extraction.get("relations"))

    graph = _read_graph_safe(user_id) or {"entities": [], "relations": []}
    existing = {e["name"].lower(): e for e in graph.get("entities", [])}
    existing_rel = {
        (r["from"].lower(), r["to"].lower(), r["relationType"])
        for r in graph.get("relations", [])
    }

    to_create: list[dict] = []
    obs_adds: list[dict] = []
    for e in entities:
        # 别名落库约定：折进 observations（server-memory 的实体字段是自由 schema，
        # 用「别名：X」条目存最稳，读回按前缀解析）
        alias_obs = [_ALIAS_PREFIX + a for a in e.get("aliases") or []]
        hit = existing.get(e["name"].lower())
        if hit is None:
            to_create.append({
                "name": e["name"],
                "entityType": e["entityType"],
                "observations": (e["observations"] + alias_obs)[:_OBS_FULL],
            })
            existing[e["name"].lower()] = e
        else:
            have = set(hit.get("observations") or [])
            new_obs = [o for o in e["observations"] if o not in have]
            new_obs += [a for a in alias_obs if a not in have]  # 新别名也补
            if new_obs:
                obs_adds.append({"entityName": hit["name"], "contents": new_obs[:_OBS_FULL]})

    valid_names = set(existing.keys())
    rel_seen = set(existing_rel)
    rel_to_create: list[dict] = []
    for r in relations:
        key = (r["from"].lower(), r["to"].lower(), r["relationType"])
        if key in rel_seen:
            continue
        if r["from"].lower() not in valid_names or r["to"].lower() not in valid_names:
            continue
        rel_seen.add(key)
        rel_to_create.append(r)

    bridge.create_entities(user_id, to_create)
    bridge.add_observations(user_id, obs_adds)
    bridge.create_relations(user_id, rel_to_create)
    return {
        "entities_created": len(to_create),
        "observations_added": sum(len(o["contents"]) for o in obs_adds),
        "relations_created": len(rel_to_create),
    }


def rebuild_from_history(db: Session, user_id: str, *, max_messages: int = 120) -> dict[str, Any]:
    """清空记忆 → 按批次重放历史消息重建图谱（冷启动/修复用，同步执行）。"""
    if not bridge.available():
        return {"ok": False, "error": "MCP 记忆服务不可用（缺 node 或 server-memory）"}

    msgs = (
        db.query(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.user_id == user_id)
        .order_by(Message.created_at.asc())
        .limit(max_messages)
        .all()
    )
    bridge.delete_all(user_id)

    chunks = _chunk_messages(msgs, size=8)
    total = {"entities_created": 0, "observations_added": 0, "relations_created": 0}
    known: list[str] = []
    for i, chunk in enumerate(chunks):
        transcript = "\n".join(
            f"{'学生' if m.role == 'user' else '导师'}：{(m.text or '')[:400]}"
            for m in chunk
        )
        user_part = "\n".join((m.text or "")[:400] for m in chunk if m.role == "user")
        asst_part = "\n".join((m.text or "")[:400] for m in chunk if m.role == "assistant")
        extraction = _extract(
            "history",
            user_part or transcript,
            asst_part or "",
            known=known,
            fallback_text=transcript,
        )
        if extraction is None:
            continue
        stats = apply_extraction(user_id, extraction)
        for k in total:
            total[k] += stats[k]
        # 已知实体滚动更新，减少后批次的重复建名
        g = _read_graph_safe(user_id)
        if g:
            known = [e["name"] for e in g.get("entities", [])][:40]

    total.update({"ok": True, "chunks": len(chunks), "messages_scanned": len(msgs)})
    return total


# ── 内部：LLM 抽取与校验 ─────────────────────────────────


def _read_graph_safe(user_id: str) -> dict[str, Any] | None:
    if not bridge.available():
        return None
    try:
        return bridge.read_graph(user_id)
    except MemoryUnavailable:
        return None


def _known_names(user_id: str, limit: int = 40) -> list[str]:
    g = _read_graph_safe(user_id)
    if not g:
        return []
    return [e["name"] for e in g.get("entities", [])][:limit]


def _normalize_text(s: str) -> str:
    """L1 匹配用归一化：去空白 + 小写，让「PID 控制器」「pid控制器」等价。"""
    return re.sub(r"\s+", "", s or "").lower()


def _extract(
    course_id: str,
    user_text: str,
    assistant_text: str,
    *,
    known: list[str],
    fallback_text: str | None = None,
) -> dict[str, Any] | None:
    """调 GLM 做结构化抽取；解析失败重试一次裸 JSON 提取，仍失败返回 None。

    对齐 docs/07 §4.3 实验结论：GLM 5.3 Flash 的 thinking 关不掉
    （enable_thinking=False 只是「不主动开启」），reasoning 会先吃 max_tokens——
    抽取输出比意图分类长，max_tokens 必须给 reasoning 留足空间（实测 1200 会被
    吃穿导致 content 为空），且 content 为空时 reasoning_content 里往往已有
    完整 JSON 草稿，值得兜底解析。
    """
    prompt = _EXTRACT_TMPL.format(
        course=course_id or "general",
        known="、".join(known) if known else "（暂无）",
        user_text=(user_text or "")[:2000],
        assistant_text=(assistant_text or "")[:2000],
    )
    if fallback_text:
        prompt += f"\n\n<补充上下文>\n{fallback_text[:1500]}\n</补充上下文>"

    for attempt in range(2):
        try:
            resp = glm_client.chat(
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=2048,         # 给 reasoning 留足空间，防 content 被吃空
                enable_thinking=False,   # 不主动开启（实际默认仍开，见上）
                reasoning_effort="low",  # 结构化抽取，压低思考档位省时省 token
                response_format={"type": "json_object"},
            )
            msg = resp.choices[0].message
            raw = (msg.content or "").strip()
            if not raw:
                # content 被 reasoning 吃空：从思维链里捞 JSON 草稿兜底
                reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
                raw = reasoning
        except Exception as e:  # noqa: BLE001
            logger.warning("记忆抽取 LLM 调用失败(第 %s 次): %s", attempt + 1, e)
            continue
        data = _parse_json(raw)
        if data is not None:
            return data
    return None


def _parse_json(raw: str) -> dict[str, Any] | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _validate_entities(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for e in raw:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()[:_NAME_MAX]
        if not name or name.lower() in seen:
            continue
        etype = str(e.get("entityType") or "概念").strip()
        if etype not in ENTITY_TYPES:
            etype = "概念"
        obs = [
            str(o).strip()[:_OBS_MAX]
            for o in (e.get("observations") or [])
            if str(o).strip()
        ][:_MAX_OBS_PER_ENTITY]
        # 别名（混合召回的第二通道）：只收概念，去重、去与本体同名
        aliases: list[str] = []
        if etype == "概念":
            for a in e.get("aliases") or []:
                a = str(a).strip()[:_NAME_MAX]
                if a and a.lower() != name.lower() and a.lower() not in {x.lower() for x in aliases}:
                    aliases.append(a)
                if len(aliases) >= _MAX_ALIASES:
                    break
        seen.add(name.lower())
        out.append({"name": name, "entityType": etype, "observations": obs, "aliases": aliases})
        if len(out) >= _MAX_ENTITIES_PER_TURN:
            break
    return out


def _validate_relations(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    if not isinstance(raw, list):
        return out
    for r in raw:
        if not isinstance(r, dict):
            continue
        src = str(r.get("from") or "").strip()[:_NAME_MAX]
        dst = str(r.get("to") or "").strip()[:_NAME_MAX]
        rtype = str(r.get("relationType") or "相关").strip()[:16] or "相关"
        if not src or not dst:
            continue
        key = (src.lower(), dst.lower(), rtype)
        if key in seen:
            continue
        seen.add(key)
        out.append({"from": src, "to": dst, "relationType": rtype})
        if len(out) >= _MAX_RELATIONS_PER_TURN:
            break
    return out


def _chunk_messages(msgs: list[Any], size: int = 8) -> list[list[Any]]:
    """把消息切成尽量以 user 开头的批次（一个回合一个回合地喂）。"""
    chunks: list[list[Any]] = []
    cur: list[Any] = []
    for m in msgs:
        if m.role == "user" and len(cur) >= size:
            chunks.append(cur)
            cur = []
        cur.append(m)
    if cur:
        chunks.append(cur)
    return chunks
