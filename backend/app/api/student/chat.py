"""学生端 · AI 伴学 Agent 对话（同步 + 流式）。

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
from app.agent.cards import kg_hit_concepts
from app.core.db import get_session
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.core.security import get_current_user
from app.mcp import current_user_id
from app.models import User
from app.repositories import chat_repo
from app.schemas.card import (
    CardEvidence,
    CardMessage,
    CardPayload,
)
from app.schemas.chat import (
    ChatIn,
    ChatOut,
)
from app.api.student.support import (
    after_chat_tasks,
    build_cards,
    build_updated_context,
    history_to_messages,
    image_urls,
    memory_context,
    save_message,
)

router = APIRouter()

logger = logging.getLogger(__name__)


def _prepare_chat(
    payload: ChatIn, user: User, db: Session
) -> tuple[Any, list[dict[str, Any]], AgentOrchestrator, list[str] | None, dict[str, Any]]:
    """同步/流式两条链路的公共准备：会话、用户消息落库、历史、意图装配、动态上下文。

    返回 (conversation, history_msgs, orchestrator, allowed_tools, learning)。
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
    save_message(db, conv.id, "user", user_card)

    # 3) 取最近历史 → 注入 GLM
    history_msgs = history_to_messages(
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
    mem_ctx = memory_context(user.id, payload.message)
    if mem_ctx:
        session_parts.append(mem_ctx)
    from app.services.session_memory import render_session_context
    sess_ctx = render_session_context(conv.summary, conv.scratchpad)
    if sess_ctx:
        session_parts.append(sess_ctx)
    # 学习状态视图（docs/17）：截至上一轮的结构化状态 + 遗留疑问，供切卡做
    # 跨轮 warning 判定（本轮命中在 build_cards 里从 result.tool_calls 取）
    pad = conv.scratchpad or {}
    learning = {"state": pad.get("learning_state"), "pending": pad.get("pending")}
    orchestrator = AgentOrchestrator(
        dynamic_context="\n\n".join(session_parts).strip(),
        allowed_tools=allowed_tools,
        user_id=user.id,
    )
    return conv, history_msgs, orchestrator, allowed_tools, learning


def _chat_impl(payload: ChatIn, user: User, db: Session) -> dict:
    conv, history_msgs, orchestrator, allowed_tools, learning = _prepare_chat(
        payload, user, db
    )
    _t0 = time.perf_counter()
    try:
        result: AgentResult = orchestrator.run(
            history=history_msgs[:-1],  # 最后一条就是刚写入的用户消息，run 里会拼
            user_text=payload.message,
            image_urls=image_urls(payload.images),
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
        save_message(db, conv.id, "assistant", CardMessage(
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
    main_card, extra_cards = build_cards(
        result, user_text=payload.message, course_id=payload.course_id,
        learning=learning,
    )
    save_message(db, conv.id, "assistant", main_card)
    for ex in extra_cards:
        save_message(db, conv.id, "assistant", ex)

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
    updated_context = build_updated_context(db, user.id, payload.course_id)

    # 8) 轮后任务（后台线程）：记忆抽取 + 会话记忆（画像回推已在上面同步做）
    bg = BackgroundTasks()
    bg.add_task(
        after_chat_tasks, user.id, payload.course_id, payload.message,
        main_card.text or "", conv.id, kg_hit_concepts(result),
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
            conv, history_msgs, orchestrator, allowed_tools, learning = _prepare_chat(
                payload, user, db
            )
            yield _sse("meta", {"conversation_id": conv.id, "title": conv.title})
            event_gen = orchestrator.run_stream(
                history=history_msgs[:-1],  # 最后一条就是刚写入的用户消息
                user_text=payload.message,
                image_urls=image_urls(payload.images),
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
            main_card, extra_cards = build_cards(
                result, user_text=payload.message, course_id=payload.course_id,
                learning=learning,
            )
            save_message(db, conv.id, "assistant", main_card)
            for ex in extra_cards:
                save_message(db, conv.id, "assistant", ex)
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
                updated_context=build_updated_context(db, user.id, payload.course_id),
            )
            yield _sse("done", {"data": chat_out.model_dump(mode="json")})
            # 轮后任务（记忆抽取 + 会话记忆）在流结束后随响应异步执行；
            # prepare 在生成器内完成，conv 只有这里拿得到
            bg.add_task(
                after_chat_tasks, user.id, payload.course_id, payload.message,
                main_card.text or "", conv.id, kg_hit_concepts(result),
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
                save_message(db, conv.id, "assistant", CardMessage(
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
                save_message(db, conv.id, "assistant", CardMessage(
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


@router.get("/tools")
def tools() -> dict:
    """透出当前启用的工具列表（供前端调试 / 演示）。"""
    return ok(tool_schemas())
