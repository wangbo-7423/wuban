"""AI 理工科伴学 · 系统提示词加载。

真源与结构（Cache + 渐进式披露）：
- **Agent.md**（仓库根目录）= 静态主提示词唯一真源，整体注入 system 消息；
  依赖智谱隐式前缀缓存，内容必须每轮不变——改完跑 cache_sanity 验证命中。
- **prompts/guide_*.md** = 场景工具指南（渐进式披露）：平时不下发，
  意图路由命中对应场景才由 orchestrator 注入到消息尾部（不进 system，保缓存）。

约束：
- 不要把用户隐私写进 system 消息；
- 动态上下文（课程/记忆/任务状态）一律走 orchestrator 的尾部注入，绝不拼进 system。
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path

from app.core.config import BACKEND_DIR

logger = logging.getLogger(__name__)

# 仓库根目录（Agent.md / prompts/ 都在那里）
_REPO_ROOT = BACKEND_DIR.parent


@functools.lru_cache(maxsize=1)
def _load_md(path: Path, label: str) -> str:
    """读一个提示词 md 文件，失败直接抛——提示词缺失是配置错误，宁可启动失败
    也不能静默退化成空提示词（Agent 会失去全部行为约束）。"""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise RuntimeError(
            f"提示词文件缺失或不可读：{label}（{path}）。"
            f"该文件是 Agent 行为的真源，请先还原它再启动。原因: {e}"
        ) from e
    if not text:
        raise RuntimeError(f"提示词文件为空：{label}（{path}）")
    return text


def load_system_prompt() -> str:
    """静态主提示词（Agent.md）。lru_cache：进程内只读一次磁盘。"""
    return _load_md(_REPO_ROOT / "Agent.md", "Agent.md")


def load_scene_guide(guide_file: str) -> str:
    """渐进式披露：加载工具 spec 的 guide 字段声明的场景指南。

    指南内容进的是**尾部动态上下文**，不进 system——同一场景内连续轮次
    内容一致，不影响前缀缓存；换了场景指南也换，缓存损失仅限尾部。
    文件名由各 ToolSpec / SkillSpec 就地声明（取代旧的 guide_{intent}.md
    命名约定）；声明为空 / 文件不存在返回空串——指南缺失不是配置错误
    （与主提示词不同：没有指南模型只是少场景提示，不该拦启动）。
    """
    if not guide_file:
        return ""
    path = _REPO_ROOT / "prompts" / guide_file
    if not path.is_file():
        logger.warning("场景指南文件不存在（已跳过）: %s", path)
        return ""
    return _load_md(path, f"prompts/{guide_file}")


# 兼容旧导出：只返回静态文本（extra 参数已废弃，见 docstring）
def build_system_prompt(extra: str | None = None) -> str:
    """.. deprecated:: 动态上下文请走 orchestrator 的尾部注入，别拼进 system。"""
    return load_system_prompt()


def build_system_messages() -> list[dict[str, str]]:
    """静态 system 块（Cache 策略的落点）。

    实测（GLM 5.3 Flash，2026-08）：
    - 缓存按**前缀**命中，且存在最小可缓存长度（~1.7K token）——单条 SYSTEM_PROMPT
      恰好卡在门槛下，垫一条静态说明后连续调用稳定命中 1728/1781 token；
    - 任何一条 system 消息内容变化 → 整个 system 块前缀作废。
    """
    return [
        {"role": "system", "content": load_system_prompt()},
        {"role": "system", "content": _STATIC_CONTEXT_NOTE},
    ]


_STATIC_CONTEXT_NOTE = (
    "以上是产品行为约束。这位学生的动态上下文（当前课程、本轮场景、长期记忆、"
    "任务状态）在每轮消息末尾以系统注入的形式给出，以最新一条为准；"
    "它不属于学生的提问本身。"
)
