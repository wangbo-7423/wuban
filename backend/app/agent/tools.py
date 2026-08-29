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
    """从静态 KG 匹配节点（按关键词）。仅当 settings 启用的 KG 存在时返回节点摘要。"""
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
    return {
        "ok": True,
        "matched": {
            "id": n.id,
            "name": n.name,
            "difficulty": n.difficulty,
            "prerequisites": n.prerequisites,
            "summary": n.summary,
        },
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
    name: str
    description: str
    enabled: bool
    schema: dict[str, Any]
    func: Callable[..., dict[str, Any]]


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
                "在指定课程知识图谱里按文本模糊匹配节点，返回节点摘要和前置节点。"
                "用于定位学生可能卡在哪一个概念。"
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
                )
            )

    if settings.enable_web_search:
        specs.append(
            ToolSpec(
                name="web_search",
                description="联网搜索",
                enabled=True,
                schema=_web_search_schema(),
                func=web_search,
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
