---
name: <skill-name>
description: <一句话说清这个 skill 做什么、什么时候该调用它>
when_to_use: |
  - <触发场景 1>
  - <触发场景 2>
  - <触发场景 3>
---

# <Skill 中文名> · 教学策略

> 本文件会在工具执行成功后随结果返回给模型（字段 `teaching_hints`）。
> 请按本策略组织讲解，**不要**把 `steps` 原样丢给学生。

## 何时使用

<用 2~3 句话描述典型触发场景，帮模型判断该不该调这个 skill。>

## 解题 / 讲解流程（务必按此顺序）

1. **<第一步>**：<做什么，为什么>
2. **<第二步>**：<做什么，为什么>
3. **<检验>**：<怎么验证结果对不对>

## 常见误区 / Gotchas

- <学生最高频的错误 1>
- <学生最高频的错误 2>
- <学生最高频的错误 3>

## 参考资料

- `references/xxx.md`：<这份资料什么时候读>

## 脚手架策略（认知负荷控制）

- **学生卡住时**：先反问「你觉得第一步该做什么」，不要直接给答案——生成效应。
- **学生出错时**：先肯定做对的部分，再指出具体错在哪一步，最后让他自己修正。
- **学生做对时**：追问一道同类变式，促成迁移。
- **一步别给太多**：拆成 2~4 轮对话，每轮确认学生跟上再继续。

---

## 开发者备注（以下内容不注入给模型）

### 新增一个 skill 的完整步骤

1. 复制本目录为 `app/skills/<name>/`；
2. 写 `solver.py`，导出 `SKILL = SkillSpec(name=..., schema=..., solve=..., module_dir=_MODULE_DIR,
   scenes=(...), digest_fields=(...), guide=...)`——三个声明式元数据必须写全：
   `scenes` 是意图场景归属（如 `("math",)`），`digest_fields` 是压缩摘要的结论字段优先级，
   `guide` 是场景指南文件名（prompts/ 下，没有就省略）。详见 docs/08-工具层设计.md；
3. 在 `app/skills/registry.py` 的 `_SKILL_MODULES` 里加上模块名；
4. 写 `scripts/sanity_check.py` 并跑通验证。

### 硬性约定（踩过坑，务必遵守）

- **`steps[].expr` 必须是 LaTeX，中文只能放 `steps[].note`**
  —— 前端 MathCard 用 KaTeX 渲染 expr，中文进去会乱码。
- 计算统一走 `app/agent/math_tools.py` 的共享能力：
  `safe_parse` / `run_with_timeout` / `latex_of` / `numeric_of`。
- 需要额外符号时用 `safe_parse(expr, extra_locals={...})`，
  **不要**往全局白名单里塞（避免为兼容性开放危险名字）。
- 返回值失败时统一 `{ok: False, error}`，**绝不抛异常**——
  让模型据此换策略，而不是让整个请求 500。
- 返回值建议对齐 math 卡 payload：`{problem, steps, answer, unit}`。
