"""learning_state（docs/17）：按概念键控的跨轮卡壳信号——纯函数守门测试。

覆盖三块：
- advance_learning_state：streak 累计 / 去重保序 / 窗口淘汰 / 无命中轮次只推 round；
- render_learning_context 的确定性规则：streak≥2 → 前置缺失、streak≥3 或
  一轮 ≥3 概念 → 负荷过高、遗留疑问只渲染不触发；
- 话题漂移由结构过滤：卡壳概念与本轮命中对不上时不产生规则判定。
"""
from app.services.session_memory import advance_learning_state, render_learning_context


def _streaks(state):
    return {c["concept"]: c["streak"] for c in state["stuck_concepts"]}


# ── advance_learning_state ──────────────────────────────────


def test_advance_first_round_dedup_keep_order():
    s = advance_learning_state(None, ["互斥条件", "互斥条件", "死锁预防"])
    assert s["round"] == 1
    assert s["new_concepts_last_round"] == 2  # 去重后计数
    assert s["current_topic"] == "死锁预防"  # 保序：最后命中者为本轮话题
    assert _streaks(s) == {"互斥条件": 1, "死锁预防": 1}


def test_streak_accumulates_over_rounds():
    s = None
    for _ in range(3):
        s = advance_learning_state(s, ["互斥条件"])
    assert _streaks(s) == {"互斥条件": 3}


def test_window_eviction_after_gap():
    s = None
    for _ in range(3):
        s = advance_learning_state(s, ["互斥条件"])  # 最后出现于 round 3
    for _ in range(5):
        s = advance_learning_state(s, ["虚拟内存"])  # 推进到 round 8：8-3=5，仍在窗口
    assert "互斥条件" in _streaks(s)
    s = advance_learning_state(s, ["虚拟内存"])  # round 9：9-3=6 > 5，淘汰
    names = set(_streaks(s))
    assert "互斥条件" not in names and "虚拟内存" in names


def test_empty_hits_freeze_streak_and_keep_topic():
    s = advance_learning_state(None, ["互斥条件"])
    s = advance_learning_state(s, [])  # 本轮没查 KG：streak 冻结、话题保留
    assert s["round"] == 2
    assert s["new_concepts_last_round"] == 0
    assert s["current_topic"] == "互斥条件"
    assert _streaks(s) == {"互斥条件": 1}


# ── render_learning_context：确定性规则 ─────────────────────


def test_render_empty_when_nothing_to_say():
    assert render_learning_context(None, None, []) == ""


def test_rule_forward_missing_on_second_hit():
    s = advance_learning_state(None, ["互斥条件"])  # 上一轮 streak=1
    ctx = render_learning_context(s, None, ["互斥条件"])  # 本轮再命中 → eff=2
    assert "前置缺失" in ctx
    # 首次命中（eff=1）不触发任何规则
    first = render_learning_context(None, None, ["互斥条件"])
    assert "前置缺失" not in first and "负荷过高" not in first


def test_rule_overload_on_third_hit():
    s = None
    for _ in range(2):
        s = advance_learning_state(s, ["互斥条件"])  # 既往 streak=2
    ctx = render_learning_context(s, None, ["互斥条件"])  # eff=3
    assert "负荷过高" in ctx and "前置缺失" not in ctx


def test_rule_overload_on_three_concepts_in_one_round():
    ctx = render_learning_context(None, None, ["进程", "线程", "协程"])
    assert "负荷过高" in ctx


def test_pending_rendered_but_never_triggers_rule():
    s = advance_learning_state(None, ["互斥条件"])
    ctx = render_learning_context(s, "等学生回应反问：死锁怎么预防？", [])
    assert "遗留疑问" in ctx
    assert "前置缺失" not in ctx and "负荷过高" not in ctx


def test_topic_drift_filtered_by_structure():
    """历史卡壳概念与本轮命中对不上 → 只渲染状态，不产生规则判定。"""
    s = advance_learning_state(None, ["互斥条件"])
    for _ in range(2):
        s = advance_learning_state(s, ["互斥条件"])  # streak=3
    ctx = render_learning_context(s, None, ["虚拟内存"])  # 本轮换了话题
    assert "系统规则判定" not in ctx
    assert "互斥条件" in ctx  # 状态仍可见（供参考），但不触发
