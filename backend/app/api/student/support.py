"""学生端共享支撑层（原 api/student.py 单文件的公共部分）。

路由模块（chat / practice / conversations / insights）共用的工具：
- 图片解析（URL → GLM 可吃形态）；
- AgentResult → 卡片切分与落库；
- 历史消息还原（多模态）；
- 长期记忆注入、画像回推、轮后任务。

不负责：具体路由逻辑，见同包各模块。
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agent import AgentResult
from app.agent.cards import format_cards, kg_hit_concepts
from app.core.config import settings
from app.models.conversation import Message
from app.repositories import chat_repo
from app.schemas.card import (
    CardEvidence,
    CardMessage,
    CardPayload,
    ToolCallRecord,
)
from app.schemas.chat import ImagePart

# 本地落盘图片前缀；GLM 远程无法访问，需读盘转 base64
_UPLOAD_PREFIX = "/api/uploads-image/"


def resolve_image(url: str) -> str:
    """把图片 url 解析成 GLM 可吃的形态。

    - data:image/...;base64 开头 → 直接返回（前端旧直传兼容）
    - http(s):// → 直接返回（公网，GLM 可抓取）
    - /api/uploads-image/<file> → 读盘转 base64 data URI（防路径穿越）
    """
    if url.startswith("data:image"):
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith(_UPLOAD_PREFIX):
        fname = url[len(_UPLOAD_PREFIX):]
        # 只用 basename，杜绝 ../../ 穿越
        safe = os.path.basename(fname)
        if not safe or safe != fname:
            return ""
        full = os.path.join(settings.upload_dir, safe)
        if not os.path.isfile(full):
            return ""
        with open(full, "rb") as f:
            raw = f.read()
        ext = safe.rsplit(".", 1)[-1].lower()
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }.get(ext, "image/png")
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    return url


def image_urls(images: list[ImagePart]) -> list[str]:
    out: list[str] = []
    for img in images:
        resolved = resolve_image(img.url)
        if resolved:
            out.append(resolved)
    return out


def build_cards(
    result: AgentResult,
    *,
    user_text: str,
    course_id: str,
    learning: dict[str, Any] | None = None,
) -> tuple[CardMessage, list[CardMessage]]:
    """把 AgentResult 切成「主卡 + 额外卡片」。

    主卡携带思考链与工具调用留痕（前端可展示）；额外卡片来自 format_cards
    的结构化切分（math / engineering / practice / choice / recommendation 等）。
    learning 是截至上一轮的学习状态视图（state + pending），连同本轮 kg_lookup
    命中一起渲染成切卡的跨轮信号（docs/17）：切卡模型看不到对话历史，warning
    判定的跨轮依据全在这里——结构化、按概念键控，话题漂移由结构过滤。
    """
    from app.services.session_memory import render_learning_context
    learning = learning or {}
    learning_context = render_learning_context(
        learning.get("state"), learning.get("pending"), kg_hit_concepts(result)
    )
    cards = format_cards(
        result,
        user_text=user_text,
        course_id=course_id,
        learning_context=learning_context or None,
    )
    main = cards[0]
    main.thinking = result.thinking or main.thinking
    main.tool_calls = [
        ToolCallRecord(
            tool_name=tc["tool_name"],
            args=tc["args"],
            result=json.dumps(tc["result"], ensure_ascii=False)
            if isinstance(tc.get("result"), (dict, list))
            else str(tc.get("result", "")),
            ok=tc["ok"],
            took_ms=tc.get("took_ms"),
        )
        for tc in result.tool_calls
    ]
    return main, cards[1:]


def save_message(db: Session, conversation_id: str, role: str, card: CardMessage) -> None:
    """把卡片序列化后写库（保持 messages 表结构兼容）。"""
    payload_dict = card.payload.model_dump() if card.payload else None
    text_value = card.text or ""
    chat_repo.add_message(
        db,
        conversation_id=conversation_id,
        role=role,
        id=card.id,  # 透传卡片 id：前端 card_id 必须能定位到库里的这条消息
        text=text_value,
        card_type=card.card_type,
        payload=payload_dict,
        evidence=(
            [e.model_dump() for e in card.evidence] if card.evidence else None
        ),
        confidence=card.confidence,
        gave_answer=card.gave_answer,
        scaffold_level=card.scaffold_level,
        reason=card.reason,
        next_action=card.next_action,
        strategy=card.strategy,
        tool_calls=(
            [tc.model_dump() for tc in card.tool_calls] if card.tool_calls else None
        ),
        thinking=card.thinking,
    )


def history_to_messages(messages: list[Any], limit: int = 8) -> list[dict[str, Any]]:
    """把最近 N 条消息转成 OpenAI 形态历史（user / assistant）。

    用户消息若带了图片（payload.meta.images），还原成多模态 content，
    让 GLM 在多轮对话里也能「看到」之前传过的图（修历史丢图缺口）。
    """
    out: list[dict[str, Any]] = []
    for m in messages[-limit:]:
        role = m.role  # user/assistant
        if role not in ("user", "assistant"):
            continue
        text = m.text or ""
        # 历史图片：从持久化的 payload.meta.images 还原
        imgs: list[dict[str, Any]] = []
        payload = getattr(m, "payload", None)
        meta = payload.get("meta") or {} if isinstance(payload, dict) else {}
        for im in (meta.get("images") or []):
            url = im.get("url") if isinstance(im, dict) else None
            if url:
                resolved = resolve_image(url)
                if resolved:
                    imgs.append({"type": "image_url", "image_url": {"url": resolved}})
        if role == "user" and imgs:
            parts = imgs + [{"type": "text", "text": text}]
            out.append({"role": "user", "content": parts})
        else:
            out.append({"role": role, "content": text})
    return out


def memory_context(user_id: str, user_text: str) -> str:
    """把学生记忆按「常驻 + 检索」两层注入 extra_system；失败降级为空。

    - L0 常驻（每轮注入）：偏好/目标/误区/兴趣——任何话题都用得上的「人格级」信息；
    - L1 检索（按需注入）：当前消息命中哪些已学概念，才带出该概念及其邻接关系。
      对应上下文工程的分层预算：图谱再大，记忆注入也稳定在窗口的 5~10%。
    """
    parts: list[str] = []
    try:
        from app.services.memory_service import (
            get_persistent_digest,
            get_relevant_digest,
        )
        l0 = get_persistent_digest(user_id)
        if l0:
            parts.append(l0)
        l1 = get_relevant_digest(user_id, user_text or "")
        if l1:
            parts.append(l1)
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(parts)


def build_updated_context(db: Session, user_id: str, course_id: str) -> dict | None:
    """重算 cognitive_state 并折算成前端 LearningContext（updated_context 契约）。

    在卡片落库、commit 之后同步调用（本轮的 kg_lookup 命中要算进 mentions）；
    update_cognitive_state 内部全量兜异常，失败返回 None，前端静默保留旧 context。
    """
    from app.services import profile_service
    state = profile_service.update_cognitive_state(db, user_id)
    if state is None:
        return None
    return profile_service.build_learning_context(state, course_id)


def after_chat_tasks(
    user_id: str, course_id: str, user_text: str, assistant_text: str,
    conversation_id: str | None = None, kg_concepts: list[str] | None = None,
) -> None:
    """轮后任务（FastAPI BackgroundTasks，独立线程跑）：
    1) 从本轮对话抽取实体/关系写入记忆图谱（长期记忆层 Write）；
    2) 更新会话草稿纸 + 推进学习状态（streak，docs/17）+ 视情况压缩滚动摘要
       （会话级 Write）。

    画像回推（cognitive_state）已改为请求路径内同步重算——它要喂给
    ChatOut.updated_context 随响应下发；全部各自兜异常，任何一路挂了
    都不能影响下一次聊天。
    """
    try:
        from app.services import memory_service
        memory_service.extract_and_write(user_id, course_id, user_text, assistant_text)
    except Exception:  # noqa: BLE001
        pass
    if conversation_id:
        try:
            from app.services import session_memory
            session_memory.after_turn(conversation_id, hit_concepts=kg_concepts or [])
        except Exception:  # noqa: BLE001
            pass
