"""工具结果压缩（Compress ①②）单测：摘要字段、一行化、就地压缩与幂等。

digest 字段由各工具 spec 的 digest_fields 就地声明（领域知识就地打包），
本文件显式传参测通用格式化；「按 tool_call_id 反查工具名 → 取 spec 字段」
的端到端路径另有专门用例。
"""
from __future__ import annotations

import json

from app.agent.compress import (
    _DIGEST_MARK,
    compress_old_tool_results,
    digest_tool_content,
)


class TestDigestToolContent:
    def test_ok_and_value(self):
        out = digest_tool_content(json.dumps({"ok": True, "value": "42"}), ("value",))
        assert out.startswith(_DIGEST_MARK)
        assert "ok" in out
        assert "value=42" in out

    def test_failure_keeps_error(self):
        # error 是通用字段，任何 spec 都保留
        out = digest_tool_content(json.dumps({"ok": False, "error": "boom 爆炸"}))
        assert "失败" in out
        assert "error=boom 爆炸" in out

    def test_answer_field_preferred(self):
        out = digest_tool_content(
            json.dumps({"ok": True, "answer": "x = 1", "value": "1"}), ("answer", "value")
        )
        assert "answer=x = 1" in out

    def test_matched_dict_extracts_name(self):
        out = digest_tool_content(
            json.dumps({"ok": True, "matched": {"name": "进程与线程", "id": "n1"}}),
            ("matched",),
        )
        assert "matched=进程与线程" in out

    def test_entities_list_of_dicts(self):
        out = digest_tool_content(
            json.dumps({"ok": True, "entities": [{"name": "傅里叶变换"}]}), ("entities",)
        )
        assert "entities=['傅里叶变换']" in out

    def test_stdout_flattened_to_one_line(self):
        out = digest_tool_content(
            json.dumps({"ok": True, "stdout": "line1\nline2\n\nline3"}), ("stdout",)
        )
        assert "line1 line2 line3" in out
        assert "\n" not in out.replace(_DIGEST_MARK, "", 1).lstrip(_DIGEST_MARK)

    def test_field_capped_at_80_chars(self):
        out = digest_tool_content(json.dumps({"ok": True, "value": "a" * 500}), ("value",))
        assert "value=" + "a" * 80 in out
        assert len(out) < 200

    def test_list_fields_counted_not_dumped(self):
        out = digest_tool_content(
            json.dumps({"ok": True, "steps": [{"expr": "x"}] * 3})
        )
        assert "steps×3" in out
        assert "expr" not in out

    def test_non_json_fallback(self):
        out = digest_tool_content("这不是 JSON")
        assert out.startswith(_DIGEST_MARK)
        assert "这不是 JSON" in out

    def test_non_dict_json_fallback(self):
        out = digest_tool_content("[1, 2, 3]")
        assert out.startswith(_DIGEST_MARK)

    def test_never_raises(self):
        # None / 空串等异常输入也不能把主链路炸掉
        assert digest_tool_content("").startswith(_DIGEST_MARK)


class TestSpecDrivenDigest:
    """端到端：tool_call_id 反查工具名 → 读该工具 spec 的 digest_fields。"""

    def test_calculus_result_keeps_answer(self):
        """真实注册表：calculus skill 声明 answer 是结论字段。"""
        result = json.dumps(
            {"ok": True, "answer": "π", "teaching_hints": "很长一段教学提示" * 20},
            ensure_ascii=False,
        )
        out = digest_tool_content(result, ("answer",))
        assert "answer=π" in out
        assert "教学提示" not in out  # 非结论字段不进摘要

    def test_unknown_tool_falls_back(self):
        from app.agent.compress import _digest_fields_for

        assert _digest_fields_for("no_such_tool")  # 兜底字段非空，压缩不失败
        assert _digest_fields_for("")  # 查不到名字也不炸

    def test_spec_fields_resolve_by_name(self):
        """已注册工具都能解析出非空 digest_fields。"""
        from app.agent.compress import _digest_fields_for

        for name in ("kg_lookup", "code_runner"):
            fields = _digest_fields_for(name)
            assert fields, f"{name} 的 spec 未声明 digest_fields"


class TestCompressOldToolResults:
    @staticmethod
    def _messages() -> list[dict]:
        big_result = json.dumps({"ok": True, "steps": [{"expr": "x" * 50}] * 5})
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "问题"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "function": {"name": "calculator", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "t1", "content": big_result},
        ]

    def test_saves_chars_and_replaces_content(self):
        msgs = self._messages()
        original_len = len(msgs[3]["content"])
        saved = compress_old_tool_results(msgs)
        assert saved > 0
        assert msgs[3]["content"].startswith(_DIGEST_MARK)
        assert len(msgs[3]["content"]) < original_len

    def test_only_tool_messages_touched(self):
        msgs = self._messages()
        sys_content = msgs[0]["content"]
        user_content = msgs[1]["content"]
        compress_old_tool_results(msgs)
        assert msgs[0]["content"] == sys_content
        assert msgs[1]["content"] == user_content

    def test_idempotent(self):
        msgs = self._messages()
        first = compress_old_tool_results(msgs)
        second = compress_old_tool_results(msgs)
        assert first > 0
        assert second == 0  # 已压过的不重复压

    def test_resolves_tool_name_from_call_id(self):
        """tool_call_id 反查：同一条消息流里不同工具各按自己的字段压。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "calculus", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "kg_lookup", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps(
                {"ok": True, "answer": "π", "steps": [{"expr": "x" * 40}] * 4})},
            {"role": "tool", "tool_call_id": "c2", "content": json.dumps(
                {"ok": True, "matched": {"name": "傅里叶变换", "id": "n1"},
                 "course": "信号与系统"})},
        ]
        compress_old_tool_results(msgs)
        assert "answer=π" in msgs[2]["content"]
        assert "matched=傅里叶变换" in msgs[3]["content"]
