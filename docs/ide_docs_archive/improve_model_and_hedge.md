# 改进模型与对冲完善实施计划

## Context

### 问题背景
当前系统已实现双门禁机制 (ReturnExpectationGate + HedgeCompletenessGate),但门禁的作用是"拦截"而非"促进达标"。用户的真实目标是:**改进模型预测和对冲执行,让指标真正达到 V9 基线,使门禁自然通过,可接入实盘**。

### 缺口诊断 (调研已验证)

**模型层缺口** (predict 4.79% vs V9 基线 19.62%):
- `predict_annual_return.py` 完全静态硬编码,与 V9/Pipeline/Shadow 零耦合
- 20 只标的的 bull/base/bear 收益是手工填的 (如海光信息 base=0.22),权益加权 base 收益仅 0.066
- `phase_factor=0.70` + `build_ratio=0.40` 双重保守折扣
- V9 模型全链路被 5 个 Feature Flag 关闭,signal_post_processing.py 路径 Bug 指向不存在的 7.1 目录

**对冲层缺口** (beta=0.798, net_delta=0.798):
- VIX 硬编码 18.5,VolHedger (vix_trigger=30) 永远不触发 → 无期权对冲订单
- BetaHedger beta_trigger=0.7,组合 Beta 在 0.3-0.7 之间不触发期货空头
- VolHedger 期权预算过低 (budget_low_pct=0.003,5M 组合仅 15,000,远低于需要的 300,000+)
- `hedge_coordinator.py:113` 使用 `np.isfinite()` 但无 `import numpy as np`
- `coordinate()` 调用未传 `hwm_drawdown`/`bs_loss`,TailRiskHedger 4 状态机判定不完整

### 数学达标条件

从 `test_gate_manager.py:205-226` 已验证的通过用例反推:
- **期货空头 notional ≥ (beta_before − 0.30) × portfolio_value**,把 beta 压到 ≤ 0.30
- **期权 negative delta ≥ portfolio_beta_after × portfolio_value − 0.05 × portfolio_value**,把 |net_delta| 压到 < 0.05

**predict 达标的数学路径**:
- V9 回测年化 19.62% 是对权益资本的收益率
- 权益占比 60% (3M/5M) → 对总资本贡献上限 ≈ 11.8%
- 加上期权 theta 净收入 (+2.43%) + 现金收益 (+0.16%) - 交易成本 (-0.5%) - 期货拖累 (-0.12%)
- **expected_return ≈ 13.8%**,若 `phase_factor=1.0` (建仓完成) 则 `phase_adjusted_return ≈ 13.8%`
- 要达到 15%+,需权益占比提升到 70%+ (3.5M/5M) 或降低对冲成本

### 用户决策
1. **实施范围**: 先做阶段 0+1 (模型优先),验证后再做阶段 2 (对冲改进)
2. **V9 信号来源**: 用 Shadow 基线兜底 (读 `config/shadow_account_config.json` 的 `backtest_benchmark.annual_return=0.1962`)
3. **对冲模式**: 切换到 MIXED 模式 (启用期货空头+期权保护) — 阶段 2 实施

---

## 阶段 0: 修复阻塞性 Bug (P0,无 Feature Flag,直接修)

**目标**: 修复 4 个让 V9 链路和对冲链路"形同虚设"的硬 Bug。默认行为等价于现状,2321 单测 + 71 集成测不受影响。

### 0.1 修复 hedge_coordinator.py 的 np.isfinite Bug
- **文件**: [hedge_coordinator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/src/hedging/hedge_coordinator.py)
- **修改**: L17-21 import 区域加 `import numpy as np` (对齐 [beta_hedger.py:22](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/src/hedging/beta_hedger.py#L22) 的 import 模式)
- **风险**: 零。当前触发该分支会抛 NameError,被 daily_workflow.py 的 `except Exception` 吞掉,导致 coordinated 落入 ERROR 分支
- **验证**: 新增 `tests/unit/test_hedge_coordinator_np_bug.py`,构造 returns 全 NaN 的 DataFrame,断言不抛异常

### 0.2 修复 signal_post_processing.py 路径 Bug
- **文件**: [signal_post_processing.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/signal_post_processing.py)
- **修改**: L10 改为 `PROJECT_ROOT = Path(__file__).resolve().parent` (指向项目根 8.4 目录)
- **风险**: 零。当前路径不存在,脚本根本无法运行
- **验证**: 手动运行 `python signal_post_processing.py`,断言能找到 `reports/qlib_v9_train_*.json` 或优雅退出

### 0.3 修复 daily_workflow.py 的 coordinate 缺参
- **文件**: [daily_workflow.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py) L2214-2221
- **修改**: 在 `hc.coordinate(...)` 调用中显式传入:
  - `hwm_drawdown = float(self.state.get("phases", {}).get("risk", {}).get("drawdown_status", {}).get("hwm_drawdown", 0.0))`
  - `bs_loss = float(self.state.get("phases", {}).get("market", {}).get("portfolio_drop", 0.0))`
- **风险**: 零。coordinate 签名默认 hwm_drawdown=0.0, bs_loss=0.0,不传等价于传 0
- **验证**: 新增 `tests/unit/test_coordinate_hwm_drawdown.py`,构造 hwm_drawdown=0.05 的 state,断言 regime 字段非空

### 0.4 修复 positions 等权假设
- **文件**: [daily_workflow.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py) L2113-2143
- **修改**: 从 `_pos_data["positions"][code]` 读取 `shares` 和 `est_price`/`avg_cost` 计算真实 market_value,而非用 `self.capital / _n` 等权
- **风险**: 中。Beta 计算结果会变化,可能让 `test_phase_hedge_sim_branch.py` 断言失败,需同步更新测试 mock
- **验证**: 跑 `tests/unit/test_phase_hedge_sim_branch.py`,通过则 OK

### 阶段 0 完成标志
- `pytest tests/unit/test_hedge_coordinator_np_bug.py tests/unit/test_phase_hedge_sim_branch.py tests/unit/test_gate_manager.py` 全绿
- `pytest tests/` 总数 ≥2321 + 71 且全绿
- `python signal_post_processing.py` 不报路径错

---

## 阶段 1: predict_annual_return 动态化 (Shadow 基线兜底)

**目标**: 让 predict 反映 V9 真实 alpha,从 4.79% 提升到 13%+ (权益占比 60% 下的理论上限)。Flag 关闭时行为等价现状,Flag 开启后用 Shadow 基线动态计算。

### 1.1 新增 Feature Flag
- **文件**: [feature_flags.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/config/feature_flags.yaml)
- **修改**: 新增 `USE_DYNAMIC_RETURN_PREDICTION` Flag (default: false, fallback: "降级为静态硬编码 20 标的 v7.6 行为")
- **复用**: [feature_flags.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/infra/feature_flags.py) 的 `is_enabled()` API

### 1.2 重构 predict_annual_return.py 为动态+静态双路径
- **文件**: [predict_annual_return.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/predict_annual_return.py)
- **修改**: 在 `predict_annual_return_struct()` 顶部插入 Flag 检查
  - Flag 关闭 → 调用 `_static_predict()` (现有代码打包成函数,行为不变)
  - Flag 开启 → 调用 `_dynamic_predict()`
- **`_dynamic_predict()` 核心改动**:
  1. **权益收益**: 从 `config/shadow_account_config.json` 读 `backtest_benchmark.annual_return=0.1962` 作为 base 情景权益收益基准
     - bull = benchmark × 1.3 = 0.255
     - base = benchmark = 0.1962
     - bear = -benchmark.max_drawdown = -0.0995
  2. **build_ratio**: 从 `config/positions.json` 读取真实已建仓 shares / target_shares,而非硬编码 0.40
  3. **phase_factor**: 动态读取 — 若 `build_ratio >= 0.80` 则 `phase_factor=1.0` (建仓完成不打折),否则 `phase_factor=0.70`
  4. **post_build_return_factor**: 若 `build_ratio >= 0.95` 则 `1.0` (全年满仓),否则维持 `0.92`
  5. **OPTIONS_BUDGET**: 从 `portfolio.yaml` 的 `hedge_capital × put_option_budget_pct` 读取 (对齐 shadow_account_config.json 的 0.60)
  6. **期权成本/收入**: 保留静态值 (阶段 2 对冲改进后再联动)
- **辅助函数** (全部 try-except 包裹,失败回退静态值):
  - `_load_shadow_benchmark()` → 从 `config/shadow_account_config.json` 读 backtest_benchmark
  - `_compute_dynamic_build_ratio()` → 从 `config/positions.json` 读真实建仓比例
  - `_compute_phase_factor(build_ratio)` → 建仓完成度 → phase_factor 映射
- **复用**: [ConfigManager](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/config_manager.py) 的 `get_config()` API 读取配置

### 1.3 调整 gate_thresholds.json 的 phase_factor 逻辑
- **文件**: [gate_thresholds.json](file:///e:/各种PY程序/28-终极量化交易系统8.4/config/gate_thresholds.json)
- **修改**: `use_phase_adjusted` 保持 `true`,但 `phase_factor` 改为 `0.0` (表示"从 predict 动态读取,不覆盖")
- **原因**: predict 动态化后,phase_factor 已由 build_ratio 动态决定 (建仓完成=1.0),gate 不应再硬编码覆盖

### 1.4 (可选) V9 信号写回 positions.json
- **文件**: [signal_post_processing.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/signal_post_processing.py) (阶段 0 已修路径)
- **修改**: 增加 V9 信号 (`qlib_v9_train_*.json` 的 `signals` 字典) 写回 `positions.json` 的支持
- **优先级**: 低 (用户选了 Shadow 基线兜底,不依赖 V9 信号文件)。仅在未来需要更精确的标的级预测时实施

### 阶段 1 预期效果

| 指标 | 当前 (静态) | 阶段 1 后 (动态, build_ratio=0.40) | 阶段 1 后 (动态, build_ratio=0.85+) |
|------|------------|-----------------------------------|-------------------------------------|
| equity_return_base | 0.066 | 0.1962 | 0.1962 |
| base_equity | ¥182,160 | ¥541,512 | ¥541,512 |
| expected_return | 6.85% | ~10.5% | ~13.8% |
| phase_factor | 0.70 | 0.70 | 1.0 |
| phase_adjusted_return | 4.79% | ~7.4% | ~13.8% |
| ReturnExpectationGate | warn (不达标) | warn (不达标) | warn (接近 15%,缺口 1.2%) |

**关键发现**: 权益占比 60% (3M/5M) 限制了 expected_return 上限 ≈ 13.8%,即使 V9 年化 19.62% 也无法达到门禁 15%。要达到 15%+,需:
- **选项 A**: 提升权益占比到 70% (3.5M/5M) → expected_return ≈ 15.7% ✓ (需改 portfolio.yaml)
- **选项 B**: 降低门禁阈值到 12% (对齐权益占比 60% 的合理预期)
- **选项 C**: 接受 sim_mode warn,等 Shadow 14 天后 phase_factor=1.0 自然达标

**推荐**: 阶段 1 先实施动态化 (从 4.79% → 13.8%),然后在阶段 2 (对冲改进) 中通过降低对冲成本或调整资金分配来补上最后 1.2% 缺口。

### 阶段 1 验证
1. **Flag 关闭**: `pytest tests/unit/test_predict_annual_return_struct.py` 全绿 (现有 9 个测试不变)
2. **Flag 开启**: 新增 `tests/unit/test_predict_dynamic.py`:
   - mock shadow_account_config.json 存在 → 断言 `expected_return >= 0.10`
   - mock build_ratio=0.85 → 断言 `phase_factor=1.0`
   - mock build_ratio=0.40 → 断言 `phase_factor=0.70`
   - mock 文件不存在 → 断言回退静态路径,`expected_return ≈ 0.068`
3. **手动验证**: 开启 Flag 后跑 `python v8.3_institutional/predict_annual_return.py`,确认 phase_adjusted_return 从 4.79% 提升到 10%+

### 阶段 1 完成标志
- Flag 关闭: 2321 + 71 + 阶段 0 新增测试全绿,行为等价阶段 0
- Flag 开启: predict phase_adjusted_return 从 4.79% 提升到 10%+ (build_ratio=0.40) 或 13%+ (build_ratio=0.85+)
- ReturnExpectationGate 的 blockers 中 annual_return 缺口从 10% 缩小到 2% 以内

---

## 阶段 2: 对冲双路径 + MIXED 模式 (后续实施,用户已确认方向)

**目标**: 让 HedgeCoordinator 在 sim_mode 下自动输出"期货压 beta + 期权补 delta"的双路径订单,使 HedgeCompletenessGate 通过。用户已确认切换到 MIXED 模式。

### 2.1 新增 Feature Flag: USE_BETA_DELTA_DUAL_HEDGE
### 2.2 BetaHedger: beta_trigger 从 0.70 → 0.30 (Flag 控制)
### 2.3 VolHedger: budget_low_pct 从 0.003 → 0.06 + 补 delta 模式 (Flag 控制)
### 2.4 hedge_coordinator: 双路径协调 (BetaHedger 输出后计算 delta_gap,传给 VolHedger)
### 2.5 daily_workflow: VIX 从硬编码 18.5 → 沪深300 30日历史波动率代理
### 2.6 portfolio.yaml: hedge_mode 从 OPTIONS_ONLY → MIXED (Flag 控制)

(阶段 2 详细设计在阶段 0+1 验证通过后展开)

---

## 验证方法

### 单元测试增量

| 测试文件 | 覆盖阶段 | 数量 |
|---------|---------|------|
| `tests/unit/test_hedge_coordinator_np_bug.py` | 0.1 | 2 |
| `tests/unit/test_coordinate_hwm_drawdown.py` | 0.3 | 2 |
| `tests/unit/test_predict_dynamic.py` | 1.2 | 6 |
| `tests/unit/test_signal_post_processing.py` | 0.2 | 2 |

### 端到端冒烟测试

```bash
# 阶段 0 验证
pytest tests/unit/test_hedge_coordinator_np_bug.py tests/unit/test_phase_hedge_sim_branch.py -v

# 阶段 1 验证 (Flag 关闭,行为等价)
pytest tests/unit/test_predict_annual_return_struct.py -v

# 阶段 1 验证 (Flag 开启,动态路径)
python -c "from utils.infra.feature_flags import enable; enable('USE_DYNAMIC_RETURN_PREDICTION')"
python v8.3_institutional/predict_annual_return.py
# 期望: phase_adjusted_return 从 4.79% 提升到 10%+

# 全量回归
pytest tests/ -q
# 期望: 2321 + 71 + 新增测试全绿
```

---

## 关键文件路径

### 待修改文件 (阶段 0+1)
1. [hedge_coordinator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/src/hedging/hedge_coordinator.py) — 阶段 0.1
2. [signal_post_processing.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/signal_post_processing.py) — 阶段 0.2
3. [daily_workflow.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py) — 阶段 0.3 + 0.4
4. [feature_flags.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/config/feature_flags.yaml) — 阶段 1.1
5. [predict_annual_return.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/predict_annual_return.py) — 阶段 1.2
6. [gate_thresholds.json](file:///e:/各种PY程序/28-终极量化交易系统8.4/config/gate_thresholds.json) — 阶段 1.3

### 复用资产 (只读)
- [ConfigManager](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/config_manager.py) — 4 级优先级配置加载
- [feature_flags.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/infra/feature_flags.py) — Feature Flag 框架
- [shadow_account_config.json](file:///e:/各种PY程序/28-终极量化交易系统8.4/config/shadow_account_config.json) — V9 基线 benchmark (annual_return=0.1962, max_drawdown=0.0995, sharpe=1.315)
- [gate_manager.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/gate_manager.py) — 门禁逻辑 (不改,只调)
- [positions.json](file:///e:/各种PY程序/28-终极量化交易系统8.4/config/positions.json) — 真实持仓数据 (build_ratio 计算)
- [portfolio.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/config/portfolio.yaml) — 资金分配配置

### 参考测试
- [test_predict_annual_return_struct.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/tests/unit/test_predict_annual_return_struct.py) — 现有 9 个测试,阶段 1 后须保持全绿
- [test_gate_manager.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/tests/unit/test_gate_manager.py) — 门禁测试,含通过用例 L205-226
- [test_phase_hedge_sim_branch.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/tests/unit/test_phase_hedge_sim_branch.py) — 阶段 0.4 须保持全绿
