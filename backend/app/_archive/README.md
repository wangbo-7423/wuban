# _archive/

`pipeline_v1/` 下是早期「意图识别 → KG → 诊断 → 策略 → 卡片」流水线的旧实现。
保留仅作为参考；当前产品已切换为「GLM 5.3 Flash + Tools」自主决策的 Agent 架构。

新代码请：
- 不要 import 这些模块；
- 不再扩展流水线规则；
- 行为差异以 `app/agent/orchestrator.py` 为准。
