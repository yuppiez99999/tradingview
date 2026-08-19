# DQC Phase 2 + Wave 3 第四阶段 实现计划

## Context

两个 P1 任务并行推进：
1. **DQC Phase 2** — 在 Phase 1（P2 检查点 + 四维指标）基础上，补齐 P3 检查点 + F 维度（分布稳定性）+ X 维度（跨源校验），形成完整的"六维×五检查点"数据质量监控体系。当前 `utils/dqc/metrics/__init__.py` 注释明确标注 F 维度"Phase 2 接入"、X 维度"Phase 3 实现"。
2. **Wave 3 第四阶段** — PRINT 清零 + ruff BLE001 门禁增强。当前 `ruff.toml` 的 `select` 未启用 "T"（flake8-print）和 "BLE"（flake8-blind-except）规则族，导致 6000+ print() 和 250+ `except Exception` 无门禁拦截。

---

## 任务 1: DQC Phase 2

### 复用点（已确认存在）

| 复用对象 | 位置 | 用途 |
|---|---|---|
| `compute_psi()` 模块级函数 | `utils/alpha/drift_monitor.py:529` | F-01 因子 PSI 计算（工业级阈值 <0.1/0.1-0.25/0.25-0.5/≥0.5） |
| P2 检查点架构模板 | `utils/dqc/checkpoints/p2_cache_quality.py` | P3FactorQualityGate 的类结构、_publish/_is_gate_enabled 模式 |
| metrics 实现模板 | `utils/dqc/metrics/completeness.py` | check_xxx() → list[DQCEvent] 模式，make_event() 便捷构造 |
| DQC 事件类型 | `utils/dqc/event_types.py` | DQCLevel/DQCCheckpoint/DQCMetric 枚举（X/F 维度已定义） |
| AlertAggregator | `utils/dqc/aggregator.py` | 告警聚合（should_emit 抑制 + 升级） |

### 新建文件

#### 1.1 `utils/dqc/metrics/distribution.py` — F 维度分布稳定性

```python
def check_distribution_drift(
    baseline_df: pd.DataFrame,
    current_df: pd.DataFrame,
    factor_cols: list[str],
    checkpoint: DQCCheckpoint = DQCCheckpoint.P3_FACTOR,
) -> list[DQCEvent]:
    """F 维度检查入口: F-01~F-04"""
```

子检查：
- **F-01 因子 PSI**: 调用 `compute_psi(baseline, current)`，阈值 <0.1 INFO / 0.1-0.25 WARN / 0.25-0.5 ERROR / ≥0.5 CRITICAL
- **F-02 均值漂移**: `|mean_cur - mean_base| / std_base`，阈值 >0.5 WARN / >1.0 ERROR
- **F-03 方差漂移**: `std_cur / std_base` 比率，阈值 <0.5 或 >2.0 WARN / <0.25 或 >4.0 ERROR
- **F-04 极值频率**: 当前分布超出基线 3σ 的比例，阈值 >5% WARN / >10% ERROR

#### 1.2 `utils/dqc/metrics/consistency.py` — X 维度跨源校验

```python
def check_consistency(
    df: pd.DataFrame,
    cross_source_df: Optional[pd.DataFrame] = None,
    history_cache: Optional[pd.DataFrame] = None,
    checkpoint: DQCCheckpoint = DQCCheckpoint.P2_CACHE,
) -> list[DQCEvent]:
    """X 维度检查入口: X-01~X-03, X-05"""
```

子检查：
- **X-01 跨源价格偏差**: `|price_A - price_B| / price_B`，阈值 <0.1% INFO / 0.1-1% WARN / >1% ERROR（cross_source_df 为 None 时跳过）
- **X-02 跨源成交量偏差**: `|vol_A - vol_B| / vol_B`，阈值 <1% INFO / 1-5% WARN / >5% ERROR
- **X-03 历史值不变性**: 对比今日读取的历史数据与昨日缓存，任何历史值变更即 ERROR（HC-DQC3 硬约束）
- **X-05 指数成分股一致**: 成分股列表对比（简化版，检查标的池是否变化）

#### 1.3 `utils/dqc/checkpoints/p3_factor_quality.py` — P3 检查点

```python
class P3FactorQualityGate:
    """P3 检查点: 因子质量门禁 (训练样本生成前)"""

    FLAG_USE_P3_GATE = "USE_DQC_P3_GATE"  # 默认 False

    def run(
        self,
        target_date: date,
        factor_df: pd.DataFrame,
        baseline_df: pd.DataFrame,
        factor_cols: list[str],
        symbols: Optional[list[str]] = None,
    ) -> tuple[bool, list[DQCEvent]]:
        """执行 P3 检查: F 维度(分布稳定性) + U-02(因子重复) + X-04(可复现性)"""
```

架构复用 P2 模式：
- `BLOCKING_LEVELS = {DQCLevel.ERROR, DQCLevel.CRITICAL}`
- `_publish()` → RiskBus + 审计日志（复用 P2 的实现）
- `_is_gate_enabled()` → Feature Flag USE_DQC_P3_GATE
- fail-safe: DQC 自身异常降级为通过（HC-DQC4）

### 更新文件

#### 1.4 `utils/dqc/metrics/__init__.py`
- 导出 `check_distribution_drift`, `check_consistency`
- 更新注释（移除 "Phase 2 接入"/"Phase 3 实现" 标注）

#### 1.5 `utils/dqc/__init__.py`
- 导出 `P3FactorQualityGate`, `run_p3_gate`
- 更新 docstring（Phase 2 完成）

### 接入策略
- P3 先实现为**独立模块**（与 P2 一致），不接入 PipelineOrchestrator
- 后续 P2/P3 一起接入流水线（Feature Flag 默认 False，观察模式）

---

## 任务 2: Wave 3 第四阶段

### 2.1 ruff.toml 配置增强

**当前状态**: `select = ["E","F","W","B","C90","I","N","UP","ANN"]` — 未启用 T（flake8-print）和 BLE（flake8-blind-except）

**修改**:
```toml
[lint]
select = [
    "E", "F", "W", "B", "C90", "I", "N", "UP", "ANN",
    "T",    # flake8-print (T201 print, T203 pprint) — PRINT 清零门禁
    "BLE",  # flake8-blind-except (BLE001 except Exception) — 宽泛 except 门禁
]

[lint.per-file-ignores]
# 已有规则保持不变，新增:
"cli/**/*.py" = ["T201"]              # CLI 工具允许 print (用户交互)
"scripts/**/*.py" = ["T201", "BLE001"] # 脚本放宽
"tests/**/*.py" = ["T201", "BLE001"]   # 测试放宽
# 根目录入口脚本保留 print (待后续逐文件清理)
"量化策略系统_统一入口_v8.6.py" = ["T201"]  # 大文件, 后续单独清理
```

### 2.2 PRINT 清零（门禁范围内）

**范围**: 根目录 *.py + utils/ + v8.3_institutional/src/（pre-commit ruff 门禁范围）

**策略**:
1. 高密度文件优先（top 5）:
   - `today_hedge_decision.py` (108) — 改为 `logger.info/warning`
   - `hedge_quantity_calculator.py` (101) — 改为 `logger.info`
   - `hedge_execution_orders.py` — 改为 `logger.info`
   - `daily_trade_executor.py` — 改为 `logger.info`
   - `system_integration.py` — 改为 `logger.info`
2. 标准替换模式:
   ```python
   # 文件顶部确保有 logger
   import logging
   logger = logging.getLogger(__name__)
   # print("xxx") → logger.info("xxx")
   # print(f"警告: {x}") → logger.warning("警告: %s", x)
   ```
3. 大文件豁免: `量化策略系统_统一入口_v8.6.py` (158 个 print) 用 per-file-ignores 豁免，后续单独清理

### 2.3 BLE001 门禁增强

**当前状态**: 250+ 个 `except Exception`，部分已带 `# noqa: BLE001`

**策略**:
1. 启用 BLE 规则后，逐文件审查 `except Exception`
2. 合法 fail-safe 分支（交易路径不崩溃）: 加 `# noqa: BLE001  # fail-safe, 交易路径不崩溃`
3. 应精确化的: 改为具体异常类型（如 `except (ValueError, KeyError) as e:`）
4. 已有 `# noqa: BLE001` 的保持不变
5. utils/ 和 v8.3_institutional/src/ 优先处理（核心交易路径）

---

## 验证方案

### DQC Phase 2 验证
```bash
# 1. 语法验证
python -m py_compile utils/dqc/metrics/distribution.py utils/dqc/metrics/consistency.py utils/dqc/checkpoints/p3_factor_quality.py

# 2. import 验证
python -c "from utils.dqc import run_p3_gate, P3FactorQualityGate; from utils.dqc.metrics import check_distribution_drift, check_consistency; print('OK')"

# 3. 功能验证 (PSI 计算 + P3 门禁)
python -c "
import pandas as pd
from datetime import date
from utils.dqc import run_p3_gate
baseline = pd.DataFrame({'factor_1': [1,2,3,4,5,6,7,8,9,10]})
current = pd.DataFrame({'factor_1': [1,2,3,4,5,6,7,8,9,10]})  # 相同分布
passed, events = run_p3_gate(date(2026,8,4), current, baseline, ['factor_1'])
print(f'P3 passed={passed}, events={len(events)}')
"
```

### Wave 3 第四阶段验证
```bash
# 1. ruff 规则验证 (确认 T201/BLE001 已启用)
ruff check --select T201,BLE001 utils/dqc/ --statistics

# 2. PRINT 清零验证 (门禁范围内 0 个 print)
ruff check --select T201 today_hedge_decision.py hedge_quantity_calculator.py

# 3. pre-commit 全量验证
pre-commit run ruff --all-files
```

---

## 文件清单

### 新建 (3 个)
- `utils/dqc/metrics/distribution.py` — F 维度分布稳定性
- `utils/dqc/metrics/consistency.py` — X 维度跨源校验
- `utils/dqc/checkpoints/p3_factor_quality.py` — P3 检查点

### 更新 (2 个 DQC + 1 个 ruff + N 个 PRINT 清零)
- `utils/dqc/metrics/__init__.py` — 导出新指标
- `utils/dqc/__init__.py` — 导出 P3 接口
- `ruff.toml` — select 添加 T/BLE + per-file-ignores
- `today_hedge_decision.py` / `hedge_quantity_calculator.py` 等 — print→logger

### 硬约束遵守
- HC-DQC3: 历史数据只标记不修改（X-03 检测变更但不修改）
- HC-DQC4: DQC 自身失败 fail-safe（P3 异常降级为通过）
- USE_DQC_P3_GATE 默认 False（观察模式，不阻断）
- 所有异常处理用具体类型（符合 project memory 规范）
