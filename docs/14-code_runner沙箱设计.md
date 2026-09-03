# 14 · code_runner 沙箱设计

> 2026-09-02 定稿。回答三件事：**执行后端怎么选**（process / docker 双后端）、
> **怎么量化评测**（M1~M5 指标 + 探针脚本）、**为什么不搞子智能体**（单 Agent 调沙箱）。
> 可执行形态：`backend/app/agent/scripts/sandbox_eval.py`；守门测试 `tests/test_code_runner.py`。

---

## 1. 定位与威胁模型（先讲清楚防什么）

「工科微项目线」要求学生把代码贴出来、AI 跑一遍给审阅式反馈——`code_runner` 是
**教育场景的单次短任务执行器**，不是通用算力平台。

| 威胁 | 防吗 | 靠什么 |
| --- | --- | --- |
| 学生误跑死循环 / 超重任务 | ✅ | 硬超时 + terminate / 容器 rm -f |
| 学生代码 import 危险模块（subprocess / socket / ctypes…） | ✅ | prologue 按调用方作用域拦截（§4） |
| 学生代码调 os.system / popen / exec* | ✅ | prologue 直接废掉 |
| 蓄意逃逸（对象能力路径：`__subclasses__` 链、C 层旁路） | ⚠️ 尽力 | process 后端拦不全（诚实边界）；docker 后端物理隔离兜底 |
| 文件越界读写 | ❌ process / ✅ docker | process 无文件系统隔离；docker `--read-only` + 只挂产物卷 |
| 资源滥用（内存 / 进程数 / CPU） | ❌ process / ✅ docker | docker `--memory --pids-limit --cpus` 硬配额 |
| 网络外联（绕过 import 拦截的旁路） | ⚠️ process / ✅ docker | docker `--network none` 物理断网 |

**对评委要诚实**：process 后端是演示级沙箱，防粗心不防蓄意；公网多租户必须
`CODE_RUNNER_BACKEND=docker`。这条边界声明本身就是答辩加分项。

---

## 2. 执行后端：双实现、同契约、配置切换

```
run_code(code, lang, timeout)          ← tool 层唯一入口，返回契约恒定
  ├── _prepare()                        入参校验（结构化错误）+ 落盘 _run.py + 产物目录
  ├── backend=process → _run_in_process()   子进程 + prologue 拦截（dev 默认）
  └── backend=docker   → _run_in_docker()   容器硬隔离（公网多租户）
        共同：prologue（按作用域拦 import + 废 os 命令）+ postlude（惰性收图）
```

配置（`app/core/config.py`，写错值启动即失败）：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `CODE_RUNNER_BACKEND` | `process` | `process \| docker`，validator 强校验 |
| `CODE_RUNNER_DOCKER_IMAGE` | `python:3.12-slim` | 生产建议自建含 matplotlib 的镜像（slim 无三方库） |
| `CODE_RUNNER_KEEP_TEMP` | 未设 | 设 `1` 保留 `_run.py` 供排障 |

docker 后端硬化参数（每项对应一类威胁）：`--network none`（断网）·
`--memory 256m --pids-limit 64 --cpus 0.5`（资源配额）· `--read-only`（文件系统）·
`--cap-drop ALL --security-opt no-new-privileges`（提权）· `--user 65534:65534`（非 root）·
`--tmpfs /tmp:rw,noexec,nosuid,size=32m`（临时目录）· 产物目录挂卷是唯一可写点。
超时：客户端 `timeout+5s` 后 `docker rm -f` 强杀——**docker CLI 被杀不会连带容器**，
必须显式清容器，杀净优先于快。

---

## 3. 量化评测：M1~M5

运行 `python -m app.agent.scripts.sandbox_eval [--backend docker] [--json out.json]`。

| 指标 | 定义 | 判据 |
| --- | --- | --- |
| **M1 恶意探针拦截率** | 9 个逃逸探针被判「执行失败」 | 9/9 |
| **M2 良性程序误伤率** | 4 个正常程序跑挂即误伤（缺依赖自动跳过） | 0 |
| **M3 沙箱开销** | 同环境「沙箱跑 − 裸跑」中位差（n=3） | ≤100ms（process） |
| **M4 超时杀灭** | 死循环 timeout=3s，timed_out 标记 + 实际耗时 | 标记为真且按时杀灭 |
| **M5 边界观测** | 越界文件写（产物目录之外） | process 预期泄漏 / docker 预期拦截 |

### 3.1 实测结果（2026-09-02，Windows 11 + Docker Desktop，Python 3.13.14）

| 指标 | process（实测） | docker（实测） |
| --- | --- | --- |
| M1 拦截率 | **9/9** | **9/9** |
| M2 误伤率 | **0/4**（matplotlib 出图 1 张、sympy 正常） | **0/2**（slim 镜像无 matplotlib/sympy，自动跳过） |
| M3 开销 | **78.2 ms**（裸跑 976ms → 沙箱 1054ms） | **355.3 ms**（容器内同配置对比） |
| M4 杀灭 | **3.05s**（timeout=3s） | **8.54s**（含 5s 客户端宽限 + rm -f） |
| M5 越界文件写 | **!! 泄漏**（诚实边界） | **已拦**（只读文件系统） |

结论：process 后端满足教育场景全部功能与性能指标，唯一实质缺口是 M5（+资源配额），
恰好是 docker 后端补齐的两项——**双后端策略成立，切换由部署形态决定**。

### 3.2 评测反哺修复的三个真实缺陷（M1/M2/M3 首跑全红）

1. **importlib 旁路逃逸**（M1 8/9）：`__import__` 拦了但 `importlib.import_module` 没拦 →
   prologue 补 import_module + `_gcd_import` 作用域拦截；
2. **显式 `__import__('socket')` 逃逸**：显式调用可以不带 globals 参数，调用方判定
   拿不到 `__name__` → 修为**缺省拒绝**（globals 未知一律拦，只放行能证明自己是库内部的导入）；
3. **合法库误伤 + ~480ms 平摊开销**（M2 2/4）：全局 import 拦截炸掉 matplotlib（内部
   要 shutil）和 sympy（探测 gmpy 要 ctypes）→ 拦截改**按调用方作用域**（只拦
   `__main__` 学生代码）；postlude 无条件 `import matplotlib` 是开销大头 → 改**惰性**
   （学生没 import pyplot 就整段跳过），开销 480ms → 78ms。

这就是「可量化评测」的价值：**探针跑了才知道拦没拦住，误伤跑了才知道教不了**。

---

## 4. 为什么不用子智能体调沙箱（单 Agent + 容器化沙箱）

评估过「子智能体（subagent）负责跑代码、主智能体只拿结论」的方案，**不采纳**：

1. **子智能体解决的是上下文污染，不是执行安全**。学生代码的威胁是逃逸与资源滥用，
   答案在执行层隔离（容器），与「谁调用沙箱」正交——上了子智能体照样需要 docker，
   两件事不能互相替代。
2. **教育闭环需要完整输出**。审阅式反馈要看学生代码的完整 stdout/stderr/图表；
   子智能体的摘要式回传会丢失教学细节（报错在第几行、图长什么样），反而伤价值主张。
   输出污染问题已有更便宜的解：输出截断（4000 字符）+ `digest_fields` 压缩。
3. **延迟与成本不可接受**。GLM 限流窗口内单次调用排队 60~90s，子智能体每轮多一次
   LLM 调用；沙箱本身只是百毫秒级工具步（M3 实测），为它引入一次 LLM 往返得不偿失。
4. **与 docs/09 §5 的 Subagents 不采纳决定一致**：对话型短任务没有「20 轮脏活」需要
   上下文隔离。重估条件同样适用——若做「后台长周期自动推进微项目」（subagent 跑
   几小时），docker 沙箱就是它的执行底座，两者届时是**叠加**关系而非二选一。

一句话：**执行安全靠容器，不靠分身；上下文干净靠摘要，不靠代理。**

---

## 5. 不采纳清单

| 方案 | 理由 |
| --- | --- |
| 子智能体调沙箱 | 见 §4；延迟/成本/教学信息完整性三输 |
| gVisor / Firecracker | 单人项目运维成本爆炸；docker 已满足本项目威胁模型 |
| WASM (Pyodide) | matplotlib/numpy 生态残缺，工科微项目线核心恰恰是画图 |
| 单独的沙箱微服务 | 引入网络与部署复杂度；单机部署形态下进程内分发 + 容器执行已足够 |

## 6. 红线与后续

- ❌ 多租户生产不得用 process 后端（M5 泄漏 + 无资源配额）——部署清单必查 `CODE_RUNNER_BACKEND=docker`；
- ❌ 不要把 docker 宽限时间调小来「提速杀灭」：rm -f 之前容器还活着，杀净优先；
- ⚠️ prologue 是 best-effort（对象能力路径拦不全），答辩口径：**量化评测 + 诚实边界声明**；
- 🔜 生产镜像：自建 `python:3.12-slim + matplotlib + numpy + sympy`（slim 跑不了画图，M2 跳过项即为镜像缺口清单）；
- 🔜 每次改沙箱逻辑，跑一遍 `sandbox_eval` 对比 M1~M5——防回归和防逃逸同等重要。
