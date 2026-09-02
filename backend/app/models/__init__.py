"""ORM 模型。

增加新模型请同时：
1. 在此处 `from .xxx import ...` 一下；
2. 由 `app.core.db.init_db()` 自动 create_all（仅本地）；生产用 Alembic 迁移。
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
