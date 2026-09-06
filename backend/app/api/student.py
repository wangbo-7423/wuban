"""学生端 · AI 伴学 Agent 对话。

主要责任：
- 接收用户消息（含图片）；会话/消息持久化；
- 调用 `AgentOrchestrator` 让 GLM 5.3 Flash 自主思考 + 工具调用；
- 把模型输出打成一张或多张「卡片」返给前端（可流式／整体回包）。

不负责：
- 故意拼接多轮模板化的诊断话术 —— 让模型自己决定。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent import AgentOrchestrator, AgentResult, tool_schemas
from app.agent import glm_client
from app.agent.cards import format_cards
from app.core.db import get_session
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.core.security import get_current_user
from app.mcp import current_user_id
from app.models import User
from app.models.conversation import Message
from app.repositories import chat_repo
from app.schemas.card import (
    CardEvidence,
    CardMessage,
    CardPayload,
    ToolCallRecord,
)
from app.schemas.chat import (
    ChatIn,
    ChatOut,
    ConversationDetail,
    ConversationOut,
    ImagePart,
    NewConvIn,
    PracticeSubmitIn,
    PracticeSubmitOut,
)

router = APIRouter()

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# 工具：把图像数组 → GLM message 用的 url 列表
# ────────────────────────────────────────────────────────────

import base64
import os

from app.core.config import settings

# 本地落盘图片前缀；GLM 远程无法访问，需读盘转 base64
_UPLOAD_PREFIX = "/api/uploads-image/"


def _resolve_image(url: str) -> str:
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


def _image_urls(images: list[ImagePart]) -> list[str]:
    out: list[str] = []
    for img in images:
        resolved = _resolve_image(img.url)
        if resolved:
            out.append(resolved)
    return out


# ────────────────────────────────────────────────────────────
# 工具：把 AgentResult → 前端可消费的 CardMessage
# ────────────────────────────────────────────────────────────


def _build_cards(
    result: AgentResult, *, user_text: str, course_id: str
) -> tuple[CardMessage, list[CardMessage]]:
    """把 AgentResult 切成「主卡 + 额外卡片」。

    主卡携带思考链与工具调用留痕（前端可展示）；额外卡片来自 format_cards
    的结构化切分（math / engineering / practice / choice / recommendation 等）。
    """
    cards = format_cards(result, user_text=user_text, course_id=course_id)
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


def _save_message(db: Session, conversation_id: str, role: str, card: CardMessage) -> None:
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


# ────────────────────────────────────────────────────────────
# 上下文摘要：把若干历史消息压成可注入 system 的几句话
# ────────────────────────────────────────────────────────────


def _history_to_messages(messages: list[Any], limit: int = 8) -> list[dict[str, Any]]:
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
                resolved = _resolve_image(url)
                if resolved:
                    imgs.append({"type": "image_url", "image_url": {"url": resolved}})
        if role == "user" and imgs:
            parts = imgs + [{"type": "text", "text": text}]
            out.append({"role": "user", "content": parts})
        else:
            out.append({"role": role, "content": text})
    return out


# ────────────────────────────────────────────────────────────
# 长期记忆（MCP 知识图谱）：读注入 + 后台抽取回推
# ────────────────────────────────────────────────────────────


def _memory_context(user_id: str, user_text: str) -> str:
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


def _build_updated_context(db: Session, user_id: str, course_id: str) -> dict | None:
    """重算 cognitive_state 并折算成前端 LearningContext（updated_context 契约）。

    在卡片落库、commit 之后同步调用（本轮的 kg_lookup 命中要算进 mentions）；
    update_cognitive_state 内部全量兜异常，失败返回 None，前端静默保留旧 context。
    """
    from app.services import profile_service
    state = profile_service.update_cognitive_state(db, user_id)
    if state is None:
        return None
    return profile_service.build_learning_context(state, course_id)


def _after_chat_tasks(
    user_id: str, course_id: str, user_text: str, assistant_text: str,
    conversation_id: str | None = None,
) -> None:
    """轮后任务（FastAPI BackgroundTasks，独立线程跑）：
    1) 从本轮对话抽取实体/关系写入记忆图谱（长期记忆层 Write）；
    2) 更新会话草稿纸 + 视情况压缩滚动摘要（会话级 Write）。

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
            session_memory.after_turn(conversation_id)
        except Exception:  # noqa: BLE001
            pass


# ────────────────────────────────────────────────────────────
# 路由
# ────────────────────────────────────────────────────────────


@router.post("/chat", response_model=None)
def chat(
    payload: ChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    # 记忆检索工具（memory_search）靠 ContextVar 拿 user_id；
    # 整个 orchestrator 调用链都在本线程栈内，set 一次即可
    token = current_user_id.set(user.id)
    try:
        return _chat_impl(payload, user, db)
    finally:
        current_user_id.reset(token)


def _prepare_chat(
    payload: ChatIn, user: User, db: Session
) -> tuple[Any, list[dict[str, Any]], AgentOrchestrator, list[str] | None]:
    """同步/流式两条链路的公共准备：会话、用户消息落库、历史、意图装配、动态上下文。

    返回 (conversation, history_msgs, orchestrator, allowed_tools)。
    orchestrator 已带 user_id，工具执行段内会自行绑定 ContextVar（流式生成器
    跨线程，端点里 set 无效）；allowed_tools 供遥测记录本轮工具装配。
    """
    # 1) 取/建会话
    conv = None
    if payload.conversation_id:
        conv = chat_repo.get_conversation(db, payload.conversation_id, user.id)
        if conv is None:
            raise BizError(ErrorCode.NOT_FOUND, "会话不存在或无权访问")
    if conv is None:
        title = (payload.message or "新对话")[:16].strip() or "新对话"
        conv = chat_repo.create_conversation(db, user.id, payload.course_id, title)

    # 2) 写用户消息（包含图片 meta）
    user_card = CardMessage(
        id=str(uuid.uuid4()),
        role="user",
        card_type="text",
        text=payload.message,
        payload=CardPayload(meta={
            "images": [img.model_dump() for img in payload.images],
            "course_id": payload.course_id,
        }),
        evidence=[CardEvidence(source=payload.course_id, confidence=1.0)],
        created_at=datetime.now(timezone.utc),
    )
    _save_message(db, conv.id, "user", user_card)

    # 3) 取最近历史 → 注入 GLM
    history_msgs = _history_to_messages(
        chat_repo.list_messages(db, conv.id), limit=8
    )

    # 4) 意图路由（Select 策略）：按学生诉求装配本轮工具子集 + 场景指南（渐进式披露）
    from app.agent.intent import build_scene_context
    allowed_tools, scene_note = build_scene_context(payload.message)

    #    动态上下文：课程 + 场景装配说明/指南 + 学生长期记忆（L0/L1 分层）+ 会话级 Write 进展
    #    （全部注入尾部 user 消息，system 块保持静态——Cache 策略）
    session_parts = [f"当前课程：{payload.course_id}"]
    if scene_note:
        session_parts.append(scene_note)
    mem_ctx = _memory_context(user.id, payload.message)
    if mem_ctx:
        session_parts.append(mem_ctx)
    from app.services.session_memory import render_session_context
    sess_ctx = render_session_context(conv.summary, conv.scratchpad)
    if sess_ctx:
        session_parts.append(sess_ctx)
    orchestrator = AgentOrchestrator(
        dynamic_context="\n\n".join(session_parts).strip(),
        allowed_tools=allowed_tools,
        user_id=user.id,
    )
    return conv, history_msgs, orchestrator, allowed_tools


def _chat_impl(payload: ChatIn, user: User, db: Session) -> dict:
    conv, history_msgs, orchestrator, allowed_tools = _prepare_chat(payload, user, db)
    _t0 = time.perf_counter()
    try:
        result: AgentResult = orchestrator.run(
            history=history_msgs[:-1],  # 最后一条就是刚写入的用户消息，run 里会拼
            user_text=payload.message,
            image_urls=_image_urls(payload.images),
        )
    except BizError as e:
        # 遥测（Harness 警示二）：失败也要留痕——错误码 + 耗时，随错误卡一起 commit
        from app.services.telemetry_service import record_agent_turn
        record_agent_turn(
            db,
            user_id=user.id, conversation_id=conv.id, course_id=payload.course_id,
            allowed_tools=allowed_tools, success=False,
            error=f"{e.code}: {e.message}",
            latency_ms=(time.perf_counter() - _t0) * 1000,
        )
        _save_message(db, conv.id, "assistant", CardMessage(
            id=str(uuid.uuid4()),
            role="assistant",
            card_type="text",
            text="⚠️ AI 服务暂时不可用，本次回复未生成，请稍后重试。",
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        raise
    latency_ms = (time.perf_counter() - _t0) * 1000

    # 5) 把模型输出切成主卡 + 额外卡片，写库
    main_card, extra_cards = _build_cards(
        result, user_text=payload.message, course_id=payload.course_id
    )
    _save_message(db, conv.id, "assistant", main_card)
    for ex in extra_cards:
        _save_message(db, conv.id, "assistant", ex)

    # 6) 更新会话时间
    if conv.title == "新对话" and payload.message:
        conv.title = payload.message[:16]

    # 6.5) Agent 遥测落库（Harness 警示二：failure log 一等公民；随本事务 commit）
    from app.services.telemetry_service import record_agent_turn
    record_agent_turn(
        db,
        user_id=user.id, conversation_id=conv.id, course_id=payload.course_id,
        allowed_tools=allowed_tools, result=result,
        success=True, latency_ms=latency_ms,
    )
    db.commit()

    # 7) 画像回推 + 学习上下文：卡片已落库，本轮证据算得进 mentions；
    #    失败静默（updated_context 留 null，前端保留旧 context）
    updated_context = _build_updated_context(db, user.id, payload.course_id)

    # 8) 轮后任务（后台线程）：记忆抽取 + 会话记忆（画像回推已在上面同步做）
    bg = BackgroundTasks()
    bg.add_task(
        _after_chat_tasks, user.id, payload.course_id, payload.message,
        main_card.text or "", conv.id,
    )

    # 9) 回包（主卡 + extras 多卡）
    chat_out = ChatOut(
        message=main_card.model_dump(),
        extras=[e.model_dump() for e in extra_cards],
        thinking=result.thinking or None,
        tool_calls=[tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in main_card.tool_calls or []],
        conversation_id=conv.id,
        title=conv.title,
        updated_context=updated_context,
    )
    # JSONResponse + background：响应送达后 FastAPI 会在同一线程池跑轮后任务。
    # 卡片里的 created_at 是 datetime，JSONResponse 不做类型转换，先过 jsonable_encoder。
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content=jsonable_encoder(ok(chat_out.model_dump())), background=bg
    )


# ────────────────────────────────────────────────────────────
# 流式对话（SSE）：先推思考/正文增量，结束推完整卡片包
# ────────────────────────────────────────────────────────────

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # 关闭 nginx 缓冲，增量即时下发
}


def _sse(event: str, data: dict) -> str:
    """编码一帧 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
def chat_stream(
    payload: ChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """流式对话：`text/event-stream`。

    事件序列：
    - `status`    阶段提示 {phase}（preparing=写库/意图路由中），最最先发出——
                  prepare 要调 LLM 做意图路由，常耗数秒，先给前端一帧反馈；
    - `meta`      会话信息（conversation_id / title）；
    - `reasoning` 思考链增量 {delta}（GLM thinking 模式）；
    - `tool`      工具开始执行 {tool_name, args}（前端可显示轨迹）；
    - `delta`     回答正文增量 {text}；
    - `answer`    正文全文已定 {text, thinking}——发在切卡之前：format_cards 是
                  二次 LLM 调用，可能再花十几秒，先让前端把正文落定、状态条切换成
                  「正在整理卡片」，不再对着流完的文字干等；
    - `done`      完整 ChatOut（主卡 + extras 已切好、已落库，updated_context 已回推），
                  前端用它替换流式占位；
    - `error`     失败 {code, message}（错误卡已落库，刷新不丢上下文）。

    卡片切分（format_cards 二次 LLM 调用）在流结束后进行——结构化卡片
    需要完整回答才能切，所以 `delta` 阶段是纯文本渐进渲染，`done` 时
    一次性升级为结构化卡片。
    """
    # BackgroundTasks 在生成器成功路径末尾注册（prepare 在生成器内完成，
    # 端点线程拿不到 conv）；响应流式发完后由 FastAPI 在线程池执行
    bg = BackgroundTasks()

    def gen():
        _t0 = time.perf_counter()
        conv = None
        allowed_tools: list[str] | None = None
        try:
            # 第一帧必须秒到：prepare（写库 + 意图路由 LLM）在生成器内执行，
            # 之前的 3~4 秒空白由这帧 status 填掉
            yield _sse("status", {"phase": "preparing"})
            # 公共准备（写库、意图路由）；orchestrator 带 user_id，
            # 生成器各执行段内自行绑定 ContextVar（迭代跨线程，set 只在栈内有效）
            conv, history_msgs, orchestrator, allowed_tools = _prepare_chat(payload, user, db)
            yield _sse("meta", {"conversation_id": conv.id, "title": conv.title})
            event_gen = orchestrator.run_stream(
                history=history_msgs[:-1],  # 最后一条就是刚写入的用户消息
                user_text=payload.message,
                image_urls=_image_urls(payload.images),
            )
            try:
                while True:
                    ev = next(event_gen)
                    if ev["type"] == "reasoning":
                        yield _sse("reasoning", {"delta": ev["text"]})
                    elif ev["type"] == "delta":
                        yield _sse("delta", {"text": ev["text"]})
                    elif ev["type"] == "tool":
                        yield _sse("tool", {"tool_name": ev["tool_name"], "args": ev["args"]})
            except StopIteration as stop:
                result: AgentResult = stop.value

            # 正文全文已定（含「思维链兜底」修正），抢在切卡前推给前端：
            # 前端把占位文本落定、状态切到「整理卡片」，感知上的生成到此结束
            yield _sse("answer", {
                "text": result.text or "",
                "thinking": result.thinking or "",
            })

            # 切卡 + 落库（与同步链路同一套逻辑）
            main_card, extra_cards = _build_cards(
                result, user_text=payload.message, course_id=payload.course_id
            )
            _save_message(db, conv.id, "assistant", main_card)
            for ex in extra_cards:
                _save_message(db, conv.id, "assistant", ex)
            if conv.title == "新对话" and payload.message:
                conv.title = payload.message[:16]

            # Agent 遥测落库（成功）：随本事务 commit
            from app.services.telemetry_service import record_agent_turn
            record_agent_turn(
                db,
                user_id=user.id, conversation_id=conv.id, course_id=payload.course_id,
                allowed_tools=allowed_tools, result=result,
                success=True, latency_ms=(time.perf_counter() - _t0) * 1000,
            )
            db.commit()

            chat_out = ChatOut(
                message=main_card.model_dump(),
                extras=[e.model_dump() for e in extra_cards],
                thinking=result.thinking or None,
                tool_calls=[
                    tc.model_dump() if hasattr(tc, "model_dump") else tc
                    for tc in main_card.tool_calls or []
                ],
                conversation_id=conv.id,
                title=conv.title,
                # 卡片已落库后重算画像（失败静默，留 null 前端保留旧 context）
                updated_context=_build_updated_context(db, user.id, payload.course_id),
            )
            yield _sse("done", {"data": chat_out.model_dump(mode="json")})
            # 轮后任务（记忆抽取 + 会话记忆）在流结束后随响应异步执行；
            # prepare 在生成器内完成，conv 只有这里拿得到
            bg.add_task(
                _after_chat_tasks, user.id, payload.course_id, payload.message,
                main_card.text or "", conv.id,
            )
        except BizError as e:
            db.rollback()
            # 遥测（失败也留痕）+ 错误卡落库：prepare 阶段失败时 conv 尚未创建，
            # 两者都依赖 conv.id，判空跳过（前端仍能收到 error 事件）
            if conv is not None:
                from app.services.telemetry_service import record_agent_turn
                record_agent_turn(
                    db,
                    user_id=user.id, conversation_id=conv.id, course_id=payload.course_id,
                    allowed_tools=allowed_tools, success=False,
                    error=f"{e.code}: {e.message}",
                    latency_ms=(time.perf_counter() - _t0) * 1000,
                )
                # 失败也落一条 assistant 错误卡，否则库里只剩用户消息（悬空提问）
                _save_message(db, conv.id, "assistant", CardMessage(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    card_type="text",
                    text=f"⚠️ {e.message}",
                    created_at=datetime.now(timezone.utc),
                ))
                db.commit()
            yield _sse("error", {"code": e.code, "message": e.message})
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式对话失败 conv=%s", conv.id if conv else "(未创建)")
            db.rollback()
            # 按异常特征细分文案：超时/限流是已知高发场景（GLM 排队可达分钟级），
            # 笼统的「服务不可用」会让用户以为坏了而不是要等。
            s = str(exc).lower()
            if "timeout" in s or "timed out" in s:
                err_msg = "AI 模型响应超时（可能正在限流排队），请稍后重试或换个问法"
            elif "429" in s or "rate limit" in s:
                err_msg = "AI 模型限流中，请稍等半分钟再试"
            else:
                err_msg = "AI 服务暂时不可用，请稍后重试"
            if conv is not None:
                from app.services.telemetry_service import record_agent_turn
                record_agent_turn(
                    db,
                    user_id=user.id, conversation_id=conv.id, course_id=payload.course_id,
                    allowed_tools=allowed_tools, success=False,
                    error="internal: unhandled exception（详见服务端日志）",
                    latency_ms=(time.perf_counter() - _t0) * 1000,
                )
                _save_message(db, conv.id, "assistant", CardMessage(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    card_type="text",
                    text=f"⚠️ {err_msg}",
                    created_at=datetime.now(timezone.utc),
                ))
                db.commit()
            yield _sse("error", {"code": 500, "message": err_msg})

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers=_SSE_HEADERS, background=bg
    )


# ────────────────────────────────────────────────────────────
# 练习/选择卡作答闭环：作答留痕 → LLM 审阅反馈 → 画像回推
# ────────────────────────────────────────────────────────────

_REVIEW_SYSTEM = """你是「AI 伴学」的审阅反馈器。学生在练习卡/选择卡上提交了作答，
请给**审阅式反馈**（不是判分）：

1. 先肯定作答里做对的部分——具体指出是哪一步/哪个判断立住了，为什么它对；
2. 再指出问题或漏洞，并**说明原因**（错在哪、背后的概念是什么）；
3. 如果作答基本正确，往前再带一步：抛一个「你有没有想过…」式的引申疑问，
   把话题主动权交还学生；
4. 不要打分，不用「你对/你错」给人贴标签，只针对这一步推理本身；
5. Markdown，300 字以内；语气是陪练，不是考官。"""

_REVIEW_TMPL = """课程：{course_id}
卡片类型：{card_type}

原卡内容：
{text}

{options_block}

学生作答：{answer}

请输出审阅式反馈正文（Markdown）。"""


def _options_block(payload_dict: dict | None, answer: str) -> str:
    """choice 卡：把学生选的 value 翻译成 label，连同全部选项给反馈器。"""
    if not payload_dict:
        return ""
    options = payload_dict.get("options") or []
    if not options:
        return ""
    lines = ["候选选项："]
    for o in options:
        if isinstance(o, dict):
            mark = " ← 学生选择" if str(o.get("value")) == answer else ""
            lines.append(f"- {o.get('value')}: {o.get('label')}{mark}")
    return "\n".join(lines)


def _review_feedback_llm(
    *, course_id: str, card_type: str, text: str, payload_dict: dict | None, answer: str
) -> str:
    """对学生作答生成审阅式反馈（feedback 卡正文）。失败返回兜底文案。"""
    prompt = _REVIEW_TMPL.format(
        course_id=course_id,
        card_type=card_type,
        text=(text or "")[:2000],
        options_block=_options_block(payload_dict, answer),
        answer=answer,
    )
    try:
        resp = glm_client.chat(
            messages=[
                {"role": "system", "content": _REVIEW_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            # GLM 5.3 Flash 无法真正关掉 thinking：max_tokens 太小时 reasoning
            # 吃掉全部额度，content 为空（见 intent.py 同款坑），给足余量
            max_tokens=4096,
            enable_thinking=False,  # 反馈不需要思维链，快进快出
            reasoning_effort="low",  # 审阅反馈是结构化输出，压低档位降低等待
        )
        text_out = (resp.choices[0].message.content or "").strip()
        return text_out or "（反馈生成为空，请重试）"
    except Exception as e:  # noqa: BLE001
        logger.warning("审阅反馈 LLM 调用失败: %s", e)
        return (
            "已收到你的作答，但反馈生成出了点问题。可以换个说法再提交一次，"
            "或者直接在对话里继续讨论这道题。"
        )


@router.post("/practice/submit")
def practice_submit(
    payload: PracticeSubmitIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """练习/选择卡作答提交。

    闭环三步（全部留在 messages 表，无新表）：
    1. 作答留痕：写入原卡 payload.meta.attempts（刷新/回放可见已作答）；
    2. 学生作答以 user 消息入流：会话记忆、Scratchpad、后续对话都能看见；
    3. 生成 feedback 审阅卡落库，并回推学习画像（practice 过程性证据）。
    """
    conv = chat_repo.get_conversation(db, payload.conversation_id, user.id)
    if conv is None:
        raise BizError(ErrorCode.NOT_FOUND, "会话不存在或无权访问")
    msg = chat_repo.get_message(db, conv.id, payload.card_id)
    if msg is None:
        raise BizError(ErrorCode.NOT_FOUND, "卡片不存在")
    if msg.card_type not in ("practice", "choice"):
        raise BizError(ErrorCode.BAD_REQUEST, "该卡片不是可作答的练习/选择卡")

    answer = payload.answer.strip()
    if not answer:
        raise BizError(ErrorCode.BAD_REQUEST, "作答内容不能为空")

    # 1) 作答留痕（JSON 列整体重赋值，保证 SQLAlchemy 变更可见）
    now_iso = datetime.now(timezone.utc).isoformat()
    card_payload = dict(msg.payload or {})
    meta = dict(card_payload.get("meta") or {})
    attempts = list(meta.get("attempts") or [])
    attempts.append({"answer": answer, "at": now_iso})
    meta["attempts"] = attempts
    meta["answered"] = True
    card_payload["meta"] = meta
    msg.payload = card_payload

    # 2) 学生作答以 user 消息入流（带 practice_ref 指回原卡）
    answer_card = CardMessage(
        id=str(uuid.uuid4()),
        role="user",
        card_type="text",
        text=answer,
        payload=CardPayload(meta={
            "practice_ref": {"card_id": msg.id, "card_type": msg.card_type},
            "course_id": conv.course_id,
        }),
        created_at=datetime.now(timezone.utc),
    )
    _save_message(db, conv.id, "user", answer_card)

    # 3) 审阅反馈卡
    feedback_card = CardMessage(
        id=str(uuid.uuid4()),
        role="assistant",
        card_type="feedback",
        text=_review_feedback_llm(
            course_id=conv.course_id or "general",
            card_type=msg.card_type,
            text=msg.text,
            payload_dict=card_payload,
            answer=answer,
        ),
        gave_answer=False,  # 反馈不直接给答案，指向修正方向
        next_action="ask",
        created_at=datetime.now(timezone.utc),
    )
    _save_message(db, conv.id, "assistant", feedback_card)
    db.commit()

    # 4) 画像回推：作答是「预测→验证」的过程性证据，进 cognitive_state.practice
    try:
        from app.services import profile_service
        profile_service.update_cognitive_state(db, user.id)
    except Exception:  # noqa: BLE001
        logger.exception("practice 提交后画像回推失败（不影响主流程）")

    out = PracticeSubmitOut(
        conversation_id=conv.id,
        card=chat_repo.message_to_card(msg).model_dump(mode="json"),
        feedback=feedback_card.model_dump(mode="json"),
    )
    return ok(out.model_dump())


@router.get("/conversations")
def conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    items = [
        ConversationOut(
            id=c.id,
            course_id=c.course_id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in chat_repo.list_conversations(db, user.id)
    ]
    return ok([i.model_dump() for i in items])


@router.post("/conversations")
def new_conversation(
    payload: NewConvIn = NewConvIn(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    conv = chat_repo.create_conversation(
        db, user.id, payload.course_id, payload.title or "新对话"
    )
    out = ConversationOut(
        id=conv.id,
        course_id=conv.course_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )
    return ok(out.model_dump())


@router.get("/conversations/{conv_id}/messages")
def messages(
    conv_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    conv = chat_repo.get_conversation(db, conv_id, user.id)
    if conv is None:
        raise BizError(ErrorCode.NOT_FOUND, "会话不存在")
    msgs = [
        chat_repo.message_to_card(m).model_dump()
        for m in chat_repo.list_messages(db, conv_id)
    ]
    detail = ConversationDetail(
        id=conv.id,
        course_id=conv.course_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=msgs,
    )
    return ok(detail.model_dump())


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    conv = chat_repo.get_conversation(db, conv_id, user.id)
    if conv is None:
        raise BizError(ErrorCode.NOT_FOUND, "会话不存在")
    db.delete(conv)
    db.commit()
    return ok({"deleted": conv_id})


# ── 探索档案：从历史消息聚合（刻意不建新表）────────────────────

# 深度提问的信号：学生从「怎么算」走向「为什么」，是理解深化的标志
_DEEP_Q_MARKS = (
    "为什么", "为何", "怎么会", "怎么判断", "如何判断",
    "如果", "假如", "是不是", "会不会", "能不能", "能否", "难道",
)
# AI 抛出过、学生可能还没接住的引申疑问
_OPEN_THREAD_MARKS = (
    "你有没有想过", "有没有想过", "你想过", "不妨想想", "不妨思考",
    "试想", "你会怎么", "留给你", "可以想想",
)


@router.get("/context")
def learning_context(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """学习上下文（前端 LearningContext）：路径 / 探索深度 / 复习到期。

    与 ChatOut.updated_context 同一份数据形状（build_learning_context）：
    - 进入主界面时拉一次（顶栏进度、侧栏「我的路径」「该回顾了」的首次数据源）；
    - 练习提交等不带回推的入口之后，前端可再调它刷新。
    内部会顺带重算 cognitive_state（无消息记录时返回空上下文，不报错）。
    """
    from app.services import profile_service
    state = profile_service.update_cognitive_state(db, user.id)
    return ok(profile_service.build_learning_context(state, "general"))


@router.get("/exploration")
def get_exploration(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
    limit: int = 12,
) -> dict:
    """探索档案：把「提过的好问题 / 未展开的线索 / 探索过的主题」从历史消息里读出来。

    **刻意不建新表** —— 这些都是对话里已经存在的信号，聚合即可。
    依据 docs/00 §6：这里的「探索深度」来自**过程性证据**（提问深度、提及次数），
    **不是判分结果**；本产品不给学生打分。
    """
    limit = max(1, min(int(limit or 12), 50))

    convs = chat_repo.list_conversations(db, user.id)
    if not convs:
        return ok(
            {
                "good_questions": [],
                "open_threads": [],
                "topics": [],
                "works": [],
                "stats": {"conversations": 0, "good_question_count": 0, "topic_count": 0},
            }
        )

    conv_ids = [c.id for c in convs]
    course_by_conv = {c.id: c.course_id for c in convs}

    # 一次查完，避免逐会话 N+1
    msgs = (
        db.query(Message)
        .filter(Message.conversation_id.in_(conv_ids))
        .order_by(Message.created_at.desc())
        .limit(1000)
        .all()
    )

    good_questions: list[dict[str, Any]] = []
    open_threads: list[dict[str, Any]] = []
    topics: dict[str, dict[str, Any]] = {}

    for m in msgs:
        text = (m.text or "").strip()
        created = m.created_at.isoformat() if m.created_at else None

        if text and m.role == "user":
            # 深度提问：含追问信号 + 长度不过短（排除「嗯」「好的」这类应答）
            if len(text) >= 8 and any(k in text for k in _DEEP_Q_MARKS):
                good_questions.append(
                    {
                        "text": text[:160],
                        "conversation_id": m.conversation_id,
                        "created_at": created,
                    }
                )
        elif text and m.role == "assistant":
            if any(k in text for k in _OPEN_THREAD_MARKS):
                open_threads.append(
                    {
                        "text": text[:160],
                        "conversation_id": m.conversation_id,
                        "created_at": created,
                    }
                )

        # 主题：优先取 kg_lookup 真正命中过的概念（有知识底座支撑，比瞎切关键词靠谱）
        for tc in m.tool_calls or []:
            if not isinstance(tc, dict) or tc.get("tool_name") != "kg_lookup":
                continue
            args = tc.get("args") or {}
            query = str(args.get("query", "")).strip()
            if not query:
                continue
            item = topics.setdefault(
                query,
                {
                    "topic": query,
                    "mentions": 0,
                    "course": course_by_conv.get(m.conversation_id, "general"),
                },
            )
            item["mentions"] += 1

    topic_list = sorted(topics.values(), key=lambda x: -x["mentions"])[:limit]
    for t in topic_list:
        # 探索深度：聊得越多次、越深入 → 越接近 1。
        # 这是「过程性证据的估计值」，不是考试分数。
        t["depth"] = round(min(1.0, 0.3 + t["mentions"] * 0.15), 2)

    return ok(
        {
            "good_questions": good_questions[:limit],
            "open_threads": open_threads[:limit],
            "topics": topic_list,
            "works": [],  # 作品：需学生主动提交，待 StudentWork 表（见 docs/01 §8）
            "stats": {
                "conversations": len(convs),
                "good_question_count": len(good_questions),
                "topic_count": len(topics),
            },
        }
    )


@router.get("/tools")
def tools() -> dict:
    """透出当前启用的工具列表（供前端调试 / 演示）。"""
    return ok(tool_schemas())
