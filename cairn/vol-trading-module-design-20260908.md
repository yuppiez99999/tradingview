---
title: vol_trading 模块设计草案（对冲基金 P2 前置）
date: 2026-09-08
status: draft
phase: P2 前置设计（09-19 启动前提早成稿）
related:
  - cairn/hedge-fund-style-shadow-plan-20260907.md
  - utils/etf_option_combo/combo_base.py
  - utils/etf_option_combo/iv_adaptive.py
  - utils/etf_option_combo/combo_backtest.py
---

# vol_trading 模块设计草案

> **定位**：补齐对冲基金模式缺口 — 波动率交易引擎 + delta neutral 框架。
> **约束**：冻结窗内只写 shadow 代码（`utils/vol_trading/` 新目录不触生产 import），09-19 P2 启动后按此实施。
> **复用铁律**：全部复用 `etf_option_combo` 既有组件，不重复造轮子。

---

## 1. 可复用组件清单（已确认存在）

| 组件 | 来源 | 复用点 |
|------|------|--------|
| `OptionChainFetcher.get_option_chain()` | `combo_base.py:284` | 期权链含 iv/delta/gamma/theta/vega 全 Greeks |
| `OptionChainFetcher.get_iv_term_structure()` | `combo_base.py:366` | IV 期限结构（日历价差/期限套利用） |
| `OptionChainFetcher.get_spot_price()` | `combo_base.py:263` | 现货价（data_layer P0-P6 降级链） |
| `ComboLeg/ComboOrder/ComboResult` | `combo_base.py:60-138` | frozen dataclass 值对象（U-03 不可变性） |
| `ComboBase` 抽象基类 | `combo_base.py:411` | 生命周期模板 generate → adjust → roll → close |
| `build_iv_rank_provider()` | `iv_adaptive.py` | IV Rank 数据源（均值回归信号输入） |
| `ComboBacktest` 逐日驱动模式 | `combo_backtest.py:37` | 回测框架（逐日 → 信号 → 建仓 → Greeks → 滚仓 → 成本 → 绩效） |
| `ComboRiskManager` | `combo_risk_manager.py` | 风控审批模式（预算/肥手指/KillSwitch） |
| `_bs_price` BS 定价 | `option_data_fetcher.py` | 零第三方依赖定价 |

## 2. 模块结构

```
utils/vol_trading/
├── __init__.py              # 导出引擎类
├── vol_signal.py            # 波动率信号层（IV Rank / IV 期限 / 实现波动率 vs 隐含）
├── iv_mean_reversion.py     # IV 均值回归引擎（卖高买低）
├── gamma_scalp.py           # gamma scalping 引擎（ATM straddle + delta 对冲）
├── delta_neutral.py         # 组合层 delta 中性框架（跨策略聚合）
└── vol_backtest.py          # 波动率策略回测（复用 combo_backtest 模式）
```

**文件规模约束**：单文件 ≤ 400 行（AGENTS.md 5.3），全部遵循零第三方依赖（numpy 可用）。

## 3. 各模块设计

### 3.1 vol_signal.py — 波动率信号层

```python
class VolSignalEngine:
    """波动率信号计算 — 三个子信号, 各自独立可关."""

    def compute_iv_rank(self, underlying, window_days=252) -> float | None:
        """IV Rank = (当前IV - 最低IV) / (最高IV - 最低IV)。
        数据源: build_iv_rank_provider (iv_adaptive.py), 不足窗口返回 None。"""

    def compute_iv_term_slope(self, underlying) -> float | None:
        """期限斜率 = IV(近月) - IV(远月)。
        数据源: get_iv_term_structure; 近月 > 远月 = backwardation = 高波动信号。"""

    def compute_realized_vs_implied(self, underlying, window=20) -> dict:
        """实现波动率(20日 HV) vs 隐含波动率(ATM IV)。
        返回 {hv, iv, vr_ratio}: vr_ratio = iv/hv, >1.2 显著卖权价值, <0.8 买权价值。"""
```

### 3.2 iv_mean_reversion.py — IV 均值回归引擎

**策略逻辑**（方向中性核心：卖权收权利金，不赌涨跌）：

| 状态 | 触发 | 动作 | 平仓 |
|------|------|------|------|
| IV Rank > 80 | 高估 | 卖出 strangle（OTM put + OTM call，各 5%） | IV Rank 回落 < 40 |
| IV Rank < 20 | 低估 | 买入 straddle（ATM） | IV Rank 回升 > 40 |
| 20 ≤ Rank ≤ 80 | 中性 | 持有观望 | — |

**设计要点**：
- 继承 `ComboBase`，复用 `_select_legs` 模式 → 返回 `ComboLeg` 元组（SELL PUT OTM + SELL CALL OTM）
- 保证金约束：strangle 双腿保证金 ≤ 15% 总资产（复用 ComboRiskManager）
- 极端保护：卖腿两侧各留 2% 预算买深 OTM 保护（等价 iron condor，防黑天鹅）
- IV 突变 > 3σ → 强制减仓 + 通知（Kill Criteria）
- 状态持久化：复用 `ComboStateManager`

### 3.3 gamma_scalp.py — gamma scalping 引擎

**策略逻辑**（在波动中赚钱的直接实现）：

1. 买入 ATM straddle（+gamma +vega -theta）
2. 日常对冲：现货 delta 漂移超 ±0.15 → 买卖现货回中性（收割 gamma）
3. 到期前 7 日平仓（theta 损耗加速区）

**收益条件**：实际波动 > 隐含波动（HV > IV）。`vr_ratio < 1.0` 时禁开新仓。

```python
class GammaScalpEngine(ComboBase):
    def generate(self, underlying, spot_position, market_state=None) -> ComboResult:
        """买入 ATM straddle (同月到期, DTE 25-45)。"""

    def adjust(self, hedge_band: float = 0.15) -> list[ComboOrder]:
        """日度 delta 对冲: 组合 delta 漂移 > band → 现货回归指令。"""
```

**对冲执行**：现货单走普通股票通道（QMT/xtquant），期权单走期权通道 — 订单生成只产出 `ComboOrder`，执行复用现有 broker 层。

### 3.4 delta_neutral.py — 组合层 delta 中性框架

**定位**：跨策略聚合层 — 市场中性 + 期权卖方 + 波动率交易的所有 delta 汇总归零。

```python
@dataclass(frozen=True)
class PortfolioDelta:
    """组合 delta 快照 — 不可变值对象。"""
    equity_delta: float        # 股票/ETF 现货 delta (= 市值/beta 调整)
    option_delta: float        # 全部期权持仓 delta 聚合 (1张=multiplier 10000)
    futures_delta: float       # 股指期货 delta (IC/IM/IF)
    target_delta: float        # 目标 (= 0.0, 方向中性)
    net_delta: float           # equity + option + futures

class DeltaNeutralManager:
    def compute_portfolio_delta(self, positions: dict) -> PortfolioDelta: ...

    def generate_hedge_orders(self, pd_: PortfolioDelta, band: float = 0.05) -> list:
        """|net_delta| > band → 生成对冲指令。
        优先级: 股指期货(成本低) > ETF 现货(精确) — 复用 hedge_engine 的
        generate_futures_hedge (Beta 加权 IC/IM/IF) 而非重写。"""
```

**关键决策**：delta_neutral 不自带对冲实现，**委托 `hedge_engine.generate_futures_hedge()`** 做期货腿 — 与五阶段联动引擎（Phase 2 hedge_decision）保持同一对冲路径，避免双轨。

### 3.5 vol_backtest.py — 波动率策略回测

复用 `ComboBacktest` 逐日驱动模式，扩展 IV 路径模拟：

```python
class VolBacktest(ComboBacktest):
    """波动率策略回测 — 在 combo_backtest 基础上增加:
    1. IV 路径生成: 历史真实 IV 序列优先, 无则 GBM 模拟 (mean-reverting)
    2. gamma scalp 逐日再平衡模拟 (delta band 触发现货交易)
    3. VR ratio 逐日统计 (实现 vs 隐含)
    输入: etf 日线 (data_layer P3 akshare hfq) + IV 序列
    输出: reports/shadow/hedge_fund/vol_trading_backtest.json
    """
```

**回测区间**：2021-2026（与 hedge_rebalance_backtest 对齐，可做同窗对比）。

## 4. 风控参数（Shadow 配置草案）

```yaml
# config/vol_trading.yaml (P2 启动时创建, shadow)
capital:
  total: 300_000            # 对冲基金方案中 vol_trading 分配 10% (300 万 × 0.10)

iv_mean_reversion:
  rank_high: 80             # IV Rank > 80 卖 strangle
  rank_low: 20              # IV Rank < 20 买 straddle
  rank_exit: 40             # 回归 40 平仓
  otm_pct: 0.05             # strangle 双腿 5% OTM
  margin_max_pct: 0.15      # 保证金 ≤ 15%
  tail_protection_budget: 0.02  # 尾部保护预算 2% (iron condor 化)

gamma_scalp:
  dte_range: [25, 45]       # 开仓 DTE 窗口
  delta_rebalance_band: 0.15  # delta 漂移再平衡阈值
  close_days_before_expiry: 7  # 到期前 7 日平仓
  vr_ratio_max: 1.0         # IV ≥ HV 才开仓

delta_neutral:
  target_delta: 0.0
  hedge_band: 0.05          # 组合 delta 偏离 5% 触发对冲
  futures_first: true       # 优先期货对冲 (成本低)

risk:
  iv_jump_sigma: 3.0        # IV 突变 > 3σ 减仓
  max_daily_loss_pct: 0.02  # 单日亏损 2% 强制检查
  kill_switch_drawdown: 0.10  # 组合回撤 10% Kill Switch
```

## 5. 与现有系统的接线（P2 实施时）

| 接线点 | 方式 |
|--------|------|
| 影子调度 | `scripts/hedge_fund_shadow_runner.py` 日度 EOD 调用（方案文档 Phase 4 T4.1，cron 16:40） |
| 数据源 | 复用 `OptionChainFetcher`（data_layer P0-P6 降级链），不新建数据管道 |
| 对冲执行 | delta_neutral 委托 `hedge_engine.generate_futures_hedge()` |
| 报告输出 | `reports/shadow/hedge_fund/`（方案文档 §6 决策材料清单） |
| 生产隔离 | I-02 双门禁：`utils/vol_trading/` 仅 shadow import，生产零引用（G5 检查） |

## 6. 实施清单（P2 窗口 09-19 ~ 10-26）

| 任务 | 文件 | 估时 |
|------|------|------|
| P2-1 | vol_signal.py + 单测 | 0.5 天 |
| P2-2 | iv_mean_reversion.py + 单测 | 1 天 |
| P2-3 | gamma_scalp.py + 单测 | 1 天 |
| P2-4 | delta_neutral.py + 单测 | 1 天 |
| P2-5 | vol_backtest.py + 2021-2026 回测运行 | 1.5 天 |
| P2-6 | config/vol_trading.yaml + 影子接线 | 0.5 天 |

合计 ~5.5 人天，冻结窗内（09-19 起）shadow 代码不受 Change Budget 限制（`shadow: 无限制`）。