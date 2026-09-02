"""project_guide：工科微项目线的「任务型」skill（横切，不是某一门学科）。

## 为什么它是 skill 而不是工具

calculus / ode 这类 skill 回答「怎么算」；本项目还有一类需求 docs/00 §8 强调的：
**把工程知识点落成 20~60 分钟能做完、有可展示产出的小项目**。这类需求跨信号/
控制/计科/电路多门课，形态是「提议项目 → 给脚手架 → 给审阅式反馈」，不是单步计算。
它同样有教学价值（脚手架策略、常见坑、完成标准），所以做成 skill，执行成功后把
`SKILL.md` 注入 `teaching_hints`，让 GLM 拿到「怎么带学生做项目」的策略。

## 它做什么

`solve(topic)` 从一个**纯 Python、零外部依赖**的微项目目录里挑出匹配 topic 的
2~3 个难度递增选项（目标 / 脚手架步骤 / 常见坑 / 完成标准）。GLM 调一次就拿到
「可以提议给学生哪些小项目、分别怎么搭脚手架」，无需每次现编。

所有示例项目都刻意只用标准库，保证 `code_runner` 开箱即跑（不依赖 numpy/matplotlib）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.skills.base import SkillSpec

_MODULE_DIR = Path(__file__).resolve().parent


# ── 微项目目录（纯 Python，零外部依赖；对应 docs/00 §8 四例）──────
_PROJECTS: dict[str, dict[str, Any]] = {
    "傅里叶": {
        "aliases": ["傅里叶", "fourier", "级数", "频谱", "信号"],
        "blurb": "用 Python 亲手把周期信号拆成正弦叠加，看见「频域」长什么样。",
        "options": [
            {
                "title": "方波的傅里叶级数可视化",
                "minutes": 25,
                "goal": "给定周期方波，手算前 N 项傅里叶系数并叠加，画出逼近效果。",
                "scaffolding": [
                    "写出方波在一个周期的解析式（如取值 ±1）",
                    "推导 a0 / an / bn 的积分公式（方波 bn 非零、an=0）",
                    "用循环累加前 N 项三角级数，逐 N 比较逼近",
                    "打印不同 N 下的系数，观察吉布斯现象（边缘过冲）",
                ],
                "pitfalls": [
                    "忘记 n 为偶数时 bn=0，白算一半",
                    "把周期 T 和角频率 ω 搞混（ω=2π/T）",
                    "叠加时相位写错，图形平移",
                ],
                "done_when": [
                    "能正确打印前若干项系数",
                    "能说出『为什么方波只含奇次谐波』",
                ],
            },
            {
                "title": "自己实现 DFT 并和理论频谱对拍",
                "minutes": 50,
                "goal": "不调 numpy.fft，按定义实现离散傅里叶变换，对一个合成信号求频谱。",
                "scaffolding": [
                    "写 X[k] = Σ x[n] e^{-j2πkn/N} 的直译实现",
                    "构造一个含两个频率分量的信号",
                    "打印幅度谱，指出峰值对应的频率",
                    "（进阶）和 math 卡算出的理论频率对比",
                ],
                "pitfalls": [
                    "e^{-j...} 用复数，忘记 Python 的 1j",
                    "N 选太小导致频谱泄漏看不清",
                    "把索引 k 直接当频率（要除以 N·Δt）",
                ],
                "done_when": [
                    "DFT 输出幅度谱峰值位置正确",
                    "能解释『频率分辨率 = 1/(N·Δt)』",
                ],
            },
        ],
    },
    "PID": {
        "aliases": ["pid", "控制", "控制器", "反馈", "仿真"],
        "blurb": "写一个一阶被控对象的离散仿真，调 P / I / D 三参数看阶跃响应变化。",
        "options": [
            {
                "title": "一阶系统 PID 阶跃响应仿真",
                "minutes": 30,
                "goal": "对 G(s)=1/(Ts+1) 做离散仿真，实现 PID 控制，打印 Kp 不同时的超调/稳态误差。",
                "scaffolding": [
                    "把连续系统离散成 x_{k+1}=x_k+(−x_k+u_k)·dt/T",
                    "实现误差 e、积分项累加、微分项 (e−e_prev)/dt",
                    "输出 u = Kp·e + Ki·∫e + Kd·de/dt，并做输出限幅",
                    "扫几组 Kp，打印稳态误差与超调",
                ],
                "pitfalls": [
                    "积分项不累加 / 不抗饱和 → 稳态误差消不掉或震荡",
                    "微分项直接用噪声大的原始信号 → 抖",
                    "dt 过大导致离散仿真不稳",
                ],
                "done_when": [
                    "能看到『只加 Kp 有稳态误差、加 Ki 消除』",
                    "能定性解释 Kd 抑制超调",
                ],
            },
            {
                "title": "抗积分饱和的 PID",
                "minutes": 45,
                "goal": "在上一个基础上加积分限幅（clamping），对比饱和前后行为。",
                "scaffolding": [
                    "记录积分项上下界",
                    "饱和时停止累积（或回退本步增量）",
                    "设计执行器限幅 ±u_max",
                    "对比『带/不带抗饱和』的 recovers 速度",
                ],
                "pitfalls": [
                    "限幅位置放错（应在累加后而非累加前）",
                    "忘记把执行器饱和反馈回积分项",
                ],
                "done_when": [
                    "能演示抗饱和显著加快退出饱和",
                ],
            },
        ],
    },
    "进程调度": {
        "aliases": ["进程", "调度", "scheduler", "os", "操作系统", "cpu"],
        "blurb": "用纯 Python 模拟几种 CPU 调度算法，打印甘特图与平均等待时间。",
        "options": [
            {
                "title": "FCFS 与 SJF 调度对比",
                "minutes": 25,
                "goal": "给定一组 (到达时间, 运行时间) 的进程，模拟先来先服务与短作业优先，打印平均等待时间。",
                "scaffolding": [
                    "用列表存进程，按到达时间排序",
                    "FCFS：依次跑，累计完成时间",
                    "SJF：每次从『已到达』里挑剩余最短的",
                    "分别计算等待时间 = 完成 − 到达 − 运行",
                ],
                "pitfalls": [
                    "忽略『到达时间』，误以为进程一开始就都在",
                    "SJF 忘了只在已到达集合里挑",
                    "平均等待时间分母数错",
                ],
                "done_when": [
                    "能打印两种算法的平均等待时间并解释 SJF 为何更短",
                ],
            },
            {
                "title": "时间片轮转 RR",
                "minutes": 40,
                "goal": "实现 Round Robin，给定时间片 q，模拟轮转并打印每个进程的周转时间。",
                "scaffolding": [
                    "用队列维护就绪进程",
                    "每个进程最多跑 q，剩下的放回队尾",
                    "维护剩余运行时间",
                    "打印甘特序列与平均周转时间",
                ],
                "pitfalls": [
                    "时间片内进程结束但忘了移除",
                    "q 大于某进程运行时间时仍切走（应一次跑完）",
                    "就绪队列空窗期没推进时钟",
                ],
                "done_when": [
                    "q 很大时退化为 FCFS、q 很小时切换开销高，能说清",
                ],
            },
        ],
    },
    "运放": {
        "aliases": ["运放", "opamp", "运算放大", "电路", "放大", "负反馈"],
        "blurb": "用节点方程手算运放电路的闭环增益，再用 Python 高斯消元验证。",
        "options": [
            {
                "title": "反相放大器增益计算",
                "minutes": 20,
                "goal": "对反相放大电路，由虚短/虚断推出 Vout=−(Rf/Rin)·Vin，并用节点方程验证。",
                "scaffolding": [
                    "列出反相端节点电流方程（流入=流出）",
                    "用『虚断』(输入电流≈0) 与『虚短』(V−≈V+=0)",
                    "整理出 Vout/Vin",
                    "用 Python 解线性方程组核对",
                ],
                "pitfalls": [
                    "记错反相/同相符号",
                    "忘记同相端通常通过电阻接地 → V+=0",
                    "把负载电阻算进反馈比",
                ],
                "done_when": [
                    "能推出并验证闭环增益 = −Rf/Rin",
                ],
            },
            {
                "title": "自己写高斯消元解节点方程",
                "minutes": 40,
                "goal": "实现一个小的高斯消元（带部分主元），对任意电阻网络列节点方程求各点电压。",
                "scaffolding": [
                    "按 KCL 对每个未知节点列方程",
                    "组装系数矩阵 A 与右端 b",
                    "实现消元 + 回代（含部分主元防除零）",
                    "用已知电路验证结果",
                ],
                "pitfalls": [
                    "主元为 0 不处理 → 崩溃",
                    "节点方程符号写反（流出为正还是流入为正要统一）",
                    "参考节点选错导致方程奇异",
                ],
                "done_when": [
                    "能对任意小电路解出节点电压且自洽",
                ],
            },
        ],
    },
}

# 审阅式反馈清单（注入 teaching_hints，教 GLM 怎么点评学生作品）
_REVIEW_CHECKLIST = [
    "先肯定做对的部分（哪怕只是『跑通了』），再指出可改处",
    "指出问题时要附『原因』，不只是『这里不对』",
    "不打分、不排名；用『更稳/更清晰/更贴近原理』这类表述",
    "能挑一个可迁移的点让学生举一反三（如『你这里限幅的思路，PID 也用』）",
    "学生卡住时让他自己改，而不是替他重写",
]


def _match_topic(topic: str | None) -> str | None:
    if not topic:
        return None
    t = topic.strip().lower()
    for key, meta in _PROJECTS.items():
        if t in key.lower() or key.lower() in t:
            return key
        for alias in meta["aliases"]:
            if alias.lower() in t or t in alias.lower():
                return key
    return None


def solve(topic: str | None = None, level: str | None = None) -> dict[str, Any]:
    """返回匹配 topic 的微项目选项（2~3 个难度递增）。

    Args:
        topic: 学科/知识点关键词，如 "傅里叶" / "PID" / "进程调度" / "运放"。
            为空或匹配不到时返回可用 topic 列表供 GLM 提议。
        level: 可选 "easy"/"medium"/"hard"，用于在选项里进一步挑难度。

    Returns:
        ok=True, matched=bool, 命中则带 options[]（每项含 scaffolding/pitfalls/
        done_when），并附 review_checklist；未命中带 suggestions[]。
    """
    key = _match_topic(topic)
    if key is None:
        return {
            "ok": True,
            "matched": False,
            "topic": topic,
            "suggestions": [
                {"name": k, "blurb": v["blurb"]} for k, v in _PROJECTS.items()
            ],
            "review_checklist": _REVIEW_CHECKLIST,
        }

    meta = _PROJECTS[key]
    options = list(meta["options"])
    if level:
        idx = {"easy": 0, "medium": 1, "hard": 2}.get((level or "").strip().lower())
        if idx is not None and idx < len(options):
            options = [options[idx]]

    return {
        "ok": True,
        "matched": True,
        "topic": key,
        "blurb": meta["blurb"],
        "options": options,
        "review_checklist": _REVIEW_CHECKLIST,
    }


def _schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "project_guide",
            "description": (
                "工科微项目提议器：给定一个工程/编程知识点（傅里叶 / PID / 进程调度 / 运放等），"
                "返回 2~3 个难度递增、20~60 分钟能做完的小项目选项（目标 / 脚手架步骤 / 常见坑 / "
                "完成标准）。用于『把工程题落到可展示产出』时，先调它拿到可提议的项目，再让学生挑。"
                "所有示例纯 Python、零外部依赖，学生贴出代码后用 code_runner 跑、你给审阅式反馈。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "工程知识点，如 '傅里叶' / 'PID' / '进程调度' / '运放'；留空则返回可提议的 topic 列表",
                    },
                    "level": {
                        "type": "string",
                        "description": "可选难度过滤：easy / medium / hard",
                        "enum": ["easy", "medium", "hard"],
                    },
                },
            },
        },
    }


SKILL = SkillSpec(
    name="project_guide",
    title="工科微项目指导",
    description="工科微项目提议（脚手架 + 常见坑 + 完成标准），用于把工程题落到可展示产出",
    schema=_schema(),
    solve=solve,
    module_dir=_MODULE_DIR,
    scenes=("engineering",),
    digest_fields=("options",),
    guide="guide_engineering.md",
)
