# AI 伴学 · 项目根说明

> **AI 伴学** = 面向高校**工科生**的一对一 AI 学习陪伴助手（不开教师端、不用邮箱注册）。融合**认知心理学**设计，围绕「概念解释、数学计算、定理公式、工程实操」四类核心学习场景，让学生**真的懂**，而不是只拿到答案。
>
> 后端大模型：**GLM 5.3 Flash**（启用 thinking 模式）+ 工具调用（`calculator / kg_lookup / web_search`，后端可插拔）；前后端通过**类型契约**严格对齐。

## 项目结构

```
.
├── Agent.md      # Agent 静态主提示词唯一真源（system 注入，依赖前缀缓存勿放动态内容）
├── prompts/      # 场景工具指南（渐进式披露：意图路由命中才注入）
├── backend/      # FastAPI + zai-sdk（GLM 5.3 Flash）+ PostgreSQL 16
├── frontend/     # Vue 3 + TS + Pinia + Element Plus + Vite
└── docs/         # 设计文档（00~07 共 8 篇，下方索引）
```

| 仓 | 真源 |
| --- | --- |
| **Agent 提示词** | `Agent.md`（静态主提示词）+ `prompts/guide_*.md`（场景指南，渐进式披露）↔ `backend/app/agent/system.py`（加载器） |
| **接口字段契约** | `docs/02-API接口文档.md` ↔ `backend/app/schemas/*.py` ↔ `frontend/src/api/types.ts` |
| **卡片协议** | `docs/03-卡片协议.md` ↔ `backend/app/schemas/card.py` ↔ `frontend/src/api/types.ts` ↔ `frontend/src/components/chat/CardRenderer.vue` |

> 三处/四处真源改任何一处都要同步其余每一处，详见 [06-开发环境 §7](./docs/06-开发环境与运行指南.md)。

## 快速开始

```bash
# 1) 启 PostgreSQL（端口 5433，本地不和 5432 冲突）
cd backend && docker compose -f docker-compose.yml up -d

# 2) 填环境变量并启后端
cp .env.example .env && vi .env        # 必填 GLM_API_KEY / JWT_SECRET(≥32) / DATABASE_URL
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --reload   # 或 pip 安装后跑 python -m uvicorn ...

# 3) 启前端
cd ../frontend && npm install && npm run dev     # 默认 :5173
```

跑单测（纯逻辑，不连 GLM / 不连库）：

```bash
cd backend && .venv/Scripts/python.exe -m pytest -q
```

打开 `http://localhost:5173` → 注册（`username + nickname + password`，**无邮箱无角色**）→ 进入学习主界面。
后端 OpenAPI（开发自检）：`http://localhost:8000/docs`。

完整跑通指引、联调约定、工具扩展位、类型契约改动流程见 [`docs/06-开发环境与运行指南.md`](./docs/06-开发环境与运行指南.md)。

## 当前能力速览

- 鉴权：注册/登录（`username + nickname + password`，无邮箱、无教师端、无角色）→ 自动签发 JWT（24h）；
- 学习主界面：对话流 + 16 种结构化卡片（`text/math/engineering/question/understand/practice/feedback/warning/...` 独立组件渲染）+ 左侧会话列表 + 右侧学习状态侧栏；
- **流式对话（SSE）**：`POST /student/chat/stream` 逐段推送思考链 / 正文增量 / 工具轨迹，
  结束时下发切好的结构化卡片包；前端 fetch + ReadableStream 解析，支持中途停止；
- Agent：GLM 5.3 Flash thinking 模式 + 系统提示词约束认知科学规则 + 意图路由动态装配工具（math/engineering/concept/chat 四场景各装配 3~5 个候选）+ 4 轮工具循环上限；
- 卡片切卡：`format_cards()` 用 GLM JSON mode 把最终回答切成 1~3 张结构化卡（math/engineering/practice/choice 等），失败回退启发式单卡；
- 联网搜索：`web_search` 后端可插拔（Tavily 有 key 优先，否则 ddgs 多引擎），结果裁剪有界，
  全部后端不可用时返回结构化错误 + hint，GLM 自动降级用本地知识并声明确定度；
- 长期记忆（上下文工程）：MCP 知识图谱按「L0 常驻层（偏好/目标/误区）+ L1 检索层（当前消息按实体名+别名双通道命中的已学概念 + 一步邻接关系）」分层注入，聊天后异步抽取实体/别名/关系回写图谱；
- 多模态：题目图片 ≤4 张（base64 data URI 上传）；
- 会话持久化：用户/助手卡片整卡 JSON 落 `messages` 表，思维链与工具调用全留痕；
- 会话级记忆（Write 策略）：早期对话 LLM 滚动压缩摘要 + 任务状态 Scratchpad（在学什么/进行到哪/等学生做什么），窗口里只放「摘要 + 最近 8 条」，长对话不再无限膨胀；
- 工具结果压缩（Compress 策略①②）：多轮工具调用时旧轮 Observation 压成一行结论（实测 92% 压缩率），当前轮完整保留、留痕全量不丢；
- 前缀缓存（Cache 策略）：system 块全静态 + 动态上下文尾部注入，适配智谱隐式前缀缓存（命中半价），生产链路实测二轮命中率 42% 且可观测；
- 学习画像：与认证解耦的 `learner_profiles.cognitive_state` JSON 字段位、掌握度/认知负荷/复习到位的回推预留位；
- **练习闭环**：`practice`/`choice` 卡作答提交（`/student/practice/submit`）→ `payload.meta.attempts` 留痕
  → LLM 审阅反馈卡（先肯定→指出问题并说明原因→抛引申疑问，不打分）→ `cognitive_state.practice` 过程性证据回推；
- 质量保障：后端 pytest 单测 81 个（压缩/意图路由/卡片解析/会话记忆/流式编排/选项反馈等纯逻辑），前端 vue-tsc 类型检查。

完整字段、约束、错误码见 [`docs/02-API接口文档.md`](./docs/02-API接口文档.md)；卡片结构见 [`docs/03-卡片协议.md`](./docs/03-卡片协议.md)。

## 文档索引（按阅读顺序）

| 编号 | 文档 | 一句话 |
| --- | --- | --- |
| 00 | [产品定位与认知科学设计](./docs/00-产品定位与认知科学设计.md) | 业务是什么、服务谁、认知心理学如何落成产品机制 |
| 01 | [系统架构与 Agent 设计](./docs/01-系统架构与Agent设计.md) | 系统分几层、Agent 怎么编排、有哪些工具、知识底座怎么插拔 |
| 02 | [API 接口文档](./docs/02-API接口文档.md) | **前后端契约真源**：每个接口的请求/响应字段、类型、约束、错误码 |
| 03 | [卡片协议](./docs/03-卡片协议.md) | `CardMessage` 全字段定义、16 种卡片类型与 payload 结构 |
| 04 | [数据库设计](./docs/04-数据库设计.md) | 6 张表的字段、类型、约束与设计要点 |
| 05 | [前端设计与组件规范](./docs/05-前端设计与组件规范.md) | 页面结构、组件分层、状态管理、前端给后端传什么/收到什么字段 |
| 06 | [开发环境与运行指南](./docs/06-开发环境与运行指南.md) | 后端/前端怎么起、联调约定、常见坑、类型契约改动流程 |
| 07 | [上下文工程设计](./docs/07-上下文工程设计.md) | 上下文窗口分层预算、五策略落地与实测数据、提示词真源（Agent.md）与渐进式披露 |

> 历史演进文档（旧的「单 Agent 平台 / 新业务 / 多层流水线」等）已归档于 [`docs/_archive`](./docs/_archive/README.md)，**不再维护**。

## 子仓 README

- [backend/README.md](./backend/README.md) — 后端模块拆分、API 速查、Schema 真源、工具扩展位
