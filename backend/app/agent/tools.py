"""AI Agent 工具 / MCP 接口层。

设计原则：
- **协议独立于实现**：每个工具暴露 (1) OpenAI 形态的 JSON Schema 定义，
  (2) 同名 Python 函数执行；未来迁移到 MCP server 也只需把执行端点换掉；
- **失败也要返回结构化结果**，让 GLM 在下一轮对话里有上下文可读；
- **危险操作（shell / 浏览器 / 文件）默认关闭**，仅当 settings 显式开启才可用。

## 两层分工

- **本模块（能力层）**：通用工具，回答「怎么算这个表达式」；
- **app/skills/（场景层）**：学科场景专家（微积分 / 微分方程 / ...），
  回答「怎么教这个知识点」。skill 以工具形态注册进来，由本模块统一透出
  schema 并分发执行；执行成功后会自动注入该场景的教学提示。
"""
from __future__ import annotations

import ast
import logging
import operator
from dataclasses import dataclass
from typing import Any, Callable

from app.agent.math_tools import available as sympy_available
from app.agent.code_runner import run_code, code_runner_schema
from app.core.config import settings
from app.skills.base import SkillSpec
from app.skills.registry import execute_skill, get_skill, skill_schemas

logger = logging.getLogger(__name__)


# ── 简单内存工具（不依赖外部服务，竞赛 Demo 自带）─────────────


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ALLOWED_NAMES = {
    "pi": __import__("math").pi,
    "e": __import__("math").e,
    "sqrt": __import__("math").sqrt,
    "sin": __import__("math").sin,
    "cos": __import__("math").cos,
    "tan": __import__("math").tan,
    "log": __import__("math").log,
    "log10": __import__("math").log10,
    "abs": abs,
    "round": round,
    "pow": pow,
    "max": max,
    "min": min,
}


def _safe_calc_eval(expr: str) -> float:
    """受限表达式求值：只允许字面量、四则、幂、模、函数调用。

    不允许：变量赋值、属性访问、import、调用内置函数（除 _ALLOWED_NAMES）。
    """
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"不支持的字面量: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持二元运算符: {type(node.op).__name__}")
        return op(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持一元运算符: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("只允许直接函数调用")
        fn = _ALLOWED_NAMES.get(node.func.id)
        if not callable(fn):
            raise ValueError(f"不允许调用: {node.func.id}")
        return fn(*[_eval_node(a) for a in node.args])
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_NAMES:
            v = _ALLOWED_NAMES[node.id]
            return float(v) if isinstance(v, (int, float)) else v
        raise ValueError(f"未定义变量: {node.id}")
    raise ValueError(f"不支持的语法节点: {type(node).__name__}")


def calculator(expression: str) -> dict[str, Any]:
    """受限表达式求值：避免任意代码执行风险。"""
    try:
        value = _safe_calc_eval(expression)
        return {"ok": True, "expression": expression, "value": value}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "expression": expression, "error": str(e)}


# ── 知识图谱查询（保留旧 KG 作为静态资料库，不参与决策）──────


def kg_lookup(course_id: str, query: str) -> dict[str, Any]:
    """从静态 KG 匹配节点（关键词表优先，节点名/摘要兜底）。

    返回：命中节点摘要 + **前置节点详解**（id/name/difficulty/summary，
    供「先补前置再讲新概念」直接引用）+ 学科策略权重（GLM 参考，自行决定
    引导倾向，不做硬约束）+ refs 出处锚点（教材章节，自然引用零版权风险）。
    """
    try:
        from app.kg import get_kg
        kg = get_kg(course_id)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": f"未找到课程知识图谱: {course_id}"}

    nodes = kg.nodes()
    node_id = kg.match_node(query)
    if not node_id:
        return {"ok": True, "matched": None, "course": kg.course_name}
    n = nodes[node_id]
    prereq_details = [
        {
            "id": p.id,
            "name": p.name,
            "difficulty": p.difficulty,
            "summary": p.summary,
        }
        for p in (nodes[pid] for pid in n.prerequisites if pid in nodes)
    ]
    return {
        "ok": True,
        "matched": {
            "id": n.id,
            "name": n.name,
            "difficulty": n.difficulty,
            "prerequisites": n.prerequisites,
            "prerequisite_details": prereq_details,
            "summary": n.summary,
            "refs": n.refs,
        },
        "strategy_weights": kg.strategy_weights(),
        "course": kg.course_name,
    }


# ── 长期记忆检索（MCP 知识图谱；数据来自历史对话抽取）────────


def memory_search(query: str) -> dict[str, Any]:
    """查这位学生的长期记忆图谱（学过的概念/误区/偏好）。

    user_id 从请求级 ContextVar 取（/chat 端点入口 set）；
    拿不到（后台/非请求上下文）返回结构化错误让 GLM 换策略。
    """
    from app.mcp import MemoryUnavailable, bridge, current_user_id

    uid = current_user_id.get()
    if not uid:
        return {"ok": False, "error": "当前上下文没有用户身份，无法查记忆"}
    if not bridge.available():
        return {"ok": False, "error": "记忆服务未启用"}
    try:
        from app.services.memory_service import search_for_agent
        return search_for_agent(uid, query)
    except MemoryUnavailable as e:
        return {"ok": False, "error": f"记忆服务不可用: {e}"}


def _memory_search_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "查询这位学生的长期记忆图谱：他学过哪些概念、暴露过哪些误区、"
                "有什么学习偏好/目标。当需要回忆之前聊过的内容、判断学生基础、"
                "或避免重复纠正同一个误区时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查的概念/主题关键词，如 '傅里叶' '积分'",
                    }
                },
                "required": ["query"],
            },
        },
    }


# ── 认知状态工具（docs/10-认知状态与工具边界.md）──────────────
# 分工：scaffold_state = 读档案（按需替代全量注入）；
#       mastery_evidence = 写证据标签（只记行为时刻，绝不记分数）。

# 受控证据词表：只描述「观察到的行为时刻」，与应试闭环（打分/判对错）划清界限
_EVIDENCE_TYPES = (
    "self_explained",      # 学生自己把概念解释通了
    "deep_question",       # 学生提出了「为什么」层的深层问题
    "stuck",               # 学生在同一概念上连续卡壳
    "transferred",         # 学生把概念迁移到了新场景
    "prediction_checked",  # 学生先预测、再用工具/推导验证了
)


def _normalize_evidence_type(raw: Any) -> str | None:
    """校验证据类型：受控词表外一律拒绝（防御 GLM 发明新类型）。"""
    v = str(raw or "").strip().lower()
    return v if v in _EVIDENCE_TYPES else None


def scaffold_state(topic: str = "") -> dict[str, Any]:
    """查这位学生的认知档案：脚手架级别、认知负荷、已知误区、到期回顾主题。

    数据来自 learner_profiles.cognitive_state（轮后离线聚合的过程性证据），
    本工具是它的**按需读取口**——模型拿不准引导深浅时查，而不是每轮全量注入。
    user_id 从请求级 ContextVar 取；无档案时返回 has_state=False 的提示。
    """
    from app.mcp import current_user_id

    uid = current_user_id.get()
    if not uid:
        return {"ok": False, "error": "当前上下文没有用户身份，无法查认知档案"}

    from app.core.db import SessionLocal
    from app.models import LearnerProfile

    with SessionLocal() as db:
        row = (
            db.query(LearnerProfile).filter(LearnerProfile.user_id == uid).first()
        )
    state: dict[str, Any] = (row.cognitive_state or {}) if row else {}
    if not state:
        return {
            "ok": True,
            "has_state": False,
            "hint": "该学生还没有认知档案，按初次接触的新学生对待",
        }

    scaff = state.get("scaffolding") or {}
    out: dict[str, Any] = {
        "ok": True,
        "has_state": True,
        "cognitive_load": state.get("cognitive_load"),
        "scaffold_level": scaff.get("recent_scaffold_level"),
        "gave_answer_ratio": scaff.get("gave_answer_ratio"),
        "misconceptions": (state.get("misconceptions") or [])[:5],
        "review_queue": [
            {
                "topic": e.get("topic"),
                "overdue_days": e.get("overdue_days"),
                "reason": e.get("reason"),
            }
            for e in (state.get("review_queue") or [])[:3]
        ],
    }

    # 可选聚焦：查当前话题在该学生档案里的状态（探索中/深化中/该回顾）
    key = (topic or "").strip()
    if key:
        out["topic"], out["status"], out["mentions"] = None, None, None
        for name, t in (state.get("topics") or {}).items():
            if key in name or name in key:
                out.update({
                    "topic": name,
                    "status": t.get("status"),
                    "mentions": t.get("mentions"),
                    "review": t.get("review"),
                })
                break
    return out


def _scaffold_state_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "scaffold_state",
            "description": (
                "查询这位学生当前的认知档案：脚手架级别、认知负荷、已知误区、"
                "到期该回顾的主题。当你不确定该用多深的引导、怀疑学生之前"
                "学过或卡过当前话题、或想避免重复纠正同一个误区时调用；"
                "可传 topic 聚焦查看单个概念的状态。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "可选，当前正在讨论的概念关键词（如 '傅里叶'）；"
                            "传入时额外返回该话题的历史状态"
                        ),
                    }
                },
                "required": [],
            },
        },
    }


def mastery_evidence(evidence_type: str, topic: str, note: str = "") -> dict[str, Any]:
    """记录一次过程性证据（受控词表，只记标签不记分数）。

    写入 learning_evidence 表，由 profile_service 轮后聚合进 cognitive_state。
    校验在连库之前：词表外的类型直接结构化拒绝，防御 GLM 发明新类型。
    """
    et = _normalize_evidence_type(evidence_type)
    if et is None:
        return {
            "ok": False,
            "error": f"evidence_type 必须是 {'/'.join(_EVIDENCE_TYPES)} 之一",
            "got": str(evidence_type),
        }
    topic_clean = (topic or "").strip()[:128]
    if not topic_clean:
        return {"ok": False, "error": "topic 不能为空"}

    from app.mcp import current_user_id

    uid = current_user_id.get()
    if not uid:
        return {"ok": False, "error": "当前上下文没有用户身份，无法记录证据"}

    from app.core.db import SessionLocal
    from app.models.evidence import LearningEvidence

    note_clean = (note or "").strip()[:500] or None
    with SessionLocal() as db:
        db.add(LearningEvidence(
            user_id=uid, evidence_type=et, topic=topic_clean, note=note_clean,
        ))
        db.commit()
    return {"ok": True, "evidence_type": et, "topic": topic_clean}


def _mastery_evidence_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "mastery_evidence",
            "description": (
                "记录你在对话中观察到的过程性证据标签（注意：是行为记录，不是"
                "给学生打分）：self_explained=学生自己解释通了；deep_question="
                "提出了「为什么」层的深层问题；stuck=在同一概念上连续卡壳；"
                "transferred=把概念迁移到了新场景；prediction_checked=先预测"
                "再验证。只在明显观察到该时刻时调用一次，不要每轮都调。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_type": {
                        "type": "string",
                        "enum": list(_EVIDENCE_TYPES),
                        "description": "证据类型（受控词表）",
                    },
                    "topic": {
                        "type": "string",
                        "description": "证据相关的概念关键词，如 'PID' '卷积'",
                    },
                    "note": {
                        "type": "string",
                        "description": "可选，一句话现场描述（学生说了什么/做了什么）",
                    },
                },
                "required": ["evidence_type", "topic"],
            },
        },
    }


# ── 代码执行（工科微项目线核心）──────────────────────────────


def code_runner(code: str, lang: str = "python") -> dict[str, Any]:
    """在受控沙箱执行学生提交的代码，返回 stdout/stderr/退出码/可选图表。"""
    return run_code(code, lang=lang)


def _code_runner_schema() -> dict[str, Any]:
    return code_runner_schema()


# ── 联网搜索（Tavily / ddgs 可插拔，见 web_search.py）────────


def web_search(query: str, top_k: int = 5) -> dict[str, Any]:
    """联网搜索：本地知识不够新（论文/新版本 API）时核实。

    后端可插拔（Tavily 有 key 优先，否则 ddgs 多引擎）；未启用
    settings.enable_web_search 时不注册本工具；启用但后端全不可用时
    返回结构化错误 + hint，让 GLM 直接用本地知识并声明确定度。
    """
    from app.agent.web_search import web_search_impl
    return web_search_impl(query, top_k)


# ────────────────────────────────────────────────────────────
# 工具注册表
# ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    """通用工具定义。

    声明式元数据（领域知识就地打包，见 docs/08-工具层设计.md）：
    - scenes：本工具服务的意图场景；"*" = 所有场景都装配（如 memory_search）。
      intent.py 据此动态装配工具候选，替代集中式白名单表；
    - digest_fields：压缩摘要的字段优先级。compress.py 据此把旧轮本工具
      的完整结果压成一行结论，替代集中式字段表；
    - guide：渐进式披露的场景指南文件名（prompts/ 目录下）。
      命中场景时随尾部动态上下文注入，替代 guide_{intent}.md 命名约定。

    新增一个工具 = 在 _build_registry 声明一处 spec（含上述元数据），不改
    intent.py / compress.py / system.py 任何一行。
    """

    name: str
    description: str
    enabled: bool
    schema: dict[str, Any]
    func: Callable[..., dict[str, Any]]
    scenes: tuple[str, ...] = ()                # 所属意图场景（"*" = 全场景）
    digest_fields: tuple[str, ...] = ()         # 压缩摘要字段优先级
    guide: str | None = None                    # 场景指南文件名（prompts/ 下）


def _calculator_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "受限的数学表达式求值：支持 + - * / ^ %、"
                "以及 sqrt/sin/cos/tan/log/abs/pow 等函数。"
                "不执行任意代码，输入应为单个数学表达式字符串。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 'sqrt(2)**2 + 1'",
                    }
                },
                "required": ["expression"],
            },
        },
    }


def _kg_lookup_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "kg_lookup",
            "description": (
                "在指定课程知识图谱里匹配概念节点，返回节点摘要、前置节点详解"
                "（学生可能缺的前置知识，应先补前置再讲新概念）、本学科引导策略权重"
                "和教材出处。用于定位学生可能卡在哪一个概念、讲新概念前查前置。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "string",
                        "description": "课程/学科 id：os / autocontrol / signals 等",
                    },
                    "query": {
                        "type": "string",
                        "description": "学生提到的关键词或短语",
                    },
                },
                "required": ["course_id", "query"],
            },
        },
    }


def _web_search_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索（慎用），仅当本地知识明显不足时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    }


def _viz_brief() -> str:
    """可用可视化模板一句话清单（进工具描述，registry 懒构建时才 import）。"""
    from app.viz import builder as _viz
    return _viz.template_brief()


def make_visual(
    template: str,
    title: str | None = None,
    preview_text: str | None = None,
    probe_question: str | None = None,
    reveal_hint: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成交互可视化卡（模板填参，docs/16）。

    给 GLM 的观察只回小结——HTML 不进模型上下文；切卡阶段（cards.py）用工具
    留痕的相同参数从模板重建（确定性等价）。频控（每轮 1 张）在切卡侧执行。
    """
    from app.viz import builder as _viz
    try:
        payload = _viz.build(
            template, params=params, title=title, preview_text=preview_text,
            probe_question=probe_question, reveal_hint=reveal_hint,
        )
    except _viz.VizError as e:
        return {"ok": False, "error": str(e), "hint": f"可用模板：{_viz.template_brief()}"}
    meta = payload["meta"] or {}
    return {
        "ok": True,
        "template": template,
        "title": meta.get("title"),
        "preview_text": payload["preview_text"],
        "probe_question": (payload.get("probe") or {}).get("question"),
        "html_bytes": len(payload["html"].encode("utf-8")),
        "note": (
            "交互可视化卡已生成并将自动附在回复卡片里（payload 由系统注入，正文无需复述其内容）。"
            "正文职责：先抛上面的预测问题让学生猜 → 提示他动手拖 → 请他汇报观察到的现象；不打分。"
        ),
    }


def _make_visual_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "make_visual",
            "description": (
                "生成交互可视化实验卡（学生可在卡片里拖拽参数、实时看结果）。"
                "当学生想直观感受某个概念的行为/参数影响、且命中可用模板主题时调用；"
                "每轮至多一张。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "enum": ["harmonic", "pid_tuner", "curve_lab"],
                        "description": (
                            "harmonic=傅里叶谐波逐层逼近方波；pid_tuner=PID 三参数调阶跃响应；"
                            "curve_lab=通用曲线实验台（表达式驱动，覆盖阻尼振动/拍频/指数增长衰减/"
                            "三角函数/导数几何等一切「y=f(x) 带参数」类概念）"
                        ),
                    },
                    "title": {"type": "string", "description": "卡片标题（可选，默认用模板名）"},
                    "preview_text": {"type": "string", "description": "一句话说明能拖什么、看什么（可选）"},
                    "probe_question": {"type": "string", "description": "预测问题：让学生先猜结果再拖动（推荐给出）"},
                    "reveal_hint": {"type": "string", "description": "预测的思考提示（可选）"},
                    "params": {
                        "type": "object",
                        "description": (
                            "模板初始参数。harmonic: default_n(1~25)；pid_tuner: kp(0~10)/ki(0~5)/kd(0~5)；"
                            "curve_lab: {expr: 关于x的数学表达式(如 A*exp(-d*x)*sin(w*x)，只用 sin cos tan exp log sqrt abs 等), "
                            "sliders: [{key,min,max,step,default}]（最多5个，key 就是表达式里的变量名）, "
                            "x_range: [xmin,xmax], x_label/y_label 可选}"
                        ),
                    },
                },
                "required": ["template"],
            },
        },
    }


def _build_registry() -> tuple[ToolSpec, ...]:
    specs: list[ToolSpec] = []

    # 数学能力由 app/skills/ 下的场景工具（calculus / ode / ...）提供。
    # 这里只在 sympy 不可用时注册一个受限数值计算器兜底，
    # 保证环境缺依赖时 demo 仍不至于完全没有计算能力。
    if settings.enable_calculator and not sympy_available():
        logger.warning("sympy 不可用，注册受限数值 calculator 兜底")
        specs.append(
            ToolSpec(
                name="calculator",
                description="受限数学计算（数值）",
                enabled=True,
                schema=_calculator_schema(),
                func=calculator,
                scenes=("math", "engineering"),
                digest_fields=("value",),
            )
        )

    # kg_lookup 默认开启（语义有用）
    specs.append(
        ToolSpec(
            name="kg_lookup",
            description="知识图谱节点检索",
            enabled=True,
            schema=_kg_lookup_schema(),
            func=kg_lookup,
            scenes=("math", "engineering", "concept"),
            digest_fields=("matched",),
            guide="guide_concept.md",  # 概念讲解指南（含表里反差揭示式讲解），随装配渐进式披露
        )
    )

    # 长期记忆检索（MCP server-memory）：启用且依赖齐全才注册
    if settings.enable_mcp_memory:
        from app.mcp import bridge as _bridge
        if _bridge.available():
            specs.append(
                ToolSpec(
                    name="memory_search",
                    description="学生长期记忆检索（学过的概念/误区/偏好）",
                    enabled=True,
                    schema=_memory_search_schema(),
                    func=memory_search,
                    scenes=("*",),
                    digest_fields=("entities",),
                )
            )

    # 认知状态读工具：按需查档案，替代每轮全量注入（Select 原则）
    if settings.enable_scaffold_state:
        specs.append(
            ToolSpec(
                name="scaffold_state",
                description="学生认知档案查询（脚手架级别/负荷/误区/到期回顾）",
                enabled=True,
                schema=_scaffold_state_schema(),
                func=scaffold_state,
                scenes=("*",),
                digest_fields=("topic", "status", "cognitive_load"),
            )
        )

    # 认知状态写工具：过程性证据标签（只记行为时刻，绝不记分数）
    if settings.enable_mastery_evidence:
        specs.append(
            ToolSpec(
                name="mastery_evidence",
                description="记录过程性证据标签（自我解释/深问/卡壳/迁移/预测验证）",
                enabled=True,
                schema=_mastery_evidence_schema(),
                func=mastery_evidence,
                scenes=("*",),
                digest_fields=("evidence_type", "topic"),
            )
        )

    if settings.enable_web_search:
        specs.append(
            ToolSpec(
                name="web_search",
                description=(
                    "联网搜索：核实可检验的客观事实（库/框架版本与 API 变更、"
                    "发布日期、论文/著作归属、官方语法），不用于概念讲解本身"
                ),
                enabled=True,
                schema=_web_search_schema(),
                func=web_search,
                # concept 查出处、engineering 查版本/API——后者是本地知识
                # 最不可靠、搜索收益最高的场景（docs/11 §3.1）
                scenes=("concept", "engineering"),
                digest_fields=("results",),
                guide="guide_search.md",
            )
        )

    if settings.enable_code_runner:
        specs.append(
            ToolSpec(
                name="code_runner",
                description="受控沙箱执行 Python 代码（禁网络/禁系统命令/硬超时）",
                enabled=True,
                schema=_code_runner_schema(),
                func=code_runner,
                scenes=("engineering",),
                digest_fields=("stdout", "timed_out", "returncode"),
            )
        )

    # 交互可视化：模板填参生成可拖拽实验卡（docs/16，P0 仅两个模板）
    specs.append(
        ToolSpec(
            name="make_visual",
            description=(
                "生成交互可视化实验卡（学生可在卡片里拖拽参数、实时看结果）。"
                "学生想要「动手调参数/拖滑块/直观感受」时**优先选本工具**，而不是用 "
                "code_runner 画静态图——可拖拽的探究体验是静态图给不了的。"
                f"可用模板：{_viz_brief()}。命中主题即调用；每轮至多一张。"
            ),
            enabled=True,
            schema=_make_visual_schema(),
            func=make_visual,
            scenes=("concept", "engineering"),
            digest_fields=("title", "template"),
        )
    )

    return tuple(specs)


_TOOLS: tuple[ToolSpec, ...] | None = None


def list_tools() -> tuple[ToolSpec, ...]:
    """获取当前启用的工具列表（懒构建）。"""
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = _build_registry()
    return _TOOLS


def get_tool(name: str) -> ToolSpec | None:
    for t in list_tools():
        if t.name == name:
            return t
    return None


def spec_for(name: str) -> ToolSpec | SkillSpec | None:
    """按名字取工具或 skill 的 spec（工具优先）。

    压缩 / 路由等通用机制只依赖 spec 上的声明式元数据
    （scenes / digest_fields / guide），不感知「工具 vs skill」的区别。
    """
    tool = get_tool(name)
    if tool is not None:
        return tool
    return get_skill(name)


def tool_schemas(allowed: set[str] | None = None) -> list[dict[str, Any]]:
    """供 GLM 的 tools 字段使用（通用工具 + 场景 skill）。

    allowed 传 None 装配全量；传名字集合则只装配名单内的（意图路由，
    少而准的候选能提升工具选择准确率）。名字对不上已注册工具的自然跳过。
    """
    if allowed is None:
        return [t.schema for t in list_tools()] + skill_schemas()
    return [t.schema for t in list_tools() if t.name in allowed] + [
        s for s in skill_schemas()
        if (s.get("function") or {}).get("name") in allowed
    ]


def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """执行一个工具或场景 skill（结构化返回 ok/error）。

    skill 优先：场景化工具（calculus / ode / ...）语义比通用工具更明确，
    且执行成功后会自动带上该场景的教学提示。
    """
    if get_skill(name) is not None:
        return execute_skill(name, args)
    tool = get_tool(name)
    if tool is None:
        return {"ok": False, "error": f"未注册或未启用的工具: {name}"}
    try:
        result = tool.func(**args)
        if not isinstance(result, dict):
            return {"ok": False, "error": "工具返回非结构化结果", "raw": str(result)}
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
