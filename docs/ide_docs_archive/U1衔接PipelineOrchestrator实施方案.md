# U1 衔接 PipelineOrchestrator 实施方案

## Context

**背景**：U1 升级（08-05 完成）在 [utils/alpha_factor/base.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha_factor/base.py) 新增了 `calc_ic_series_from_history` + `calc_ic_ir` + `evaluate_factors` 双模式（时序优先，降级单点 IC），21 个单元测试全通过。但**新函数目前没有任何业务代码调用**：
- `evaluate_factors` 在 [library.py:180](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha_factor/library.py#L180) 被调用时只传 2 个参数，走降级单点 IC 模式，时序能力闲置
- research 版 PipelineOrchestrator 用独立的 `compute_rolling_ic_series`（Pearson）+ `compute_ic_ir`，与 U1 函数算法 1:1 一致但实现独立

**目标**：激活 U1 时序 IC/ICIR 能力，统一算法源，让 `portfolio_optimizer.run_offline_pipeline` 的 Shadow 审批流程用上 U1 新函数。

**用户决策**（08-05 确认）：
1. Pearson → Spearman：接受风险直接切换（U1 已验证符号一致率 91.67%-100%）
2. factor_history 数据流：方案 1，PipelineResult 暴露 factor_history 字段

---

## 实施方案（路径 A+B 组合）

### 阶段 A：library 接入时序模式（低风险，~1.5h）

#### A1. 修改 `utils/alpha_factor/library.py` 的 `compute_all` 签名

[line 99-108](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha_factor/library.py#L99) 增加两个可选参数：

```python
def compute_all(
    self,
    price_data: dict[str, dict[str, list[float]]],
    fundamentals: dict[str, dict[str, float]] | None = None,
    industries: dict[str, str] | None = None,
    benchmark_returns: list[float] | None = None,
    fundamentals_prev: dict[str, dict[str, float]] | None = None,
    technical_selected_ids: list[str] | None = None,
    graph=None,
    factor_history: dict[str, list[dict[str, float]]] | None = None,        # U1 新增
    forward_returns_history: list[dict[str, float]] | None = None,           # U1 新增
) -> FactorLibraryResult:
```

#### A2. 修改 evaluate_factors 调用点

- [line 180](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha_factor/library.py#L180): `evaluate_factors(result, price_data)` → `evaluate_factors(result, price_data, factor_history=factor_history, forward_returns_history=forward_returns_history)`
- [line 221](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha_factor/library.py#L221): 同样修改（`_evaluate_factors` 方法，向后兼容委托）

**零行为变更保证**：`evaluate_factors` 在 [base.py:316-321](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha_factor/base.py#L316) 已实现 `use_timeseries` 检查，`factor_history=None` 时走降级单点 IC，与现有行为完全一致。

---

### 阶段 B：research 版统一算法源（中风险，~2h）

#### B1. 修改 import

[research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py:35-40](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py#L35):

```python
# 保留: build_factor_history, compute_ic_decay
from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history,
    compute_ic_decay,
)
# 新增: U1 统一算法源
from utils.alpha_factor.base import calc_ic_series_from_history, calc_ic_ir
```

#### B2. 替换 IC 计算调用（8 处）

| 行号 | 原调用 | 替换为 |
|---|---|---|
| L678 | `compute_rolling_ic_series(factor_history, forward_returns_history)` | `calc_ic_series_from_history(factor_history, forward_returns_history)` |
| L679 | `compute_ic_ir(ic_series, min_periods=20)` | `calc_ic_ir(ic_series, min_periods=20)` |
| L1534 | `compute_rolling_ic_series(hist_a, fwd_returns)` | `calc_ic_series_from_history(hist_a, fwd_returns)` |
| L1535 | `compute_rolling_ic_series(hist_b, fwd_returns)` | `calc_ic_series_from_history(hist_b, fwd_returns)` |
| L1538 | `compute_ic_ir(ic_series_a)` | `calc_ic_ir(ic_series_a)` |
| L1539 | `compute_ic_ir(ic_series_b)` | `calc_ic_ir(ic_series_b)` |
| L1551 | `compute_rolling_ic_series(combined_history, fwd_returns)` | `calc_ic_series_from_history(combined_history, fwd_returns)` |
| L1552 | `compute_ic_ir(combined_ic_series)` | `calc_ic_ir(combined_ic_series)` |

**保留**：L682 和 L1553 的 `compute_ic_decay`（U1 无对应函数）。

**返回值兼容性**：U1 的 `calc_ic_ir` 返回 `(ic_ir, ic_mean, ic_std)` 三元组，与 research 版 `compute_ic_ir` 完全一致，无需修改解包代码。

#### B3. PipelineResult 暴露 factor_history 字段

[pipeline_orchestrator.py:180-202](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py#L180) 的 `PipelineResult` dataclass 增加两个字段：

```python
@dataclass
class PipelineResult:
    # ... 现有字段 ...
    factor_combinations: list[dict[str, Any]] = field(default_factory=list)
    # U1 衔接新增: 暴露 factor_history 供下游 portfolio_optimizer 使用
    factor_history: dict[str, list[dict[str, float]]] = field(default_factory=dict)
    forward_returns_history: list[dict[str, float]] = field(default_factory=list)
```

#### B4. run() 方法写入 factor_history 到 result

[pipeline_orchestrator.py:395-410](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py#L395) 在 `build_factor_history` 调用后，把结果写入 `result`：

```python
factor_history, fwd_returns_hist, valid_dates = build_factor_history(...)
result.factor_history = factor_history                    # U1 衔接
result.forward_returns_history = fwd_returns_hist         # U1 衔接
```

**注意**：`to_dict()` 用 `asdict()`（line 205），新增字段会自动序列化，但 factor_history 可能很大（120 天 × N 因子 × M 标的），需确认审计日志体积。可选：`to_dict()` 排除 factor_history 字段。

---

### 阶段 AB：数据流衔接（~1h）

#### AB1. portfolio_optimizer 取用 factor_history

[utils/portfolio_optimizer.py:460-474](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/portfolio_optimizer.py#L460) 调用 `orchestrator.run()` 后，从 `result.factor_history` 取用，传给 `AlphaFactorLibrary.compute_all`：

在 Step 4（line 522-533）计算最新因子值时，调用 `VibeTradingFactorAdapter` 之后，可选用 `AlphaFactorLibrary.compute_all` 评估因子有效性：

```python
# Step 4.5 (新增, 可选): 用 U1 时序 IC 评估因子有效性
if result.factor_history:
    from utils.alpha_factor.library import AlphaFactorLibrary
    lib = AlphaFactorLibrary()
    lib_result = lib.compute_all(
        price_data=price_data,
        fundamentals=fundamentals,
        factor_history=result.factor_history,
        forward_returns_history=result.forward_returns_history,
    )
    logger.info(
        "[PortfolioOptimizer] U1 时序 IC 评估: strong=%d effective=%d",
        len(lib_result.strong_factors), len(lib_result.effective_factors),
    )
```

**注**：这一步是可选的因子有效性评估，不影响现有 Step 4-6 的信号生成流程。主要价值是激活 U1 的 `evaluate_factors` 时序模式，让强因子/有效因子列表基于时序 IC_IR 而非单点 IC。

---

### 阶段 C：测试验证（~1h）

#### C1. 单元测试：library 层接入 factor_history

新增 `tests/unit/test_u1_library_integration.py`：
- 测试 `compute_all` 传入 factor_history 时走时序模式（fval.ic_ir 被填充）
- 测试 `compute_all` 不传 factor_history 时走降级单点 IC（向后兼容）
- 测试 factor_history 部分因子有历史部分无时的混合模式

#### C2. 集成测试：research 版 IC 函数替换

新增 `tests/unit/test_u1_research_pipeline_integration.py`：
- 用固定 price_data + factor_history 跑一次 `compute_rolling_ic_series` 和 `calc_ic_series_from_history`，验证结果一致（IC 序列长度相同，符号一致率 >=90%）
- 验证 `compute_ic_ir` 和 `calc_ic_ir` 同输入同输出

#### C3. Shadow 审批一致性验证（手动）

跑一次 `portfolio_optimizer.run_offline_pipeline()`，对比替换前后的：
- `combined_ic_ir`（应符号一致，数值差异 <10%）
- `live_dsr`（应同号）
- Shadow 通过/拒绝结果（应一致）

**回滚条件**：如果 Shadow 审批结果从通过变为拒绝（或反之），回滚 B 阶段，保留 A 阶段。

---

## 关键文件清单

| 文件 | 修改内容 | 风险 |
|---|---|---|
| `utils/alpha_factor/library.py` | A1+A2: compute_all 增加 2 参数 + 2 处 evaluate_factors 调用 | 低（零行为变更） |
| `research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py` | B1+B2+B3+B4: import + 8 处调用替换 + PipelineResult 字段 + run() 写入 | 中（Pearson→Spearman） |
| `utils/portfolio_optimizer.py` | AB1: 新增 Step 4.5 可选评估 | 低（可选步骤） |
| `tests/unit/test_u1_library_integration.py` | C1: 新建 | — |
| `tests/unit/test_u1_research_pipeline_integration.py` | C2: 新建 | — |

---

## 验证步骤

1. **单元测试**：`py -3.8 -m pytest tests/unit/test_u1_library_integration.py tests/unit/test_u1_research_pipeline_integration.py -v`
2. **U1 回归**：`py -3.8 -m pytest tests/unit/test_u1_ic_series.py -v`（确保 U1 原有测试不回归）
3. **Shadow 审批一致性**：`py -3.8 -c "from utils.portfolio_optimizer import PortfolioOptimizer; PortfolioOptimizer().run_offline_pipeline()"`，检查日志中 `combined_ic_ir` 和 `live_dsr`
4. **E2E 回归**：`py -3.8 -m pytest tests/e2e/test_full_pipeline_e2e.py tests/e2e/test_shadow_account_lifecycle_e2e.py -v`

---

## 风险与回滚

| 风险 | 等级 | 缓解 |
|---|---|---|
| Pearson→Spearman 导致 Shadow 审批结果翻转 | 🟡 中 | C3 手动验证；若翻转则回滚 B 阶段 |
| PipelineResult.to_dict() 体积膨胀 | 🟢 低 | factor_history 120 天×N 因子，可接受；必要时 to_dict() 排除 |
| library.compute_all 向后兼容 | 🟢 低 | evaluate_factors 已实现降级逻辑，factor_history=None 时行为不变 |

**回滚策略**：阶段 A 和 B 独立，A 不依赖 B。若 B 出问题可单独回滚 research 版改动，保留 library 层接入。
