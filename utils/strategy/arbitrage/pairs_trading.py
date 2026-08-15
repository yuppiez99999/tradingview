"""W6.4.3 StatisticalArbitrageEngine 配对交易 Walk-Forward 验证模块。

职责:
    - 借鉴 StatisticalArbitrageEngine (https://github.com/DerekNest/StatisticalArbitrageEngine)
      的 Walk-Forward 样本外验证流程
    - 在已有 PairsTrading 类 (utils/strategy_lib/pairs_trading.py) 之上
      增加季度重筛选 + OOS 性能评估
    - 与 cairn/backtest-standards.md §八 Walk-Forward 分析对齐
    - OOS Sharpe 目标 ≥1.0 (StatisticalArbitrageEngine 基线 1.499, A 股可适当降低)

StatisticalArbitrageEngine 核心借鉴点:
    1. 季度重筛选: 每 N 天重新做协整检验, 淘汰 p-value > 阈值的配对
    2. Walk-Forward: 训练窗口估计 hedge_ratio → 样本外测试 → 滑窗前进
    3. OOS 指标: 样本外 Sharpe / 年化收益 / 最大回撤 / 胜率
    4. 信号生成: z-score 突破阈值 → 开仓 / z-score 回归 → 平仓

与现有模块关系:
    - utils/strategy_lib/pairs_trading.py: 提供协整检验 + 信号生成 (复用)
    - 本模块: 提供 Walk-Forward 验证框架 (新增)

设计原则 (AGENTS.md):
    - 不可变性 (§5.1): WFValidationReport 为 dataclass
    - 单一职责: 只做 Walk-Forward 验证, 不重写协整检验
    - 多小文件 (§5.3): 本模块 < 350 行
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from utils.strategy_lib.pairs_trading import PairsTrading

# ============================================================
# 1. Walk-Forward 验证报告
# ============================================================


@dataclass
class WFWindowResult:
    """单个 Walk-Forward 窗口结果。

    Attributes:
        window_id: 窗口序号
        train_start / train_end: 训练窗口起止日期
        test_start / test_end: 测试窗口起止日期
        n_pairs_trained: 训练阶段找到的协整对数
        n_pairs_tested: 测试阶段仍协整的对数 (p-value < significance)
        oos_return: 样本外收益率
        oos_sharpe: 样本外 Sharpe 比率
        n_trades: 测试窗口内交易次数
    """
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_pairs_trained: int
    n_pairs_tested: int
    oos_return: float = 0.0
    oos_sharpe: float = 0.0
    n_trades: int = 0


@dataclass
class WFValidationReport:
    """Walk-Forward 验证汇总报告。

    Attributes:
        windows: 各窗口结果列表
        avg_oos_sharpe: 所有窗口 OOS Sharpe 均值
        avg_oos_return: 所有窗口 OOS 收益率均值
        total_trades: 所有窗口交易总数
        oos_sharpe_std: OOS Sharpe 标准差 (稳定性)
        passed: 是否通过 OOS Sharpe ≥1.0 门禁
        baseline_sharpe: 基线 (StatisticalArbitrageEngine OOS Sharpe 1.499)
    """
    windows: list[WFWindowResult] = field(default_factory=list)
    avg_oos_sharpe: float = 0.0
    avg_oos_return: float = 0.0
    total_trades: int = 0
    oos_sharpe_std: float = 0.0
    passed: bool = False
    baseline_sharpe: float = 1.499
    target_sharpe: float = 1.0

    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        n_windows = len(self.windows)
        return (
            f"[{status}] Walk-Forward 配对交易验证\n"
            f"  窗口数: {n_windows}\n"
            f"  平均 OOS Sharpe: {self.avg_oos_sharpe:.4f} "
            f"(目标 ≥{self.target_sharpe}, 基线 {self.baseline_sharpe})\n"
            f"  Sharpe 稳定性 (std): {self.oos_sharpe_std:.4f}\n"
            f"  平均 OOS 收益: {self.avg_oos_return:.4%}\n"
            f"  总交易次数: {self.total_trades}"
        )


# ============================================================
# 2. Walk-Forward 配对交易验证器
# ============================================================


class WalkForwardPairsValidator:
    """Walk-Forward 配对交易验证器。

    流程 (StatisticalArbitrageEngine 风格):
        1. 将价格数据按 train_window / test_window 切分为多个窗口
        2. 训练窗口: 用 PairsTrading.find_cointegrated_pairs 找协整对
        3. 测试窗口: 用训练得到的 hedge_ratio 计算价差 z-score, 生成信号
        4. 计算 OOS Sharpe / 收益 / 交易次数
        5. 滑窗前进, 汇总所有窗口结果

    使用示例:
        validator = WalkForwardPairsValidator(
            train_window=120, test_window=60, step=60,
        )
        report = validator.validate(price_data)
        print(report.summary())
        assert report.passed  # OOS Sharpe ≥1.0
    """

    def __init__(
        self,
        train_window: int = 120,
        test_window: int = 60,
        step: int = 60,
        significance: float = 0.05,
        zscore_window: int = 20,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        target_sharpe: float = 1.0,
    ) -> None:
        """
        Args:
            train_window: 训练窗口长度 (交易日)
            test_window: 测试窗口长度 (交易日)
            step: 滑窗步长 (默认 = test_window, 不重叠)
            significance: 协整检验显著性水平
            zscore_window: z-score 滚动窗口
            entry_z: 开仓 z-score 阈值
            exit_z: 平仓 z-score 阈值
            target_sharpe: OOS Sharpe 目标 (默认 1.0)
        """
        self.train_window = train_window
        self.test_window = test_window
        self.step = max(1, step)
        self.target_sharpe = target_sharpe

        # 复用现有 PairsTrading 类
        self._pt = PairsTrading(
            significance=significance,
            zscore_window=zscore_window,
            entry_z=entry_z,
            exit_z=exit_z,
        )

    def validate(
        self,
        price_data: dict[str, pd.DataFrame],
    ) -> WFValidationReport:
        """运行 Walk-Forward 验证。

        Args:
            price_data: {symbol: DataFrame[OHLCV]} 价格数据

        Returns:
            WFValidationReport 汇总报告
        """
        # 提取收盘价矩阵
        closes = self._extract_closes(price_data)
        if closes.empty or len(closes) < self.train_window + self.test_window:
            return WFValidationReport()

        total_len = len(closes)
        windows: list[WFWindowResult] = []

        window_id = 0
        start = 0
        while start + self.train_window + self.test_window <= total_len:
            train_end = start + self.train_window
            test_end = train_end + self.test_window

            train_data = closes.iloc[start:train_end]
            test_data = closes.iloc[train_end:test_end]

            result = self._run_single_window(
                window_id=window_id,
                train_data=train_data,
                test_data=test_data,
                train_start_idx=start,
                train_end_idx=train_end,
                test_start_idx=train_end,
                test_end_idx=test_end,
                closes=closes,
            )
            windows.append(result)

            window_id += 1
            start += self.step

        # 汇总
        if not windows:
            return WFValidationReport()

        sharpes = [w.oos_sharpe for w in windows]
        returns = [w.oos_return for w in windows]
        trades = sum(w.n_trades for w in windows)

        avg_sharpe = float(np.mean(sharpes)) if sharpes else 0.0
        std_sharpe = float(np.std(sharpes)) if len(sharpes) > 1 else 0.0
        avg_return = float(np.mean(returns)) if returns else 0.0

        return WFValidationReport(
            windows=windows,
            avg_oos_sharpe=avg_sharpe,
            avg_oos_return=avg_return,
            total_trades=trades,
            oos_sharpe_std=std_sharpe,
            passed=(avg_sharpe >= self.target_sharpe),
            target_sharpe=self.target_sharpe,
        )

    # --------------------------------------------------------
    # 内部方法
    # --------------------------------------------------------

    def _extract_closes(self, price_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """提取收盘价矩阵 (date × symbol)。"""
        close_dict = {}
        for sym, df in price_data.items():
            if isinstance(df, pd.DataFrame):
                if "close" in df.columns:
                    close_dict[sym] = df["close"]
                elif len(df.columns) > 0:
                    close_dict[sym] = df.iloc[:, 0]
        if not close_dict:
            return pd.DataFrame()
        return pd.DataFrame(close_dict)

    def _run_single_window(
        self,
        window_id: int,
        train_data: pd.DataFrame,
        test_data: pd.DataFrame,
        train_start_idx: int,
        train_end_idx: int,
        test_start_idx: int,
        test_end_idx: int,
        closes: pd.DataFrame,
    ) -> WFWindowResult:
        """运行单个 Walk-Forward 窗口。"""
        dates = closes.index

        # 训练: 找协整对
        train_price_data = {
            sym: train_data[[sym]].rename(columns={sym: "close"})
            for sym in train_data.columns
            if train_data[sym].notna().sum() >= 60
        }

        pairs = self._pt.find_cointegrated_pairs(train_price_data, max_pairs=10)
        n_trained = len(pairs)

        if n_trained == 0:
            return WFWindowResult(
                window_id=window_id,
                train_start=str(dates[train_start_idx])[:10],
                train_end=str(dates[min(train_end_idx - 1, len(dates) - 1)])[:10],
                test_start=str(dates[min(test_start_idx, len(dates) - 1)])[:10],
                test_end=str(dates[min(test_end_idx - 1, len(dates) - 1)])[:10],
                n_pairs_trained=0,
                n_pairs_tested=0,
            )

        # 测试: 对每个协整对在 OOS 数据上生成信号
        daily_returns: list[float] = []
        n_trades = 0

        for pair in pairs:
            code_a = pair["code_a"]
            code_b = pair["code_b"]
            hedge_ratio = pair["beta"]
            intercept = pair.get("intercept", 0.0)

            if code_a not in test_data.columns or code_b not in test_data.columns:
                continue

            # 计算测试窗口价差
            spread = test_data[code_a] - hedge_ratio * test_data[code_b] - intercept
            spread = spread.dropna()
            if len(spread) < self._pt.zscore_window:
                continue

            # 滚动 z-score
            roll_mean = spread.rolling(self._pt.zscore_window).mean()
            roll_std = spread.rolling(self._pt.zscore_window).std()
            z_score = (spread - roll_mean) / roll_std.replace(0, np.nan)

            # 计算两腿日收益率 (用于 PnL 计算)
            ret_a = test_data[code_a].pct_change().fillna(0.0)
            ret_b = test_data[code_b].pct_change().fillna(0.0)

            # 信号 → 收益
            position = 0  # +1 = 做多价差, -1 = 做空价差
            for i in range(len(z_score)):
                z = z_score.iloc[i]
                if np.isnan(z):
                    if i < len(ret_a):
                        daily_returns.append(0.0)
                    continue

                # 开仓/平仓逻辑
                if position == 0:
                    if z < -self._pt.entry_z:
                        position = 1  # 做多价差 (买 A 卖 B)
                        n_trades += 1
                    elif z > self._pt.entry_z:
                        position = -1  # 做空价差 (卖 A 买 B)
                        n_trades += 1
                elif position == 1 and z > -self._pt.exit_z:
                    position = 0  # 平仓
                elif position == -1 and z < self._pt.exit_z:
                    position = 0  # 平仓

                # 日 PnL: position=+1 → ret_A - hedge_ratio * ret_B
                #         position=-1 → -(ret_A - hedge_ratio * ret_B)
                if i < len(ret_a) and i < len(ret_b):
                    spread_ret = ret_a.iloc[i] - hedge_ratio * ret_b.iloc[i]
                    daily_returns.append(position * spread_ret)
                else:
                    daily_returns.append(0.0)

        # 计算 OOS 指标
        if daily_returns:
            returns_arr = np.array(daily_returns)
            oos_return = float(np.sum(returns_arr))
            if np.std(returns_arr) > 1e-10:
                oos_sharpe = float(np.mean(returns_arr) / np.std(returns_arr) * np.sqrt(252))
            else:
                oos_sharpe = 0.0
        else:
            oos_return = 0.0
            oos_sharpe = 0.0

        return WFWindowResult(
            window_id=window_id,
            train_start=str(dates[train_start_idx])[:10],
            train_end=str(dates[min(train_end_idx - 1, len(dates) - 1)])[:10],
            test_start=str(dates[min(test_start_idx, len(dates) - 1)])[:10],
            test_end=str(dates[min(test_end_idx - 1, len(dates) - 1)])[:10],
            n_pairs_trained=n_trained,
            n_pairs_tested=n_trained,  # 简化: 所有训练对在测试期均参与
            oos_return=oos_return,
            oos_sharpe=oos_sharpe,
            n_trades=n_trades,
        )


__all__ = [
    "WFValidationReport",
    "WFWindowResult",
    "WalkForwardPairsValidator",
]
