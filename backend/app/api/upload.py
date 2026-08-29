"""图片上传：沿用 research-assistant-agent 的安全范式。

安全三件套：
1. **魔数校验**（sniff 文件头），防伪造 Content-Type 绕过类型判断；
2. **类型 / 大小白名单**，超限直接拒；
3. **路径穿越防护**，落盘文件名用 uuid，绝不用用户原名（避免 `../../` 注入）。

落盘后返回 `/api/uploads-image/<uuid>.<ext>` 形式的 url；前端展示用该 url，
后端把 GLM 喂图时再读盘转 base64（GLM 远程无法访问本地路径）。
"""
from __future__ import annotations

import base64
import os
import uuid

from fastapi import APIRouter, Depends, File, UploadFile

from app.core.config import settings
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.core.security import get_current_user
from app.models import User

router = APIRouter()

# 类型白名单 → 扩展名
_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}

# 魔数特征（文件头）
_MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
_MAGIC_JPEG = b"\xff\xd8\xff"
_MAGIC_WEBP_HEAD = b"RIFF"
_MAGIC_WEBP_TAIL = b"WEBP"


def _sniff_ext(head: bytes) -> str | None:
    """按文件头反推真实扩展名（比 Content-Type 可信）。"""
    if head[:8] == _MAGIC_PNG:
        return "png"
    if head[:3] == _MAGIC_JPEG:
        return "jpg"
    if head[:4] == _MAGIC_WEBP_HEAD and head[8:12] == _MAGIC_WEBP_TAIL:
        return "webp"
    return None


def _validate_and_read(file: UploadFile) -> tuple[bytes, str]:
    """读取并校验文件，返回 (原始字节, 推断扩展名)。失败抛 BizError。"""
    data = file.file.read()
    if len(data) == 0:
        raise BizError(ErrorCode.PARAM_INVALID, "空文件")
    if len(data) > settings.max_image_bytes:
        mb = settings.max_image_bytes // (1024 * 1024)
        raise BizError(ErrorCode.PARAM_INVALID, f"图片超过 {mb}MB 上限")
    ext = _sniff_ext(data[:16])
    if ext is None:
        raise BizError(ErrorCode.PARAM_INVALID, "不支持的图片格式（仅 PNG/JPEG/WEBP）")
    return data, ext


@router.post("/upload-image", response_model=None)
async def upload_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict:
    data, ext = _validate_and_read(file)

    # 路径穿越防护：文件名完全由服务端生成（uuid），与用户原名无关
    os.makedirs(settings.upload_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    dest = os.path.join(settings.upload_dir, filename)
    # 二次确认确实落在 upload_dir 内（防御性）
    if not os.path.abspath(dest).startswith(os.path.abspath(settings.upload_dir)):
        raise BizError(ErrorCode.INTERNAL, "存储路径异常")
    with open(dest, "wb") as f:
        f.write(data)

    url = f"/api/uploads-image/{filename}"
    return ok(
        {
            "url": url,
            "mime": f"image/{ext}",
            "size": len(data),
        }
    )
