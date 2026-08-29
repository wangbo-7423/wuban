"""卡片格式化器（cards.py）单测：JSON 抽取、枚举清洗、兜底卡与降级路径。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent import cards as cards_mod
from app.agent.cards import (
    _extract_json,
    _fallback_card,
    _parse_card,
    format_cards,
)
from app.agent.orchestrator import AgentResult


def _fake_llm(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _result(text: str = "回答正文") -> AgentResult:
    return AgentResult(
        text=text,
        thinking="thinking…",
        tool_calls=[],
    )


class TestExtractJson:
    def test_bare_json(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fenced_no_lang(self):
        assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_invalid_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("不是 JSON")


class TestParseCard:
    def test_valid_math_card(self):
        card = _parse_card(
            {
                "card_type": "math",
                "text": "引导语",
                "payload": {
                    "math": {
                        "problem": "求 ∫x²dx",
                        "steps": [{"expr": "\\int x^2 dx = \\frac{x^3}{3}", "note": "幂函数积分"}],
                        "answer": "\\frac{x^3}{3}+C",
                    }
                },
                "scaffold_level": "faded",
                "confidence": 0.9,
                "gave_answer": False,
                "strategy": ["分解"],
            }
        )
        assert card is not None
        assert card.card_type == "math"
        assert card.payload.math.answer == "\\frac{x^3}{3}+C"
        assert card.scaffold_level == "faded"
        assert card.gave_answer is False

    def test_invalid_card_type_downgrades_to_text(self):
        card = _parse_card({"card_type": "poem", "text": "正文"})
        assert card.card_type == "text"
        assert card.text == "正文"  # 保卡不丢内容

    def test_invalid_scaffold_cleaned_but_card_kept(self):
        # 模型偶尔输出协议外枚举（如 "open"）：清洗为 None，不丢整卡
        card = _parse_card({"card_type": "understand", "text": "x", "scaffold_level": "open"})
        assert card.scaffold_level is None
        assert card.text == "x"

    def test_confidence_out_of_range_cleaned(self):
        card = _parse_card({"card_type": "text", "text": "x", "confidence": 1.5})
        assert card.confidence is None

    def test_next_action_invalid_cleaned(self):
        card = _parse_card({"card_type": "text", "text": "x", "next_action": "blame"})
        assert card.next_action is None

    def test_payload_protocol_violation_drops_card(self):
        # options 必须是 list[dict]，协议外类型宁可丢卡也不脏渲染
        assert _parse_card({"card_type": "choice", "text": "x", "payload": {"options": "A"}}) is None

    def test_evidence_without_source_dropped(self):
        card = _parse_card(
            {
                "card_type": "text",
                "text": "x",
                "evidence": [{"confidence": 1.0}, {"source": "教材§2.1"}],
            }
        )
        assert card.evidence is not None
        assert [e.source for e in card.evidence] == ["教材§2.1"]


class TestFallbackCard:
    def test_question_mark_routes_to_question(self):
        card = _fallback_card(_result("你有没有想过它的逆命题？"))
        assert card.card_type == "question"

    def test_multiline_routes_to_understand(self):
        card = _fallback_card(_result("第一行\n第二行"))
        assert card.card_type == "understand"

    def test_plain_routes_to_text(self):
        assert _fallback_card(_result("一句话")).card_type == "text"

    def test_keeps_thinking_and_asks_back(self):
        card = _fallback_card(_result("正文"))
        assert card.thinking == "thinking…"
        assert card.next_action == "ask"


class TestFormatCards:
    def test_llm_failure_falls_back_to_single_card(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("GLM down")

        monkeypatch.setattr(cards_mod.glm_client, "chat", _boom)
        out = format_cards(_result("正文"), user_text="q", course_id="math")
        assert len(out) == 1
        assert out[0].text == "正文"

    def test_llm_success_splits_cards(self, monkeypatch):
        payload = {
            "cards": [
                {
                    "card_type": "math",
                    "text": "引导语",
                    "payload": {
                        "math": {"problem": "p", "steps": [{"expr": "e"}], "answer": "a"}
                    },
                },
                {"card_type": "question", "text": "你会怎么验证？"},
            ]
        }
        monkeypatch.setattr(
            cards_mod.glm_client,
            "chat",
            lambda *a, **k: _fake_llm(json.dumps(payload, ensure_ascii=False)),
        )
        out = format_cards(_result("原文"), user_text="q", course_id="math")
        assert [c.card_type for c in out] == ["math", "question"]

    def test_llm_empty_cards_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            cards_mod.glm_client,
            "chat",
            lambda *a, **k: _fake_llm('{"cards": []}'),
        )
        out = format_cards(_result("正文"), user_text="q", course_id="math")
        assert len(out) == 1
