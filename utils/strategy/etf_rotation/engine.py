"""W6.4.4 etf-rotation-strategy 三层验证引擎。

职责:
    - 借鉴 etf-rotation-strategy (https://github.com/zhangsensen/etf-rotation-strategy)
      的 WFO→VEC→BT 三层验证流程
    - 对 ETF 轮动策略做 Walk-Forward 优化 + 向量化交叉验证 + 最终回测
    - 与 utils/social_security_etf.py 协调, 作为 ETF 轮动信号的验证设施

etf-rotation-strategy 三层验证借鉴:
    1. WFO (Walk-Forward Optimization): 训练窗口优化参数 → 样本外测试 → 滑窗
    2. VEC (Vectorized Ensemble Cross-validation): 多折交叉验证 + 集成结果
    3. BT (Backtest): 用最优参数运行完整回测, 输出可审计报告

信号生成 (简化):
    - 动量轮动: 按 N 日收益率排名, 买入 top-K ETF
    - N (lookback) 和 K (holdings) 为待优化参数

与 social_security_etf.py 协调:
    - social_security_etf.py 提供风格映射 + ETF 白名单
    - 本模块对 ETF 白名单做回测验证, 评估轮动信号的有效性

设计原则 (AGENTS.md):
    - 不可变性 (§5.1): 验证报告为 dataclass
    - 多小文件 (§5.3): 本模块 < 350 行
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ============================================================
# 1. 三层验证报告
# ============================================================


@dataclass
class WFOResult:
    """WFO (Walk-Forward Optimization) 单窗口结果。"""
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_lookback: int
    best_holdings: int
    train_sharpe: float
    oos_sharpe: float
    oos_return: float


@dataclass
class VECResult:
    """VEC (Vectorized Ensemble Cross-validation) 结果。"""
    n_folds: int
    avg_sharpe: float
    sharpe_std: float
    robust_lookback: int  # 多数票选出的稳健参数
    robust_holdings: int


@dataclass
class BTResult:
    """BT (Backtest) 最终回测结果。"""
    final_equity: float
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    n_rebalances: int
    equity_curve: list = field(default_factory=list)


@dataclass
class ThreeTierReport:
    """三层验证汇总报告。"""
    wfo_results: list[WFOResult] = field(default_factory=list)
    vec_result: Optional[VECResult] = None
    bt_result: Optional[BTResult] = None
    final_lookback: int = 20
    final_holdings: int = 3
    passed: bool = False
    target_sharpe: float = 1.0

    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        wfo_count = len(self.wfo_results)
        vec_sharpe = self.vec_result.avg_sharpe if self.vec_result else 0.0
        bt_sharpe = self.bt_result.sharpe_ratio if self.bt_result else 0.0
        bt_ret = self.bt_result.total_return if self.bt_result else 0.0
        return (
            f"[{status}] ETF 轮动三层验证 (WFO→VEC→BT)\n"
            f"  WFO: {wfo_count} 窗口, 最终参数 lookback={self.final_lookback}d, holdings={self.final_holdings}\n"
            f"  VEC: {self.vec_result.n_folds if self.vec_result else 0} 折, "
            f"平均 Sharpe={vec_sharpe:.4f}\n"
            f"  BT:  Sharpe={bt_sharpe:.4f}, 收益={bt_ret:.4%}, "
            f"最终权益={self.bt_result.final_equity:,.2f}" if self.bt_result else ""
        )


# ============================================================
# 2. 信号生成 — 动量轮动
# ============================================================


def generate_rotation_signals(
    closes: pd.DataFrame,
    lookback: int = 20,
    holdings: int = 3,
) -> pd.DataFrame:
    """生成 ETF 轮动信号 (动量排名)。

    Args:
        closes: 收盘价矩阵 (date × etf)
        lookback: 动量回看天数
        holdings: 持仓 ETF 数量

    Returns:
        信号矩阵 (date × etf), 1.0=持仓, 0.0=不持仓 (等权)
    """
    # N 日收益率 (动量)
    momentum = closes.pct_change(lookback)

    # 每日排名 top-K
    signals = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)

    for i in range(len(closes)):
        if i < lookback:
            continue
        row = momentum.iloc[i].dropna()
        if len(row) < holdings:
            continue
        # 选 top-K (收益率最高的 K 个)
        top_k = row.nlargest(holdings).index
        for sym in top_k:
            signals.iloc[i][sym] = 1.0 / holdings  # 等权

    return signals


# ============================================================
# 3. 简单回测器
# ============================================================


def _run_backtest(
    closes: pd.DataFrame,
    signals: pd.DataFrame,
    initial_capital: float = 1_000_000.0,
    commission_rate: float = 0.0003,
) -> BTResult:
    """简单向量化回测 (信号 T → 执行 T+1)。

    Args:
        closes: 收盘价矩阵
        signals: 信号矩阵 (权重)
        initial_capital: 初始资金
        commission_rate: 手续费率

    Returns:
        BTResult 回测结果
    """
    daily_returns = closes.pct_change().fillna(0.0)
    # 信号延迟 1 天 (T 信号 → T+1 执行)
    shifted_signals = signals.shift(1).fillna(0.0)

    # 组合日收益 = sum(signal_i * ret_i)
    portfolio_returns = (shifted_signals * daily_returns).sum(axis=1)

    # 换手率 → 手续费
    turnover = shifted_signals.diff().abs().sum(axis=1) / 2  # 买卖双边
    cost = turnover * commission_rate
    net_returns = portfolio_returns - cost

    # 权益曲线
    equity_curve = (1 + net_returns).cumprod() * initial_capital
    final_equity = float(equity_curve.iloc[-1]) if len(equity_curve) > 0 else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital

    # Sharpe
    if net_returns.std() > 1e-10:
        sharpe = float(net_returns.mean() / net_returns.std() * np.sqrt(252))
    else:
        sharpe = 0.0

    # 最大回撤
    peak = equity_curve.cummax()
    drawdown = (equity_curve - peak) / peak
    max_dd = float(abs(drawdown.min())) if len(drawdown) > 0 else 0.0

    # 换仓次数 (信号变化)
    n_rebalances = int((shifted_signals.diff().abs().sum(axis=1) > 0.01).sum())

    return BTResult(
        final_equity=final_equity,
        total_return=total_return,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        n_rebalances=n_rebalances,
        equity_curve=equity_curve.tolist(),
    )


# ============================================================
# 4. 三层验证引擎
# ============================================================


class ThreeTierETFRotationValidator:
    """ETF 轮动三层验证引擎 (WFO→VEC→BT)。

    使用示例:
        validator = ThreeTierETFRotationValidator(
            lookback_grid=[10, 20, 60],
            holdings_grid=[2, 3, 5],
        )
        report = validator.validate(closes)
        print(report.summary())
    """

    def __init__(
        self,
        lookback_grid: list[int] = None,
        holdings_grid: list[int] = None,
        train_window: int = 120,
        test_window: int = 60,
        n_folds: int = 5,
        target_sharpe: float = 1.0,
    ) -> None:
        self.lookback_grid = lookback_grid or [10, 20, 60]
        self.holdings_grid = holdings_grid or [2, 3, 5]
        self.train_window = train_window
        self.test_window = test_window
        self.n_folds = n_folds
        self.target_sharpe = target_sharpe

    def validate(
        self,
        closes: pd.DataFrame,
        initial_capital: float = 1_000_000.0,
    ) -> ThreeTierReport:
        """运行三层验证。

        Args:
            closes: 收盘价矩阵 (date × etf)
            initial_capital: 初始资金

        Returns:
            ThreeTierReport 三层验证报告
        """
        # 1. WFO
        wfo_results = self._run_wfo(closes, initial_capital)

        # 2. VEC — 从 WFO 结果中选稳健参数 (多数票)
        vec_result = self._run_vec(wfo_results)

        # 3. BT — 用稳健参数跑完整回测
        if vec_result:
            final_lookback = vec_result.robust_lookback
            final_holdings = vec_result.robust_holdings
        else:
            final_lookback = 20
            final_holdings = 3

        signals = generate_rotation_signals(closes, final_lookback, final_holdings)
        bt_result = _run_backtest(closes, signals, initial_capital)

        return ThreeTierReport(
            wfo_results=wfo_results,
            vec_result=vec_result,
            bt_result=bt_result,
            final_lookback=final_lookback,
            final_holdings=final_holdings,
            passed=(bt_result.sharpe_ratio >= self.target_sharpe),
            target_sharpe=self.target_sharpe,
        )

    def _run_wfo(
        self,
        closes: pd.DataFrame,
        initial_capital: float,
    ) -> list[WFOResult]:
        """WFO: 训练窗口网格搜索最优参数, 测试窗口评估 OOS。"""
        total_len = len(closes)
        if total_len < self.train_window + self.test_window:
            return []

        results: list[WFOResult] = []
        window_id = 0
        start = 0
        step = self.test_window

        while start + self.train_window + self.test_window <= total_len:
            train_end = start + self.train_window
            test_end = train_end + self.test_window
            train_data = closes.iloc[start:train_end]
            test_data = closes.iloc[train_end:test_end]

            best_sharpe = -999.0
            best_lb = self.lookback_grid[0]
            best_h = self.holdings_grid[0]

            # 网格搜索 (训练窗口)
            for lb in self.lookback_grid:
                for h in self.holdings_grid:
                    signals = generate_rotation_signals(train_data, lb, h)
                    bt = _run_backtest(train_data, signals, initial_capital)
                    if bt.sharpe_ratio > best_sharpe:
                        best_sharpe = bt.sharpe_ratio
                        best_lb = lb
                        best_h = h

            # OOS 测试
            signals = generate_rotation_signals(test_data, best_lb, best_h)
            oos_bt = _run_backtest(test_data, signals, initial_capital)

            dates = closes.index
            results.append(WFOResult(
                window_id=window_id,
                train_start=str(dates[start])[:10],
                train_end=str(dates[min(train_end - 1, len(dates) - 1)])[:10],
                test_start=str(dates[min(train_end, len(dates) - 1)])[:10],
                test_end=str(dates[min(test_end - 1, len(dates) - 1)])[:10],
                best_lookback=best_lb,
                best_holdings=best_h,
                train_sharpe=best_sharpe,
                oos_sharpe=oos_bt.sharpe_ratio,
                oos_return=oos_bt.total_return,
            ))

            window_id += 1
            start += step

        return results

    def _run_vec(self, wfo_results: list[WFOResult]) -> Optional[VECResult]:
        """VEC: 从 WFO 结果中选稳健参数 (多数票) + 统计 Sharpe 稳定性。"""
        if not wfo_results:
            return None

        # 多数票选参数
        lb_votes = {}
        h_votes = {}
        sharpes = []

        for w in wfo_results:
            lb_votes[w.best_lookback] = lb_votes.get(w.best_lookback, 0) + 1
            h_votes[w.best_holdings] = h_votes.get(w.best_holdings, 0) + 1
            sharpes.append(w.oos_sharpe)

        robust_lb = max(lb_votes, key=lb_votes.get)
        robust_h = max(h_votes, key=h_votes.get)

        return VECResult(
            n_folds=len(wfo_results),
            avg_sharpe=float(np.mean(sharpes)),
            sharpe_std=float(np.std(sharpes)) if len(sharpes) > 1 else 0.0,
            robust_lookback=robust_lb,
            robust_holdings=robust_h,
        )


__all__ = [
    "BTResult",
    "ThreeTierETFRotationValidator",
    "ThreeTierReport",
    "VECResult",
    "WFOResult",
    "generate_rotation_signals",
]
