"""student.py 纯辅助函数单测：历史消息还原（含多模态图片回放）。"""
from __future__ import annotations

from types import SimpleNamespace

from app.api.student.support import history_to_messages


def _msg(role: str, text: str, payload: dict | None = None):
    return SimpleNamespace(role=role, text=text, payload=payload)


class TestHistoryToMessages:
    def test_basic_text_roundtrip(self):
        msgs = [
            _msg("user", "什么是进程？"),
            _msg("assistant", "进程是…"),
        ]
        out = history_to_messages(msgs)
        assert out == [
            {"role": "user", "content": "什么是进程？"},
            {"role": "assistant", "content": "进程是…"},
        ]

    def test_only_user_and_assistant_kept(self):
        msgs = [
            _msg("system", "内部消息"),
            _msg("user", "问题"),
        ]
        out = history_to_messages(msgs)
        assert len(out) == 1

    def test_limit_keeps_latest(self):
        msgs = [_msg("user", f"m{i}") for i in range(12)]
        out = history_to_messages(msgs, limit=8)
        assert len(out) == 8
        assert out[0]["content"] == "m4"

    def test_user_images_restored_before_text(self, tmp_path, monkeypatch):
        # resolve_image 只认上传目录里真实存在的文件；造一个真文件再回放
        from app.core.config import settings

        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        (upload_dir / "a.png").write_bytes(b"\x89PNG fake")
        monkeypatch.setattr(settings, "upload_dir", str(upload_dir))

        msgs = [
            _msg(
                "user",
                "看这道题",
                payload={"meta": {"images": [{"url": "/api/uploads-image/a.png"}]}},
            ),
        ]
        out = history_to_messages(msgs)
        content = out[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content[-1] == {"type": "text", "text": "看这道题"}

    def test_missing_image_file_degrades_to_text(self):
        # 文件已不存在（如清过盘）：优雅降级为纯文本，不抛异常
        msgs = [
            _msg(
                "user",
                "看这道题",
                payload={"meta": {"images": [{"url": "/api/uploads-image/ghost.png"}]}},
            ),
        ]
        out = history_to_messages(msgs)
        assert out[0]["content"] == "看这道题"

    def test_assistant_payload_images_ignored(self):
        # 图片只随用户消息回放；assistant 卡上的 meta 不参与多模态还原
        msgs = [
            _msg(
                "assistant",
                "收到",
                payload={"meta": {"images": [{"url": "/api/uploads-image/a.png"}]}},
            ),
        ]
        out = history_to_messages(msgs)
        assert out[0]["content"] == "收到"
