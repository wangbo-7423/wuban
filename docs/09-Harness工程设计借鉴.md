# 09 · Harness 工程设计借鉴

> 本文回答：Anthropic 系「Harness Engineering（驾驭工程）」方法论里，哪些已被本项目隐性落地、哪些本轮正式迁移、哪些评估后**刻意不采纳**。
> 课程笔记原文在 `Harness/docs/`（八篇），核心框架：**一个公式 · 八大故障 · 八大机制 · 三大支柱**。
> 代码真源：`backend/app/agent/orchestrator.py`（编排）、`compress.py`（压缩）、`code_runner.py`（沙箱）、`models/telemetry.py`（遥测）。

---

## 1. 核心公式与本项目的定位

**Agent = Model + Harness**（Vivek Trivedy 原创；Anthropic 系博文传播）

| 组件 | 课程语境 | 本项目对应 |
| --- | --- | --- |
| Model | LLM 本体（推理/知识/指令遵循），"发动机" | GLM 5.3 Flash（zai-sdk，thinking + 工具调用） |
| Harness | 包裹模型的工程系统：循环控制、工具编排、上下文管理、验证闭环、权限约束，"底盘/方向盘/安全带/仪表盘" | `app/agent/` 全家：orchestrator + compress + intent + tools + code_runner + telemetry |

推论一致：**模型再强，没有 harness 也跑不远**。同一个 GLM，换一套编排质量，表现天差地别。

---

## 2. 八大故障 × 本项目对照矩阵

> 逐列扫一遍：每种故障至少要有一个"主解"级机制覆盖（Harness 矩阵思维）。

| # | 故障 | 课程机制 | 本项目落地 | 状态 |
| --- | --- | --- | --- | --- |
| ① | 循环失控 | Agent Loop + max_iterations 硬刹车 | `orchestrator.MAX_TOOL_STEPS = 4` | ✅ |
| ② | Context 溢出 | 压缩（治标）+ 拆细（治本）+ 隔离（防污染）三路齐上 | 工具结果摘要压缩（compress.py）+ 意图路由按场景装配工具 + 历史 8 条滑窗 + 会话滚动摘要（session_memory） | ✅（对话场景够用，见 §5） |
| ③ | Cache Miss | system 前缀稳定命中缓存 | 静态 system 块 + 动态上下文一律走尾部 user 消息 + `cached_tokens` 命中率日志 | ✅ |
| ④ | Tool 错误吞 | 结构化 schema 回传 `{status, error}` | `execute_tool` 统一 `{ok, error}`；摘要压缩时 error 永远保留 | ✅ |
| ⑤ | 状态丢失 | progress 文件 + git commit 双 checkpoint | 会话/消息/画像/Scratchpad 全落 PostgreSQL；对话型场景无"断点续跑"需求 | ✅（场景适配） |
| ⑥ | 缺权限闸 | Permission Gate | `code_runner` 沙箱三禁（禁网络/禁系统命令/硬超时）；多租户生产需容器隔离（已知，未做） | ✅ dev |
| ⑦ | 缺自动化评审 | Generator-Evaluator 对抗评审 | 未做——审阅反馈目前单 agent 自评。教育版方案见 §5 | ⏸ 未采纳（本轮） |
| ⑧ | 成本失控 | Token Budget | 压缩 + 缓存 + 意图路由间接控本；**本轮新增遥测落库补上观测侧**；per-user 预算闸未做 | 🟡 部分 |

---

## 3. 概念映射：课程机制的教育版同构

这几组对应是**答辩时的理论背书**——产品设计暗合 Anthropic 官方一手文献（见 §6）：

| Harness 机制 | 本项目对应物 | 同构点 |
| --- | --- | --- |
| **Verification Loop**（"声称完成"≠"真的完成"，真机验证） | 「预测 → 验证」探究范式：学生先猜，AI 用 `sympy_calc` / `code_runner` **真算真跑**，结果对比猜想 | 都拒绝"自说自话式通过"，靠外部真实反馈闭环 |
| **Progress Tracking**（claude-progress.txt 跨 session 续传） | 探索档案：好问题 / 未展开线索 / 主题深度，跨对话沉淀并注入下一轮 | 都是把过程状态持久化到窗口外，重启不丢 |
| **Feature List**（单次只做一件事，源头切细 context） | 意图路由 Select 策略：按学生诉求只装配本轮工具子集 + 场景指南 | 都是"别一次贪心做太多"，从源头控制窗口膨胀 |
| **Context Management**（超阈值压缩，保前缀稳定） | compress.py 工具结果摘要（≈ LangChain ClearToolUsesMiddleware）+ 静态 system 块 | 同一套「量的清理 + 前缀稳定」组合拳 |
| **Tool Use**（MCP schema 契约） | ToolSpec/SkillSpec 声明式注册，MCP 接入位预留 | 同一套"工具失败必须结构化可见"契约 |

### 3.1 三大支柱对照：看见什么 × 能做什么 × 能跑多久

课程把八个机制收拢到三根**正交的工程维度**上（对照 Claude Code 各给三例）。本项目三根柱全有可见实现——逐柱对齐：

| 支柱 | 问题 | Claude Code 三例 | 本项目三例 |
| --- | --- | --- | --- |
| **Context Engineering**（输入端） | Agent 能"看见"什么？ | CLAUDE.md · session compaction · progress 文件 | `Agent.md` + `prompts/guide_*.md` 场景指南渐进式披露（≈ 项目级 RAG system prompt）· 会话滚动摘要 + 8 条滑窗（≈ session compaction）· 探索档案 + Scratchpad + 记忆图谱跨对话注入（≈ progress 文件） |
| **Architectural Constraints**（控制流） | Agent 能"做"什么？ | 27 种 hook · Permission Gate · Task tool/subagent | 工具声明式注册白名单 + 意图路由按场景装配子集（≈ hook：工具调用前就限定了能调什么）· `code_runner` 沙箱三禁 + config 开关（≈ Permission Gate）· Subagents 刻意不采纳（§5，对话场景无隔离需求） |
| **Garbage Collection**（状态流） | Agent 能"跑"多久？ | token budget · session compaction · compaction→reset | `agent_telemetry` 观测底座 + 压缩控本（硬预算闸未做，§5）· compress.py 丢原始返回留一行结论 · 8 条滑窗即最朴素的 reset（ Anthropic 2025→2026 的 compaction→reset 路线，本项目天然在"重"的一侧） |

两点说明：

1. **三柱不是机制的新分类，而是机制的另一个投影**——§3 表里的 Context Management / Progress Tracking 挂输入端柱，Agent Loop / Tool Use / Verification Loop 挂控制流柱，压缩/拆细/隔离同时挂状态流柱。两张表合用，就是「故障轴 × 机制 × 支柱」完整的定位坐标系。
2. **缺口也在图上**：控制流柱的"27 种 hook"级细粒度约束本项目没有（也没必要——工具总数 ≤8，白名单即够）；状态流柱的 token budget 硬闸是多租户生产项。查缺口用柱扫，和 §2 用故障扫，结论互相印证。

---

## 4. 本轮正式迁移：Agent 遥测（failure log 当一等公民）

### 4.1 动机

> "The dataset of failures from your current harness is more valuable than the harness itself."
> —— Phil Schmid（Bitter Lesson 三原则之一）：**harness 可以扔，数据不能扔**。

改动前，`AgentResult` 里的 cached_tokens / compressed_chars / 工具成败只打日志就丢了。落库后一张表吃三个用途：

1. **迭代侧**：换模型 / 重构编排后，这批数据是判断新 harness 是否退步的基准（课程警示一：同类任务成功率连续降 5pp 即重写信号）；
2. **运维侧**：缓存命中率、压缩节省、工具失败率——对应企业级架构的"新可观测性体系"；
3. **教育侧**：学生卡壳证据链的原始数据——哪轮工具失败、AI 引导几步收敛。

### 4.2 设计

- **表**：`agent_telemetry`（`models/telemetry.py`），一行 = 一轮 Agent 调用的聚合画像；
- **接入点**：`api/student.py` 四处——同步成功 / 同步 BizError / 流式成功 / 流式两种异常。失败也留痕（错误码 + 耗时），随错误卡同一事务 commit；
- **编排侧**：`AgentResult` 新增 `tool_steps`（工具循环轮数），run / run_stream 各自累加；
- **服务层**：`services/telemetry_service.record_agent_turn()`。

### 4.3 三条硬约束（test_telemetry.py 守门）

1. **绝不抛异常**：遥测挂了只打 warning，聊天主链路零感知；
2. **随调用方事务**：只 `db.add()` 不 commit，语义跟随端点既有 commit/rollback；
3. **单行聚合**：不存逐 token 明细，查询友好；`tool_fail_names` 去重截断。

### 4.4 开关

`settings.enable_agent_telemetry`（默认 True）；压测/多租户可关。

---

## 5. 评估后刻意不采纳（及未来接入位）

> 课程自己的警示三：驾驭工程仍在演化，「保持独立判断」。以下机制针对 **long-running 编码 agent** 设计，本项目是**对话型短任务教育 agent**，照搬即过度工程。

| 机制 | 不采纳理由 | 未来何时启用 |
| --- | --- | --- |
| **Subagents**（子代理 context 隔离） | 对话场景没有"20 轮脏活"需要隔离；主对话本身就是短链条 | 若做「长周期学习项目自动推进」（后台 agent 跑几小时）再考虑 |
| **Feature List 完整体**（JSON 任务清单机制） | 已有意图路由 Select 策略这个轻量等价物 | 单轮需要 5+ 个子任务串行时（目前 MAX_TOOL_STEPS=4 内未出现瓶颈） |
| **Generator-Evaluator**（对抗评审） | 审阅反馈翻倍调用，GLM 限流下延迟/成本不可接受 | 教育版方案已想清：**终审拆两步**——第一次只看学生代码 + 运行结果独立评审（不看 AI 自己的引导过程），第二次以学长口吻组织反馈。只在微项目终审等高价值场景启用 |
| **Token Budget 硬闸** | 单机 dev 无预算压力 | 多租户生产：per-user 每日 token 限额 + 遥测表做计量底座 |
| **history Compaction/Reset** | 已有 8 条滑窗 + 会话滚动摘要 + 记忆图谱三层兜底 | 单会话突破几十轮、滑窗丢关键上下文成为实测问题时 |
| **GraphRAG**（LLM 抽实体建图索引 + 多跳聚合检索；知识架构决策，非 Harness 机制，一并记录于此） | 无大规模文档语料：KG 是百级节点的人工策展静态库，记忆是逐对话蒸馏三元组（写入即成图），GraphRAG 在此规模负收益——token 抽取成本 + 索引维护换不来检索质量增益。完整论述见 docs/01 §6.1 | 引入整本教材级大语料检索（docs/12 RAG 线），且 chunk-RAG 实测跨文档多跳失效时 |

---

## 6. 课程文献索引（答辩引用）

| 文献 | 日期 | 支撑点 |
| --- | --- | --- |
| Building agents with the Claude Agent SDK（Anthropic） | 2025-09-29 | Agent Loop 四相循环、Tool Use、Subagents |
| Effective Harnesses for Long-Running Agents（Justin Young, Anthropic） | 2025-11-26 | Progress Tracking、Feature List、Verification Loop |
| Harness Design for Long-Running Application Development（Prithvi Rajasekaran, Anthropic） | 2026-03-24 | Generator-Evaluator 三角色对抗 |
| MCP（Model Context Protocol, Anthropic） | 2024-11-25 | 工具 schema 契约（"Agent 世界的 USB-C"） |
| Phil Schmid, Bitter Lesson 三原则 | 2026-01-05 | Build to Delete：失败数据 > harness 本身 |

---

## 7. 一句话总结

本项目的 harness 不是照课程搭的，而是先长出来、再用课程的坐标系**验伤**——八大故障里六个已解、一个部分（⑧，本轮补上观测侧）、一个评估后明确不做（⑦，接入位已留）。这套「矩阵查缺口」的方法本身，就是 Harness 课程最值得迁移的工程理性。
