# -*- coding: utf-8 -*-
"""
Triple Barrier Labeling — 三维标签引擎 v1.0

基于《Advances in Financial Machine Learning》(Marcos Lopez de Prado) 第三章方法：
- 竖立上/下两道屏障（止盈/止损百分比边界）
- 竖立时间屏障（最大持仓天数）
- 哪个屏障先被触及，标签就是哪个方向

优势：
- 相比简单次日涨跌标签，Triple Barrier 考虑了实际交易中的止盈止损
- 显著提升标签的"实盘可执行性"
- 与ML模型结合后，可输出"买入后持N天"的实际操作建议
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class BarrierConfig:
    """三道屏障配置"""
    upper_barrier: float      # 上屏障（止盈），如 0.05 = +5%
    lower_barrier: float      # 下屏障（止损），如 -0.03 = -3%
    time_barrier: int         # 时间屏障（持有天数），如 10
    volatility_scaled: bool = False  # 是否根据波动率动态调整屏障宽度
    volatility_window: int = 20     # 计算波动率的窗口


@dataclass
class LabelResult:
    """单个标的的标签结果"""
    labels: np.ndarray        # -1=下触, 0=时间耗尽, 1=上触
    first_touch_barrier: np.ndarray  # 0=下屏障, 1=上屏障, 2=时间屏障
    days_to_touch: np.ndarray      # 触及首个屏障所需天数
    returns_at_touch: np.ndarray   # 触及首个屏障时的累计收益率


class TripleBarrierLabeler:
    """Triple Barrier 标签生成器

    对每个时间点，竖立三道屏障：
    1. 上屏障（止盈位）：close * (1 + upper_barrier)
    2. 下屏障（止损位）：close * (1 - lower_barrier)  
    3. 时间屏障：T个交易日后的收盘价

    标签 = 最先触及的屏障：
    - +1：上屏障先被触及 → 看多信号
    - -1：下屏障先被触及 → 看空信号
    -  0：时间屏障先被耗尽 → 无明确方向
    """

    def __init__(self, config: BarrierConfig = None):
        self.config = config or BarrierConfig(
            upper_barrier=0.05,
            lower_barrier=0.03,
            time_barrier=10,
            volatility_scaled=True,
            volatility_window=20,
        )

    def generate_labels(self, df: pd.DataFrame,
                        upper: float = None,
                        lower: float = None,
                        time_barrier: int = None) -> LabelResult:
        """
        为给定K线数据生成 Triple Barrier 标签。

        Args:
            df: 包含 'close' 列的 DataFrame，按时间升序排列
            upper: 上屏障百分比（覆盖 config 默认值）
            lower: 下屏障百分比（覆盖 config 默认值）
            time_barrier: 时间屏障天数（覆盖 config 默认值）

        Returns:
            LabelResult 包含 labels / first_touch_barrier / days_to_touch / returns_at_touch
        """
        if 'close' not in df.columns:
            raise ValueError("DataFrame 必须包含 'close' 列")

        upper = upper if upper is not None else self.config.upper_barrier
        lower = lower if lower is not None else self.config.lower_barrier
        t_barrier = time_barrier if time_barrier is not None else self.config.time_barrier

        close = df['close'].values.astype(np.float64)
        n = len(close)

        labels = np.full(n, 0, dtype=np.int8)
        first_touch = np.full(n, 2, dtype=np.int8)  # 默认时间耗尽
        days_to_touch = np.full(n, t_barrier, dtype=np.int32)
        returns_at_touch = np.zeros(n, dtype=np.float64)

        # 动态屏障宽度（基于波动率）
        if self.config.volatility_scaled:
            vol = pd.Series(close).pct_change().rolling(
                self.config.volatility_window).std().values
            upper_vec = upper * (1 + np.nan_to_num(vol, nan=0) / 0.02)  # 20%基准波动率归一化
            lower_vec = lower * (1 + np.nan_to_num(vol, nan=0) / 0.02)
        else:
            upper_vec = np.full(n, upper)
            lower_vec = np.full(n, lower)

        for i in range(n - t_barrier):
            entry_price = close[i]
            upper_price = entry_price * (1 + upper_vec[i])
            lower_price = entry_price * (1 - lower_vec[i])

            # 扫描未来 t_barrier 天
            touched_up = False
            touched_down = False
            touch_day = t_barrier

            for j in range(1, t_barrier + 1):
                if i + j >= n:
                    break
                current_price = close[i + j]
                if current_price >= upper_price:
                    touched_up = True
                    touch_day = j
                    break
                elif current_price <= lower_price:
                    touched_down = True
                    touch_day = j
                    break

            if touched_up:
                labels[i] = 1
                first_touch[i] = 1
                days_to_touch[i] = touch_day
                returns_at_touch[i] = (close[i + touch_day] / entry_price) - 1
            elif touched_down:
                labels[i] = -1
                first_touch[i] = 0
                days_to_touch[i] = touch_day
                returns_at_touch[i] = (close[i + touch_day] / entry_price) - 1
            else:
                # 时间屏障耗尽
                labels[i] = 0
                first_touch[i] = 2
                days_to_touch[i] = t_barrier
                returns_at_touch[i] = (close[i + t_barrier] / entry_price) - 1

        return LabelResult(
            labels=labels,
            first_touch_barrier=first_touch,
            days_to_touch=days_to_touch,
            returns_at_touch=returns_at_touch,
        )

    def generate_labels_dataframe(self, df: pd.DataFrame,
                                   upper: float = None,
                                   lower: float = None,
                                   time_barrier: int = None) -> pd.DataFrame:
        """
        生成标签并返回增强的 DataFrame（包含原始数据 + 标签列）。

        Returns:
            包含 'triple_barrier_label', 'barrier_type', 'days_to_touch', 'return_at_touch' 的 DataFrame
            以及 'label_binary'（将 -1/+1 合并为做多/做空二分类：1=做多, 0=做空/观望）
        """
        result = self.generate_labels(df, upper, lower, time_barrier)
        df_out = df.copy()
        df_out['triple_barrier_label'] = result.labels
        df_out['barrier_type'] = result.first_touch_barrier
        df_out['days_to_touch'] = result.days_to_touch
        df_out['return_at_touch'] = result.returns_at_touch
        # 生成二分类标签：上触=做多(1), 下触=做空/时间耗尽=观望(0)
        df_out['label_binary'] = (result.labels == 1).astype(int)
        return df_out

    def get_label_stats(self, labels: np.ndarray) -> Dict[str, Any]:
        """统计标签分布"""
        total = len(labels)
        up_count = np.sum(labels == 1)
        down_count = np.sum(labels == -1)
        neutral_count = np.sum(labels == 0)
        return {
            'total_samples': total,
            'up_touch': up_count,
            'up_touch_pct': round(up_count / total * 100, 1) if total > 0 else 0,
            'down_touch': down_count,
            'down_touch_pct': round(down_count / total * 100, 1) if total > 0 else 0,
            'time_exhausted': neutral_count,
            'time_exhausted_pct': round(neutral_count / total * 100, 1) if total > 0 else 0,
            'signal_ratio': round((up_count + down_count) / total * 100, 1) if total > 0 else 0,
        }


# ── Meta-Labeling 二层模型辅助 ──

class MetaLabeler:
    """
    Meta-Labeling 二层标签生成器。
    
    在 Triple Barrier 基础上，生成"当前信号是否值得执行"的二层标签：
    - Meta-Label = 1：跟随主模型信号可获得正收益
    - Meta-Label = 0：跟随主模型信号会亏损
    
    用于训练二层过滤模型，减少主模型的假阳性信号。
    """

    def __init__(self, barrier_config: BarrierConfig = None):
        self.barrier_labeler = TripleBarrierLabeler(barrier_config)

    def generate_meta_labels(self, df: pd.DataFrame,
                              primary_signals: np.ndarray,
                              upper: float = 0.05,
                              lower: float = 0.03,
                              time_barrier: int = 10) -> np.ndarray:
        """
        生成 Meta-Label。

        Args:
            df: 包含 'close' 列的 DataFrame
            primary_signals: 主模型预测信号，1=做多, 0=观望/做空
            upper/lower/time_barrier: Triple Barrier 参数

        Returns:
            meta_labels: 1=主信号正确（执行获利），0=主信号错误（执行亏损）
            对于 primary_signal=0 的样本，meta_label 固定为 0
        """
        triple_result = self.barrier_labeler.generate_labels(
            df, upper=upper, lower=lower, time_barrier=time_barrier
        )

        meta_labels = np.zeros(len(df), dtype=np.int8)

        for i in range(len(df)):
            if primary_signals[i] == 1:
                # 主模型建议做多
                if triple_result.labels[i] == 1:
                    meta_labels[i] = 1  # 实际上屏障被触 → 做多正确
                else:
                    meta_labels[i] = 0  # 未触及上屏障 → 做多错误
            else:
                meta_labels[i] = 0

        return meta_labels


# ── 便捷批处理 ──

def batch_label(stock_data: Dict[str, pd.DataFrame],
                upper: float = 0.05,
                lower: float = 0.03,
                time_barrier: int = 10) -> Dict[str, pd.DataFrame]:
    """
    批量为多只标的生成 Triple Barrier 标签。

    Args:
        stock_data: {code: DataFrame(含 'close' 列)}
        upper/lower/time_barrier: 屏障参数

    Returns:
        {code: DataFrame(含标签列)}
    """
    labeler = TripleBarrierLabeler(BarrierConfig(
        upper_barrier=upper,
        lower_barrier=lower,
        time_barrier=time_barrier,
        volatility_scaled=True,
    ))

    results = {}
    for code, df in stock_data.items():
        try:
            labeled_df = labeler.generate_labels_dataframe(df, upper, lower, time_barrier)
            results[code] = labeled_df
        except Exception as e:
            print(f"[TripleBarrier] 标签生成失败 {code}: {e}")

    return results


if __name__ == '__main__':
    # 简单自测
    np.random.seed(42)
    n_days = 500
    # 模拟带趋势+噪声的价格序列
    close = 100 * np.exp(np.cumsum(np.random.randn(n_days) * 0.01))
    df = pd.DataFrame({'close': close})

    labeler = TripleBarrierLabeler()
    result = labeler.generate_labels(df, upper=0.05, lower=0.03, time_barrier=10)
    stats = labeler.get_label_stats(result.labels)

    print("=== Triple Barrier Label 统计 ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  上触天数均值: {result.days_to_touch[result.labels == 1].mean():.1f}")
    print(f"  下触天数均值: {result.days_to_touch[result.labels == -1].mean():.1f}")
