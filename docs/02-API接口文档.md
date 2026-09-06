# 02 · API 接口文档（前后端契约）

> **契约真源**：本文 ↔ `backend/app/schemas/*.py` ↔ `frontend/src/api/types.ts`，三者字段级对齐。**改任何一处必须同步其余两处**；字段命名与可空性都是契约的一部分。
> 卡片结构（`CardMessage`）字段繁多，单独成文：[03-卡片协议](./03-卡片协议.md)。本文引用处仅作概要。

---

## 1. 通用约定

| 项 | 约定 |
| --- | --- |
| Base URL | `/api`（开发环境前端经 Vite proxy 转发到 `http://localhost:8000`） |
| 协议 | REST + JSON；UTF-8；时间一律 ISO 8601（UTC） |
| 鉴权 | JWT Bearer。请求头 `Authorization: Bearer <token>`。token 有效期 24h（`expires_in` 秒数随登录返回） |
| 角色 | 只有 `student`，无角色字段、无教师端 |
| 字段命名 | 前后端统一 `snake_case`（前端不做驼峰转换） |
| OpenAPI | 后端自动生成：`http://localhost:8000/docs` |

### 1.1 统一响应体

所有接口（成功与失败）的 body 都是三段式：

```json
{ "code": 0, "message": "ok", "data": { } }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `int` | `0`=成功；非 0 见 §1.3 |
| `message` | `string` | 成功为 `ok`（注册成功为 `注册成功`）；失败为可读信息 |
| `data` | `any` | 业务数据；失败时为 `{"error": {}}`（校验失败时为字段级错误，见 1.2） |

### 1.2 校验失败（HTTP 422）

Pydantic 校验失败时，`data.error` 为 **字段 → 错误列表** 的映射：

```json
{
  "code": 422,
  "message": "参数校验失败",
  "data": { "error": { "password": ["String should have at least 8 characters"] } }
}
```

### 1.3 错误码与 HTTP 状态的映射（重要）

| 场景 | HTTP 状态 | `code` | 说明 |
| --- | --- | --- | --- |
| 成功 | 200 | 0 | — |
| **业务错误（BizError，4xx 段码）** | **码即 HTTP 状态**（400/401/403/404/409/429） | 同 HTTP 码 | 统一体格式；axios error 分支可直接按 `status` 分流 |
| **业务错误（BizError，1xxx/2xxx 段码）** | **500** | 原业务码（1002/2001/…） | 非 HTTP 语义的业务码由 500 承载，`body.code` 保留业务码可细分 |
| 参数校验失败 | 422 | 422 | 带 `data.error` 字段级信息 |
| 路径不存在等 HTTPException | 404 等 | 同 HTTP 码 | 统一体格式 |
| 未捕获异常 | 500 | 500 | 不泄漏内部细节 |

> 2026-09-06 起：业务错误不再包成 HTTP 200（旧行为导致前端只能靠 `body.code` 识别失败、
> 登录态过期变「僵尸登录态」）。body 仍为 `{code, message, data}` 统一体，但 HTTP 状态
> 码即真实语义——axios 拦截器 error 分支拿到 `err.response.status`，401 清 token 跳登录页。
> 实现见 `backend/app/core/exceptions.py`（`_http_status`）。

业务错误码：

| code | 含义 | 典型触发 |
| --- | --- | --- |
| 400 | 参数/请求错误 | — |
| 401 | 未认证 | 缺少/过期 token、用户名或密码错误、用户不存在或被禁用 |
| 403 | 无权限 | 账号已被禁用（登录时 status≠active） |
| 404 | 资源不存在 | 会话/画像/学习域/项目不存在或不属于当前用户 |
| 409 | 冲突 | 用户名已被占用 |
| 422 | 参数校验失败 | 字段级错误 |
| 429 | 限流（预留） | — |
| 500 | 服务器内部错误 | 未捕获异常 |
| 1001 | 学情/管线业务错误（预留） | — |
| 1002 | AI 模型不可用 | GLM 调用失败 / 模型无有效输出 |
| 2001 | RAG 检索失败（预留） | — |
| 2002 | 审批状态冲突（预留） | — |

---

## 2. 鉴权 `auth`

### 2.1 POST `/api/auth/register`（公开）

注册即登录：成功直接返回 token，前端无需再调 login。注册时自动创建骨架学习画像（`cognitive_state={}`，`profile_confidence=0.0`）。

**请求体 `RegisterIn`**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `username` | `string` | 是 | 3~32 位；正则 `^[a-zA-Z][a-zA-Z0-9_]*$`（字母开头） | 登录名，唯一 |
| `nickname` | `string` | 是 | 1~32 位 | 展示昵称 |
| `password` | `string` | 是 | 8~128 位 | 明文传输（HTTPS），服务端 bcrypt 哈希后丢弃 |

```json
{ "username": "zhangsan", "nickname": "张三", "password": "S3cret_pwd!" }
```

**响应 `data`：`TokenOut`**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `token` | `string` | JWT（payload 含 `sub/iat/exp/iss`） |
| `expires_in` | `int` | 有效期秒数（默认 86400），前端可据此设过期处理 |
| `nickname` | `string` | 昵称 |
| `username` | `string` | 用户名 |

```json
{ "code": 0, "message": "注册成功",
  "data": { "token": "eyJ...", "expires_in": 86400, "nickname": "张三", "username": "zhangsan" } }
```

**错误**：`409 用户名已被占用`；`422` 字段级校验（如 username 格式、password 长度）。

### 2.2 POST `/api/auth/login`（公开）

**请求体 `LoginIn`**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `username` | `string` | 是 | 1~64 位 | — |
| `password` | `string` | 是 | 1~128 位 | — |

**响应 `data`**：`TokenOut`（同 2.1，`message` 为 `ok`）。
**错误**：`401 用户名或密码错误`（统一提示，防账号枚举）；`403 账号已被禁用`。

### 2.3 GET `/api/auth/me`（需登录）

**响应 `data`：`AuthMeOut`**：

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | `string` | 否 | 用户 UUID |
| `username` | `string` | 否 | — |
| `nickname` | `string` | 否 | — |
| `avatar_url` | `string` | 是 | 头像 URL（当前未上传功能） |
| `status` | `string` | 否 | `active` / `banned` |
| `created_at` | `string`(datetime) | 否 | ISO 8601 |

**错误**：`401`（token 缺失/过期/用户不存在）。

---

## 3. 学习画像与学习域 `learning`

> 学习域（domain）/ 学习项目（project）是「个人伴学」里组织学习内容的轻量实体，替代传统的课程/班级。

### 3.1 GET `/api/me/profile`（需登录）

**响应 `data`：`LearnerProfileOut`**：

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | `string` | 否 | 画像 UUID |
| `user_id` | `string` | 否 | 所属用户 |
| `learner_type` | `string` | 是 | 学习者类型（预留，如 大学） |
| `goal_type` | `string` | 是 | 目标类型（预留，如 学分/技能/兴趣） |
| `learning_style` | `string` | 是 | 学习风格自述（预留） |
| `cognitive_state` | `object` | 是 | 认知状态 JSON（掌握度/负荷/校准等，结构由 Agent 决定，当前为空对象起步） |
| `profile_confidence` | `number` | 否 | 画像置信度 0~1，初始 0.0 |
| `updated_at` | `string`(datetime) | 否 | — |

**错误**：`404 学习画像不存在`（正常注册流程不会出现）。

### 3.2 PATCH `/api/me/profile`（需登录）

部分更新，只传要改的字段。

**请求体 `LearnerProfileUpdate`**（全部可选）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `learner_type` | `string \| null` | 见 3.1 |
| `goal_type` | `string \| null` | 见 3.1 |
| `learning_style` | `string \| null` | 见 3.1 |
| `cognitive_state` | `object \| null` | 整体覆盖式更新 |

**响应 `data`**：更新后的 `LearnerProfileOut`。

### 3.3 POST `/api/domains`（需登录）— 创建学习域

**请求体 `DomainIn`**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `name` | `string` | 是 | 1~128 位 | 域名称，如「操作系统」 |
| `subject` | `string` | 否 | ≤64 位 | 学科，如「计算机」 |
| `meta` | `object` | 否 | — | 附加信息 |

**响应 `data`：`DomainOut`**：`{ id, name, subject(可空), meta(可空), created_at }`。

### 3.4 GET `/api/domains`（需登录）— 学习域列表

**查询参数**：`keyword`（string，可选，按名称模糊过滤）。
**响应 `data`**：`DomainOut[]`（数组，非分页）。

### 3.5 GET `/api/domains/{domain_id}`（需登录）

**响应 `data`**：`DomainOut`。**错误**：`404 学习域不存在`。

### 3.6 POST `/api/projects`（需登录）— 创建学习项目

**请求体 `ProjectIn`**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `domain_id` | `string \| null` | 否 | — | 关联学习域 |
| `goal` | `string \| null` | 否 | ≤512 位 | 项目目标（如「两周吃透进程调度」） |
| `start_state` | `object \| null` | 否 | — | 起点状态快照（自评基础等） |
| `status` | `string` | 否 | 枚举 `active / paused / done`，默认 `active` | 项目状态 |

**响应 `data`：`ProjectOut`**：

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | `string` | 否 | 项目 UUID |
| `user_id` | `string` | 否 | 所属用户（服务端写入） |
| `domain_id` | `string` | 是 | — |
| `goal` | `string` | 是 | — |
| `start_state` | `object` | 是 | — |
| `status` | `string` | 否 | — |
| `created_at` | `string`(datetime) | 否 | — |

### 3.7 GET `/api/projects` / GET `/api/projects/{project_id}`（需登录）

列表返回 `ProjectOut[]`（仅当前用户的）；详情按 id 查询。
**错误**：`404 学习项目不存在`（含不属于当前用户的情况）。

---

## 4. AI 对话 `student`（核心）

### 4.1 POST `/api/student/chat`（需登录）

发一条消息，Agent 思考（可调工具）后返回一张主卡片。会话不存在 `conversation_id` 时自动新建。

**请求体 `ChatIn`**：

| 字段 | 类型 | 必填 | 约束/默认 | 说明 |
| --- | --- | --- | --- | --- |
| `message` | `string` | 是 | 1~4096 位 | 用户主文本 |
| `images` | `ImagePart[]` | 否 | ≤4 个，默认 `[]` | 题目/截图等多模态输入 |
| `course_id` | `string` | 否 | ≤64 位，默认 `"general"` | 课程/学科标识（如 `os`/`autocontrol`/`signals`），作为 Agent 软上下文 |
| `conversation_id` | `string \| null` | 否 | 默认 `null` | 已有会话 id；null 则新建会话（标题取消息前 16 字） |

`ImagePart`：

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `url` | `string` | 是 | — | 公网 URL 或 `data:image/...;base64,...`（前端目前用 base64 直传） |
| `detail` | `string` | 否 | `"auto"` | 枚举 `auto / low / high`，图像识别精度 |

```json
{
  "message": "这道积分怎么算？",
  "images": [{ "url": "data:image/png;base64,iVBOR...", "detail": "auto" }],
  "course_id": "os",
  "conversation_id": "7c9e…-…"
}
```

**处理行为**（前端需要知道的后端语义）：

1. 用户消息先落库（含图片 meta），再取该会话**最近 8 条**历史注入模型；
2. Agent 主循环最多 4 轮工具调用（`kg_lookup` / `code_runner` / 场景 skill `calculus` / `ode` / `project_guide` 等），全程留痕；
3. 新会话若标题仍是「新对话」，会更新为消息前 16 字。

**响应 `data`：`ChatOut`**：

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `message` | `CardMessage` | 否 | **主卡片**（本次回复的唯一入口，字段见 [03-卡片协议](./03-卡片协议.md)） |
| `extras` | `CardMessage[]` | 否（`[]`） | 后续卡片（多卡输出：练习/反问/推荐/工程脚手架等；后端 `format_cards()` 已切多卡，前端 `learn.ts` 已 push 上屏） |
| `thinking` | `string \| null` | 是 | GLM 思维链全文（thinking 模式） |
| `tool_calls` | `ToolCallRecord[]` | 否（`[]`） | 本次调用的工具及结果摘要（与 `message.tool_calls` 同源） |
| `conversation_id` | `string` | 否 | 会话 id（新建时前端要更新本地状态） |
| `title` | `string \| null` | 是 | 会话标题（刷新列表用） |
| `updated_context` | `object \| null` | 是 | 学习上下文回推 `{course, path, mastery, review_due, cognitive}`，卡片落库后重算 `cognitive_state` 折算生成（与 §4.1c `GET /student/context` 同形状）；重算失败为 null，前端保留旧 context。**`mastery` 语义是「过程性探索深度估计值」，不是考试分数**（docs/00 §6 红线） |

主卡片由后端 `format_cards()`（GLM JSON mode 二次切卡）产出，输入含回答原文、本轮工具摘要与会话状态摘要（`summary/scratchpad`——切卡模型看不到对话历史，跨轮 warning 信号由此带入），失败时回退启发式单卡：含「？」→`question`；含步骤词/多行→`understand`；否则 `text`；`payload.meta.thinking_chars` 记录思维链长度；`next_action="ask"`。结构化 `math`/`engineering` 卡片协议见 03。

```json
{
  "code": 0, "message": "ok",
  "data": {
    "message": {
      "id": "b0a1…", "role": "assistant", "card_type": "understand",
      "text": "我们先确认题意：求 ∫ x/√(x²−1) dx…",
      "payload": { "meta": { "thinking_chars": 486 } },
      "thinking": "用户问积分…先换元…",
      "tool_calls": [
        { "tool_name": "calculator", "args": {"expression": "sqrt(2)**2 + 1"},
          "result": "{\"ok\": true, \"value\": 3.0}", "ok": true }
      ],
      "next_action": "ask", "created_at": "2026-08-28T09:00:00Z"
    },
    "extras": [],
    "thinking": "用户问积分…先换元…",
    "tool_calls": [ /* 同上 */ ],
    "conversation_id": "7c9e…-…",
    "title": "这道积分怎么算？",
    "updated_context": {
      "course": {"id": "signals", "name": "信号与系统", "subject": "信号与系统", "goal": ""},
      "path": [
        {"node": "傅里叶变换", "status": "deepening", "mastery": 1.0, "reason": "聊过 5 次"},
        {"node": "采样定理", "status": "exploring", "mastery": 0.45, "reason": "聊过 1 次"}
      ],
      "mastery": {"傅里叶变换": 1.0, "采样定理": 0.45},
      "review_due": [
        {"kc": "采样定理", "due": "2026-08-29T09:00:00+00:00", "course": "signals",
         "reason": "已经 9 天没碰了，安排一次回顾吧", "overdue_days": 7}
      ],
      "cognitive": {"load": "medium"}
    }
  }
}
```

**错误**：`404 会话不存在或无权访问`（传了别人的 conversation_id）；`1002 AI 模型调用失败 / AI 没有返回可用内容`。

### 4.1a POST `/api/student/chat/stream`（需登录）— 流式对话（SSE）

`4.1` 的流式版本：请求体同为 `ChatIn`，响应为 `text/event-stream`。落库、切卡、轮后任务与同步版完全一致；`delta` 阶段是纯文本渐进渲染，`done` 时前端用切好的结构化卡片整体替换流式占位（EventSource 带不了 Authorization 头，前端用 fetch + ReadableStream 解析，见 `learn.ts streamChat`）。

**事件序列**（每帧 `event: <类型>\ndata: <JSON>\n\n`）：

| 事件 | data 载荷 | 说明 |
| --- | --- | --- |
| `meta` | `{conversation_id, title}` | 最先发出；前端据此绑定会话 |
| `reasoning` | `{delta}` | 思考链增量（GLM thinking 模式） |
| `tool` | `{tool_name, args}` | 某工具**开始执行**（可显示轨迹） |
| `delta` | `{text}` | 回答正文增量 |
| `done` | `{data: ChatOut}` | 完整回包（同 4.1 的 `data`，主卡+extras 已切好已落库） |
| `error` | `{code, message}` | 失败（错误卡已落库，刷新不丢上下文） |

响应头：`Cache-Control: no-cache`、`X-Accel-Buffering: no`。

### 4.1b POST `/api/student/practice/submit`（需登录）— 练习/选择卡作答闭环

学生对 `practice` / `choice` 卡提交作答。闭环三步（全部留在 `messages` 表，无新表）：
1. **作答留痕**：写入原卡 `payload.meta.attempts`（`[{answer, at}]`）并置 `meta.answered=true`；
2. **作答入流**：学生作答以一条 user 消息落库（`payload.meta.practice_ref={card_id, card_type}`），会话记忆/Scratchpad/后续对话都能看见；
3. **审阅反馈**：LLM 生成 `feedback` 卡（先肯定→指出问题并说明原因→抛引申疑问，**不打分**），落库并随响应返回；同时回推 `cognitive_state.practice`（`{submissions, last_submit_at}` 过程性证据）。

**请求体 `PracticeSubmitIn`**：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `conversation_id` | `string` | 是 | 所属会话 |
| `card_id` | `string` | 是 | 练习/选择卡的消息 ID（即 `CardMessage.id`，与库内主键一致） |
| `answer` | `string` | 是 | 1~2000 位；practice 卡为自由作答，choice 卡为所选项 `value`（后端按卡上 `options` 翻译成 label 再给反馈） |

**响应 `data`：`PracticeSubmitOut`**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `conversation_id` | `string` | 会话 id |
| `card` | `CardMessage` | 更新后的练习/选择卡（`payload.meta.attempts` 已留痕，前端原地替换） |
| `feedback` | `CardMessage` | assistant 审阅反馈卡（`card_type="feedback"`，前端 push 上屏） |

**错误**：`400 该卡片不是可作答的练习/选择卡 / 作答内容不能为空`；`404 会话不存在或无权访问 / 卡片不存在`。

### 4.1c GET `/api/student/context`（需登录）— 学习上下文（路径 / 探索深度 / 复习到期）

与 `ChatOut.updated_context` 同一份数据形状（后端 `build_learning_context(cognitive_state)` 折算）。用途：

- 进入学习主界面时拉一次（顶栏进度/复习到期、侧栏「我的路径」「该回顾了」的首次数据源）；
- 练习提交等不带回推的入口之后，前端可再调它刷新。

内部会顺带重算 `cognitive_state`（无消息记录时返回空上下文，不报错）。复习闭环交互约定：侧栏「该回顾了」列表项由前端一键发起**回忆式复习会话**——新建会话并以学生口吻发出种子消息（「先别直接讲结论，抛引导问题让我自己回忆」），AI 按 Agent.md 的检索练习规则引导，不判分。

**响应 `data`**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `course` | `{id, name, subject, goal}` | 当前课程（name 由 KG 注册表解析，未注册退回 course_id） |
| `path` | `{node, status, mastery, reason}[]` | 按 mentions 排序的前 10 个主题；`status ∈ exploring / deepening / to_review`；`mastery` = 探索深度估计值 `min(1, 0.3 + mentions×0.15)` |
| `mastery` | `Record<string, number>` | 同 path 的深度估计值（顶栏「进度」= 各主题均值） |
| `review_due` | `{kc, due, course, reason, overdue_days}[]` | 到期主题（SM-2-lite 间隔复习队列，按超期天数降序，最多 8 条）；前端点击即发起复习会话 |
| `cognitive` | `{load?}` | 认知负荷粗估 `low / medium / high`（追问密度），无证据时为 `{}` |

### 4.2 GET `/api/student/conversations`（需登录）

当前用户会话列表（按更新时间倒序）。

**响应 `data`：`ConversationOut[]`**：

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | `string` | 否 | 会话 UUID |
| `course_id` | `string \| null` | 是 | 建会话时的课程标识 |
| `title` | `string` | 否 | 默认「新对话」，首条消息后自动更新 |
| `created_at` | `string`(datetime) | 是 | — |
| `updated_at` | `string`(datetime) | 是 | — |

### 4.3 POST `/api/student/conversations`（需登录）

**请求体 `NewConvIn`**（均可选）：`course_id`（默认 `"general"`）、`title`（默认「新对话」）。
**响应 `data`**：`ConversationOut`（同 4.2 单项）。

### 4.4 GET `/api/student/conversations/{conv_id}/messages`（需登录）

**响应 `data`：`ConversationDetail`** = `ConversationOut` 全部字段 + `messages: CardMessage[]`（按时间正序，用户卡片与助手卡片交错）。

### 4.5 DELETE `/api/student/conversations/{conv_id}`（需登录）

级联删除会话与消息。**响应 `data`**：`{ "deleted": "<conv_id>" }`。**错误**：`404 会话不存在`。

### 4.6 GET `/api/student/tools`（需登录）

透出当前启用的工具 Schema 列表（OpenAI function-calling 形态），供前端调试/演示「AI 能用什么工具」。

```json
{ "code": 0, "message": "ok",
  "data": [ { "type": "function", "function": { "name": "kg_lookup", "parameters": { … } } } ] }
```

### 4.7 GET `/api/student/memory/graph`（需登录）

当前用户的**长期记忆图谱**全量视图（docs/07：聊天后由 GLM 异步抽取实体/关系写入 MCP server-memory，聊天时按 L0 常驻 + L1 检索分层注入）。给前端「我的记忆图谱」可视化用。

**响应 `data`**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `available` | `boolean` | `false` = MCP 记忆服务不可用（缺 node / server-memory），前端展示降级空态 |
| `entities` | `MemoryEntity[]` | `{ name, entityType: "概念\|误区\|偏好\|目标\|项目", observations: string[] }`；概念实体的 observations 可能含「别名：X」条目（L1 检索用，展示时应剔除） |
| `relations` | `MemoryRelation[]` | `{ from, to, relationType }`（如 `傅里叶变换 —前置依赖→ 傅里叶级数`） |
| `stats` | `object` | `{ entity_count, relation_count }` |

### 4.8 POST `/api/student/memory/rebuild`（需登录）

清空当前用户记忆图谱后，从全部对话历史**重放重建**（按 ~8 条一批逐批调 GLM 抽取；消息多时耗时几十秒，同步返回）。重建完成后顺带刷新 `learner_profiles.cognitive_state`。

**响应 `data`**：

```json
{ "ok": true, "chunks": 5, "messages_scanned": 40,
  "entities_created": 12, "observations_added": 8, "relations_created": 6 }
```

失败时 `ok: false` + `error`（如 MCP 记忆服务未启用）。**错误**：不抛 5xx，业务失败在 `data.ok=false` 里表达。

---

## 5. 健康检查与地基验证 `health`

| 接口 | 说明 |
| --- | --- |
| GET `/api/health` | `{ "status": "up" }` |
| POST `/api/health/demo-ok` | 正常统一响应示例（body: `{name, age}`） |
| GET `/api/health/demo-biz-error` | 业务异常示例：HTTP 409 + 统一体（`body.code=409`） |
| GET `/api/health/demo-500` | 兜底 500 统一体 |
| GET `/api/health/demo-http-404` | HTTPException 404 统一体 |

## 6. 前端对接约定（当前实现）

- **axios 封装**（`src/api/http.ts`）：`baseURL='/api'`，超时 10s；请求拦截器自动附 `Authorization`；响应拦截器解包统一体，HTTP 401（含 body.code=401 防御分支）时清 token 并跳 `/login`；业务错误为真实 4xx/5xx 状态码，走 error 分支抛统一错误体，调用方在 `catch` 里拿 `message`。
- **登录态**：token 存 `localStorage('token')`；`nickname/username` 来自 login/register 响应。
- **路由守卫**：未登录访问任何页 → `/login`；已登录访问 `/login`、`/register` → `/learn`。
- **mock 开关**：`src/api/config.ts` 的 `USE_MOCK_AUTH` / `USE_MOCK_LEARN`（当前均 `false`，走真实后端）。

---

*卡片字段全表：[03-卡片协议](./03-卡片协议.md) · 数据落库：[04-数据库设计](./04-数据库设计.md)。*
