# QUANT RESEARCH MEMO — 机构级实盘系统升级 (v7.4 → v7.5 Institutional)

| 字段 | 内容 |
|------|------|
| Memo ID | QRM-2026-07-05-001 |
| From | Quant Lead / CRO |
| To | Portfolio Manager, Execution Desk, Risk Committee |
| Re | v7.5 机构级实盘系统重构方案 |
| Classification | Internal — Quant Research |
| Date | 2026-07-05 |
| Status | Draft for Risk Committee Review |

---

## 0. Executive Summary

**当前系统状态**：`28-终极量化交易系统7.1/` 实际版本为 **v7.4**（已集成黑天鹅防护 v7.2、因果验证 v7.3、个股分档建仓 v7.4）。基础管理规模 500 万 RMB，目标年化 8.5%、最大回撤 15%、A 股 + 期货 + 期权三层结构。

**升级动因**：v7.4 已具备"预警 + 风控决策 + 执行订单"闭环，但在四个维度距离 Citadel / Point72 级别实盘系统仍有差距：
1. **风险预算**：当前 `enhanced_risk_manager.py` 使用固定阈值（VaR 95%=5%、DD=15%），未做风险平价与改进型 Kelly 的统一框架；
2. **对冲联动**：`black_swan_optimizer.py` 偏静态情景分析，缺少 Beta / Vol / Correlation 三类对冲的实时联动与成本阈值；
3. **执行算法**：`automated_execution_system.py` 仅有 7:00 / 10:00 / 14:00 三个固定时点，无冰山订单、无滑点熔断、无 NTP 时间戳对齐；
4. **回测严谨性**：现有回测为 in-sample 全样本拟合，缺少 Walk-Forward 与 2020-03 / 2022-05 / 2024-08 三段极端行情的合规压力测试。

**v7.5 目标**：在保留 v7.4 全部功能的前提下，引入 **Risk Parity + 改进 Kelly** 双轨仓位、**Beta/Vol/Corr 三联对冲**、**Smart Order Routing (SOR) + Iceberg**、**Walk-Forward + 三段压力测试**，并将目录结构按 `src/{alpha,hedging,execution,risk,backtest}` 重新组织。

**关键约束**：
- 年化收益 ≥ 8%（A 股基准，扣除 1.5‰ 单边成本后）
- 最大回撤 < 15%
- 单笔风险 ≤ 总资金 1.5%
- 7×24 无人值守（A 股日内 + 夜盘期货对冲）

---

## 1. 风险预算与仓位管理（Risk Budgeting）

### 1.1 现状诊断

当前 `enhanced_risk_manager.py` 的仓位规则为：

```python
# 现状：固定阈值 + 线性减仓
'var_threshold_95': 0.05,
'drawdown_threshold': 0.15,
'concentration_limit': 0.20
```

**缺陷**：
- 未区分**风险来源**（系统性 vs. 特质），导致仓位调整"一刀切"；
- Kelly 公式直接使用历史均值收益，未做 **贝叶斯收缩（Bayesian Shrinkage）** 与 **Merton 收益分解**，存在参数估计偏差；
- 回撤阈值 15% 触发即清仓，缺少 10% 防御模式与 14% 熔断的中间态。

### 1.2 改进型 Kelly + Risk Parity 双轨模型

#### 1.2.1 改进型 Kelly（带收缩与下行保护）

经典 Kelly：

$$
f^* = \frac{\mu - r_f}{\sigma^2}
$$

存在问题：$\hat{\mu}$ 估计噪声极大，半 Kelly 已是业界共识。我们进一步引入 **James-Stein 收缩**：

$$
\hat{\mu}_{shrink} = \omega \hat{\mu}_{hist} + (1-\omega) \mu_{prior}, \quad \omega = \frac{\tau^2}{\tau^2 + \sigma_{\hat{\mu}}^2}
$$

其中 $\mu_{prior}$ 取 CAPM 隐含收益 $\mu_{prior} = r_f + \beta(\mathbb{E}[R_M] - r_f)$，$\tau^2$ 为先验方差。

**仓位上限**：
$$
f_{final} = \min\left( \frac{1}{2} f^*_{shrink},\ \frac{0.015 \cdot C}{\sigma_i \cdot |w_i|} \right)
$$

- 第一项：半 Kelly 上限
- 第二项：单笔风险 1.5% 硬约束（$C$ 总资金，$\sigma_i$ 标的波动率，$w_i$ 单位风险敞口）

#### 1.2.2 Risk Parity（风险平价）

对 N 个资产，权重 $w$ 满足各资产风险贡献相等：

$$
w_i \cdot (\Sigma w)_i = w_j \cdot (\Sigma w)_j, \quad \forall i,j
$$

求解采用 **Newton-Raphson + 旋转恢复**：

$$
w^{(k+1)} = w^{(k)} - \eta \cdot J^{-1} \cdot \nabla L(w^{(k)})
$$

其中 $L(w) = \sum_{i,j} (RC_i - RC_j)^2$，$RC_i = w_i (\Sigma w)_i / \sqrt{w^\top \Sigma w}$。

**协方差估计**：采用 **Ledoit-Wolf 收缩协方差**，避免样本协方差在 $N > T$ 时奇异：

$$
\Sigma_{LW} = \delta \cdot \Sigma_{target} + (1-\delta) \cdot \Sigma_{sample}
$$

$\Sigma_{target} = \bar{\sigma}^2 I$（单位方差乘以平均波动率平方），$\delta$ 通过最小化 Frobenius 范数期望获得。

### 1.3 三级回撤防御机制

| 回撤区间 | 模式 | 仓位动作 | 允许操作 |
|---------|------|---------|---------|
| DD < 10% | NORMAL | 全 Risk Parity 权重 | 开 / 平 / 对冲 |
| 10% ≤ DD < 14% | DEFENSE | 仓位 ×0.5，**只减不加** | 减仓 / 加对冲 / 不允许新开多 |
| DD ≥ 14% | CIRCUIT_BREAKER | 强制清仓 + 停止交易 24h | 仅允许平仓 + 紧急对冲 |

**关键改进**：DD 计算采用 **滚动 60 日 High-Water Mark**，避免单日噪音触发：

$$
DD_t = \frac{HWM_t - E_t}{HWM_t}, \quad HWM_t = \max_{s \in [t-60, t]} E_s
$$

### 1.4 风险预算伪代码

```python
class RiskBudgeter:
    """v7.5 风险预算器：Risk Parity + 改进 Kelly + 三级回撤"""

    def __init__(self, total_capital: float,
                 target_return: float = 0.08,
                 max_dd: float = 0.15,
                 single_trade_risk: float = 0.015):
        self.C = total_capital
        self.target = target_return
        self.max_dd = max_dd
        self.single_risk = single_trade_risk
        self.hwm = total_capital           # 滚动高水位
        self.hwm_window = deque(maxlen=60) # 60 交易日 HWM
        self.mode = "NORMAL"
        self.circuit_break_until = None

    def update_drawdown(self, equity: float, ts: pd.Timestamp) -> str:
        self.hwm_window.append(equity)
        self.hwm = max(self.hwm_window)
        dd = (self.hwm - equity) / self.hwm

        if dd >= 0.14:
            self.mode = "CIRCUIT_BREAKER"
            self.circuit_break_until = ts + pd.Timedelta(hours=24)
            return "LIQUIDATE_ALL"
        elif dd >= 0.10:
            if self.mode != "DEFENSE":
                self.mode = "DEFENSE"      # 仅一次性减半，避免反复触发
                return "HALVE_POSITION"
            return "DEFENSE_HOLD"
        else:
            self.mode = "NORMAL"
            return "NORMAL"

    def kelly_weight(self, mu_hist: float, sigma: float, beta: float,
                     rf: float, mu_market: float, n: int) -> float:
        # James-Stein 收缩
        mu_prior = rf + beta * (mu_market - rf)
        sigma_mu = sigma / np.sqrt(n)                  # 估计标准误
        tau2 = 0.01                                    # 先验方差
        omega = tau2 / (tau2 + sigma_mu**2)
        mu_shrink = omega * mu_hist + (1 - omega) * mu_prior

        f_kelly = (mu_shrink - rf) / (sigma**2)
        f_half = 0.5 * f_kelly                          # 半 Kelly

        # 单笔风险硬约束
        f_risk = (self.single_risk * self.C) / (sigma * abs(1.0))
        return float(np.clip(min(f_half, f_risk), 0, 0.20))  # 单标的上限 20%

    def risk_parity_weights(self, cov: np.ndarray) -> np.ndarray:
        """Ledoit-Wolf 收缩 + Newton-Raphson 求解 ERC 权重"""
        from sklearn.covariance import LedoitWolf
        # cov 已是样本协方差，这里做收缩
        lw = LedoitWolf().fit(cov)  # 注：实盘应直接传入 returns
        sigma_lw = lw.covariance_

        n = sigma_lw.shape[0]
        w = np.ones(n) / n
        for _ in range(100):
            port_var = w @ sigma_lw @ w
            marginal = sigma_lw @ w
            rc = w * marginal / np.sqrt(port_var)
            grad = 2 * (rc - rc.mean())
            # 牛顿步（对角近似 Hessian）
            w = w - 0.01 * grad / (np.abs(grad).max() + 1e-8)
            w = np.clip(w, 1e-4, None)
            w = w / w.sum()
        return w
```

---

## 2. 自动化对冲方案（Auto-Hedging Module）

### 2.1 现状诊断

v7.4 已有 `black_swan_optimizer.py` + `protective_put_manager.py`，但：
- 对冲触发仅基于 VIX 阈值与情景分析，缺少**实时 Beta 估计**；
- 缺少 **Correlation Hedge**（相关性崩溃对冲）；
- 期权对冲无成本阈值，VIX > 40 时权利金膨胀未被纳入决策。

### 2.2 三联对冲框架（Beta / Vol / Correlation）

#### 2.2.1 Beta Hedging（空头期货对冲）

**Beta 估计**：采用 **滚动 60 日 + 衰减因子 λ=0.94 的 EWMA Beta**：

$$
\beta_t = \frac{\sigma_{iM,t}}{\sigma_{M,t}^2}, \quad
\sigma_{iM,t} = \lambda \sigma_{iM,t-1} + (1-\lambda) r_{i,t} r_{M,t}
$$

**对冲条件**：组合净 Beta $> 0.7$，对冲至 $\beta_{net} \le 0.3$。

**对冲手数**：
$$
N_{hedge} = \left\lfloor \frac{(\beta_{port} - 0.3) \cdot V_{port}}{\beta_{fut} \cdot \text{Multiplier} \cdot P_{fut}} \right\rceil
$$

其中 $V_{port}$ 组合市值，$\beta_{fut}$ 期货 Beta（IF≈1.0, IC≈1.2, IM≈1.1），Multiplier=300（IF/IC/IM），$P_{fut}$ 期货价格。

**成本阈值**：仅当**对冲成本 / 组合市值 < 0.3%** 时执行，否则降级至**反向 ETF（如 510300 反向）** 或**期权 Put Spread**。

#### 2.2.2 Volatility Hedge（波动率对冲）

**触发**：VIX > 30 或 50ETF 期权隐含波动率 > 30%。

**工具选择**（成本自适应）：
- VIX ∈ (30, 40]：买入 **Put Spread**（虚值两档），成本可控；
- VIX ∈ (40, 60]：买入 **裸虚值 Put**（Delta ≈ -0.2），权利金预算 = 组合市值 × 0.5%；
- VIX > 60：**期权流动性折价**生效（v7.2 已实现），实际覆盖率随 VIX 反比衰减：

$$
\text{Coverage}_{actual} = \text{Coverage}_{target} \cdot \max\left(0.015,\ 1 - \frac{VIX - 40}{50}\right)
$$

#### 2.2.3 Correlation Hedge（相关性对冲）

**相关性矩阵监控**：滚动 30 日相关系数矩阵 $\rho_t$，计算**平均非对角相关**：

$$
\bar{\rho}_t = \frac{2}{N(N-1)} \sum_{i<j} \rho_{ij,t}
$$

**触发条件**：$\bar{\rho}_t > 0.85$ 且较 60 日均值上升 $> 0.15$（"相关性趋同事件"）。

**避险资产配置**：
- 黄金 ETF（518880）：权重 $w_{gold} = 0.10 \cdot \min(1, (\bar{\rho}_t - 0.85) / 0.15)$
- 国债逆回购（GC001 / GC007）：现金部分自动逆回购，年化约 1.5-2.5%

### 2.3 对冲联动伪代码

```python
class AutoHedger:
    """v7.5 三联对冲引擎：Beta + Vol + Correlation"""

    def __init__(self, capital: float):
        self.capital = capital
        self.beta_window = 60
        self.lambda_ewma = 0.94

    def hedge(self, positions: dict, market_data: pd.DataFrame,
              vix: float) -> list:
        orders = []
        beta_port = self._portfolio_beta(positions, market_data)
        avg_corr = self._avg_correlation(market_data)

        # --- Beta Hedge ---
        if beta_port > 0.7:
            cost_ratio = self._estimate_hedge_cost(beta_port)
            if cost_ratio < 0.003:
                orders.append(self._short_futures(beta_port, target=0.3))
            else:
                orders.append(self._put_spread_hedge(beta_port))

        # --- Volatility Hedge ---
        if vix > 30:
            orders.append(self._vol_hedge(vix, self.capital))

        # --- Correlation Hedge ---
        if avg_corr > 0.85:
            orders.append(self._safe_haven_alloc(avg_corr))

        return orders

    def _portfolio_beta(self, pos, md) -> float:
        # EWMA Beta 加权
        betas = {s: self._ewma_beta(md[s], md['market']) for s in pos}
        V = sum(pos[s] * md[s].iloc[-1] for s in pos)
        return sum(pos[s] * md[s].iloc[-1] * betas[s] for s in pos) / V

    def _vol_hedge(self, vix: float, capital: float) -> dict:
        if vix <= 40:
            return {'type': 'PUT_SPREAD', 'budget': capital * 0.003}
        elif vix <= 60:
            return {'type': 'BARE_PUT', 'budget': capital * 0.005,
                    'delta_target': -0.2}
        else:
            coverage = max(0.015, 1 - (vix - 40) / 50)
            return {'type': 'EMERGENCY_PUT', 'budget': capital * 0.008,
                    'coverage': coverage}

    def _safe_haven_alloc(self, avg_corr: float) -> dict:
        w_gold = 0.10 * min(1.0, (avg_corr - 0.85) / 0.15)
        return {'type': 'SAFE_HAVEN', 'gold_etf': w_gold,
                'reverse_repo': max(0, 0.30 - w_gold)}
```

---

## 3. 自动化执行逻辑（Execution Engine）

### 3.1 现状诊断

`automated_execution_system.py` 现状：
- 三时点固定执行（7:00 / 10:00 / 14:00）；
- 无 Smart Order Routing；
- 无 Iceberg / TWAP / VWAP 算法；
- 无滑点熔断；
- 无 NTP 时间同步。

### 3.2 Smart Order Routing (SOR) + Iceberg

**拆单逻辑**（Iceberg）：
- 单笔委托 ≤ **盘口最优档位深度的 10%**；
- 残余订单按 **VWAP** 切分到 5 分钟窗口；
- 撮合失败超过 3 次 → 切换至**对手价 + 1 tick**。

**滑点熔断**：
- 单笔实际成交价 vs. 决策价偏离 > 0.5% → 立即撤单；
- 同一标的当日累计滑点 > 1.0% → 暂停该标的 30 分钟；
- 全市场滑点中位数 > 0.3% → 全局降速 50%。

**NTP 时间戳对齐**：
- 启动时同步 NTP（`pool.ntp.org`），本地时钟漂移 > 50ms 自动校准；
- 每笔订单携带 `server_ts` + `local_ts` + `ntp_offset` 三字段；
- 决策信号必须使用 `server_ts`，避免本地时钟被人为回拨造成"未来函数"。

### 3.3 执行算法伪代码

```python
import ntplib
from datetime import datetime, timedelta

class SmartOrderRouter:
    """v7.5 智能订单路由 + Iceberg + 滑点熔断"""

    def __init__(self, broker_api):
        self.broker = broker_api
        self.ntp_offset = self._sync_ntp()
        self.slip_per_symbol = defaultdict(float)
        self.slip_pause_until = {}

    def _sync_ntp(self) -> float:
        try:
            c = ntplib.NTPClient()
            resp = c.request('pool.ntp.org', version=3, timeout=5)
            return resp.tx_time - time.time()
        except Exception:
            return 0.0

    def server_ts(self) -> datetime:
        return datetime.utcnow() + timedelta(seconds=self.ntp_offset)

    def execute(self, symbol: str, target_qty: int, side: str,
                decision_price: float) -> list:
        if self._is_paused(symbol):
            return [{'status': 'PAUSED'}]

        fills = []
        remaining = target_qty
        max_attempts = 5

        while remaining > 0 and max_attempts > 0:
            depth = self.broker.get_order_book(symbol, levels=5)
            slice_qty = min(remaining, int(depth['bid1_vol'] * 0.10))
            if slice_qty <= 0:
                slice_qty = max(100, remaining // 5)

            order = self.broker.place(symbol, slice_qty, side,
                                     order_type='LIMIT',
                                     price=depth['bid1'] if side == 'BUY'
                                           else depth['ask1'])
            fill = self.broker.wait_fill(order, timeout=30)

            if fill is None:
                self.broker.cancel(order)
                max_attempts -= 1
                continue

            slip = abs(fill['price'] - decision_price) / decision_price
            if slip > 0.005:
                self.broker.cancel(order)
                self.slip_per_symbol[symbol] += slip
                if self.slip_per_symbol[symbol] > 0.01:
                    self.slip_pause_until[symbol] = self.server_ts() + timedelta(minutes=30)
                return [{'status': 'SLIPPAGE_BREAK', 'slip': slip}]

            fills.append(fill)
            remaining -= fill['qty']
            time.sleep(2)   # 节流，避免被识别为高频

        return fills

    def _is_paused(self, symbol: str) -> bool:
        until = self.slip_pause_until.get(symbol)
        return until is not None and self.server_ts() < until
```

### 3.4 执行配置（YAML）

```yaml
# config/execution.yaml
sor:
  iceberg_pct_of_depth: 0.10
  max_attempts: 5
  throttle_seconds: 2
slippage:
  per_trade_break: 0.005      # 0.5%
  daily_break: 0.010          # 1.0%
  global_slow_threshold: 0.003
ntp:
  server: pool.ntp.org
  resync_interval_min: 60
  max_drift_ms: 50
trading_windows:
  - {session: 'OPEN',  start: '09:30', end: '09:45', algo: 'TWAP'}
  - {session: 'MORN',  start: '09:45', end: '11:30', algo: 'VWAP'}
  - {session: 'NOON',  start: '13:00', end: '14:30', algo: 'VWAP'}
  - {session: 'CLOSE', start: '14:30', end: '15:00', algo: 'TWAP'}
```

---

## 4. 收益与回测验证（Backtest & Optimization）

### 4.1 目标函数重定义

**弃用**：单纯最大化年化收益。

**新目标函数**：

$$
\mathcal{J} = \text{Sortino Ratio} + 0.5 \cdot \text{Calmar Ratio} - \lambda \cdot \|\mathbf{w}\|_2^2
$$

其中：
- $\text{Sortino} = \frac{\mathbb{E}[R_p - r_f]}{\sigma_D}$，$\sigma_D$ 为下行波动率；
- $\text{Calmar} = \frac{\text{Annualized Return}}{\text{Max DD}}$；
- $\lambda \|\mathbf{w}\|_2^2$ 为 $L_2$ 正则项，防止权重过度集中；
- $\lambda = 0.1$（经验值，可通过 CV 调优）。

### 4.2 Walk-Forward Analysis（滚动样本外）

**禁止未来函数**。采用 3 年历史数据，按 **train 24m / test 3m / step 3m** 滚动：

```
Window 1: Train [2022-07 ~ 2024-06]  Test [2024-07 ~ 2024-09]
Window 2: Train [2022-10 ~ 2024-09]  Test [2024-10 ~ 2024-12]
Window 3: Train [2023-01 ~ 2024-12]  Test [2025-01 ~ 2025-03]
Window 4: Train [2023-04 ~ 2025-03]  Test [2025-04 ~ 2025-06]
Window 5: Train [2023-07 ~ 2025-06]  Test [2025-07 ~ 2025-09]
...
```

**参数调优**：每个 Window 在训练集上做 5-fold CV，选出超参数，在测试集上**仅评估一次**，禁止"窥探"。

**最终指标**：所有测试集拼接后的整体 Sortino / Calmar / Max DD，而非均值。

### 4.3 压力测试（Stress Test）—— 三段极端行情

| 场景 | 时间区间 | 市场冲击 | 关键特征 |
|------|---------|---------|---------|
| 新冠闪崩 | 2020-02-19 ~ 2020-03-23 | S&P -33.5%, A 股 -13% | 流动性枯竭、相关性趋 1 |
| Luna 崩盘 | 2022-05-01 ~ 2022-05-12 | LUNA -99.9%, 加密传染 | 加密相关资产大幅波动 |
| 日元 Carry Trade 平仓 | 2024-08-01 ~ 2024-08-05 | 日经 -12.4%, VIX 65 | 全球去杠杆、套息平仓 |

**通过判据**：
- 在三段期间，组合最大回撤 < 15%；
- 若任一段回撤 ≥ 15%，策略视为 **失败**，需重新调整对冲参数（增加 Beta Hedge 比例或下调 Kelly 收缩系数）；
- 若回撤 ∈ [12%, 15%)，标记为"高风险"，需 CRO 签字方可上线。

### 4.4 回测参数（严格设定）

```yaml
# config/backtest.yaml
frequency: '1min'             # 分钟级
commission:
  stock: 0.00025              # 万 2.5
  futures: 0.000023           # 万 0.23
  options: 5.0                # 元 / 张
slippage_model: 'square_root' # Almgren-Chriss
  coefficient: 0.142
  volatility_scaling: true
financing:
  margin_long: 0.06           # 融资利率 6%
  margin_short: 0.06
  repo: 0.018                 # 国债逆回购 1.8%
walk_forward:
  train_months: 24
  test_months: 3
  step_months: 3
  cv_folds: 5
stress_periods:
  - {name: 'COVID_CRASH', start: '2020-02-19', end: '2020-03-23'}
  - {name: 'LUNA_CRASH',  start: '2022-05-01', end: '2022-05-12'}
  - {name: 'YEN_CARRY',   start: '2024-08-01', end: '2024-08-05'}
pass_criteria:
  max_dd_global: 0.15
  max_dd_stress: 0.15
  min_sortino: 1.0
  min_calmar: 0.5
```

### 4.5 过拟合诊断（Overfitting Check）

**数据窥探偏差（Data Snooping Bias）检测**：
- **White's Reality Check / Hansen's SPA Test**：对策略族进行多重检验校正；
- **Deflated Sharpe Ratio (Bailey & López de Prado)**：

$$
DSR = \Phi\left( (\hat{SR} - \mathbb{E}[\hat{SR}_{max}]) \cdot \sqrt{T-1} \right)
$$

若 DSR < 0.95，则策略 Sharpe 不能拒绝"随机取得"的原假设，需重新设计。

**正则化**：
- 因子合成采用 **LASSO (L1)** 进行特征选择：$\min \|y - Xw\|_2^2 + \alpha \|w\|_1$
- 权重优化采用 **岭回归 (L2)**：$\min \|y - Xw\|_2^2 + \alpha \|w\|_2^2$
- $\alpha$ 通过 5-fold CV 选优

---

## 5. 代码重构与伪代码（完整风控 + 对冲模块）

```python
# src/risk/risk_manager.py
"""
v7.5 RiskManager —— 三级回撤 + 风险预算 + 对冲联动
"""
import numpy as np
import pandas as pd
from collections import deque
from datetime import datetime, timedelta
from typing import Optional


class RiskManager:
    def __init__(self, total_capital: float,
                 target_return: float = 0.08,
                 max_dd: float = 0.15,
                 single_trade_risk: float = 0.015):
        self.capital = total_capital
        self.target = target_return
        self.max_dd = max_dd
        self.single_risk = single_trade_risk
        self.peak = total_capital
        self.current_dd = 0.0
        self.hwm_window = deque(maxlen=60)
        self.mode = "NORMAL"
        self.circuit_break_until: Optional[datetime] = None
        self.position_size = 1.0     # 仓位系数

    # ---------- 回撤计算 ----------
    def update_drawdown(self, current_equity: float,
                        ts: Optional[datetime] = None) -> str:
        self.hwm_window.append(current_equity)
        self.peak = max(self.hwm_window)
        self.current_dd = (self.peak - current_equity) / self.peak

        if ts and self.circuit_break_until and ts < self.circuit_break_until:
            return "CIRCUIT_BREAK_ACTIVE"

        if self.current_dd > 0.14:
            self.mode = "CIRCUIT_BREAKER"
            self.circuit_break_until = (ts or datetime.utcnow()) + timedelta(hours=24)
            self.position_size = 0.0
            return "LIQUIDATE_ALL"
        elif self.current_dd > 0.10:
            if self.mode != "DEFENSE":
                self.mode = "DEFENSE"
                self.position_size = 0.5     # 仓位减半，只减不加
            return "DEFENSE_MODE"
        else:
            self.mode = "NORMAL"
            self.position_size = 1.0
            return "NORMAL"

    # ---------- 风险预算 ----------
    def size_position(self, sigma: float, beta: float,
                      mu_hist: float, rf: float = 0.02,
                      mu_market: float = 0.08, n: int = 252) -> float:
        """Kelly 收缩 + 单笔风险硬约束"""
        mu_prior = rf + beta * (mu_market - rf)
        sigma_mu = sigma / np.sqrt(n)
        omega = 0.01 / (0.01 + sigma_mu**2)
        mu_shrink = omega * mu_hist + (1 - omega) * mu_prior
        f_kelly = (mu_shrink - rf) / (sigma**2)
        f_half = 0.5 * f_kelly
        f_risk = (self.single_risk * self.capital) / (sigma * max(beta, 0.1))
        w = float(np.clip(min(f_half, f_risk), 0, 0.20))
        return w * self.position_size     # 应用回撤模式系数

    # ---------- 自动对冲 ----------
    def auto_hedge(self, portfolio_beta: float, vix_level: float,
                   avg_correlation: float = 0.5) -> list:
        actions = []

        # Beta 对冲
        if portfolio_beta > 0.7:
            target_beta = 0.3
            hedge_ratio = (portfolio_beta - target_beta) / portfolio_beta
            actions.append({
                'type': 'BETA_HEDGE',
                'instrument': 'IF_futures',
                'ratio': hedge_ratio,
                'target_beta': target_beta
            })

        # 波动率对冲
        if vix_level > 30:
            if vix_level <= 40:
                actions.append({'type': 'PUT_SPREAD',
                                'budget': self.capital * 0.003})
            elif vix_level <= 60:
                actions.append({'type': 'BARE_PUT',
                                'budget': self.capital * 0.005,
                                'delta_target': -0.2})
            else:
                coverage = max(0.015, 1 - (vix_level - 40) / 50)
                actions.append({'type': 'EMERGENCY_PUT',
                                'budget': self.capital * 0.008,
                                'coverage': coverage})

        # 相关性对冲
        if avg_correlation > 0.85:
            w_gold = 0.10 * min(1.0, (avg_correlation - 0.85) / 0.15)
            actions.append({'type': 'SAFE_HAVEN',
                            'gold_etf_weight': w_gold,
                            'reverse_repo_weight': max(0, 0.30 - w_gold)})

        return actions

    # ---------- 风险预算检查 ----------
    def check_budget(self, positions: dict, market_data: pd.DataFrame) -> dict:
        """检查组合风险预算"""
        from sklearn.covariance import LedoitWolf
        returns = pd.DataFrame({s: market_data[s].pct_change()
                                for s in positions}).dropna()
        lw = LedoitWolf().fit(returns)
        cov = lw.covariance_

        # 风险贡献
        w = np.array([positions[s] for s in returns.columns])
        w = w / w.sum() if w.sum() > 0 else w
        port_var = w @ cov @ w
        rc = w * (cov @ w) / np.sqrt(port_var) if port_var > 0 else w * 0

        return {
            'portfolio_vol': np.sqrt(port_var) * np.sqrt(252),
            'risk_contributions': dict(zip(returns.columns, rc)),
            'max_concentration': float(np.max(w)),
            'beta_to_market': self._calc_beta(returns, market_data['market'])
        }

    def _calc_beta(self, returns: pd.DataFrame, market: pd.Series) -> float:
        cov = returns.covwith(market).sum() if hasattr(returns, 'covwith') \
              else np.cov(returns.values.T, market.values)[0, 1:].sum()
        var_m = market.var()
        return float(cov / var_m) if var_m > 0 else 0.0
```

---

## 6. 文件结构升级（v7.5 Institutional）

```
E:\各种PY程序\28-终极量化交易系统7.1\        # 保留现有 v7.4 文件，向后兼容
└── v7.5_institutional\                       # 新增子目录，渐进式迁移
    ├── config/
    │   ├── settings.yaml                    # 核心参数（止损线、杠杆、API Key 引用）
    │   ├── portfolio.yaml                   # 组合配置（继承 v7.4 15 标的）
    │   ├── execution.yaml                   # 执行算法参数
    │   ├── backtest.yaml                    # 回测协议
    │   └── risk_budget.yaml                 # 风险预算参数
    ├── data/
    │   ├── raw/                             # 原始 Tick / Minute 数据（Parquet）
    │   │   ├── stock/
    │   │   ├── futures/
    │   │   └── options/
    │   ├── processed/                       # 清洗后特征数据
    │   └── cache/                           # Wind MCP / iFinD 缓存
    ├── src/
    │   ├── alpha/
    │   │   ├── signal_generator.py          # 多因子 Alpha 生成
    │   │   ├── factor_library.py            # 价值/质量/动量/增长/安全五维
    │   │   └── signal_fusion.py             # ML+AI+康波融合（继承 v7.4）
    │   ├── hedging/
    │   │   ├── beta_hedger.py               # IF/IC/IM 期货空头
    │   │   ├── vol_hedger.py                # 期权保护性 Put
    │   │   ├── correlation_hedger.py        # 黄金+逆回购避险
    │   │   └── hedge_coordinator.py         # 三联对冲协调器
    │   ├── execution/
    │   │   ├── smart_order_router.py        # SOR + Iceberg
    │   │   ├── algo_engine.py               # TWAP / VWAP / POV
    │   │   ├── ntp_sync.py                  # 时间戳同步
    │   │   └── broker_api.py                # CTP / Wind 接口
    │   ├── risk/
    │   │   ├── risk_manager.py              # 三级回撤 + Kelly + Risk Parity
    │   │   ├── risk_budgeter.py             # 风险预算分配
    │   │   ├── circuit_breaker.py           # 熔断引擎（继承 v7.2 四级）
    │   │   └── stress_tester.py             # 压力测试
    │   └── backtest/
    │       ├── walk_forward.py              # 滚动样本外
    │       ├── cost_model.py                # 交易成本模型
    │       ├── metrics.py                   # Sortino / Calmar / DSR
    │       └── scenario_lib.py              # 2020-03 / 2022-05 / 2024-08
    ├── logs/
    │   ├── trades.log                       # 成交记录（JSON Lines）
    │   ├── risk.log                         # 风控事件
    │   ├── hedge.log                        # 对冲动作
    │   └── errors.log                       # 异常日志
    ├── tests/
    │   ├── test_risk_manager.py
    │   ├── test_hedger.py
    │   ├── test_sor.py
    │   ├── test_walk_forward.py
    │   └── test_stress_periods.py           # 三段极端行情必过测试
    ├── main.py                              # 启动入口
    ├── monitor.py                           # Streamlit 监控面板
    └── README_v7.5.md                       # 升级文档
```

### 6.1 核心配置文件 settings.yaml

```yaml
# config/settings.yaml —— v7.5 全局配置
system:
  version: "7.5-institutional"
  base_capital: 5_000_000       # 500 万 RMB
  target_annual_return: 0.08
  max_drawdown: 0.15
  timezone: "Asia/Shanghai"
  trading_hours_24x7: false     # A 股日内 + 夜盘期货对冲

risk:
  single_trade_risk: 0.015      # 单笔风险 1.5%
  defense_dd_threshold: 0.10
  circuit_break_dd: 0.14
  circuit_break_hours: 24
  kelly_shrinkage_prior: "CAPM"
  kelly_half: true
  risk_parity_erc: true
  cov_estimator: "ledoit_wolf"

hedging:
  beta_target: 0.3
  beta_trigger: 0.7
  beta_ewma_lambda: 0.94
  beta_window: 60
  vix_trigger: 30
  vix_emergency: 60
  corr_trigger: 0.85
  corr_window: 30
  hedge_cost_max: 0.003

execution:
  iceberg_pct_depth: 0.10
  slippage_break: 0.005
  ntp_server: "pool.ntp.org"
  ntp_max_drift_ms: 50

backtest:
  frequency: "1min"
  walk_forward_train: 24
  walk_forward_test: 3
  walk_forward_step: 3
  stress_periods:
    - "2020-02-19:2020-03-23"
    - "2022-05-01:2022-05-12"
    - "2024-08-01:2024-08-05"

data_sources:
  primary: "wind_terminal"      # P0
  secondary: "wind_mcp"         # P1
  tertiary: "ifind_mcp"         # P2
  fallback: "akshare"           # P3

api_keys:
  wind_api_key: "${WIND_API_KEY}"
  ifind_token: "${IFIND_TOKEN}"
```

---

## 7. 实施路线图与验收标准

### 7.1 三阶段实施

| 阶段 | 模块 | 验收标准 |
|------|------|---------|
| Phase A | Risk Budgeter + 三级回撤 | 单笔风险 ≤ 1.5%，DD=10% 触发减半、DD=14% 触发清仓 |
| Phase B | 三联对冲 + SOR + Iceberg | Beta < 0.3、滑点 < 0.3%、NTP 漂移 < 50ms |
| Phase C | Walk-Forward + 三段压力测试 | DSR ≥ 0.95，三段 Max DD < 15% |

### 7.2 上线 Gate（CRO 签字项）

- [ ] Walk-Forward 全部 5 个测试窗口拼接后 Sortino ≥ 1.0
- [ ] 三段压力测试 Max DD < 15%
- [ ] Deflated Sharpe Ratio ≥ 0.95
- [ ] 无未来函数（PIT 检查通过）
- [ ] NTP 漂移 < 50ms 持续 7 个交易日
- [ ] 滑点熔断在历史回放中正确触发
- [ ] CRO 签字

---

## 8. 风险提示与免责

1. 本 Memo 中所有数学模型与参数均为**研究建议**，非实盘配置；
2. 实盘部署前必须通过 Risk Committee 三审（量化、风控、合规）；
3. Walk-Forward 与压力测试结果不可作为未来收益保证；
4. Kelly 公式与 Risk Parity 在极端尾部行情下可能失效，必须配合第 2 节的三联对冲与第 1.3 节的熔断机制使用；
5. NTP 同步不能完全消除网络延迟，对极高频策略（持仓 < 1 分钟）不适用。

---

**Memo 结束**

*Prepared by Quant Research Desk — v7.5 Institutional Upgrade Initiative*
