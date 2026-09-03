# 12 · 用户知识库与 RAG 检索设计

> 本文回答：**学生自带语料（讲义/课件/作业）的「个人知识库」怎么建；参考项目 research-assistant-agent 里哪些资产可以直接迁移、哪些教训必须内化为设计约束；以及用什么指标和评测体系证明检索质量。**
> 本文是 [11-联网检索与事实核验设计](./11-联网检索与事实核验设计.md) §7.3（`user_docs_search` 设计占位）的展开。
> 参考项目：`research-assistant-agent`（NestJS 11 + MongoDB + Milvus + 通义 embedding/rerank 的科研 RAG 平台，本地路径 `C:\Users\97689\Desktop\research-assistant-agent`，仓库外，仅供参照）。

---

## 1. 目标与定位

学生上传**自己的**讲义、课件、作业 PDF → 系统解析、切片、向量化入库 → Agent 在对话中按需检索（`user_docs_search` 工具）→ 引用落到卡片来源角标（复用 docs/11 §3.4 的 evidence 通路）。

这是通用联网搜索替代不了的语料：老师的符号约定、课程划的范围、作业出题风格——不在任何模型权重里，也搜不到。且学生自有资料无版权问题。解锁场景：「对着我的讲义带我复习」「用咱们课上的记号做这道题」。

**边界**：单用户语料规模 = 一门课的资料，几十~几百个 chunk。这个规模**不需要** Milvus（与 docs/07「此规模上向量库是负收益」的判断一致，且我们比它多一个数量级余量）。

## 2. 参考项目哪些做法值得抄（总览）

| 参考项目实现 | 判断 | 迁移方式 |
| --- | --- | --- |
| 摄入管线：解析 → RecursiveCharacterTextSplitter(800/100) → 分批 embedding | ✅ 直接抄参数与结构 | 换 Python 栈（pypdf + python-docx + 自实现递归切片） |
| RRF 混合检索（向量 0.7 + 关键词 0.3，k=60） | ✅ 纯函数，逐行可移植 | 关键词路从 Milvus LIKE 换 PostgreSQL ILIKE |
| `preciseSearch`：rerank 阈值过滤 + lowConfidence 兜底 | ✅ 抄模式，缓实现真 rerank | 先用 RRF 归一化近似分，rerank 接口留位 |
| 规则版置信度四维打分（零 LLM、确定性） | ✅ 抄思路 | 喂卡片 `confidence` 与低置信提示，先透出不拦截 |
| **评测体系 L0~L3 + 程序化指标 + CI 门禁** | ✅✅ **最大迁移资产** | 见 §4，几乎整体平移 |
| **五个实测教训**（降级掩盖故障/魔法数字未校准/兜底吃掉 fail 路径…） | ✅✅ 内化为设计约束 | 见 §5，逐条对应预防措施 |
| Milvus collection-per-user、MongoDB、Redis、NestJS | ❌ 不迁 | 栈差异，见 §8 |
| clarify/fact/perspective 用户可见意图路由 + pending 状态机 | ❌ 不迁 | wuban 是单 Agent + Select 工具装配，KB 检索就是一个工具，不需要用户可见的路由分类 |

## 3. 可直接迁移的资产 → wuban 落法

### 3.1 摄入管线

参考实现（`fileanagement.service.ts`）：上传 → 临时文件 → PDFLoader/DocxLoader → 合并全文 → `RecursiveCharacterTextSplitter(chunkSize=800, chunkOverlap=100)` → 分批 embedding（BATCH_SIZE 批处理）→ 向量入库（带 docId/docTitle 元数据）。

**wuban 落法**：pypdf + python-docx 解析；自实现递归切片（按 `\n` → 句号 → 硬切，800/100 参数照抄）；embedding 走**智谱 embedding-3**（已在 GLM 生态，维度取 1024，config 新增 `embedding_api_key`/`embedding_model`；本地 bge-small-zh 作为离线降级选项，可选依赖）。上传走图片上传同款模式（先落盘拿 url → 解析入库存文本，不存原始 Buffer）。embedding 分批 + **查询向量内存缓存**（他们为评测提速做的，直接抄）。

### 3.2 混合检索 + RRF（纯函数，逐行可移植）

他们的 `hybridSearch`：向量/关键词两路并发 → `score = Σ weight/(k+rank)`（0.7/0.3，k=60）→ 按块去重融合排序；关键词路失败自动降级纯向量。**RRF 是纯函数，与存储无关**，直接移植。

wuban 的两路：向量路 = 查询向量 × `document_chunks` 全量余弦（单人几百 chunk，Python 暴力算毫秒级）；关键词路 = PostgreSQL `ILIKE` OR 拼接 + **规则关键词抽取**（他们的 `extractKeywordsByRule` 可移植；LLM 关键词提取做成可选增强，默认关——每查询省一次 LLM 调用，规则版先跑，评测说话）。

### 3.3 精排与阈值模式（模式先抄，真 rerank 缓接）

他们的 `preciseSearch`：宽候选集 → rerank 打分（未配置/失败降级 RRF 归一化近似分）→ 阈值 0.75 过滤 → 不足 1 条取 Top1 并标 `lowConfidence`。**降级近似 + 阈值 + 兜底的三段式结构**先照抄；真实 rerank（DashScope qwen3-rerank 或本地 bge-reranker）作为可选后端留接口——他们自己的扫参结论是「当前规模 rerank 阈值影响有限、topK 才是决定性杠杆」，我们不必第一天就上 rerank。

### 3.4 证据与引用（复用 docs/11 既有通路）

chunk 天然带 `doc_id/doc_title/chunk_index`，`user_docs_search` 返回结构与 `search_verify` 对齐（`results[]` 带 title/source/score）→ `_web_evidence` 同款合并路径把「讲义名 · 页码/节」写进卡片 `evidence[]`，前端 EvidenceChips 零改动。教学侧含义：AI 说「你讲义第 3 节是这么定义的」时，学生点角标能回到原文——**这是联网搜索给不了的 grounding**。

### 3.5 规则版置信度信号（先透出，后拦截）

他们的 `ConfidenceAgent`：零依赖、纯规则、四维分数（来源可靠性/内容相关性/引用完整性/跨源一致性）+ pass/warn/fail。wuban 版：规则打分函数输入检索结果（分数、来源数、是否低置信兜底），输出写进卡片 `confidence` 与可选的「依据不足」提示。**红线（来自他们的教训 §5-2/5-3）：校准前绝不拦截回答，只透出分数**——他们默认阈值在真实数据上 100% 误杀，我们是教育产品，误杀一个学生的正确提问比放过一次幻觉伤害更大。

## 4. 最大的迁移资产：评测体系（L0~L3）

他们的 `RAG_EVALUATION_DESIGN.md` 的 v2 方法论几乎可以整体平移，核心四原则先立此存照：

1. **先有评测集和埋点，否则一切指标无从谈起**；
2. **能用代码判定的绝不用模型**——LLM-as-Judge 不可复现、有位置/长度偏差、成本高，只做每周抽样人工复核，不进任何门禁；
3. **所有魔法数字（topK/阈值/权重）必须经评测集验证后才允许改**；
4. **置信度/精排模块既是信号源，也要被评测**。

四层结构 → wuban 版：

| 层 | 参考项目 | wuban 落法 |
| --- | --- | --- |
| **L0 业务 runner** | NestJS testing 注入 Service 跑批 | `backend/eval/` 目录：pytest 直调 `doc_service.search()`，不走 HTTP |
| **L1 评测集** | JSONL，chunk 级 `relevant_chunks` 标注 + `has_basis` + 20 条库外题 | 同格式 `backend/eval/datasets/kb-retrieval-<日期>.jsonl`；文件名带日期，结果绑定版本 |
| **L2 指标纯函数** | Recall@k / Precision@k / MRR / nDCG / HitRate | `backend/eval/metrics.py`：输入 `(ranked_ids, relevant_ids)` 输出分数，与检索实现完全解耦；pytest 单测锁行为 |
| **L3 运行时信号** | ConfidenceAgent 分数 + preciseSearch 统计直接进 trace | 检索分数/命中数/低置信标志写 `agent_telemetry`（§6.5），线上离线同一套信号 |

**造集技巧直接抄**：从已入库 chunk 随机抽 50~100 个，LLM 反向生成问题、人工校对相关性（比正向标注快 5 倍）；库外问题（`relevant_chunks=[]`）专测「该拒答时拒答」；每意图 ≥30 条。

**CI 门禁**：30 条核心冒烟集只看确定性指标（Recall@5 / MRR / 引用正确率 / 拒答正确率），低于基线（如 Recall@5 降 >3%）即失败——防止改提示词/换模型/调参数时悄悄劣化。这与我们已有的 pytest 体系同栈（pytest + 纯函数），不需要他们那样从零建 Jest 基建。

## 5. 参考项目的五个实测教训 → 设计约束（重点）

他们在真实数据上跑出过五个代价不小的发现。**每一条都直接变成我们的约束**：

| # | 他们的教训 | 实测证据 | wuban 设计约束 |
| --- | --- | --- | --- |
| 1 | **降级兜底会掩盖故障**：rerank 响应解析漏一个分支，静默降级 RRF 近似分，长期无人察觉（指标完全相同才暴露） | rerank 形同虚设两周 | 每个可降级组件（rerank/关键词路/本地 embedding）必须有**连通性 probe + 生效占比指标**；评测报告显式标记「真实生效还是降级」 |
| 2 | **魔法数字必须校准**：置信度默认 pass 阈值 0.8 在真实 rerank 分数分布下 **pass 误杀率 100%**（21/21 库内题全 warn），扫描后定 0.65 | 100 条集校准记录 | 所有阈值/权重进 config 并带 `configHash` 指纹；上线前评测集扫参，校准前后各留指标快照；**校准前只透出分数不拦截** |
| 3 | **兜底逻辑会吃掉 fail 路径**：lowConfidence 兜底保证结果 ≥1 条后，置信度 fail 分支永不触发，库外题全部误放行（fail 召回率 0%） | 5/5 库外题误放行 | 兜底发生时**显式传递信号**（`low_confidence=true` 随结果走），消费方按「无依据」处理而不是按「有 1 条结果」处理 |
| 4 | **触发词表是要养的**：路由准确率 0.692 被误判为「标注问题」，扩集+逐条复核后确认 15 条是触发词表缺词，补词后 0.812 | 17 条冲突人工复核 | wuban 的意图启发式词表（`intent.py` 的 math/engineering 标记）也进评测集守护；「路由错了还是标错了」必须逐条复核才能下结论 |
| 5 | **参数结论受语料规模约束，扫参出真知**：topK 3→2 使 Precision@k +52% 且 Recall 不掉，是最大优化杠杆；而 RRF 权重在 2 篇文档库上完全无区分度 | 100 条 × 参数网格 | topK/阈值类参数只在评测集上定；结论必须注明「受限于当前语料规模，扩库后重扫」 |

## 6. wuban 落地方案

### 6.1 数据模型（PostgreSQL，两张表）

```sql
CREATE TABLE user_documents (
    id          VARCHAR(36) PRIMARY KEY,
    user_id     VARCHAR(36) NOT NULL INDEX,
    filename    VARCHAR(255) NOT NULL,
    file_type   VARCHAR(16) NOT NULL,          -- pdf / docx / md / txt
    file_size   INTEGER NOT NULL,
    full_text   TEXT,                           -- 解析全文（供预览/重建索引）
    status      VARCHAR(16) NOT NULL DEFAULT 'processing',  -- processing/ready/failed
    error       TEXT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE document_chunks (
    id          VARCHAR(36) PRIMARY KEY,
    user_id     VARCHAR(36) NOT NULL INDEX,    -- 按用户隔离（对照参考项目 collection-per-user）
    doc_id      VARCHAR(36) NOT NULL INDEX,
    doc_title   VARCHAR(255) NOT NULL,
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,                  -- ≤~900 字符
    embedding   JSONB NOT NULL,                 -- float[]；留 pgvector 迁移钩子
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

单人几百 chunk，余弦用 Python 暴力算即可；若未来语料上量，`embedding` 列原样迁 pgvector（SQL 侧改 `ORDER BY embedding <=> :q`），检索接口不变。

### 6.2 摄入管线

`POST /student/documents`（复用图片上传的鉴权/限宽模式）→ 解析（pypdf/python-docx/md/txt 直读）→ 递归切片 800/100 → 分批 embedding（智谱 embedding-3，dim=1024，批间退避）→ 写 `document_chunks`；`user_documents.status` 状态机 processing → ready/failed，失败落 `error` 可重试。删除文档 = 级联删 chunks（参考项目的 `deleteFile` 同样做了 MongoDB + Milvus 的级联清理，照做即可）。

### 6.3 检索工具（ToolSpec 声明草案）

```python
ToolSpec(
    name="user_docs_search",
    description="检索该学生自己上传的讲义/课件/作业。学生提到「我的讲义/课件/"
                "老师说的/课上讲的」或需要贴合其课程材料时调用；通用概念与"
                "版本事实用 kg_lookup / web_search。",
    scenes=("concept", "engineering", "chat"),   # "*" 亦可——有资料就应可用
    digest_fields=("results",),
    guide="guide_user_docs.md",
)
```

实现 = §3.2 的 RRF 两路 + `preciseSearch` 三段式（近似分 → 阈值 → lowConfidence 兜底并**显式带出信号**）；返回结构与 `search_verify` 对齐，检索命中自动走 evidence 落卡（§3.4）。无资料/未就绪 → `ok=true, results=[], hint="该生还没有上传资料"`（对照他们「集合不存在不代表出错」的处理）。

### 6.4 前端

「我的资料」入口（右栏学习状态抽屉加一个 section 或独立页）：上传（≤20MB，pdf/docx/md/txt）、列表（状态/可重试/删除）。卡片来源角标沿用 EvidenceChips，`source` 存讲义文件名。

### 6.5 遥测

`agent_telemetry` 追加 `kb_calls` / `kb_hits` / `kb_low_confidence` 三列（与 `search_*` 分开，避免污染 M2/M4 口径）；结构化字段进现有单行聚合，不另建 trace 表——参考项目的 JSONL+Mongo 双通道是为没有遥测表的项目准备的，我们有 `agent_telemetry` 底座，按 docs/09「failure log 一等公民」沿用即可。

## 7. 指标与实验（衔接 docs/11 的 M/E 体系）

| # | 指标 | 口径 | 目标 |
| --- | --- | --- | --- |
| M8 | **检索 Recall@3 / @5** | 评测集跑批，L2 纯函数 | P1 期 Recall@5 ≥0.85（对照他们 100 条集基线 0.81） |
| M9 | **MRR / nDCG@5** | 同上 | 记录基线；rerank 接入后 MRR 应优于纯 RRF |
| M10 | **引用正确率** | 程序化：卡片 evidence 的 doc_title 与命中 chunk 归一化匹配 | ≥95%（程序化，零 LLM） |
| M11 | **拒答正确率** | 库外题触发「依据不足」提示的比例 | ≥90%（教训 §5-3 的直接验收） |
| M12 | **low_confidence 兜底率** | 线上 `kb_low_confidence` 占比 | 观测值；突增 = 语料漂移或检索劣化 |

**实验 E5（检索基线）**：50~100 条评测集（LLM 反向造集 + 人工校对 + 20 条库外），跑 RRF 混合 vs 纯向量 A/B（他们第一轮就靠这个发现 rerank 没生效）；**E6（参数联合扫参）**：topK {2,3,5} × 阈值 {0.6,0.75,0.85} × RRF 权重 {0.5,0.7,0.9}，双目标（Precision@3 与通过率）选参，产出带 configHash 的推荐配置。

## 8. 不迁移清单

| 不迁 | 理由 |
| --- | --- |
| Milvus / MongoDB / Redis | 栈重量与规模不匹配（§1 边界）；PostgreSQL 双表 + JSONB 向量够用，留 pgvector 钩子 |
| NestJS / DeepSeek / DashScope 多供应商 | wuban 已有 GLM 客户端与限流退避；embedding/检索配置收在 config 一处 |
| LLM 关键词提取（默认关） | 每查询多一次 LLM 调用；规则版先上，评测证明不够再开 |
| 用户可见的 clarify/fact/perspective 路由 | wuban 的 Select 工具装配已按意图裁剪工具面，KB 检索是 Agent 手里的工具而非用户-facing 分类；pending 确认状态机的职责已在 system prompt「先问再答」里 |
| 真实 rerank（第一天就上） | 他们自己的扫参结论：当前规模阈值影响有限、topK 才是杠杆；接口留位，E6 数据说话 |

## 9. 实施路线

| 阶段 | 内容 | 量级 |
| --- | --- | --- |
| **P0 摄入+检索 MVP** | 两张表 + 上传解析切片入库 + `user_docs_search`（先纯向量一路）+ evidence 落卡 + 「我的资料」前端 + pytest 冒烟 | 2~3 天 |
| **P1 混合检索 + 评测闭环** | RRF 关键词路（规则关键词 + ILIKE）+ `backend/eval/`（L1 造集 / L2 指标纯函数 / L0 runner）+ E5 A/B + E6 扫参 | 2~3 天 |
| **P2 信号与门禁** | 规则置信度四维打分（只透出）+ low_confidence 显式信号 + M10/M11 程序化校验 + 30 条 CI 冒烟门禁 + `kb_*` 遥测 | 1~2 天 |
| **P3 增强与运维** | 可选 rerank 后端（带生效 probe）+ LLM 关键词提取开关 + 每周回归定时任务 + 👎 坏例池归因飞轮 | 2 天 |

**验收口径**：P1 结束时应能复现参考项目第一轮实测的全部指标表（Recall@k/MRR/nDCG + A/B 对比），且每个可降级组件都有 §5-1 要求的 probe——这两样齐了，后面的迭代才有“没有悄悄变坏”的底线。
