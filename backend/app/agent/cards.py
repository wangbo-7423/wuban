"""把 Agent 最终回答 + 工具结果，格式化成一~多张 CardMessage（结构化输出）。

设计：
- 主 Agent 循环（orchestrator）只负责「思考 + 调工具 + 产出最终文本」；
- 本模块是独立的一步：用 GLM 的 JSON mode 把上述文本切成符合卡片协议的 CardMessage[]；
- 失败（解析/校验不过）则回退到单张 text/understand 卡，绝不破坏主流程。

这一步是「激活前端 5 个死组件（MathCard / EngineeringCard / PracticeCard /
ChoiceCard / RecommendationCard）」的关键：只要 GLM 在此打出对应 card_type +
正确 payload，前端已有组件即可渲染。
"""
from __future__ import annotations

import logging
import uuid
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.agent import glm_client
from app.agent.orchestrator import AgentResult
from app.schemas.card import (
    CardEvidence,
    CardMessage,
    CardPayload,
    InteractiveBlock,
    InteractiveProbe,
)

logger = logging.getLogger(__name__)


class _CardsEnvelope(BaseModel):
    """切卡结果的信封 schema：只锁「cards 是对象数组」这一层形状，
    字段级容错清洗仍归 _parse_card（枚举拼错不该触发重试，该清空保卡）。"""

    cards: list[dict[str, Any]] = Field(default_factory=list)

_FORMAT_SYSTEM = """你是「AI 伴学」的卡片格式化器。下面会给你一段助手的回答原文，以及它调用过的工具结果摘要。
请把这段回答拆成 1~3 张结构化卡片（CardMessage），供前端渲染。

# 卡片类型（只能选这些）
text / question / understand / math / engineering / practice / choice / recommendation / feedback / evidence / progress / warning / metacog / motivation / scaffold_progress / tool_call

# 切卡规则
- 含数学推导（公式/积分/极限/矩阵）→ 用 `math` 卡，payload.math={problem, steps:[{expr,note}], answer, unit}；expr 用 LaTeX，如 "\\frac{a}{b}"、"\\int_0^1 x^2 dx"。note 只用通俗文字说明这一步在做什么、为什么这么做，公式一律放 expr（note 里不写 LaTeX，需要提公式时用文字描述，如「用幂函数积分公式」）。
- 含工程实操步骤（命令/注意事项）→ 用 `engineering` 卡，payload.engineering_steps=[{title, command, expected, pitfalls, next_step_hint}]。
- 学生贴了代码、你用 code_runner 跑过 → 用 `feedback` 卡给审阅式反馈（先肯定、再指出可改处并说明原因、不打分）；若 code_runner 生成了图表 URL，把 url 列表放进 payload.engineering_artifacts=[{type:"image", url:"..."}]，让学生直接看到运行结果图。
- 你用 project_guide 提议了微项目 → 用 `recommendation` 卡，payload.options=[{value, label}]，label 写项目名称+耗时，reason 写为什么适合。
- 出了练习题（题干 + 可作答）→ 用 `practice` 卡，payload.stem 写题干，payload.max_hints 给 1~3。
- 概念辨析 / 反例选择题 → 用 `choice` 卡，payload.options=[{value, label}]。
- 推荐下一步学习资源/路径 → 用 `recommendation` 卡，payload.options=[{value, label}]，reason 写理由。
- 反问学生（先问再答）→ 用 `question` 卡。
- 普通解释/过渡 → `understand` 或 `text`。
- 回答中提示了学生未掌握的前置概念（如工具结果里有前置节点、或你提醒「先学 X 再看这个」）→ 补一张 `warning` 卡，语义是「前置缺失」；回答中一次引入了大量新概念/多步推导并提醒学生分步消化 → `warning` 卡，语义是「负荷过高」。「学习状态」里的系统规则判定是发卡的强依据（触发 = 建议发，是否发、如何措辞仍由你定）；卡壳概念与当前问题对不上话题时不要发。warning 卡只在确实有风险时发，不要每轮都发。
- 「学习状态」只用于判定风险与发卡参考，不要把它复述成正文（那是状态记录，不是新内容）。
- 其余（反馈/元认知等）按语义选。

# 输出格式（严格 JSON，不要多余文字）
{
  "cards": [
    {
      "card_type": "math",
      "text": "这张卡的完整正文（Markdown）",
      "payload": { "math": { "problem": "...", "steps": [{"expr":"...","note":"..."}], "answer":"...", "unit":"..." } },
      "strategy": ["分解", "可视化"],
      "scaffold_level": "faded",
      "gave_answer": false,
      "confidence": 0.9
    }
  ]
}

规则：
- `text` 是卡的完整正文，必须保留回答原文里的实质内容（解释、定义、对比表格、列表、推导），用 Markdown 写：表格用 | 分隔的 Markdown 表格，公式用 $...$ 或 $$...$$，前端会渲染。不要只写一句引导语——引导语之后必须跟正文，丢了正文学生就看不到讲解。
- 只有 math/engineering 卡的细节放 payload 对应结构化字段（此时 text 仍是引导语+说明）；其余卡片类型的全部内容都放 text。
- scaffold_level 只能取：example / faded / hint / independent。
- 不要编造工具没给出的数据；math/engineering 卡的字段必须来自回答原文或工具结果。
- 第一张卡通常是主卡；其余是补充（练习/反问/推荐）。
- 如果整段就是普通解释，返回单张 understand 卡即可，不要硬切。
"""

_VALID_TYPES = {
    "text", "question", "understand", "math", "engineering", "practice",
    "choice", "recommendation", "feedback", "evidence", "progress", "warning",
    "metacog", "motivation", "scaffold_progress", "tool_call",
}


def _summarize_tools(result: AgentResult) -> str:
    if not result.tool_calls:
        return "（无工具调用）"
    lines: list[str] = []
    for tc in result.tool_calls:
        r = tc.get("result") or {}
        ok = tc.get("ok")
        name = tc.get("tool_name")
        if not isinstance(r, dict):
            lines.append(f"- {name} (ok={ok}): {str(r)[:200]}")
            continue

        if name == "code_runner":
            # 代码执行结果：把输出/报错/退出码/图表都给到格式化器
            figs = r.get("figures") or []
            fig_note = f"；生成图表 {len(figs)} 张: " + ", ".join(
                f["url"] for f in figs
            ) if figs else ""
            lines.append(
                f"- code_runner (ok={ok}, returncode={r.get('returncode')}, "
                f"timed_out={r.get('timed_out')}):\n"
                f"  stdout:\n{(r.get('stdout') or '(空)')[:1500]}\n"
                f"  stderr:\n{(r.get('stderr') or '(空)')[:800]}{fig_note}"
            )
        elif name == "project_guide":
            if r.get("matched"):
                opts = r.get("options") or []
                opt_note = "; ".join(
                    f"{o.get('title')}({o.get('minutes')}min)" for o in opts
                )
                lines.append(
                    f"- project_guide (ok={ok}, topic={r.get('topic')}): 选项={opt_note}"
                )
            else:
                sugg = [s.get("name") for s in (r.get("suggestions") or [])]
                lines.append(
                    f"- project_guide (ok={ok}, 未命中): 可提议={sugg}"
                )
        elif name == "make_visual":
            lines.append(
                f"- make_visual (ok={ok}, template={r.get('template')}, "
                f"title={r.get('title')}): 交互可视化卡已生成并将自动附在回复卡片里"
                "（无需在正文复述其内容）。正文职责：先抛预测问题让学生猜 → "
                f"提示他动手拖 → 请他汇报观察。预测问题：{r.get('probe_question')}"
            )
        else:
            brief = str(
                r.get("value") or r.get("matched") or r.get("latex")
                or r.get("error") or ""
            )[:200]
            lines.append(f"- {name} (ok={ok}): {brief}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    """围栏兼容的 JSON 抽取——实现已上收到 glm_client.extract_json_object，
    这里保留别名供既有调用/测试引用。"""
    return glm_client.extract_json_object(text)


# 切卡侧学习上下文的防御性截断（render_learning_context 产物本身只有几行）
_LEARNING_CTX_MAX_CHARS = 1200


def kg_hit_concepts(result: AgentResult) -> list[str]:
    """本轮 kg_lookup 命中的规范化节点名（去重保序；未命中/失败不计）。

    学习状态的键控原料（docs/17）：streak 按「概念出现过的轮数」计数，
    概念名必须来自 KG 规范节点——模糊文本匹配会重新引入话题漂移问题。
    """
    out: list[str] = []
    for tc in result.tool_calls or []:
        if tc.get("tool_name") != "kg_lookup" or not tc.get("ok"):
            continue
        matched = (tc.get("result") or {}).get("matched") or {}
        name = str(matched.get("name") or "").strip()
        if name:
            out.append(name)
    return list(dict.fromkeys(out))


def format_cards(
    result: AgentResult,
    *,
    user_text: str,
    course_id: str,
    learning_context: str | None = None,
) -> list[CardMessage]:
    """把 AgentResult 格式化成 1~3 张 CardMessage。失败回退单卡。

    learning_context：结构化学习状态的渲染产物（session_memory.
    render_learning_context，含卡壳 streak 与确定性规则判定）。切卡模型本身
    单轮失忆——跨轮的「前置缺失/负荷过高」信号（学生连续多轮卡在同一概念等）
    只能从这里进来，否则 warning 判定退化为本轮快照。
    """
    prompt = (
        f"课程：{course_id}\n"
        f"学生问题：{user_text}\n\n"
        f"助手回答原文：\n{result.text}\n\n"
        f"工具调用摘要：\n{_summarize_tools(result)}"
    )
    if learning_context:
        prompt += f"\n\n{learning_context[:_LEARNING_CTX_MAX_CHARS]}"
    try:
        data = glm_client.chat_structured(
            messages=[
                {"role": "system", "content": _FORMAT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            schema=_CardsEnvelope,
            fix_attempts=1,  # 形状散架给一次自纠；字段级容错在 _parse_card，不靠重试
            enable_thinking=False,
            # 切卡是纯结构化后处理，不需要深度推理；glm-5.3 关不掉思考，
            # 只能把档位压到 low——这一步跑在 SSE 结束后，慢一秒用户就多等一秒。
            reasoning_effort="low",
            temperature=0.2,
        )
        cards_raw = data.cards
        cards = [_parse_card(c) for c in cards_raw]
        cards = [c for c in cards if c is not None]
        if cards:
            # 搜索引用确定性落卡（docs/11 §3.4）：不指望切卡模型自觉转述，
            # 本轮 web_search 命中的官方来源直接合并进主卡 evidence[]
            web_ev = _web_evidence(result)
            if web_ev:
                cards[0] = _merge_evidence(cards[0], web_ev)
            return cards + _viz_cards(result)
    except Exception as e:  # noqa: BLE001
        logger.warning("format_cards 失败，回退单卡: %s", e)
    # 切卡失败走兜底单卡，搜索来源同样落卡（evidence 不依赖切卡模型的自觉）
    web_ev = _web_evidence(result)
    if web_ev:
        return [_merge_evidence(_fallback_card(result), web_ev)] + _viz_cards(result)
    return [_fallback_card(result)] + _viz_cards(result)


def _viz_cards(result: AgentResult) -> list[CardMessage]:
    """make_visual 的确定性落卡（docs/16 §3）：HTML 不走切卡模型——用工具留痕的
    相同参数从模板重建（确定性等价，GLM 上下文里始终只有小结）。频控：每轮至多 1 张。"""
    out: list[CardMessage] = []
    for tc in result.tool_calls or []:
        if tc.get("tool_name") != "make_visual" or not tc.get("ok"):
            continue
        args = tc.get("args") or {}
        try:
            from app.viz import builder as _viz
            payload = _viz.build(
                args.get("template") or "",
                params=args.get("params"),
                title=args.get("title"),
                preview_text=args.get("preview_text"),
                probe_question=args.get("probe_question"),
                reveal_hint=args.get("reveal_hint"),
            )
        except Exception:  # noqa: BLE001
            logger.warning("make_visual 卡片重建失败（跳过）", exc_info=True)
            continue
        probe = payload.get("probe")
        block = InteractiveBlock(
            html=payload["html"],
            preview_text=payload.get("preview_text"),
            probe=InteractiveProbe(**probe) if probe else None,
            meta=payload.get("meta"),
        )
        out.append(CardMessage(
            id=str(uuid.uuid4()),
            card_type="interactive",
            text="动手试一试（拖动看看会发生什么）：",
            payload=CardPayload(interactive=block),
            strategy=["可视化"],
        ))
        break  # 频控：每轮至多 1 张（docs/16 §2.3）
    return out


def _web_evidence(result: AgentResult) -> list[dict[str, Any]]:
    """本轮 web_search 的命中 → evidence 条目（域去重，最多 3 条）。

    只取 ok=True 的调用；source 存域名的可读形式，title 进 page 字段，
    authority（web_search 域策略打的权威分）直接当 confidence——
    前端来源角标会把分数一并渲染，学生能看出「这是官方源还是博客」。
    """
    out: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    for tc in result.tool_calls:
        if tc.get("tool_name") != "web_search" or not tc.get("ok"):
            continue
        r = tc.get("result") or {}
        for item in (r.get("results") or []):
            if len(out) >= 3:
                return out
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            try:
                domain = (urllib.parse.urlparse(url).hostname or "").lower()
            except ValueError:
                continue
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            out.append(
                {
                    "source": domain,
                    "page": (str(item.get("title") or "")[:60] or None),
                    "confidence": float(item.get("authority") or 0.5),
                }
            )
    return out


def _merge_evidence(card: CardMessage, extra: list[dict[str, Any]]) -> CardMessage:
    """把搜索来源并入卡片的 evidence[]（与既有来源按域去重，总量封顶 5）。

    既有条目是 CardEvidence 模型（构造时经 pydantic 校验），新条目同样
    构造成模型再追加，保持整卡类型一致。
    """
    existing = list(card.evidence or [])

    def _src(e: Any) -> str:
        if isinstance(e, dict):
            return str(e.get("source") or "").lower()
        return str(getattr(e, "source", "") or "").lower()

    have = {_src(e) for e in existing}
    for ev in extra:
        if ev["source"] in have:
            continue
        existing.append(CardEvidence(**ev))
        have.add(ev["source"])
    card.evidence = existing[:5] or None
    return card


_VALID_SCAFFOLD = {"example", "faded", "hint", "independent"}
_VALID_NEXT_ACTION = {"accept", "adjust", "reject", "retry", "ask", "none"}


def _coerce_payload_lists(payload: dict[str, Any]) -> None:
    """模型偶发把列表字段写成单个字符串（实测：engineering.pitfalls 变成一句
    建议）——就地修成单元素列表，保卡不保形；已是列表/None 的不动。"""
    for key in ("pitfalls", "options", "risk"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            payload[key] = [v.strip()[:200]]
        elif v is not None and not isinstance(v, list):
            payload[key] = None
    for step in payload.get("engineering_steps") or []:
        if not isinstance(step, dict):
            continue
        p = step.get("pitfalls")
        if isinstance(p, str) and p.strip():
            step["pitfalls"] = [p.strip()[:200]]
        elif p is not None and not isinstance(p, list):
            step["pitfalls"] = None


def _parse_card(c: dict[str, Any]) -> CardMessage | None:
    try:
        card_type = c.get("card_type") or "text"
        if card_type not in _VALID_TYPES:
            card_type = "text"
        payload = c.get("payload")
        if isinstance(payload, dict):
            _coerce_payload_lists(payload)
            payload_obj = CardPayload(**payload)
        else:
            payload_obj = None
        # 模型偶尔会输出协议外的枚举值（如 scaffold_level="open"）；
        # 直接校验会丢弃整卡（含正文/表格），这里清洗为 None 保卡不保字段。
        scaffold = c.get("scaffold_level")
        if scaffold not in _VALID_SCAFFOLD:
            scaffold = None
        next_action = c.get("next_action")
        if next_action not in _VALID_NEXT_ACTION:
            next_action = None
        confidence = c.get("confidence")
        if isinstance(confidence, (int, float)) and not 0 <= confidence <= 1:
            confidence = None
        evidence = c.get("evidence")
        if isinstance(evidence, list):
            evidence = [e for e in evidence if isinstance(e, dict) and e.get("source")] or None
        else:
            evidence = None
        return CardMessage(
            id=str(uuid.uuid4()),
            role="assistant",
            card_type=card_type,
            text=c.get("text") or "",
            payload=payload_obj,
            evidence=evidence,
            confidence=confidence,
            gave_answer=c.get("gave_answer"),
            scaffold_level=scaffold,
            reason=c.get("reason"),
            next_action=next_action,
            strategy=c.get("strategy"),
            created_at=datetime.now(timezone.utc),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("单卡解析失败，跳过: %s", e)
        return None


def _fallback_card(result: AgentResult) -> CardMessage:
    """GLM 切卡失败时的最低保障：用简单启发保留一条可读卡片。"""
    text = result.text or ""
    ct = "text"
    if "？" in text or "?" in text:
        ct = "question"
    elif any(k in text for k in ("第一步", "第二步", "步骤")) or "\n" in text:
        ct = "understand"
    return CardMessage(
        id=str(uuid.uuid4()),
        role="assistant",
        card_type=ct,
        text=text,
        payload=CardPayload(meta={"thinking_chars": len(result.thinking)}),
        thinking=result.thinking or None,
        created_at=datetime.now(timezone.utc),
        next_action="ask",
    )
