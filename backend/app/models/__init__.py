"""ORM 模型。

增加新模型请同时：
1. 在此处 `from .xxx import ...` 一下（触发注册到 Base.metadata）；
2. `alembic revision --autogenerate` 生成迁移并**人工 review** 后再 upgrade
   （app 启动时会自动 `alembic upgrade head`）。
"""
from .conversation import Conversation, Message
from .evidence import LearningEvidence
from .learning import LearnerProfile, LearningDomain, LearningProject
from .telemetry import AgentTelemetry
from .user import User

__all__ = [
    "User",
    "LearnerProfile",
    "LearningDomain",
    "LearningProject",
    "Conversation",
    "Message",
    "AgentTelemetry",
    "LearningEvidence",
]
