# 01 · 系统架构与 Agent 设计

> 上承 [00-产品定位与认知科学设计](./00-产品定位与认知科学设计.md)。本文回答：**系统分几层、一次对话怎么流转、Agent 怎么编排、有哪些工具、知识底座怎么插拔。**

---

## 1. 总体架构

```
┌────────────────────────────────────────────────────────────────┐
│ 前端 frontend/  Vue 3 + TS + Pinia + Element Plus + Vite        │
│   Login / Register / LearnHome（对话主界面 + 卡片渲染）            │
└───────────────┬────────────────────────────────────────────────┘
                │ REST /api（JSON，JWT Bearer；Vite proxy → :8000）
┌───────────────▼────────────────────────────────────────────────┐
│ 后端 backend/app  FastAPI（统一响应 + 全局异常 + Pydantic 校验）  │
│                                                                │
│  api/     auth（注册/登录/me）  learning（画像/学习域/项目）        │
│           student（chat / 会话管理 / tools）  health              │
│  agent/   orchestrator（编排） system（提示词）                   │
│           glm_client（GLM 5.3 Flash） tools（工具注册表）          │
│  kg/      可插拔知识图谱（os / autocontrol / signals）            │
│  repositories/  models/  schemas/  core/                         │
└───────┬──────────────────────────────┬─────────────────────────┘
        │ SQLAlchemy 2                  │ zai-sdk（OpenAI 兼容形态）
┌───────▼──────────┐   ┌───────────────▼─────────────────────────┐
│ PostgreSQL 16    │   │ GLM 5.3 Flash（thinking 启用 + 工具调用） │
│ 6 张业务表        │   └─────────────────────────────────────────┘
└──────────────────┘
```

**设计立场：单 Agent 自主决策，不搞手工流水线。** 早期版本的「意图识别 → KG → 认知诊断 → 策略选择 → 卡片」五层规则路由已废弃（代码归档于 `backend/app/_archive/pipeline_v1/`），由 GLM 在 system prompt 约束下自主决定何时反问、何时调工具、何时直接讲。规则少了，行为由「提示词 + 工具 + 卡片协议」三方约束保证。

## 2. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 大模型 | **GLM 5.3 Flash**（智谱，zai-sdk） | 启用 `thinking={"type":"enabled"}`；消息形态兼容 OpenAI，便于换模型 |
| 后端 | FastAPI + SQLAlchemy 2 + psycopg2，Python ≥ 3.13 | 统一响应体 / 全局异常 / Pydantic 校验三件套先行 |
| 数据库 | **PostgreSQL 16**（强约束，拒绝 SQLite） | docker compose 起，本地端口 5433 |
| 前端 | Vue 3 + TS + Pinia + Element Plus + Vite | axios 封装统一解包；卡片按 `card_type` 分发渲染 |

## 3. 一次对话的完整流转

```
前端 ChatPanel
  → POST /api/student/chat  {message, images[], course_id, conversation_id?}
  → api/student.py
      1. 取/建会话（conversation_id 为空则新建，标题 = 消息前 16 字）
      2. 用户消息落库（含图片 meta）
      3. 取该会话最近 8 条消息作为 history
      4. new AgentOrchestrator(extra_system="当前课程：{course_id}").run(...)
  → orchestrator 主循环
      messages = [system, *history, user(文本+图片)]
      loop（最多 4 轮）:
          resp = GLM(messages, tools)          # thinking 自动累积
          if 无 tool_calls: 取 content 为最终文本，跳出
          else: 执行每个工具 → 结果 JSON 序列化(≤4000字符) 以 role="tool" 回填 → 继续
  → 结果装成主卡片 CardMessage（启发式选 text/question/understand）
      带上 thinking、tool_calls 留痕 → 落库 → 更新会话 updated_at
  → 返回 ChatOut（message / extras / thinking / tool_calls / conversation_id / title / updated_context）
  → 前端 CardRenderer 按 card_type 渲染；learn store 用 updated_context 刷新侧栏与顶栏进度
```

要点：

- **会话记忆（Write 策略）**：信息搬到窗口外持久化，窗口里只放「摘要 + 最近窗口」——
  每次请求注入最近 8 条原始历史（`_history_to_messages`）；更早的对话由
  `session_memory` 在轮后用 LLM 压缩成滚动摘要（`conversations.summary`，未压缩满
  12 条不触发，压缩后保留最近 8 条对齐窗口）。跨请求的状态都在数据库里，服务无状态。
- **任务状态（Write 策略·Scratchpad）**：每轮后从最新 assistant 卡启发式提取
  「在学什么/进行到哪/等学生做什么」存 `conversations.scratchpad`（零 LLM 成本，
  卡片协议本身就是结构化的），随 extra_system 注入，Agent 断点续传不重复已讲内容。
  实现见 `app/services/session_memory.py`。
- **前缀缓存（Cache 策略）**：智谱隐式缓存按**前缀**命中（命中部分约半价，
  `usage.prompt_tokens_details.cached_tokens` 上报）。据此定死两条结构规则：
  ① system 块两段全部静态（`build_system_messages`：SYSTEM_PROMPT + 一条静态说明——
  实测单条 SYSTEM_PROMPT ~1.7K token 恰好卡在最小可缓存长度之下，垫一条才能稳定命中）；
  ② 每轮动态上下文（课程/意图路由/记忆/任务状态）一律由 orchestrator 作为**尾部 user
  消息**注入（`dynamic_context` 参数），绝不拼进 system。生产链路实测：动态上下文整段
  变化、模型照常调工具，第二轮仍命中 3072/7251 token（42%）。`AgentResult.cached_tokens`
  累计命中量，日志输出命中率，可观测可答辩。
- **工具结果压缩（Compress 策略①②，`app/agent/compress.py`）**：多轮工具调用时，旧轮的
  tool 消息（大块 JSON 推导/stdout/图谱节点）在下一轮 tool_calls 到达、新结果未追加前，
  就地替换为一行结论摘要（`[已压缩] ok；answer=π；steps×6`）——assistant 的 thinking
  和 tool_calls（Thought + Action）完整保留，当前轮结果原样参加下一次调用（观察遮蔽）。
  只改本轮内存 messages；`AgentResult.tool_calls` 全量留痕（落库/前端 ToolTrace）不受影响，
  并累计 `compressed_chars` 供观测（实测夹具 92% 压缩率）。
- **长期记忆分层注入（Select + Write 配合）**：MCP 知识图谱沉淀的学生记忆按两层注入 system prompt——
  **L0 常驻层**（偏好/目标/误区，「人格级」信息，每轮注入，约 100~200 token）+
  **L1 检索层**（当前用户消息命中的已学概念 + 一步邻接关系，按需注入，无命中则不注入；
  匹配走「实体名 + 别名」双通道混合召回——抽取时让 GLM 记下英文缩写/口语别名存为
  「别名：X」observation，解决「学生说 FFT、图谱记的是傅里叶变换」的同义鸿沟）。
  图谱随对话增长，全量注入会膨胀成噪音；分层后记忆注入稳定在上下文窗口的 5~10%。
  实现见 `memory_service.get_persistent_digest / get_relevant_digest`，全量视图 `get_digest` 只留给 API/调试。
- **工具轮数上限**：`MAX_TOOL_STEPS = 4`，防止死循环；超限强制收敛取已有内容。
- **失败语义**：GLM 调用失败抛 `BizError(1002)`；工具失败**不抛异常**，返回 `{ok:false, error}` 让模型自己换策略。
- **流式**：当前为同步整体回包；`glm_client.iter_stream_chunks` 已具备 content/reasoning/tool_calls 三类增量解析能力，SSE 为后续扩展（见 §8）。

## 4. System Prompt：认知科学规则的可执行化

`app/agent/system.py` 是产品行为约束的核心，把 [00 §3/§4](./00-产品定位与认知科学设计.md) 的认知科学规则写成模型可执行的指令：

| 规则 | 对应原理 | 落地 |
| --- | --- | --- |
| 先问再答（问题模糊时先 1~2 个反问定位卡点） | 生成效应、元认知 | `question` 卡 |
| 分步、每步 ≤100 字，按「理解题意→列思路→计算/操作→验证」 | 认知负荷理论 | `math` / `engineering` 卡的 steps |
| 概念解释用类比/反例，不甩定义原文 | 图式理论 | `understand` 卡 + `strategy` 标签 |
| 定理给「物理含义 + 适用条件 + 具体例子」，不超条件硬套 | 可验证性 | `understand` 卡 |
| 工程实操步骤化，不允许跳过前置 | 脚手架 | `engineering` 卡 |
| 纠错先肯定再指出，最后让学生自己修 | 形成性反馈、成长型思维 | `feedback` 卡 |
| **抛出引申疑问**：讲完抛一个「你有没有想过…」，学生接就陪他深挖，**不接绝不追着考** | 问题提出效应（生成效应的高阶形态）| `question` 卡 + 正文结尾 |
| **工科要落到产出**：提议 20~60 分钟可完成的小项目（2~3 个难度选项**由学生挑**），学生贴代码/截图后给**审阅式反馈**（先肯定 → 指出可改处+说明原因 → 不打分）| 胜任感（SDT）、项目式学习、脚手架渐隐 | `engineering` 卡 |
| 工具调用前自问「是否真的让学生更清楚」 | 降低噪音 | 工具使用段落 |
| 未知就说未知，不编造 | 诚实边界 | 全局 |
| **不扮演考官**：绝不主动出题考学生、不判分、不用「对错」衡量学习效果 | **自主性（SDT）**——做成测验会摧毁内在动机 | 全局红线（见 00 §6）|

提示词同时声明了输出卡片形态（text/understand/math/engineering/question/practice/feedback/warning/metacog），不确定类型时兜底 `text`。每轮对话会追加动态上下文 `当前课程：{course_id}` + 长期记忆分层注入（L0 常驻 + L1 按当前问题检索，见 §3 要点）。

## 5. 工具层（MCP 接入位）

`app/agent/tools.py` 用 `ToolSpec` 注册表管理工具：**一个工具 = JSON Schema 定义 + 同名执行函数 + settings 开关**。Schema 用 OpenAI function-calling 形态，未来迁到 MCP server 只需换执行端点。

**意图路由（Select 策略·工具动态装配，`app/agent/intent.py`）**：每轮不再全量塞 6~8 个工具 schema，而是先判意图（启发式强特征快车道 + 极小 GLM 分类兜底），按 `INTENT_TOOLS` 白名单只装配该场景的 3~5 个候选，并在 extra_system 里声明「本轮可用工具」——候选面小了，工具选择准确率更高；分类失败/纯图片消息降级为全量，路由永不阻塞主链路。

**与 LangChain/LangGraph 中间件的对应关系**（本项目为纯 zai-sdk 自编排，取其设计、不引其依赖）：

| LangChain/LangGraph 中间件 | 本项目对应实现 | 状态 |
| --- | --- | --- |
| `SummarizationMiddleware`（token 超阈值自动摘要旧历史） | `session_memory._maybe_compress` 滚动压缩摘要（Write 策略承载） | 已实现 |
| `LLMToolSelectorMiddleware`（LLM 选相关工具再进主模型） | `intent.py` 意图路由 + `INTENT_TOOLS` 白名单装配（启发式快车道，比逐轮 LLM 选择更省） | 已实现 |
| `ClearToolUsesMiddleware`（清掉旧工具结果） | `compress.py::compress_old_tool_results` 工具结果一行摘要 | 已实现 |
| `AnthropicPromptCachingMiddleware`（提示缓存） | 静态双 system 消息 + 动态上下文尾部注入（`orchestrator.dynamic_context`）+ `cached_tokens` 命中率观测 | 已实现（Cache 策略） |

| 工具 | 职能 | 输入 | 输出 | 状态 |
| --- | --- | --- | --- | --- |
| `kg_lookup` | 在课程知识图谱里按关键词匹配节点，返回难度/前置/摘要，用于定位学生卡点与前置缺失 | `course_id: str, query: str` | `{ok, matched:{id,name,difficulty,prerequisites,summary}, course}` | 始终启用 |
| `memory_search` | 查**这位学生**的长期记忆图谱（学过的概念/暴露的误区/偏好目标）；系统注入的记忆只覆盖当前问题相关部分，此工具是主动补充 | `query: str` | `{ok, query, entities:[{name,entityType,observations}], relations:[str]}` | 已实现，`enable_mcp_memory=true` |
| `code_runner` | 受控沙箱执行学生 Python 代码（工科微项目线核心）：禁网络、禁系统命令、硬超时，捕获 stdout/stderr/退出码，matplotlib 图自动导出 URL | `code: str, lang='python'` | `{ok, stdout, stderr, returncode, timed_out, figures:[{name,url}]}` | 已实现，`enable_code_runner=true`（默认开） |
| `web_search` | 联网检索（本地知识不够新时核实）：三级后端可插拔——Tavily（配 `tavily_api_key`）→ Bing 中国区抓取（零依赖，国内网络最稳）→ ddgs 多引擎；结果在聚合层统一裁剪有界，全部不可用时返回结构化错误 + hint，GLM 自动降级用本地知识并声明确定度 | `query, top_k=5` | `{ok, provider, query, results:[{title,url,snippet}]}` / `{ok:false, error, hint}` | 已实现（`app/agent/web_search.py`），`enable_web_search=false`（默认关，开启即用） |
| `calculator` | 受限数学表达式求值（AST 白名单）——仅当 sympy 不可用时注册兜底 | `expression: str` | `{ok, expression, value}` / `{ok:false, error}` | 兜底（正常环境不出现） |

**场景 skill（`app/skills/`，回答「怎么教」）**：`calculus`（微积分）/ `ode`（常微分方程）/ `project_guide`（工科微项目提议）。执行成功后自动把该场景的 `SKILL.md` 注入返回值 `teaching_hints`——模型一次调用同时拿到「结果」和「怎么教」。新增 skill 只需建目录 + 在 `registry.py` 的 `_SKILL_MODULES` 加模块名。

工具结果统一 `{ok, ...}` 结构化返回并**全部留痕**在 `CardMessage.tool_calls`（前端 ToolTrace 可展示「AI 查了什么」）。

## 6. 可插拔知识底座（kg/）

系统不内置学科知识，课程 = 一张知识图谱。接口定义在 `app/kg/base.py`：

```python
@dataclass
class KGNode:
    id: str
    name: str
    difficulty: int = 3          # 难度 1~5，对应 ZPD 定位
    prerequisites: list[str]     # 前置节点 id（讲新概念前先补前置）
    abstract: bool = True        # 是否抽象（影响策略倾向）
    summary: str = ""            # 概念摘要（作为对话证据）
    refs: list[str] = []         # 出处锚点（"CSAPP §9.3" 等，docs/11 §5）

class KeywordMatchMixin:
    """match_node 默认实现（base.py）：关键词表按「命中数→最长词」打分，
    节点名/摘要分段兜底；新课程继承它即可，不再手写匹配。"""

class KnowledgeGraph(Protocol):
    course_id: str; course_name: str; subject: str
    def nodes(self) -> dict[str, KGNode]: ...
    def strategy_weights(self) -> dict[str, float]: ...   # 四策略权重
    def match_node(self, text: str) -> str | None: ...    # 关键词匹配（mixin 默认实现）
```

- **已注册课程**（`app/kg/__init__.py::COURSES`）：

| course_id | 课程 | 学科 | 策略权重倾向 |
| --- | --- | --- | --- |
| `os` | 操作系统 | 计算机 | 类比 1.0 / 可视化 1.0 / 分解 0.8 / 反例 0.6（抽象概念多） |
| `autocontrol` | 自动控制原理 | 自动化 | 类比 + 可视化 高（传递函数/根轨迹抽象） |
| `signals` | 信号与系统 | 电子信息 | 分解 + 可视化 高（卷积/频域结构复杂） |

- **新增一门课** = 写一个 Graph 类（继承 `KeywordMatchMixin`，填节点 + 权重 + 关键词表）+ 在 `COURSES` 注册一行，Agent 框架、工具、前端全部复用——这是「可迁移性」的落点。
- 当前 KG 作为**静态资料库**供 `kg_lookup` 检索，不参与调度决策；对话上下文中的 `course_id` 由前端随请求传入（默认 `general`）。
- **`kg_lookup` 返回契约**（2026-09-02 查缺补漏后）：命中节点摘要 + **前置节点详解**（`prerequisite_details`：id/name/difficulty/summary，支持「先补前置再讲新概念」，不再返回裸 id）+ `strategy_weights`（学科策略权重经工具结果回注 GLM，模型自行参考——这是 pipeline_v1 归档后该配置的唯一消费路径）+ `refs`（教材出处锚点，docs/11 §5）。
- **守门测试** `tests/test_kg.py`：前置 id 必须存在 / 无环 / 难度 1~5 且不倒挂（ZPD 语义）/ 四策略权重齐全 / 匹配语义冒烟（最长命中优先 + 名/摘要兜底）/ `kg_lookup` 返回形状。手写图谱的数据错误在 CI 拦下，不流进学生对话。

### 6.1 知识架构定位：双层供给，GraphRAG 刻意不采纳

系统的知识供给是**双层**的，各司其职、互不替代：

| 层 | 内容 | 供给方式 | 消费方 |
| --- | --- | --- | --- |
| **静态课程 KG**（curated knowledge） | 人工策展的节点：难度 / 前置 / 摘要 / 四策略权重 | 代码内置，`kg_lookup` 关键词检索 | 定位学生卡点与前置缺失 |
| **学生记忆图谱**（蒸馏记忆） | 对话中 GLM 异步抽取的实体 / 关系三元组 | MCP server-memory（每用户一个子进程，本地 JSONL 隔离） | `memory_search` 工具 + system prompt L0/L1 分层注入（docs/07） |

**GraphRAG 评估后明确不采纳**（2026-09-02 决定，写进 docs/09 §5 不采纳清单）：

1. **规模不匹配**：GraphRAG 的收益场景是「大规模文档语料的语义检索」——LLM 批量抽实体建索引、查询时跨社区多跳聚合。本项目没有这个前提：知识侧是百级节点的**人工策展**静态库（本就不需要 LLM 抽取），记忆侧是逐对话蒸馏的三元组（**写入即成图**，无需离线索引）。
2. **负收益**：在上述规模上，GraphRAG 只会带来 token 成本（全语料 LLM 抽取）与索引维护复杂度，换不来任何检索质量提升——和「此规模上向量库负收益」（docs/07 §memory、docs/11 §5）是同一个判断。
3. **答辩口径**：定位是「curated knowledge + 蒸馏记忆的双层知识供给」，不是「我们没做 GraphRAG」；若被问到，答案是**成熟架构判断**（成本收益 + 数据规模不匹配），并已留重估条件。

**未来何时重估**：引入「学生上传整本教材 / 整套讲义」的大语料检索需求（docs/12 RAG 线），且实测出现 chunk-RAG 解决不了的跨文档多跳关联问题，再评估 GraphRAG 或更轻的 rerank 方案。

## 7. 配置与安全

`app/core/config.py`（pydantic-settings，读 `backend/.env`）关键项：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 必填 | 仅接受 PostgreSQL 协议，否则启动失败 |
| `JWT_SECRET` | 必填 | ≥32 字节，HS256 |
| `GLM_API_KEY` / `GLM_BASE_URL` / `GLM_MODEL` | 必填 / 官方 / `glm-5.3-flash` | 模型接入 |
| `GLM_ENABLE_THINKING` | `true` | 思维链开关 |
| `ENABLE_CALCULATOR` / `ENABLE_WEB_SEARCH` / `ENABLE_CODE_RUNNER` | true / false / false | 工具开关 |

安全要点：密码 bcrypt 哈希；登录失败统一「用户名或密码错误」防枚举；JWT payload `{sub, iat, exp, iss}`，过期 24h；无 email、无 role 字段（全员 student）。

## 8. 已预留的扩展点（当前未实现，别当现状）

| 扩展 | 现状 | 预留位 |
| --- | --- | --- |
| SSE 流式对话 | **已实现**：`POST /api/student/chat/stream` 推 `reasoning/tool/delta` 增量，`done` 下发切好卡片的完整 ChatOut（见 02 §4.1a）；前端 fetch + ReadableStream，支持中途停止 | 流式期间的结构化卡片渐进升级（当前 done 一次性替换占位卡） |
| 学习上下文回推（探索档案回传）| **已实现**：聊天落库后同步重算 `cognitive_state` 折算进 `ChatOut.updated_context`；另有 `GET /api/student/context`（进主界面拉取 / 练习提交后刷新），前端顶栏进度、侧栏路径与复习到期均已接真实数据 | `cognitive` 块目前只带认知负荷粗估，掌握度数值化（mastery 写入点）、元认知校准仍是空位 |
| 间隔复习闭环 | **已实现**：`profile_service._schedule_review`（SM-2-lite：首触 2 天、重提间隔翻倍封顶 60 天）→ `review_queue` → `GET /student/context` 的 `review_due` → 侧栏「该回顾了」一键发起回忆式复习会话（种子消息要求 AI 先提问让学生回忆，不打分） | 复习会话本身尚无独立证据标记（与普通对话同链路）；`ease` 因子细化 |
| RAG 课程知识库 | 无 | `settings.milvus_uri`；错误码 `RAG_ERROR=2001` |
| 联网搜索 | **已实现**（`app/agent/web_search.py`，Tavily → Bing → ddgs 三级后端，失败结构化降级）| `settings.enable_web_search`（默认关）/ `tavily_api_key` / `web_search_backend` |
| 代码执行工具 | **已实现**（`app/agent/code_runner.py`，禁网络/禁系统命令/硬超时，`project_guide` 微项目模板可直接跑）| `settings.enable_code_runner`（默认开）/ `code_runner_timeout` |
| **探索档案**（提过的好问题 / 做过的作品 / 还想深入的点）| `cognitive_state` 恒为 `{}`，全后端无 UPDATE；`GET /api/student/exploration` 已从 messages 聚合 | `learner_profiles.cognitive_state` JSON。**可先从 messages 表聚合**，不必建新表 |
| 微项目作品集 | 无 | 若需学生提交作品，可新增 `StudentWork` 表 |

---

*契约细节：[02-API接口文档](./02-API接口文档.md) · [03-卡片协议](./03-卡片协议.md)。*
