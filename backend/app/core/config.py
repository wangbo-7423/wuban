"""应用配置：统一通过 pydantic-settings 从 .env / 环境变量读取。

设计原则：
- **任何运行环境都用同一份 config**；缺关键变量时启动失败，绝不静默降级（避免开发用 SQLite、生产错连）；
- 字段按职责分组：应用 / 数据库 / 鉴权 / 大模型 / 跨域 / MCP&工具；
- 类型 + 校验全部交给 Pydantic；下游模块只读 `settings.xxx`。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 计算 backend/app/core/config.py 上两级到 backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """统一配置：从 backend/.env 读取（自动按字段名映射环境变量）。"""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 应用 / 调试 ────────────────────────────────────────
    app_name: str = "AI 伴学"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"

    # ── 数据库（强校验：env 缺失启动失败）──────────────────
    database_url: str = Field(
        ...,  # 必填，无默认 → .env 没设就启动报错
        description="SQLAlchemy URL，仅支持 PostgreSQL，例如 postgresql+psycopg2://user:pwd@host:5432/db",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    # ── 鉴权 ───────────────────────────────────────────────
    jwt_secret: str = Field(..., min_length=32, description="HS256 密钥，至少 32 字节")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 1 天
    jwt_issuer: str = "ai-tutor"

    # ── 大模型（GLM 5.3 Flash）───────────────────────────
    glm_api_key: str = Field(..., description="智谱 AI 的 API Key")
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    glm_model: str = "glm-5.3-flash"
    glm_timeout_sec: float = 90.0  # 覆盖 429 排队窗口的 60~90s 等待
    glm_enable_thinking: bool = True
    # 推理深度档位：low / high / max（GLM-5.2+ 生效，5.3 / 5.3-flash 仅这三档）。
    # 背景：glm-5.3 系列是「强制思考」模型——thinking 关不掉（传 disabled 会报错），
    # 且 reasoning 产生的 token 计入 max_tokens、在正文之前被优先消耗。
    # 不传该参数时平台默认 max（深度推理），是首字延迟和「想得多、输出少」的主因。
    # 伴学是对话型场景，默认 low；若数学推导质量下滑可改成 high。
    glm_reasoning_effort: str = "low"
    glm_max_tokens: int = 4096
    glm_temperature: float = 0.7
    # 限流/超时重试：指数退避，仅在可重试错误（429 / 超时）上生效
    glm_rate_limit_retries: int = 2
    glm_rate_limit_backoff_sec: float = 3.0

    # ── 跨域 ───────────────────────────────────────────────
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ── MCP / 工具 / 检索（可选；空则禁用）───────────────
    milvus_uri: str = ""         # 向量库（RAG / KG grounding）
    redis_url: str = ""          # 缓存 / 限流
    minio_endpoint: str = ""     # 对象存储（图片 / 附件）
    higress_gateway: str = ""    # MCP 网关（如未来要用 Higress）
    # 默认开启（docs/11）：失败语义是结构化降级——全部后端不可用时返回
    # ok=False + hint，GLM 用本地知识回应并声明确定度，绝不阻塞主链路。
    # 部署在完全离线环境时可设回 False。
    enable_web_search: bool = True
    # web_search 后端：优先 Tavily（配 key），否则 ddgs 多引擎（pip install ddgs）。
    # ddgs 的 backend 留空用 auto；国内网络不稳时可指定 "bing"。
    tavily_api_key: str = ""
    web_search_backend: str = "auto"
    enable_calculator: bool = True           # 数学习题，启用
    # 代码执行（工科微项目线核心）。默认开启：本项目的价值主张就是让学生把仿真/
    # 脚本贴出来、AI 跑一遍给审阅式反馈。进程沙箱禁网络+禁系统命令+硬超时，仅适合
    # 本地/受信环境；公网多租户必须 CODE_RUNNER_BACKEND=docker（容器级硬隔离）。
    # 详见 docs/14-code_runner沙箱设计.md 与 app/agent/code_runner.py 顶部安全模型。
    enable_code_runner: bool = True
    code_runner_timeout: float = 15.0        # 单次代码执行硬超时（秒）
    # 执行后端（docs/14-code_runner沙箱设计.md）：process = 子进程级（dev/受信环境，
    # 默认）；docker = 容器级硬隔离（公网多租户用，宿主机需有 docker CLI）。
    # 两个后端返回契约完全一致，切换零改动 tool 层。
    code_runner_backend: str = "process"
    code_runner_docker_image: str = "python:3.12-slim"  # 生产建议自建含 matplotlib 的镜像

    # ── MCP 记忆服务（官方 @modelcontextprotocol/server-memory）──
    # 每个用户一个 memory server 子进程，通过 MEMORY_FILE_PATH 隔离
    # data/memory/user_{id}.jsonl。node 缺失时自动降级（不影响主链路）。
    enable_mcp_memory: bool = True
    mcp_memory_dir: str = str(BACKEND_DIR / "data" / "memory")
    mcp_memory_call_timeout: float = 12.0    # 单次 MCP 工具调用超时（秒）
    mcp_memory_idle_sec: float = 900.0       # 子进程空闲回收阈值（15 分钟）
    mcp_memory_max_sessions: int = 8         # 同时存活的 memory server 上限

    # ── 文件上传（图片，沿用 research-assistant-agent 安全范式）──
    upload_dir: str = Field(
        default=str(BACKEND_DIR / "uploads-image"),
        description="图片落盘目录（绝对路径）；静态服务挂载在 /api/uploads-image",
    )
    max_image_bytes: int = 10 * 1024 * 1024   # 单图上限 10MB
    allowed_image_types: list[str] = Field(
        default_factory=lambda: ["image/png", "image/jpeg", "image/webp"],
        description="图片 MIME 白名单",
    )

    # ── Agent 遥测（Harness 警示二：failure log 当一等公民）──
    # 每轮 Agent 调用（同步/流式、成功/失败）落一行 agent_telemetry；
    # 任何异常只打 warning，绝不影响聊天主链路。压测/多租户可关。
    enable_agent_telemetry: bool = True

    # ── 认知状态工具（docs/10-认知状态与工具边界.md）──────────
    # scaffold_state（读）：模型按需查学生认知档案，替代全量注入；
    # mastery_evidence（写）：模型实时记录过程性证据标签（只记标签不记分数）。
    # 两者都是全场景装配，关掉即从注册表消失，主链路零感知。
    enable_scaffold_state: bool = True
    enable_mastery_evidence: bool = True

    @field_validator("database_url")
    @classmethod
    def _only_postgres(cls, v: str) -> str:
        """约束：只接受 PostgreSQL。SQLite/MySQL 一律拒绝。"""
        if not v:
            raise ValueError("DATABASE_URL 不能为空")
        if not (v.startswith("postgresql://") or v.startswith("postgresql+")):
            raise ValueError(
                f"DATABASE_URL 必须是 PostgreSQL 协议，当前: {v[:32]}..."

            )
        return v

    @field_validator("code_runner_backend")
    @classmethod
    def _valid_runner_backend(cls, v: str) -> str:
        """执行后端只允许 process / docker（写错直接启动失败，不静默回落）。"""
        v = (v or "").strip().lower()
        if v not in ("process", "docker"):
            raise ValueError(f"CODE_RUNNER_BACKEND 只支持 process|docker，当前: {v}")
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v):
        """允许 .env 写成 JSON 数组或逗号分隔字符串。"""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                import json
                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取单例 Settings（lru_cache 保证只读一次 .env）。"""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
