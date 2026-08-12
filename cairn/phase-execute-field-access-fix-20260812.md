---
type: bug_fix_report
status: closed
authoring_mode: ai_generated
created: 2026-08-12
related:
  - cairn/LOG.md
  - cairn/daily-workflow-split-retrospective.md
  - cairn/refactoring-standards.md
---

# phase_execute 拆单字段误用修复验证报告（2026-08-12）

> 专题文档：EOD 干跑暴露的 `ExecutionSlice` / `ExecutionPlan` 字段名误用问题的根因分析、修复范围与回归验证。
> 对应 LOG 指针：2026-08-12 · ExecutionSlice/ExecutionPlan 字段名笔误修复 · 完成 ✅
> contains: dataclass-field-typo, attribute-access-without-type-check, eod-dry-run-verified

## 0. 背景与触发

2026-08-12 EOD 干跑验证（`tests/e2e/test_eod_dry_run.py`）在 `phase_execute` 阶段暴露连续 3 条警告：

```
[WARNING] [ExecAlgo] sh510300 拆单失败: 'ExecutionSlice' object has no attribute 'shares'
[WARNING] [ExecAlgo] sh510500 拆单失败: 'ExecutionSlice' object has no attribute 'shares'
[WARNING] [ExecAlgo] sz518880 拆单失败: 'ExecutionSlice' object has no attribute 'shares'
```

3 个标的的 VWAP 拆单全部失败，但 `phase_execute` 因 `try/except` 降级仍返回 PASS，导致问题被日志吞没。EOD 干跑 verdict=PASS（ERROR=0）掩盖了此处的功能性缺陷。

修复首个字段后，二次干跑暴露同源的第二类字段误用：

```
[WARNING] [ExecAlgo] sh510300 拆单失败: 'ExecutionPlan' object has no attribute 'estimated_total_cost'
```

排查确认：`daily_workflow.py` 的 `phase_execute` 拆单分支对 `utils/execution_algo_engine.py` 中两个 dataclass 的字段名存在系统性记忆偏差，共 **3 类字段、20 处误用**。

## 1. 根因分析

### 1.1 字段误用对照

| 类 | 误用字段名 | 正确字段名 | 语义 | 误用处数 |
|---|---|---|---|---|
| `ExecutionSlice` | `.shares` | `.target_shares` | 时间片目标下单股数 | 8 |
| `ExecutionPlan` | `.estimated_total_cost` | `.expected_cost` | 预估总成本 | 6 |
| `ExecutionPlan` | `.estimated_slippage_bps` | `.expected_slippage_bps` | 预估滑点 (bps) | 6 |

### 1.2 dataclass 字段定义（`utils/execution_algo_engine.py` L298-L336）

```python
@dataclass
class ExecutionSlice:
    slice_idx: int
    start_time: str
    end_time: str
    target_shares: int              # ✅ 正确
    accumulated_shares: int
    remaining_shares: int
    participation_rate: float = 0.0
    limit_price: float | None = None

@dataclass
class ExecutionPlan:
    # ... 其他字段 ...
    expected_vwap: float | None = None
    expected_slippage_bps: float = 0.0   # ✅ 正确
    expected_cost: float = 0.0           # ✅ 正确
    risk_aversion: float = 0.0
    notes: str = ""
    created_at: str = ""
```

### 1.3 根因类型

**字段名记忆偏差**（非拼写错误）：开发者凭记忆访问 dataclass 字段，未对照定义。`.shares` 是 Order/Fill/Signal 等其他交易类的通用字段名，与 `ExecutionSlice.target_shares` 语义重叠但名称不同；`.estimated_*` 前缀在 TCA 模块（`tca_pre_trade_estimator.py`）中存在，与 `ExecutionPlan.expected_*` 命名风格冲突。

## 2. 修复范围

### 2.1 修复点分布

`v8.3_institutional/daily_workflow.py` L1326-L1500，`phase_execute` 的 **4 个拆单分支**（大单/小单 × 上午/下午批次）：

| 分支 | 行号范围 | 字段误用数 |
|---|---|---|
| 大单 · 上午批次 | L1326-L1341 | 5（`.shares`×2 + `.estimated_total_cost`×1 + `.estimated_slippage_bps`×2） |
| 小单 · 上午批次 | L1360-L1376 | 5（同上模式） |
| 大单 · 下午批次 | L1450-L1467 | 5（同上模式） |
| 小单 · 下午批次 | L1483-L1500 | 5（同上模式） |
| **合计** | — | **20** |

### 2.2 修复方式

字段名更正，零逻辑改动：

```python
# 修复前
"first_slice_shares": plan.slices[0].shares if plan.slices else 0,
"last_slice_shares": plan.slices[-1].shares if plan.slices else 0,
"est_total_cost": plan.estimated_total_cost,
"est_slippage_bps": plan.estimated_slippage_bps,
plan.estimated_slippage_bps, plan.estimated_total_cost,

# 修复后
"first_slice_shares": plan.slices[0].target_shares if plan.slices else 0,
"last_slice_shares": plan.slices[-1].target_shares if plan.slices else 0,
"est_total_cost": plan.expected_cost,
"est_slippage_bps": plan.expected_slippage_bps,
plan.expected_slippage_bps, plan.expected_cost,
```

## 3. 回归验证

### 3.1 静态检查

| 项 | 结果 |
|---|---|
| `python -m py_compile v8.3_institutional/daily_workflow.py` | ✅ OK |
| `Grep \.shares\b / estimated_total_cost / estimated_slippage_bps` on daily_workflow.py | ✅ 0 匹配（全清零） |

### 3.2 EOD 干跑（`tests/e2e/test_eod_dry_run.py`）

**修复前**（2026-08-12 18:12）：

```
[ExecAlgo] VWAP BUY sh510300 50000 股 → 8 片, 预估滑点 2.2bps
[WARNING] [ExecAlgo] sh510300 拆单失败: 'ExecutionSlice' object has no attribute 'shares'
[WARNING] [ExecAlgo] sh510500 拆单失败: 'ExecutionSlice' object has no attribute 'shares'
[WARNING] [ExecAlgo] sz518880 拆单失败: 'ExecutionSlice' object has no attribute 'shares'
[ExecAlgo] 共生成 3 个拆单计划          ← 仅生成计划, 0 个成功记录元数据
```

**修复后**（2026-08-12 18:18）：

```
[ExecAlgo] VWAP BUY sh510300 50000 股 → 8 片, 预估滑点 2.2bps
[ExecAlgo] sh510300 拆单: VWAP -> 8 slices (slippage=2.2bps, cost=45)
[ExecAlgo] VWAP BUY sh510500 15000 股 → 8 片, 预估滑点 1.2bps
[ExecAlgo] sh510500 拆单: VWAP -> 8 slices (slippage=1.2bps, cost=12)
[ExecAlgo] VWAP BUY sz518880 10000 股 → 8 片, 预估滑点 1.0bps
[ExecAlgo] sz518880 拆单: VWAP -> 8 slices (slippage=1.0bps, cost=6)
[ExecAlgo] 共生成 6 个拆单计划          ← 3 标的全部成功, 元数据完整
```

**EOD 干跑汇总**：

| 项 | 修复前 | 修复后 |
|---|---|---|
| verdict | PASS | PASS |
| exit_code | 0 | 0 |
| ERROR 数 | 0 | 0 |
| 拆单成功数 | 0/3 | **3/3** |
| 拆单元数据记录 | 缺失 | 完整（slippage + cost） |

### 3.3 单元测试

```
python -m pytest tests/unit/test_daily_workflow_unit.py tests/unit/test_phase_hedge_sim_branch.py -q
.............................                                            [100%]
29 passed in 1.82s
```

**29/29 全绿**，无新增回归。

## 4. 影响评估

### 4.1 功能影响

| 影响面 | 修复前 | 修复后 |
|---|---|---|
| `phase_execute` 拆单元数据 | `execution_plans` 列表中 `first_slice_shares`/`last_slice_shares`/`est_total_cost`/`est_slippage_bps` 全为 AttributeError 后的缺失值 | 4 个字段正确填充 |
| 拆单计划 JSON 落盘 | 计划文件生成但元数据不完整 | 元数据完整 |
| `phase_report` 报告 | 拆单统计缺失 | 拆单统计准确 |
| 实盘执行 | 拆单计划元数据缺失可能影响下游 broker 接口 | 元数据完整，下游可用 |

### 4.2 非功能影响

- **零行为变更**：仅字段名更正，无逻辑改动
- **无性能影响**：字段访问开销不变
- **无 API 变更**：`ExecutionSlice` / `ExecutionPlan` 定义未动

## 5. 教训与后续建议

### 5.1 教训

1. **try/except 吞没问题**：`phase_execute` 的 `try/except Exception` 降级使 3 个标的拆单失败仅记 WARNING，未触发 phase FAIL。EOD 干跑 verdict=PASS 掩盖了功能性缺陷。**降级语义应有计数阈值**（如失败率 >50% 升级为 FAIL）。

2. **dataclass 字段访问无静态保护**：Python 动态特性使 `plan.shares` 在运行时才暴露 AttributeError，IDE/mypy 未能在编写期拦截。

3. **字段命名不一致是误用温床**：项目中 `shares`/`target_shares`/`total_shares`/`executed_shares`/`remaining_shares`/`accumulated_shares` 多种命名并存，`estimated_*`/`expected_*` 前缀混用，记忆负担大。

### 5.2 后续建议

| 建议 | 优先级 | 落地点 |
|---|---|---|
| 引入 mypy/pyright 对 dataclass 字段静态检查 | 中 | `.github/workflows/quality-gate.yml` 增量门禁 |
| 统一执行模块字段命名规范（`expected_*` 或 `estimated_*` 二选一） | 低 | `cairn/refactoring-standards.md` 补充命名规约 |
| `phase_execute` 拆单失败计数阈值降级升级 | 中 | `daily_workflow.py` L1342 `except` 块改造 |
| EOD 干跑增加拆单成功率断言 | 中 | `tests/e2e/test_eod_dry_run.py` Step 2 增加 `execute` phase 结果校验 |

## 6. 关联文档

- `cairn/LOG.md` 2026-08-12 两条目（EOD 干跑验证 + 字段名笔误修复）
- `cairn/daily-workflow-split-retrospective.md`（5 轮拆分复盘，phase_execute 为拆分后门面转发点）
- `cairn/refactoring-standards.md` §1 零行为变更底线
- `utils/execution_algo_engine.py` L298-L336（dataclass 定义源头）
- `v8.3_institutional/daily_workflow.py` L1326-L1500（修复点）
- `tests/e2e/test_eod_dry_run.py`（验证脚本）
- `eod_dry_run_summary_20260812.json`（验证结果摘要）
