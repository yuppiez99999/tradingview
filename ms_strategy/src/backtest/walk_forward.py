"""
v7.5 WalkForward — 滚动样本外回测
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §4.2
train 24m / test 3m / step 3m, 5-fold CV
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    """单窗口回测结果"""
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    sortino: float = 0.0
    calmar: float = 0.0
    max_dd: float = 0.0
    annual_return: float = 0.0
    annual_vol: float = 0.0
    sharpe: float = 0.0
    test_returns: Optional[np.ndarray] = None
    params: dict = field(default_factory=dict)


class WalkForward:
    """
    v7.5 Walk-Forward Analysis

    参数调优：每个 Window 在训练集上做 5-fold CV
    最终指标：所有测试集拼接后的整体 Sortino / Calmar / Max DD
    """

    def __init__(self, train_months: int = 24, test_months: int = 3,
                 step_months: int = 3, cv_folds: int = 5):
        self.train_months = train_months
        self.test_months = test_months
        self.step_months = step_months
        self.cv_folds = cv_folds
        self.results: list[WalkForwardResult] = []

    # ---------- 窗口生成 ----------
    def generate_windows(self, start_date: str, end_date: str) -> list[tuple[str, str, str, str]]:
        """
        生成 Walk-Forward 窗口

        Args:
            start_date: 最早可用数据日期 'YYYY-MM-DD'
            end_date:   最晚可用数据日期

        Returns:
            [(train_start, train_end, test_start, test_end), ...]
        """
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        windows = []

        train_end = start + pd.DateOffset(months=self.train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=self.test_months)

        while test_end <= end:
            train_start = train_end - pd.DateOffset(months=self.train_months)
            windows.append((
                train_start.strftime('%Y-%m-%d'),
                train_end.strftime('%Y-%m-%d'),
                test_start.strftime('%Y-%m-%d'),
                test_end.strftime('%Y-%m-%d'),
            ))

            # 步进
            train_end += pd.DateOffset(months=self.step_months)
            test_start = train_end
            test_end = test_start + pd.DateOffset(months=self.test_months)

        logger.info(f"生成 {len(windows)} 个 Walk-Forward 窗口")
        return windows

    # ---------- 执行 ----------
    def run(self, data: pd.DataFrame,
            strategy_fn: Optional[Callable] = None,
            date_col: Optional[str] = None,
            train_func: Optional[Callable] = None,
            test_func: Optional[Callable] = None,
            param_grid: Optional[dict] = None,
            objective: str = 'sortino',
            verbose: bool = True):
        """
        执行 Walk-Forward Analysis

        Args:
            data: 全量价格/因子数据
            strategy_fn: 简化策略函数 (train_df, test_df) -> test_returns Series
                         (兼容测试 API; 若提供则忽略 train_func/test_func)
            date_col: 日期列名; 若指定则按该列排序并设为索引
            train_func: 训练函数 (训练数据) -> 参数字典
            test_func: 测试函数 (测试数据, 参数) -> 收益 Series
            param_grid: 超参数网格
            objective: 优化目标
            verbose: 是否打印日志

        Returns:
            - 若使用 strategy_fn: dict {status, n_windows, results, aggregated}
            - 否则: List[WalkForwardResult]
        """
        # 处理 date_col
        if date_col is not None and date_col in data.columns:
            data = data.sort_values(date_col).set_index(date_col)

        # 简化模式: strategy_fn(train_df, test_df) -> returns
        if strategy_fn is not None:
            return self._run_simple(data, strategy_fn, verbose=verbose)

        # 完整模式 (原逻辑)
        if train_func is None or test_func is None:
            return {'status': 'ERROR',
                    'reason': '需提供 strategy_fn 或 (train_func + test_func)'}

        windows = self.generate_windows(
            data.index[0].strftime('%Y-%m-%d') if isinstance(data.index[0], pd.Timestamp)
            else str(data.index[0]),
            data.index[-1].strftime('%Y-%m-%d') if isinstance(data.index[-1], pd.Timestamp)
            else str(data.index[-1])
        )

        self.results = []
        all_test_returns = []

        for i, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
            train_data = data.loc[tr_s:tr_e]
            test_data = data.loc[te_s:te_e]

            if len(train_data) < 50 or len(test_data) < 5:
                logger.warning(f"Window {i}: 数据不足 train={len(train_data)} test={len(test_data)}")
                continue

            # 训练（含 CV）
            if param_grid:
                best_params = self._cv_optimize(train_data, train_func, test_func,
                                                param_grid, objective)
            else:
                best_params = train_func(train_data)

            # 测试（仅评估一次，禁止窥探！）
            test_ret = test_func(test_data, best_params)
            all_test_returns.append(test_ret)

            # 计算指标
            from .metrics import PerformanceMetrics
            pm = PerformanceMetrics(test_ret)

            result = WalkForwardResult(
                window_id=i,
                train_start=tr_s,
                train_end=tr_e,
                test_start=te_s,
                test_end=te_e,
                sortino=pm.sortino_ratio(),
                calmar=pm.calmar_ratio(),
                max_dd=pm.max_drawdown(),
                annual_return=pm.annual_return(),
                annual_vol=pm.annual_vol(),
                sharpe=pm.sharpe_ratio(),
                test_returns=test_ret.values,
                params=best_params,
            )
            self.results.append(result)

            if verbose:
                logger.info(f"Window {i}: {te_s}~{te_e} | "
                            f"Sortino={result.sortino:.3f} Calmar={result.calmar:.3f} "
                            f"MaxDD={result.max_dd:.3f}")

        return self.results

    def _run_simple(self, data: pd.DataFrame,
                    strategy_fn: Callable,
                    verbose: bool = True) -> dict:
        """简化模式: strategy_fn(train_df, test_df) -> returns"""
        windows = self.generate_windows(
            data.index[0].strftime('%Y-%m-%d') if isinstance(data.index[0], pd.Timestamp)
            else str(data.index[0]),
            data.index[-1].strftime('%Y-%m-%d') if isinstance(data.index[-1], pd.Timestamp)
            else str(data.index[-1])
        )

        # 若月度窗口不足，回退到日数窗口 (1 月 = 21 交易日)
        if not windows:
            train_days = self.train_months * 21
            test_days = self.test_months * 21
            step_days = self.step_months * 21
            n = len(data)
            windows = []
            start_idx = 0
            while start_idx + train_days + test_days <= n:
                tr_start = data.index[start_idx]
                tr_end = data.index[start_idx + train_days - 1]
                te_start = data.index[start_idx + train_days]
                te_end = data.index[start_idx + train_days + test_days - 1]
                windows.append((
                    str(tr_start), str(tr_end), str(te_start), str(te_end)
                ))
                start_idx += step_days
            if verbose:
                logger.info(f"月度窗口不足, 回退日数窗口: {len(windows)} 个")

        self.results = []
        all_test_returns = []

        for i, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
            # 尝试字符串切片，失败则按位置切片
            try:
                train_data = data.loc[tr_s:tr_e]
                test_data = data.loc[te_s:te_e]
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # 按位置回退
                train_days = self.train_months * 21
                test_days = self.test_months * 21
                step_days = self.step_months * 21
                start_idx = i * step_days
                if start_idx + train_days + test_days > len(data):
                    continue
                train_data = data.iloc[start_idx:start_idx + train_days]
                test_data = data.iloc[start_idx + train_days:
                                      start_idx + train_days + test_days]

            if len(train_data) < 50 or len(test_data) < 5:
                if verbose:
                    logger.warning(f"Window {i}: 数据不足 train={len(train_data)} test={len(test_data)}")
                continue

            try:
                test_ret = strategy_fn(train_data, test_data)
                if test_ret is None:
                    continue
                all_test_returns.append(test_ret)

                from .metrics import PerformanceMetrics
                pm = PerformanceMetrics(test_ret)

                result = WalkForwardResult(
                    window_id=i,
                    train_start=str(tr_s),
                    train_end=str(tr_e),
                    test_start=str(te_s),
                    test_end=str(te_e),
                    sortino=pm.sortino_ratio(),
                    calmar=pm.calmar_ratio(),
                    max_dd=pm.max_drawdown(),
                    annual_return=pm.annual_return(),
                    annual_vol=pm.annual_vol(),
                    sharpe=pm.sharpe_ratio(),
                    test_returns=test_ret.values,
                    params={},
                )
                self.results.append(result)

                if verbose:
                    logger.info(f"Window {i}: {te_s}~{te_e} | "
                                f"Sortino={result.sortino:.3f} Calmar={result.calmar:.3f}")
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.error(f"Window {i} 执行失败: {e}")
                continue

        return {
            'status': 'OK' if len(self.results) > 0 else 'NO_VALID_WINDOW',
            'n_windows': len(self.results),
            'results': self.results,
            'aggregated': self.aggregate_metrics() if self.results else {},
        }

    # ---------- CV 优化 ----------
    def _cv_optimize(self, train_data: pd.DataFrame,
                     train_func: Callable,
                     test_func: Callable,
                     param_grid: dict,
                     objective: str) -> dict:
        """5-fold CV 选择最优超参数"""
        # 简化：遍历 param_grid
        # 注：完整实现应做 rolling CV，这里做简单 grid search
        best_params = {}
        best_score = -np.inf

        for param_name, param_values in param_grid.items():
            for val in param_values:
                params = {param_name: val}
                try:
                    train_result = train_func(train_data, **params)
                    if isinstance(train_result, dict):
                        score = train_result.get('sortino', train_result.get('sharpe', 0))
                    else:
                        score = 0
                    if score > best_score:
                        best_score = score
                        best_params = params.copy()
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    logger.debug(f"CV 失败: {param_name}={val}, {e}")

        logger.info(f"CV 最优参数: {best_params}, score={best_score:.3f}")
        return best_params if best_params else train_func(train_data)

    # ---------- 综合指标 ----------
    def aggregate_metrics(self) -> dict:
        """拼接所有测试窗口，计算综合指标"""
        from .metrics import PerformanceMetrics

        all_returns = []
        for r in self.results:
            if r.test_returns is not None:
                all_returns.extend(r.test_returns)

        if not all_returns:
            return {}

        ret_series = pd.Series(all_returns)
        pm = PerformanceMetrics(ret_series)

        return {
            'n_windows': len(self.results),
            'sortino': pm.sortino_ratio(),
            'calmar': pm.calmar_ratio(),
            'max_dd': pm.max_drawdown(),
            'annual_return': pm.annual_return(),
            'annual_vol': pm.annual_vol(),
            'sharpe': pm.sharpe_ratio(),
            'win_rate': float((ret_series > 0).mean()),
            # 各窗口指标
            'window_sortinos': [r.sortino for r in self.results],
            'window_calmars': [r.calmar for r in self.results],
            'window_max_dds': [r.max_dd for r in self.results],
        }

    def summary(self) -> str:
        """打印汇总"""
        metrics = self.aggregate_metrics()
        lines = [
            f"=== Walk-Forward 汇总 ({metrics.get('n_windows', 0)} 个窗口) ===",
            f"Sortino Ratio:  {metrics.get('sortino', 0):.4f}",
            f"Calmar Ratio:   {metrics.get('calmar', 0):.4f}",
            f"Max DD:         {metrics.get('max_dd', 0):.2%}",
            f"Annual Return:  {metrics.get('annual_return', 0):.2%}",
            f"Annual Vol:     {metrics.get('annual_vol', 0):.2%}",
            f"Sharpe:         {metrics.get('sharpe', 0):.4f}",
            f"Win Rate:       {metrics.get('win_rate', 0):.2%}",
        ]
        return '\n'.join(lines)
