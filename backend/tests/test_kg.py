"""KG 查缺补漏守门测试（2026-09-02 引入，对齐 docs/01 §6）。

守的不是实现细节，是**数据完整性**：手写知识图谱最容易犯的错
（前置 id 拼错、难度越界、策略权重缺键）以前没有任何测试能拦住。
"""
from __future__ import annotations

import pytest

from app.kg import COURSES, get_kg
from app.kg.base import KeywordMatchMixin

STRATEGY_KEYS = {"类比", "分解", "反例", "可视化"}


@pytest.mark.parametrize("course_id", sorted(COURSES))
class TestGraphIntegrity:
    """每门注册课程的图完整性——数据错误在 CI 拦下，不流到学生对话里。"""

    def test_prerequisite_ids_exist(self, course_id: str):
        """前置 id 必须指向真实节点（手写 prerequisites 最易拼错）。"""
        nodes = get_kg(course_id).nodes()
        for node in nodes.values():
            for pid in node.prerequisites:
                assert pid in nodes, (
                    f"{course_id}.nodes[{node.id}].prerequisites 引用了不存在的节点: {pid}"
                )

    def test_no_self_reference(self, course_id: str):
        nodes = get_kg(course_id).nodes()
        for node in nodes.values():
            assert node.id not in node.prerequisites

    def test_prerequisites_acyclic(self, course_id: str):
        """前置关系必须是 DAG（循环前置会让「先补前置」永远走不到头）。"""
        nodes = get_kg(course_id).nodes()
        for start in nodes:
            seen, stack = set(), [start]
            while stack:
                cur = stack.pop()
                if cur in seen:
                    pytest.fail(f"{course_id} 前置链存在环: {start} → {cur}")
                seen.add(cur)
                stack.extend(nodes[cur].prerequisites)

    def test_difficulty_in_range(self, course_id: str):
        nodes = get_kg(course_id).nodes()
        for node in nodes.values():
            assert 1 <= node.difficulty <= 5, (
                f"{course_id}.nodes[{node.id}].difficulty={node.difficulty} 越界(1~5)"
            )

    def test_difficulty_not_below_prerequisites(self, course_id: str):
        """ZPD 语义：前置不应比后继难（警示性检查，节点难度≥前置难度）。"""
        nodes = get_kg(course_id).nodes()
        for node in nodes.values():
            for pid in node.prerequisites:
                assert node.difficulty >= nodes[pid].difficulty, (
                    f"{course_id}: {node.name}(难度{node.difficulty}) 的前置 "
                    f"{nodes[pid].name}(难度{nodes[pid].difficulty}) 更难，检查 ZPD 语义"
                )

    def test_strategy_weights_complete(self, course_id: str):
        weights = get_kg(course_id).strategy_weights()
        assert set(weights) == STRATEGY_KEYS, (
            f"{course_id}.strategy_weights 键应为 {STRATEGY_KEYS}，实际 {set(weights)}"
        )
        assert all(0 < w <= 1.5 for w in weights.values())

    def test_summary_and_name_nonempty(self, course_id: str):
        nodes = get_kg(course_id).nodes()
        assert nodes, f"{course_id} 节点为空"
        for node in nodes.values():
            assert node.name.strip()
            assert node.summary.strip()


class TestMatchNode:
    """KeywordMatchMixin 的匹配语义（最长关键词优先 + 名/摘要兜底）。"""

    def test_explicit_keyword_hit(self):
        assert get_kg("os").match_node("什么是死锁") == "deadlock"

    def test_longest_keyword_wins(self):
        """os 关键词：「进程」(process) vs「页面置换」(paging)；查「进程的页面置换」应命中 paging。"""
        assert get_kg("os").match_node("进程的页面置换算法") == "paging"

    def test_fallback_to_summary(self):
        """「页表」不在 os 关键词表（分页/页面/置换），但 summary 里有——兜底必须接住。"""
        assert get_kg("os").match_node("页表结构") == "paging"

    def test_fallback_to_name(self):
        """「并发与同步」是节点名而非关键词——兜底接住。"""
        assert get_kg("os").match_node("讲讲并发与同步") == "sync"

    def test_no_hit_returns_none(self):
        assert get_kg("os").match_node("今天天气不错") is None
        assert get_kg("os").match_node("") is None

    def test_all_graphs_have_mixin(self):
        """所有注册课程都应继承 KeywordMatchMixin（不再手写重复实现）。"""
        for cid, cls in COURSES.items():
            assert issubclass(cls, KeywordMatchMixin), f"{cid} 未继承 KeywordMatchMixin"


class TestKgLookupTool:
    """工具返回契约：前置详解 / 策略权重 / refs（对齐 docs/00「先补前置再讲新概念」）。"""

    def test_hit_shape(self):
        from app.agent.tools import kg_lookup

        result = kg_lookup("os", "什么是死锁")
        assert result["ok"] is True
        matched = result["matched"]
        assert matched["id"] == "deadlock"
        # 前置带详解，不再裸 id
        details = matched["prerequisite_details"]
        assert details and details[0]["id"] == "sync"
        assert {"id", "name", "difficulty", "summary"} <= set(details[0])
        assert matched["prerequisites"] == ["sync"]
        # 策略权重随结果回注（pipeline_v1 归档后的唯一消费路径）
        assert result["strategy_weights"]["类比"] == 1.0
        assert result["course"] == "操作系统"

    def test_miss_is_structured(self):
        from app.agent.tools import kg_lookup

        result = kg_lookup("os", "今天天气不错")
        assert result == {"ok": True, "matched": None, "course": "操作系统"}

    def test_unknown_course(self):
        from app.agent.tools import kg_lookup

        result = kg_lookup("nope", "进程")
        assert result["ok"] is False and "nope" in result["error"]

    def test_refs_default_empty(self):
        """refs 出处锚点字段已就位（docs/11 §5 接入位），默认空不破坏既有节点。"""
        from app.kg.os import OSGraph

        assert all(n.refs == [] for n in OSGraph().nodes().values())
