"""fix drift: agent_telemetry not null, messages fk cascade

Revision ID: b128f66432fa
Revises: 14af9d9e8982
Create Date: 2026-09-02 19:54:26.208844

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b128f66432fa'
down_revision: Union[str, Sequence[str], None] = '14af9d9e8982'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 防御性回填：其他环境若有 NULL 行（旧 ALTER 建列后写入），先补默认值，
    # 避免 SET NOT NULL 失败。当前开发库 38 行均无 NULL。
    op.execute("UPDATE agent_telemetry SET search_calls = 0 WHERE search_calls IS NULL")
    op.execute("UPDATE agent_telemetry SET search_results = 0 WHERE search_results IS NULL")
    op.execute("UPDATE agent_telemetry SET search_authority_hits = 0 WHERE search_authority_hits IS NULL")
    op.execute("UPDATE agent_telemetry SET bare_numeric = false WHERE bare_numeric IS NULL")
    op.alter_column('agent_telemetry', 'search_calls',
               existing_type=sa.INTEGER(),
               nullable=False,
               existing_server_default=sa.text('0'))
    op.alter_column('agent_telemetry', 'search_results',
               existing_type=sa.INTEGER(),
               nullable=False,
               existing_server_default=sa.text('0'))
    op.alter_column('agent_telemetry', 'search_authority_hits',
               existing_type=sa.INTEGER(),
               nullable=False,
               existing_server_default=sa.text('0'))
    op.alter_column('agent_telemetry', 'bare_numeric',
               existing_type=sa.BOOLEAN(),
               nullable=False,
               existing_server_default=sa.text('false'))
    # 旧 FK 无 ON DELETE CASCADE（init_db 时代创建），删掉重建为级联删除
    op.drop_constraint(op.f('messages_conversation_id_fkey'), 'messages', type_='foreignkey')
    op.create_foreign_key(None, 'messages', 'conversations', ['conversation_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    """Downgrade schema."""
    # CASCADE 版 FK 由 PG 自动命名为 messages_conversation_id_fkey，按名删除
    op.drop_constraint(op.f('messages_conversation_id_fkey'), 'messages', type_='foreignkey')
    op.create_foreign_key(op.f('messages_conversation_id_fkey'), 'messages', 'conversations', ['conversation_id'], ['id'])
    op.alter_column('agent_telemetry', 'bare_numeric',
               existing_type=sa.BOOLEAN(),
               nullable=True,
               existing_server_default=sa.text('false'))
    op.alter_column('agent_telemetry', 'search_authority_hits',
               existing_type=sa.INTEGER(),
               nullable=True,
               existing_server_default=sa.text('0'))
    op.alter_column('agent_telemetry', 'search_results',
               existing_type=sa.INTEGER(),
               nullable=True,
               existing_server_default=sa.text('0'))
    op.alter_column('agent_telemetry', 'search_calls',
               existing_type=sa.INTEGER(),
               nullable=True,
               existing_server_default=sa.text('0'))
    # ### end Alembic commands ###
