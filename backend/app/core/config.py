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
    enable_web_search: bool = False          # 是否启用 web_search 工具
    # web_search 后端：优先 Tavily（配 key），否则 ddgs 多引擎（pip install ddgs）。
    # ddgs 的 backend 留空用 auto；国内网络不稳时可指定 "bing"。
    tavily_api_key: str = ""
    web_search_backend: str = "auto"
    enable_calculator: bool = True           # 数学习题，启用
    # 代码执行（工科微项目线核心）。默认开启：本项目的价值主张就是让学生把仿真/
    # 脚本贴出来、AI 跑一遍给审阅式反馈。沙箱禁网络+禁系统命令+硬超时，仅适合
    # 本地/受信环境；多租户生产请改用容器/gVisor/WASM，并设回 False。详见
    # app/agent/code_runner.py 顶部安全模型说明。
    enable_code_runner: bool = True
    code_runner_timeout: float = 15.0        # 单次代码执行硬超时（秒）

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
