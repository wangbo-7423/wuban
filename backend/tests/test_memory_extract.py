"""记忆抽取新信号单测（纯逻辑，mock bridge，不碰 MCP/GLM）：

- 「兴趣」实体类型（他觉得什么有意思）；
- 「自发关联」关系（学生主动把两个东西连上，双联联想）；
- 「表里反差探针」observation（揭示式讲解后接没接住）；
- L0 常驻层注入「觉得有意思的」行。
"""
from __future__ import annotations

import pytest

from app.services import memory_service


class TestValidateEntities:
    def test_interest_type_accepted(self):
        out = memory_service._validate_entities([
            {"name": "音乐频谱", "entityType": "兴趣", "observations": ["觉得把音乐拆成频率很有意思"]},
        ])
        assert out[0]["entityType"] == "兴趣"

    def test_unknown_type_falls_back_to_concept(self):
        out = memory_service._validate_entities([
            {"name": "某物", "entityType": "玄学", "observations": []},
        ])
        assert out[0]["entityType"] == "概念"

    def test_probe_observation_recorded_on_concept(self):
        out = memory_service._validate_entities([
            {"name": "浮点数", "entityType": "概念",
             "observations": ["表里反差探针：接住"]},
        ])
        assert out[0]["observations"] == ["表里反差探针：接住"]


class TestValidateRelations:
    def test_spontaneous_link_relation_preserved(self):
        out = memory_service._validate_relations([
            {"from": "浮点数", "to": "食堂打饭", "relationType": "自发关联"},
        ])
        assert out == [{"from": "浮点数", "to": "食堂打饭", "relationType": "自发关联"}]


class _FakeBridge:
    """记录写入调用的假 bridge；read_graph 返回固定图谱。"""

    def __init__(self, graph: dict | None):
        self._graph = graph or {"entities": [], "relations": []}
        self.created: list = []
        self.obs: list = []
        self.rels: list = []

    def available(self) -> bool:
        return True

    def read_graph(self, _user_id: str) -> dict:
        return self._graph

    def create_entities(self, _user_id: str, entities: list) -> None:
        self.created.extend(entities)

    def add_observations(self, _user_id: str, obs: list) -> None:
        self.obs.extend(obs)

    def create_relations(self, _user_id: str, rels: list) -> None:
        self.rels.extend(rels)


@pytest.fixture()
def fake_bridge(monkeypatch):
    def _install(graph: dict | None) -> _FakeBridge:
        b = _FakeBridge(graph)
        monkeypatch.setattr(memory_service.bridge, "available", lambda: True)
        monkeypatch.setattr(memory_service.bridge, "read_graph", b.read_graph)
        monkeypatch.setattr(memory_service.bridge, "create_entities", b.create_entities)
        monkeypatch.setattr(memory_service.bridge, "add_observations", b.add_observations)
        monkeypatch.setattr(memory_service.bridge, "create_relations", b.create_relations)
        return b

    return _install


class TestApplyExtraction:
    def test_interest_entity_and_spontaneous_relation_written(self, fake_bridge):
        b = fake_bridge({
            "entities": [{"name": "浮点数", "entityType": "概念", "observations": []}],
            "relations": [],
        })
        stats = memory_service.apply_extraction("u1", {
            "entities": [
                {"name": "浮点数", "entityType": "概念", "observations": ["表里反差探针：接住"]},
                {"name": "食堂打饭", "entityType": "兴趣", "observations": ["学生觉得这个类比好笑"]},
            ],
            "relations": [{"from": "浮点数", "to": "食堂打饭", "relationType": "自发关联"}],
        })
        assert stats["entities_created"] == 1  # 只有兴趣是新实体
        assert stats["observations_added"] == 1  # 探针记录补进已有概念
        assert stats["relations_created"] == 1
        assert b.rels == [{"from": "浮点数", "to": "食堂打饭", "relationType": "自发关联"}]
        assert b.created[0]["entityType"] == "兴趣"


class TestPersistentDigest:
    def test_interests_injected_into_l0(self, fake_bridge):
        fake_bridge({
            "entities": [
                {"name": "偏好图示讲解", "entityType": "偏好", "observations": []},
                {"name": "音乐频谱", "entityType": "兴趣", "observations": []},
                {"name": "混淆卷积与相关", "entityType": "误区", "observations": []},
            ],
            "relations": [],
        })
        out = memory_service.get_persistent_digest("u1")
        assert out is not None
        assert "觉得有意思的：音乐频谱" in out
        assert "偏好：偏好图示讲解" in out
        assert "往「觉得有意思的」方向连" in out

    def test_no_graph_returns_none(self, monkeypatch):
        monkeypatch.setattr(memory_service.bridge, "available", lambda: False)
        assert memory_service.get_persistent_digest("u1") is None
