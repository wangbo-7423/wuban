"""会话级记忆（Write 策略）：把信息搬到上下文窗口外持久化，跨轮次保持状态。

对照上下文工程五策略中的 Write，在**会话维度**补齐两层（长期记忆层在
memory_service 的知识图谱抽取，跨会话生效，不在这里）：

1. **短期记忆层的 Write（滚动压缩摘要）**：未压缩消息超过阈值时，把早期
   对话用 GLM 压缩成摘要写进 `conversations.summary`（与旧摘要滚动合并），
   `summary_covered` 记录已覆盖条数；原始消息仍全量留痕在 messages 表。
   /chat 只注入「摘要 + 最近窗口」，历史层不再随轮数无限增长。
2. **任务状态层的 Write（Scratchpad 草稿纸）**：每轮后从最新 assistant 卡片
   用启发式规则提取「在学什么 / 进行到哪 / 等学生做什么」，写入
   `conversations.scratchpad`。零 LLM 成本——卡片协议本身就是结构化的，
   直接读字段即可，这正是结构化输出换来的免费任务状态。

两个写路径都跑在 BackgroundTasks 线程：独立开 DB 会话、各自兜异常，
失败只记日志，绝不影响主对话链路。
"""
from __future__ import annotations

import logging
from typing import Any

from app.agent import glm_client
from app.core.db import SessionLocal
from app.models import Conversation
from app.repositories import chat_repo

logger = logging.getLogger(__name__)

# 压缩触发：未压缩消息达到 12 条（6 个来回）就压一次
_COMPRESS_TRIGGER = 12
# 压缩后保留最近 8 条不压——与 /chat 的历史窗口 limit=8 严格一致
_COMPRESS_KEEP = 8
# 单条消息参与压缩的最大字符（长 math 卡截断即可，摘要不需要逐字）
_MAX_MSG_CHARS = 400
# 摘要上限（字符）。约 300~400 token，对应分层预算里的「任务状态 + 历史摘要」份额
_MAX_SUMMARY_CHARS = 700

_COMPRESS_SYSTEM = """你会话摘要器。把「AI 伴学」场景的早期对话压缩成一份简洁摘要。

只保留四类信息：
1. 学习主题与目标（他在学什么、为什么学）；
2. 已讲到的步骤与结论（讲到第几步、得出过什么结果——避免以后重复讲）；
3. 学生暴露的卡点、误区、纠错记录；
4. 悬而未决的问题（AI 抛出但学生还没接的引申疑问、待验证的预测）。

硬性规则：
- 陈述式、第三人称（称「学生」），不带寒暄与过程性细节；
- 若提供了「既有摘要」，把新对话的信息合并进去输出**一份完整摘要**（全量替换，不是增量追加）；
- 300 字以内；没有可保留的信息就尽量短。
- 只输出摘要正文，不要任何前言、标题或解释。"""

_COMPRESS_TMPL = """<既有摘要>
{old_summary}
</既有摘要>

<新对话>
{transcript}
</新对话>

输出合并后的完整摘要。"""


def after_turn(conversation_id: str, hit_concepts: list[str] | None = None) -> None:
    """轮后入口（BackgroundTasks 调用）：更新草稿纸 → 推进学习状态 → 视情况压缩摘要。"""
    try:
        _update_scratchpad(conversation_id)
    except Exception:  # noqa: BLE001
        logger.exception("Scratchpad 更新失败 conv=%s（不影响主链路）", conversation_id)
    try:
        _update_learning_state(conversation_id, hit_concepts or [])
    except Exception:  # noqa: BLE001
        logger.exception("学习状态更新失败 conv=%s（不影响主链路）", conversation_id)
    try:
        _maybe_compress(conversation_id)
    except Exception:  # noqa: BLE001
        logger.exception("会话摘要压缩失败 conv=%s（不影响主链路）", conversation_id)


# ── 任务状态层：Scratchpad（启发式，零 LLM 成本）──────────────


def _update_scratchpad(conversation_id: str) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return None
        pad = _scratchpad_from_messages(chat_repo.list_messages(db, conversation_id))
        # learning_state 是独立维护的键（docs/17），草稿纸整体重建时必须保留
        merged = dict(pad) if pad else {}
        prev_state = (conv.scratchpad or {}).get("learning_state")
        if prev_state is not None:
            merged["learning_state"] = prev_state
        conv.scratchpad = merged or None
        db.commit()
        return merged or None
    finally:
        db.close()


def _scratchpad_from_messages(msgs: list[Any]) -> dict[str, str] | None:
    """从消息序列末尾提取任务状态（鸭子类型：只要有 role/card_type/text/payload）。

    启发式规则（故意简单到可解释，答辩讲得清）：
    - topic 取学生最后一条非空消息（他在学什么以他最近问的为准）；
    - pending 由最后一张 assistant 卡的类型决定：question/practice 是明确的
      「等学生回应」，math/engineering 看结尾是否带问句；
    - 没有任何 assistant 输出（刚开场的会话）→ None，不注入。
    """
    last_asst = next((m for m in reversed(msgs) if m.role == "assistant"), None)
    if last_asst is None:
        return None
    last_user = next(
        (m for m in reversed(msgs) if m.role == "user" and (m.text or "").strip()), None
    )

    asst_text = (last_asst.text or "").strip()
    pad: dict[str, str] = {}
    if last_user is not None:
        pad["topic"] = last_user.text.strip()[:60]
    pad["progress"] = _progress_text(last_asst)
    pending = _pending_text(last_asst)
    if pending:
        pad["pending"] = pending
    return pad or None


def _progress_text(m: Any) -> str:
    """assistant 卡 → 「已进行」一句话。"""
    payload = getattr(m, "payload", None) or {}
    ctype = m.card_type or "text"
    if ctype == "math":
        math_block = payload.get("math") or {}
        steps = math_block.get("steps") or []
        if steps:
            note = (steps[-1] or {}).get("note") or (steps[-1] or {}).get("expr") or ""
            return f"已讲到第 {len(steps)} 步" + (f"（{str(note)[:50]}）" if note else "")
    if ctype == "engineering":
        steps = payload.get("engineering_steps") or []
        if steps:
            title = (steps[-1] or {}).get("title") or ""
            return f"实操共 {len(steps)} 步，最新一步：{str(title)[:50]}"
    if ctype == "feedback":
        return "已给审阅式反馈"
    if ctype == "understand":
        return "已做概念讲解"
    return f"最近输出：{((m.text or '').strip())[:50]}"


def _pending_text(m: Any) -> str | None:
    """assistant 卡 → 「等学生做什么」；没有明确的开放点就 None。"""
    payload = getattr(m, "payload", None) or {}
    ctype = m.card_type or "text"
    if ctype == "question":
        stem = (payload.get("stem") or (m.text or "")).strip()
        return f"等学生回应反问：{stem[:60]}"
    if ctype == "practice":
        stem = (payload.get("stem") or (m.text or "")).strip()
        return f"「预测→验证」进行中，等学生先猜：{stem[:60]}"
    if ctype == "engineering":
        return "等学生动手后贴出代码/截图"
    text = (m.text or "").strip()
    if text.endswith(("？", "?")):
        return f"结尾抛了疑问，等学生接：{text[-60:]}"
    return None


# ── 学习状态层：按概念键控的跨轮卡壳信号（docs/17，纯逻辑零 LLM）──
#
# 与 scratchpad/summary 回答的问题不同：那两层是「我们聊到哪了」（连续性），
# 这里是「学生在哪个概念上反复磕绊」（风险追踪）。key 用 kg_lookup 命中的
# 规范化节点名做精确匹配——跨话题安全靠结构（概念对不上就不触发），
# 不靠模型读散文猜相关性。


_LEARNING_WINDOW = 5  # streak 断更多少轮后移出窗口（docs/17 §7.1 初版取向）


def advance_learning_state(
    state: dict[str, Any] | None,
    hit_concepts: list[str],
    *,
    window: int = _LEARNING_WINDOW,
) -> dict[str, Any]:
    """纯函数：一轮结束后推进学习状态（有单测守门，tests/test_learning_state.py）。

    hit_concepts 是本轮 kg_lookup 命中的节点名（去重保序）；KG 未收录的
    概念不进列表（docs/17 §7.3——键控价值在精确匹配）。streak 语义：该概念
    出现在窗口内的轮数，每命中一轮 +1；无命中轮次只推进 round（streak 冻结，
    靠窗口淘汰退场）。
    """
    state = dict(state or {})
    rnd = int(state.get("round") or 0) + 1
    stuck = {
        str(c["concept"]): dict(c)
        for c in (state.get("stuck_concepts") or [])
        if isinstance(c, dict) and c.get("concept")
    }
    # 窗口淘汰：断更超过 window 轮的概念退场（学生换话题且没再回来）
    stuck = {
        c: v for c, v in stuck.items() if rnd - int(v.get("last_round") or 0) <= window
    }
    hits = list(dict.fromkeys(h for h in hit_concepts if h))
    for concept in hits:
        v = stuck.get(concept) or {"concept": concept, "streak": 0, "last_round": 0}
        v["streak"] = int(v["streak"]) + 1
        v["last_round"] = rnd
        stuck[concept] = v
    state.update({
        "round": rnd,
        "current_topic": hits[-1] if hits else state.get("current_topic"),
        "stuck_concepts": sorted(
            stuck.values(), key=lambda v: (-int(v["streak"]), -int(v["last_round"]))
        ),
        "new_concepts_last_round": len(hits),  # docs/17 §7.2 初版：数工具命中数
    })
    return state


def render_learning_context(
    state: dict[str, Any] | None,
    pending: str | None,
    hit_concepts: list[str],
) -> str:
    """切卡侧的跨轮信号渲染（docs/17 §5.2）：结构化状态 + 确定性规则判定。

    state 是截至上一轮的状态；hit_concepts 是本轮命中。规则只产出「建议」，
    发卡与否、措辞仍归切卡模型（docs/17 §4 的分工：规则管触发，模型管措辞）。
    返回空串表示无可注入。
    """
    hits = list(dict.fromkeys(h for h in hit_concepts if h))
    stuck = {
        str(c["concept"]): int(c.get("streak") or 0)
        for c in (state or {}).get("stuck_concepts") or []
        if isinstance(c, dict) and c.get("concept")
    }
    if not stuck and not hits and not pending:
        return ""

    lines: list[str] = ["## 学习状态（跨轮，按概念追踪）"]
    topic = (state or {}).get("current_topic")
    if topic:
        lines.append(f"- 当前话题：{topic}")
    if stuck:
        lines.append(
            "- 卡壳概念：" + "、".join(f"{c}（连续 {s} 轮）" for c, s in stuck.items())
        )
    if pending:
        lines.append(f"- 上一轮遗留疑问：{pending}")
    prev_new = int((state or {}).get("new_concepts_last_round") or 0)
    if prev_new:
        lines.append(f"- 上一轮引入新概念数：{prev_new}")
    if hits:
        lines.append(f"- 本轮命中的概念：{'、'.join(hits)}")

    # 规则判定（docs/17 §4）：eff = 既往 streak + 本轮命中 1 次
    verdicts: list[str] = []
    for c in hits:
        eff = stuck.get(c, 0) + 1
        if eff >= 3:
            verdicts.append(f"「{c}」已连续 {eff} 轮出现 → 建议发「负荷过高」warning")
        elif eff == 2:
            verdicts.append(f"「{c}」本轮再次出现（累计 {eff} 轮）→ 建议发「前置缺失」warning")
    if len(hits) >= 3:
        verdicts.append(f"本轮一次命中 {len(hits)} 个概念 → 建议发「负荷过高」warning")
    if verdicts:
        lines.append("系统规则判定（触发 = 建议发卡，是否发、如何措辞仍由你定）：")
        lines.extend(f"- {v}" for v in verdicts)
    return "\n".join(lines)


def _update_learning_state(conversation_id: str, hit_concepts: list[str]) -> None:
    """advance 的 DB 包装：读 scratchpad.learning_state → 推进 → 合并写回。

    与 _update_scratchpad 各自整档写 scratchpad，顺序上它先重建（保留本键）、
    本函数后推进，两者都兜异常（after_turn），任何一路挂了不影响主链路。
    """
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return
        pad = dict(conv.scratchpad or {})
        pad["learning_state"] = advance_learning_state(
            pad.get("learning_state"), hit_concepts
        )
        conv.scratchpad = pad
        db.commit()
    finally:
        db.close()


# ── 短期记忆层：滚动压缩摘要（LLM）───────────────────────────


def _maybe_compress(conversation_id: str) -> None:
    """未压缩消息超过阈值时，把 [covered, total-KEEP) 压缩合并进摘要。"""
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return
        msgs = chat_repo.list_messages(db, conversation_id)
        total = len(msgs)
        covered = int(conv.summary_covered or 0)
        if total - covered < _COMPRESS_TRIGGER:
            return
        cut = total - _COMPRESS_KEEP  # 留最近 KEEP 条做原始窗口
        if cut <= covered:
            return
        transcript = "\n".join(
            f"{'学生' if m.role == 'user' else 'AI'}：{(m.text or '').strip()[:_MAX_MSG_CHARS]}"
            for m in msgs[covered:cut]
            if (m.text or "").strip()
        )
        if not transcript:
            conv.summary_covered = cut
            db.commit()
            return
        summary = _compress_llm(conv.summary or "", transcript)
        if not summary:
            return  # LLM 失败：下轮重试（covered 不推进，消息仍在库里）
        conv.summary = summary[:_MAX_SUMMARY_CHARS]
        conv.summary_covered = cut
        db.commit()
        logger.info(
            "会话摘要已压缩 conv=%s covered=%s len=%s", conversation_id, cut, len(summary)
        )
    finally:
        db.close()


def _compress_llm(old_summary: str, transcript: str) -> str | None:
    """GLM 滚动压缩：旧摘要 + 新对话 → 一份完整摘要。失败返回 None。"""
    try:
        resp = glm_client.chat(
            messages=[
                {"role": "system", "content": _COMPRESS_SYSTEM},
                {
                    "role": "user",
                    "content": _COMPRESS_TMPL.format(
                        old_summary=old_summary or "（无，这是第一次压缩）",
                        transcript=transcript[:6000],
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=800,
            enable_thinking=False,  # 压缩不需要思维链，省时省钱
            reasoning_effort="low",  # glm-5.3 关不掉思考，用档位压（思考 token 也吃 800 预算）
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception as e:  # noqa: BLE001
        logger.warning("摘要压缩 LLM 调用失败（下轮重试）: %s", e)
        return None


# ── 读路径：给 /chat 注入用的渲染 ────────────────────────────


def render_session_context(summary: str | None, scratchpad: dict | None) -> str:
    """把会话级 Write 成果渲染成注入 extra_system 的文本。空则返回空串。"""
    parts: list[str] = []
    if summary:
        parts.append(
            "## 本会话早期进展摘要（更早的对话已压缩存档，原文全量在库）\n" + summary
        )
    pad = scratchpad or {}
    lines = [line for line in (
        "## 任务状态（上一轮结束时）",
        f"- 正在学习：{pad['topic']}" if pad.get("topic") else "",
        f"- 已进行：{pad['progress']}" if pad.get("progress") else "",
        f"- 待学生回应：{pad['pending']}" if pad.get("pending") else "",
    ) if line]
    if len(lines) > 1:
        lines.append("（自然衔接上轮进度，不要重复已讲内容；学生换话题就放手跟过去。）")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def build_session_context(conversation_id: str) -> str:
    """读库并渲染（/chat 同步路径用，单行查询，失败降级为空）。"""
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return ""
        return render_session_context(conv.summary, conv.scratchpad)
    except Exception:  # noqa: BLE001
        logger.exception("会话上下文读取失败 conv=%s", conversation_id)
        return ""
    finally:
        db.close()
