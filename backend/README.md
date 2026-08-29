# AI 伴学 · 后端

GLM 5.3 Flash 驱动的 AI 理工科伴学后端（FastAPI + PostgreSQL + zai-sdk）。
本仓已**彻底拆掉**「意图识别 → KG → 诊断 → 策略 → 卡片」五层手工流水线，
改为一个**真 Agent**：

> system prompt + 用户消息 + 工具函数 → 让 GLM 自己决定先问 / 先解释 / 调工具 / 给练习题。

## 速览

```
backend/
├── app/
│   ├── core/             # 配置 / DB / 安全 / 统一响应 / 异常
│   ├── models/           # SQLAlchemy ORM
│   ├── schemas/          # Pydantic DTO（前后端契约真源）
│   ├── repositories/     # 数据访问
│   ├── api/              # FastAPI 路由（auth / learning / student / health）
│   ├── agent/            # ⭐ AI Agent 大脑
│   │   ├── glm_client.py    # zai-sdk 封装（支持 thinking / 多模态 / 工具）
│   │   ├── system.py        # 提示词加载器（真源在仓库根 Agent.md + prompts/guide_*.md）
│   │   ├── intent.py        # 意图路由：动态装配本轮工具 + 场景指南（渐进式披露）
│   │   ├── compress.py      # 旧轮工具结果一行摘要（Compress 策略）
│   │   ├── tools.py         # 工具注册表（calculator / kg_lookup / 占位 web_search）
│   │   └── orchestrator.py  # Agent 主循环：GLM ↔ Tools
│   ├── kg/               # 静态知识库（OS / 自动控制 / 信号系统）
│   ├── _archive/         # 旧流水线归档（已下线）
│   └── main.py
├── docker-compose.yml    # 一键起 PostgreSQL
├── .env.example          # 环境变量样例
├── pyproject.toml
└── README.md (本文件)
```

## 起服务

```bash
# 1) 起 Postgres（端口 5433，避免和本地 5432 冲突）
docker compose -f docker-compose.yml up -d

# 2) 安装依赖（uv / pdm / pip 任你）
uv pip install -r <(uv pip compile pyproject.toml) \
  --python .venv/Scripts/python.exe   # 或你的 Python 解释器

# 3) 复制 .env 并填好 API Key
cp .env.example .env
#  GLM_API_KEY=...

# 4) 跑服务
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --reload
```

打开 `http://127.0.0.1:8000/docs` 看自动生成的 OpenAPI。

## GLM 5.3 Flash 配置

所有模型相关配置进 `.env`：

```ini
GLM_API_KEY=...
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_MODEL=glm-5.3-flash
GLM_ENABLE_THINKING=true   # 启用思维链（强烈建议保持）
GLM_TIMEOUT_SEC=60
GLM_MAX_TOKENS=4096
GLM_TEMPERATURE=0.7
```

## Agent 工作示意

```text
用户 → /api/student/chat
   ↓
AgentOrchestrator
   ↓ 拼 system + 历史 + 用户消息 + 工具 schema
GLM 5.3 Flash（启用 thinking）
   ↓
若是工具调用：执行 → 把结果塞回去 → 再问 GLM（最多 4 轮）
   ↓
拿到最终文本 + 思维链 + 工具调用记录 → 打 CardMessage → 落库 → 回前端
```

示例真实 trace（来自端到端测试）：

```
tool_calls[0] = {
  "tool_name": "kg_lookup",
  "args": {"course_id": "os", "query": "死锁"},
  "result": {"ok": true, "matched": {"id": "sync", "name": "并发与同步",
              "prerequisites": ["thread"], "summary": "锁/信号量/死锁"},
              "course": "操作系统"},
  "ok": true,
}
```

## API 速查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/api/health` | 健康检查 |
| POST | `/api/auth/register` | 注册（仅 `username + nickname + password`，自动登录） |
| POST | `/api/auth/login`    | 登录 |
| GET  | `/api/auth/me`       | 当前用户 |
| GET  | `/api/domains` 等    | 学习域 / 项目 / 画像（保留旧接口） |
| POST | `/api/student/chat`  | **核心**：发条消息给 Agent（支持多模态） |
| GET  | `/api/student/conversations` | 会话列表 |
| POST | `/api/student/conversations` | 新建会话 |
| GET  | `/api/student/conversations/{id}/messages` | 会话详情 + 消息列表 |
| DELETE | `/api/student/conversations/{id}` | 删除会话 |
| GET | `/api/student/tools` | 当前启用的工具列表（调试用） |

## Schemas 是前后端契约真源

```text
backend/app/schemas/{auth,chat,card,learning}.py
        ≡
frontend/src/api/types.ts
```

任一字段改动必须同步两端。

## 工具扩展位

`app/agent/tools.py` 里注册新工具只需三步：
1. 写一个 `def my_tool(args): dict`
2. 加 `ToolSpec(..., name="my_tool", schema={...}, func=my_tool)`
3. 如需总开关，加到 `settings` 的 `enable_xxx: bool`

未来接 MCP server 时只要把 `func` 换成 MCP 客户端调用，其他代码不动。
