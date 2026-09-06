"""练习/选择卡作答闭环：作答留痕 → LLM 审阅反馈 → 画像回推。"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agent import glm_client
from app.core.db import get_session
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.core.security import get_current_user
from app.models import User
from app.repositories import chat_repo
from app.schemas.card import CardMessage, CardPayload
from app.schemas.chat import PracticeSubmitIn, PracticeSubmitOut
from app.api.student.support import save_message

router = APIRouter()

logger = logging.getLogger(__name__)

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


def options_block(payload_dict: dict | None, answer: str) -> str:
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
        options_block=options_block(payload_dict, answer),
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
    save_message(db, conv.id, "user", answer_card)

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
    save_message(db, conv.id, "assistant", feedback_card)
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
