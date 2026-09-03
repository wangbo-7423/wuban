# 13 · Alembic 迁移

> 2026-09-02 引入。schema 演进唯一路径：**Alembic**；旧 `init_db()`（create_all + 手写补列 hack）已退役。

## 1. 为什么必须上

- 项目铁律「开发 / 生产必须同一套」——SQLite 雷区是同一逻辑，schema 管理不能例外；
- create_all 只建新表、不改旧表，手写 `ADD COLUMN IF NOT EXISTS` hack 已经攒出**真实债务**：
  - `agent_telemetry` 的 4 个 search 列在库里是 nullable，模型里是 NOT NULL（漂移）；
  - `messages.conversation_id` 外键丢了 `ON DELETE CASCADE`（语义偏差）。
  修复见迁移 `b128f66432fa`。越早引入越便宜（表少、无真实用户数据）。

## 2. 架构接线（单一配置源）

| 件 | 位置 | 要点 |
| --- | --- | --- |
| alembic.ini | `backend/alembic.ini` | **不写死 URL**；注释只用 ASCII（alembic 用系统 locale 读 ini，中文注释会 GBK 解码崩溃） |
| env.py | `backend/migrations/env.py` | URL 从 `settings.database_url`（backend/.env）注入；`ALEMBIC_DATABASE_URL` 可临时覆盖（验证用一次性库）；`target_metadata = Base.metadata`；`compare_type=True`；应用内调用时跳过 fileConfig |
| 启动迁移 | `app/core/db.py::run_migrations()` | main.py lifespan 调用，等价 `alembic upgrade head`；dev / 生产同一条路径 |

## 3. 日常工作流（改了 models 之后）

```bash
cd backend
# ① 改 app/models/*.py
# ② 生成迁移（空 diff 也常见，比如只改了 Python 端 default）
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "一句话描述"
# ③ 人工 review 生成的脚本（必做！）：
#    - autogenerate 会想把故意删掉的列/表加回来或 drop 掉它不认识的表；
#    - drop_constraint(None) 这类 PG 没法执行的占位要补成具名；
#    - SET NOT NULL 前确认无 NULL 行（必要时在迁移里先 UPDATE 回填）；
#    - 新增不可空列要带 server_default（否则存量行违反约束）。
# ④ 应用启动会自动 upgrade head；手动执行也可以：
.venv/Scripts/python.exe -m alembic upgrade head
# ⑤ 复查（应输出 No new upgrade operations detected）：
.venv/Scripts/python.exe -m alembic check
```

## 4. 版本历史

| 版本 | 内容 |
| --- | --- |
| `14af9d9e8982` | baseline：8 张表全量建表（agent_telemetry / learning_domains / learning_evidence / users / conversations / learner_profiles / learning_projects / messages） |
| `b128f66432fa` | 收编旧 hack 漂移：agent_telemetry 4 列 SET NOT NULL（含防御性回填）；messages 外键重建为 ON DELETE CASCADE |

## 5. 已验证

- 空库 `upgrade head` → `alembic check` 输出 **No new upgrade operations detected**（迁移产出与 ORM 模型完全一致）；
- `downgrade base` 可回滚；
- 开发库（aitutor@5433）：`stamp head` 接管 baseline → `upgrade head` 收编漂移修复 → `alembic check` 干净；
- 冒烟：uvicorn 启动自动迁移 + `/api/health` 通过；`tests/test_telemetry.py` 11/11。

## 6. 红线

- ❌ 不要再手写 DDL 补列（`_NEW_COLUMNS` 时代结束）；
- ❌ 不要跳过人工 review 直接 upgrade；
- ❌ 不要在 alembic.ini 写第二份 URL（配置漂移 = 事故）；
- ⚠️ autogenerate 对 server_default / 类型宽化的部分场景不敏感，`compare_type=True` 已开但 review 不能省。
