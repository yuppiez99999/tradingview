---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-03
updated: 2026-08-03
related_log: 2026-08-03 表驱动化重构 + Phase 1 提取 + Strong 清零
---

# 重构规约 (Refactoring Standards)

> 本文档沉淀自 2026-08-03 两次零行为变更重构(`plan_order` 表驱动化 + `_execution_risk_check` 提取 helper)的判断经验。
> 规约核心:**零行为变更**是底线;**表驱动化 vs 提取 helper** 的选择基于分支结构是同构还是异构。

## 1. 核心原则

### 1.1 零行为变更是底线

所有重构**必须保证输入输出完全一致**,只是内部结构变化。验证方法:
1. 重构前跑覆盖该函数的所有测试,记录基线(全部 PASSED)
2. 重构后跑同样测试,必须仍然全部 PASSED
3. 若无测试覆盖,先写行为对比脚本(重构前后对同一组输入产出 bit-for-bit 一致的输出)

```python
# 验证样板: 重构前后行为对比
import json
before_output = plan_order(algo=AlgoType.TWAP, symbol="000001", ...)
after_output = plan_order(algo=AlgoType.TWAP, symbol="000001", ...)
assert json.dumps(before_output, sort_keys=True) == json.dumps(after_output, sort_keys=True)
```

### 1.2 不在交易时间做

重构生产代码(尤其执行/风控路径)必须**在周末或非交易时段**进行,留足验证时间。交易时间只做热修复,不做结构重构。

### 1.3 优先选择结构清晰的函数

优先重构"结构清晰、逻辑重复"的函数(如同构分支路由),避免触碰"高风险核心链路"(如 `run_all_guards` 级别的风控总入口)。

## 2. 重构模式选择:表驱动化 vs 提取 helper

### 2.1 决策树

```
函数中是否有多个 if/elif 分支或独立检查块?
├─ 否 → 不需要这两种重构, 考虑其他(如参数对象/早期返回)
└─ 是 → 分支结构是同构还是异构?
    │
    ├─ 同构(分支做同一件事的不同变体)
    │   └─ 输出结构是否一致?
    │       ├─ 一致 → ✅ 表驱动化(配置表 + 查表)   → §3
    │       └─ 不一致 → 🟡 提取 helper(保留各自结构) → §4
    │
    └─ 异构(独立检查/处理块, 逻辑不同)
        └─ ✅ 提取 helper(独立块各自封装)         → §4
```

### 2.2 同构 vs 异构的判断依据

| 维度 | 同构(→表驱动化) | 异构(→提取 helper) |
|------|------------------|---------------------|
| 分支目的 | 同一件事的不同变体(如 6 种算法路由) | 独立检查(如价格/数量/流动性/金额 4 个维度) |
| 输出结构 | 一致(如都返回 `slices`) | 不一致(如 `checks` dict 有 4 种 key 结构) |
| 分支间数据依赖 | 无(各分支独立) | 可能有(如后一检查依赖前一检查的结果) |
| 分支逻辑复杂度 | 相似(都是"取参数→调用→返回") | 差异大(有的查配置,有的组合条件) |

**关键判据**:如果强行统一分支的输出结构会改变 `checks`/`result` 等 mutable dict 的 key 结构,那就是**异构**,不能表驱动化(会 behavior change)。

## 3. 模式 A:表驱动化(同构分支)

### 3.1 适用场景

多个 `elif` 分支做同一件事的不同变体,每个分支只是调用的方法/参数不同。

### 3.2 实现方式:配置表 + 查表

**参数签名一致**时用方法引用 dict:

```python
# ❌ 重构前: 6 个 elif
if algo == AlgoType.TWAP:
    slices = self._plan_twap(total, dur, slice_m, start)
elif algo == AlgoType.VWAP:
    slices = self._plan_vwap(total, dur, slice_m, start)
# ... 4 个 elif

# ✅ 重构后: 方法引用 dict
planners = {
    AlgoType.TWAP: self._plan_twap,
    AlgoType.VWAP: self._plan_vwap,
}
planner = planners.get(algo)
if planner is None:
    raise ValueError(f"不支持: {algo}")
slices = planner(total, dur, slice_m, start)
```

**参数签名不一致**时用 lambda dict(捕获局部变量做参数适配):

```python
# ✅ 参数签名不一致 + 含默认值处理(or)
algo_planners = {
    AlgoType.TWAP: lambda: self._plan_twap(total, dur, slice_m, start),
    AlgoType.POV: lambda: self._plan_pov(total, dur, slice_m, start, adv or 1_000_000),
    AlgoType.IS: lambda: self._plan_is(total, dur, slice_m, start, price or 0.0, vol or 0.25, risk),
}
planner = algo_planners.get(algo)
if planner is None:
    raise ValueError(f"不支持: {algo}")
slices = planner()
```

### 3.3 关键约束

1. **lambda 必须完全复制原调用的参数传递**,包括 `or` 默认值处理(`adv or 1_000_000`)。遗漏 = behavior change。
2. **配置表放方法内**(不放类级别):lambda 捕获 `self` + 局部参数,放方法内最简单。若非热路径,dict 重建开销可忽略。
3. **未知 key 快速失败**:`planner is None` 时 `raise ValueError`,保留原有的异常行为。

### 3.4 实际案例

| 函数 | 重构前 | 重构后 | 降幅 | 说明 |
|------|--------|--------|------|------|
| [plan_order](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/execution_algo_engine.py#L359) | 114 行 CC=19 | 93 行 CC=14 | -18% / -26% | 6 个 elif → lambda dict,含 3 处 `or` 默认值 |
| [ops_diagnoser._diagnose_from_health](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/layers/ops_diagnoser.py) | 154 行 | 49 行 | -68% | 5 段重复模板 → 规则配置表 `_OPS_HEALTH_RULES` |
| [strategy_diagnoser._diagnose_from_health](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/layers/strategy_diagnoser.py) | 100 行 | 53 行 | -47% | 3 段重复 → `_STRATEGY_HEALTH_RULES`(支持固定/条件两种严重度) |
| [choose_execution_algorithm](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/execution_selector.py#L81) | 129 行 | 86 行 | -33% | 提取 `_compute_adaptive_weights` + `_make_result` helper |

## 4. 模式 B:提取 helper(异构检查链)

### 4.1 适用场景

多个独立的检查/处理块,每个逻辑不同,输出结构不同。强行表驱动化会 behavior change。

### 4.2 实现方式:提取独立块为 helper

```python
# ❌ 重构前: 主函数含 Phase 1(L1 复用, 15 行, 3 个 if)
def _execution_risk_check(execution_plan, ..., risk_context, decision, ...):
    result = ExecutionRiskResult()
    checks = {}
    veto_reasons = []

    # Phase 1: 15 行 L1 复用逻辑(3 个 if)
    if risk_context is not None and decision is not None:
        risk_context.portfolio_value = portfolio_value
        risk_context.proposed_notional = float(execution_plan.get("notional", 0.0))
        if not risk_context.symbol:
            risk_context.symbol = execution_plan.get("symbol", decision.symbol)
        l1_result = run_hard_risk(decision, risk_context)
        for k, v in l1_result.risk_checks.items():
            checks[f"l1_{k}"] = v
        if l1_result.veto:
            result.veto = True
            veto_reasons.append(f"[L1] {l1_result.veto_reason}")

    # Phase 2: 5 个异构检查(checks 结构有 4 种变体, 不表驱动化)
    ...

# ✅ 重构后: Phase 1 提取为 helper
def _run_l1_checks(execution_plan, risk_context, decision, portfolio_value,
                   checks, veto_reasons, result) -> None:
    """Phase 1: 复用 L1 decision_gate.run_hard_risk()"""
    risk_context.portfolio_value = portfolio_value
    risk_context.proposed_notional = float(execution_plan.get("notional", 0.0))
    if not risk_context.symbol:
        risk_context.symbol = execution_plan.get("symbol", decision.symbol)
    l1_result = run_hard_risk(decision, risk_context)
    for k, v in l1_result.risk_checks.items():
        checks[f"l1_{k}"] = v
    if l1_result.veto:
        result.veto = True
        veto_reasons.append(f"[L1] {l1_result.veto_reason}")

def _execution_risk_check(execution_plan, ..., risk_context, decision, ...):
    result = ExecutionRiskResult()
    checks = {}
    veto_reasons = []

    # Phase 1: 简化为调用
    if risk_context is not None and decision is not None:
        _run_l1_checks(execution_plan, risk_context, decision, portfolio_value,
                       checks, veto_reasons, result)

    # Phase 2: 保持原样(异构, 不强行统一)
    ...
```

### 4.3 关键约束

1. **mutable state 修改顺序必须一致**:helper 接收 `checks`/`result`/`veto_reasons` 等可变对象,修改顺序必须与原块完全一致。
2. **只提取真正独立的块**:块内逻辑自成一体(如 Phase 1 是完整的 L1 复用流程),提取后主函数更清晰。
3. **不强行统一异构结构**:如果 5 个检查的 `checks` dict 有 4 种 key 结构(裸字符串/`{value,ok}`/`{value,mode,ok}`/`{value,max,ok}`),**保持原样**,不要为了"统一"而改变结构(会 behavior change)。

### 4.4 实际案例

| 函数 | 重构前 | 重构后 | 降幅 | 说明 |
|------|--------|--------|------|------|
| [_execution_risk_check](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/execution_bridge.py#L525) | 99 行 CC=17 | 90 行 CC=14 | -9% / -18% | 提取 Phase 1 为 `_run_l1_checks`;Phase 2 异构检查保持原样 |

## 5. 反模式

### 5.1 反模式:强行对异构检查链表驱动化

```python
# ❌ 5 个检查的 checks 结构有 4 种变体, 强行统一 = behavior change
CHECKS = [
    {"key": "market_state", "extract": ..., "ok": ...},  # 原来是裸字符串, 现在变 dict
    {"key": "notional", "extract": ..., "ok": ...},       # 原来有 max 字段, 现在丢了
]
# 测试会挂: checks["market_state"] 从 str 变成 dict
```

**正确做法**:异构 → 提取 helper,保留各自结构。

### 5.2 反模式:表驱动化时丢失默认值处理

```python
# ❌ 遗漏 `adv or 1_000_000` 默认值, adv=None 时 behavior change
AlgoType.POV: lambda: self._plan_pov(total, dur, slice_m, start, adv),

# ✅ 完整复制原调用的默认值处理
AlgoType.POV: lambda: self._plan_pov(total, dur, slice_m, start, adv or 1_000_000),
```

### 5.3 反模式:提取 helper 时改变 mutable state 顺序

```python
# ❌ helper 里先改 result.veto 再改 checks, 与原顺序相反
def _run_l1_checks(...):
    if l1_result.veto:
        result.veto = True              # 原来是最后改
        veto_reasons.append(...)
    for k, v in l1_result.risk_checks.items():
        checks[f"l1_{k}"] = v           # 原来是先改

# ✅ 保持原顺序: 先合并 checks, 再处理 veto
def _run_l1_checks(...):
    for k, v in l1_result.risk_checks.items():
        checks[f"l1_{k}"] = v
    if l1_result.veto:
        result.veto = True
        veto_reasons.append(...)
```

### 5.4 反模式:无测试覆盖就重构

```python
# ❌ 没有测试基线, 重构后无法验证零行为变更
# 先写测试或行为对比脚本, 再重构
```

## 6. 重构流程(参考样板)

1. **扫描定位**:运行 `python scripts/_scan_func_quality.py`,查看 HTML 报告 Top recommendation
2. **读代码判断**:按 §2 决策树判断是同构(→表驱动化)还是异构(→提取 helper)
3. **确认测试覆盖**:搜索调用点和测试文件,确认有测试覆盖
4. **跑测试基线**:重构前跑测试,记录全部 PASSED
5. **执行重构**:按 §3(表驱动化)或 §4(提取 helper)实现
6. **语法验证**:`python -c "import ast; ast.parse(open('<file>').read())"`
7. **跑测试验证**:重构后跑同样测试,必须全部 PASSED(零回归)
8. **跑扫描器确认**:`python scripts/_scan_func_quality.py --no-html`,确认 CC/行数下降
9. **更新 LOG**:在 `cairn/LOG.md` 顶部追加重构条目(含降幅/测试结果/模式)

## 7. 验证工具

| 工具 | 用途 |
|------|------|
| [scripts/_scan_func_quality.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/scripts/_scan_func_quality.py) | AST 扫描函数长度/圈复杂度/参数数,生成 HTML 卡片报告(三轴分类 Strong/Worth/Speculative) |
| `python -m pytest <测试文件> -v` | 跑测试验证零行为变更 |
| `python -c "import ast; ast.parse(open('<file>').read())"` | 语法检查 |

## 8. 三轴问题阈值

| 维度 | 阈值 | 说明 |
|------|------|------|
| 函数长度 | > 80 行 | 超长函数(扣装饰器行数) |
| 圈复杂度 | > 15 | 高复杂度(每个 if/for/while/except/with/assert/comprehension +1, BoolOp +len-1) |
| 参数数 | > 5 | 多参数(扣除 self/cls) |

**严重度分类**:
- **Strong**:三项都超标(最高优先重构)
- **Worth exploring**:两项超标
- **Speculative**:一项超标

目标:全项目 Strong 函数 = 0(已于 2026-08-03 达成)。

## 9. 踩坑记录

### 9.1 PowerShell 不支持 heredoc

contains: powershell-gotcha

执行多行 Python 代码时,`python << 'PY'` 在 PowerShell 报 "Missing file specification after redirection operator"。

**解决**:改用 `python -c "..."` 或先创建临时脚本文件再 `python <script>.py`。

### 9.2 Python 3.8 不支持 PEP 585 泛型类型注解

contains: py38-gotcha

Python 3.9+ 引入了 PEP 585，允许直接使用内置类型作为泛型注解（`list[str]`、`dict[str, int]`、`tuple[list[dict], int]` 等）。Python 3.8.9 运行时报 `TypeError: 'type' object is not subscriptable`。

**两种解决方案（按优先级）**:

**方案 A：显式替换为 `typing` 模块类型（推荐，无需 import 特殊处理）**

| PEP 585 (Py3.9+) | 替换为 (Py3.8 兼容) |
|---|---|
| `list[str]` | `List[str]` |
| `dict[str, int]` | `Dict[str, int]` |
| `tuple[list[dict], int]` | `Tuple[List[Dict], int]` |
| `set[str]` | `Set[str]` |
| `frozenset[str]` | `FrozenSet[str]` |
| `type[MyClass]` | `Type[MyClass]` |
| `collections.abc.Callable[[int], str]` | `Callable[[int], str]` |

文件顶部需要 `from typing import List, Dict, Tuple, Set, Callable, Type`。

**方案 B：`from __future__ import annotations`（所有类型注解延迟求值）**

文件第一行加 `from __future__ import annotations`，Python 3.8 会将所有类型注解视为字符串延迟求值，PEP 585 语法可直接使用。

**选择建议**: 单文件内只有少量 PEP 585 注解时用方案 A（改动小、语义清晰）；大量 PEP 585 注解时用方案 B（一次 import 全解决）。

### 9.3 扫描器误报率极高

contains: scanner-gotcha

AST 扫描器只能解决"找"的问题,不能解决"判"的问题:
- 除零风险扫描 1016 处 → 真实 7 处(误报率 99.3%)
- 静默异常扫描 368 处 → 实际 0 处(误报率 100%)
- 宽泛异常扫描 1335 处 → 真实 1009 处(误报率 24%)

**解决**:扫描器做粗筛,人工(或 AI)做精核。HTML 报告的 Top recommendation 是起点,不是终点 — 必须读代码确认建议是否贴合实际(如 plan_order 的"略长"建议偏弱,"表驱动化"建议精准)。

## 10. 相关文档

- [cairn/exception-handling-standards.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/exception-handling-standards.md) — 异常处理规约(本文档的姊妹篇,同为代码质量加固规范)
- [cairn/LOG.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/LOG.md) — 2026-08-03 表驱动化重构 + Phase 1 提取条目

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [异常处理规约 (Exception Handling Standards)](exception-handling-standards.md) (相似度 28%)
- [daily_workflow.py 拆分计划（门禁 ≤3000 行 · 长期架构重构）](daily-workflow-split-plan.md) (相似度 18%)
- [W6.3.3 预研 · QS-Trader 风格 secid 合约解析难点清单](w633_secid_contract_parsing_challenges.md) (相似度 17%)
- [因子发现 Loop Engineering 升级方案](factor-discovery-loop-engineering.md) (相似度 9%)
- [daily_workflow.py 拆分复盘报告 (5 轮 · 2026-08-12)](daily-workflow-split-retrospective.md) (相似度 8%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
