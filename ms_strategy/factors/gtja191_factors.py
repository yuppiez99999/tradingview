"""
GTJA191 因子库 (部分实现, 21/191) — Alpha191 / 国泰君安191因子

T04 更新 (2026-07-27):
    本模块已补齐 21 个低相关因子 (原仅 Alpha144), 覆盖量价/动量/波动率/反转四大类。
    因子相关性矩阵 max ρ < 0.7 (待 T04 验收测试确认)。

    后续计划:
        - 完整 191 因子需配合 Qlib Expression Engine 落地, 不是 Python 重写
        - 如需更多因子, 优先考虑 WorldQuant Alpha101 互补

当前实现 (21 个):
    量价类(5):   Alpha6/12/54/85/101
    动量类(5):   Alpha22/25/28/132/178
    波动率类(5): Alpha30/33/40/43/57
    反转类(5):   Alpha4/19/24/26/131
    其他(1):     Alpha144 (下跌日成交额效率)

数据需求:
- close, open, high, low, volume, amount (按需)
- 至少 lookback + 1 个日频数据点, 按时间升序

参考文献:
- 国泰君安证券: 《基于短周期量价特征的多因子选股》2017.06
- López de Prado: 《Advances in Financial Machine Learning》2018
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class GTJA191Factors:
    """GTJA191 短周期量价因子计算器 (21/191 部分实现)

    实现 21 个低相关因子, 覆盖量价/动量/波动率/反转四大类。
    所有方法接受 pd.DataFrame (含 close/open/high/low/volume/amount 列),
    返回 float 或 None (数据不足)。
    """

    def __init__(self, lookback: int = 20):
        """
        Args:
            lookback: 统计窗口, 默认 20 日。
        """
        self.lookback = lookback

    # ============================================================
    # 辅助函数
    # ============================================================
    @staticmethod
    def _safe_divide(a: np.ndarray, b: np.ndarray, fill: float = 0.0) -> np.ndarray:
        """安全除法, 分母为 0 时返回 fill"""
        with np.errstate(divide="ignore", invalid="ignore"):
            result = np.where(np.abs(b) > 1e-12, a / b, fill)
        return result

    @staticmethod
    def _wma(values: np.ndarray, window: int) -> float:
        """加权移动平均 (权重线性递增 1..window)"""
        if len(values) < window:
            return float("nan")
        weights = np.arange(1, window + 1, dtype=float)
        weights /= weights.sum()
        return float(np.dot(values[-window:], weights))

    @staticmethod
    def _rank(values: np.ndarray) -> np.ndarray:
        """排名归一化到 [0, 1]"""
        if len(values) == 0:
            return values
        order = values.argsort()
        ranks = np.empty_like(order, dtype=float)
        ranks[order] = np.arange(1, len(values) + 1, dtype=float)
        return ranks / (len(values) + 1e-12)

    # ============================================================
    # 量价类因子 (5 个)
    # ============================================================
    def alpha6(self, df: pd.DataFrame) -> float | None:
        """Alpha6: SUM(IF(CLOSE>DELAY(CLOSE,1), 1, 0), 20) / COUNT(CLOSE>DELAY(CLOSE,1), 20)

        含义: 上涨天数占比 (基于收盘价)
        高值: 持续上涨趋势
        低值: 持续下跌趋势
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        close = df["close"].to_numpy(dtype=float)
        up = (close[1:] > close[:-1]).astype(float)
        if len(up) < self.lookback:
            return None
        tail_up = up[-self.lookback:]
        s = tail_up.sum()
        return float(s / self.lookback) if self.lookback > 0 else 0.0

    def alpha12(self, df: pd.DataFrame) -> float | None:
        """Alpha12: SUM(IF(CLOSE>DELAY(CLOSE,1), VOLUME, 0), 20) / SUM(IF(CLOSE<=DELAY(CLOSE,1), VOLUME, 0), 20)

        含义: 上涨日成交量 / 下跌日成交量
        高值: 上涨放量, 买盘积极
        低值: 下跌放量, 卖盘积极
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        if "volume" not in df.columns:
            return None
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        up = close[1:] > close[:-1]
        down = ~up
        tail_vol_up = vol[1:][up][-self.lookback:]
        tail_vol_down = vol[1:][down][-self.lookback:]
        denom = tail_vol_down.sum()
        if abs(denom) < 1e-12:
            return 0.0
        return float(tail_vol_up.sum() / denom)

    def alpha54(self, df: pd.DataFrame) -> float | None:
        """Alpha54 (T04 修正): SUM(IF(CLOSE>OPEN, 1, 0), 20)

        T04 FIX: 原公式 (上涨天数/下跌天数) 与 alpha6 数学等价
        (alpha54 = alpha6 / (1 - alpha6))。
        改为: 阳线天数 (CLOSE > OPEN, 当日开收关系, 非前后日关系)

        含义: 过去 20 日阳线天数
        高值: 阳线多 (多头主导)
        低值: 阴线多 (空头主导)
        """
        if df is None or len(df) < self.lookback:
            return None
        if "open" not in df.columns:
            return None
        close = df["close"].to_numpy(dtype=float)
        open_ = df["open"].to_numpy(dtype=float)
        bullish = close > open_
        if len(bullish) < self.lookback:
            return None
        return float(bullish[-self.lookback:].sum())

    def alpha85(self, df: pd.DataFrame) -> float | None:
        """Alpha85 (T04 修正): SUM((HIGH-LOW)/CLOSE * VOLUME, 20) / SUM(VOLUME, 20)

        T04 FIX: 原公式 (上涨日成交额/下跌日成交额) 与 alpha12 高度相关
        (因 amount = close * volume)。
        改为: 振幅加权成交量占比 ((HIGH-LOW)/CLOSE * VOLUME) / 总成交量

        含义: 单位价格振幅的成交量强度
        高值: 振幅大且成交活跃 (剧烈波动)
        低值: 振幅小或成交清淡
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        if "volume" not in df.columns or "high" not in df.columns or "low" not in df.columns:
            return None
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        # 振幅 = (HIGH - LOW) / CLOSE
        amp = self._safe_divide(high - low, close)
        # 振幅加权成交量
        weighted_vol = amp * vol
        tail_wv = weighted_vol[-self.lookback:]
        tail_v = vol[-self.lookback:]
        denom = tail_v.sum()
        if abs(denom) < 1e-12:
            return 0.0
        return float(tail_wv.sum() / denom)

    def alpha101(self, df: pd.DataFrame) -> float | None:
        """Alpha101: RANK(CORR(CLOSE, VOLUME, 20))

        含义: 收盘价与成交量的 20 日相关系数排名
        高值: 量价齐升 (正相关)
        低值: 量价背离 (负相关)
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        if "volume" not in df.columns:
            return None
        close = df["close"].to_numpy(dtype=float)[-self.lookback:]
        vol = df["volume"].to_numpy(dtype=float)[-self.lookback:]
        if len(close) < self.lookback:
            return None
        # 计算相关系数
        c_mean = close.mean()
        v_mean = vol.mean()
        cov = ((close - c_mean) * (vol - v_mean)).mean()
        c_std = close.std()
        v_std = vol.std()
        if c_std < 1e-12 or v_std < 1e-12:
            return 0.0
        corr = cov / (c_std * v_std)
        # rank 用全历史 (但这里只有 20 个点, 简化为 corr 本身归一化)
        return float(corr)

    # ============================================================
    # 动量类因子 (5 个)
    # ============================================================
    def alpha22(self, df: pd.DataFrame) -> float | None:
        """Alpha22: MEAN(((HIGH+LOW)/2-DELAY((HIGH+LOW)/2, 5))/DELAY((HIGH+LOW)/2, 5), 20)

        含义: 中价 5 日收益率的 20 日均值
        高值: 中价持续上涨
        """
        if df is None or len(df) < self.lookback + 5:
            return None
        if "high" not in df.columns or "low" not in df.columns:
            return None
        mid = (df["high"].to_numpy(dtype=float) + df["low"].to_numpy(dtype=float)) / 2.0
        ret5 = mid[5:] / mid[:-5] - 1.0
        if len(ret5) < self.lookback:
            return None
        return float(ret5[-self.lookback:].mean())

    def alpha25(self, df: pd.DataFrame) -> float | None:
        """Alpha25 (T04 修正): SUM(IF(CLOSE>DELAY(CLOSE,1), VOLUME, 0), 20)

        T04 FIX: 原公式 WMA(CLOSE/DELAY(CLOSE,5)-1, 20) 与 alpha22/alpha132 高度相关。
        改为: 上涨日成交量累计 (量能因子, 与价格类因子区分)

        含义: 过去 20 日上涨日成交量累计
        高值: 上涨时持续放量
        低值: 上涨时缩量
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        if "volume" not in df.columns:
            return None
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        up = close[1:] > close[:-1]
        tail_up_vol = vol[1:][up][-self.lookback:]
        return float(tail_up_vol.sum())

    def alpha28(self, df: pd.DataFrame) -> float | None:
        """Alpha28 (T04 修正): RANK over last 20 days of (DELAY(CLOSE,5)/CLOSE - 1)

        T04 FIX: 原公式返回 -ret5, 与 alpha178 完美负相关 (ρ=-1.0)。
        改为: 对最近 20 天的 5 日跌幅做排名 (排名值 ∈ (0,1))。

        含义: 当前 5 日跌幅在过去 20 天中的排名
        高值: 当前跌幅处于历史高位 (反转候选)
        低值: 当前跌幅处于历史低位
        """
        if df is None or len(df) < self.lookback + 5:
            return None
        close = df["close"].to_numpy(dtype=float)
        # 计算最近 20 天的 5 日跌幅序列
        if len(close) < self.lookback + 5:
            return None
        ret5_series = close[5:] / close[:-5] - 1.0  # 长度 len-5
        if len(ret5_series) < self.lookback:
            return None
        recent = ret5_series[-self.lookback:]
        current = recent[-1]
        # rank: 当前值在历史中的排名 (0-1)
        rank = np.sum(recent <= current) / len(recent)
        return float(rank)

    def alpha132(self, df: pd.DataFrame) -> float | None:
        """Alpha132: WMA(CLOSE/DELAY(CLOSE,1)-1, 20)

        含义: 日收益率的 20 日加权移动平均
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        close = df["close"].to_numpy(dtype=float)
        ret1 = close[1:] / close[:-1] - 1.0
        if len(ret1) < self.lookback:
            return None
        return self._wma(ret1, self.lookback)

    def alpha178(self, df: pd.DataFrame) -> float | None:
        """Alpha178: (CLOSE-DELAY(CLOSE,5))/DELAY(CLOSE,5)

        含义: 5 日收益率 (简化版动量)
        """
        if df is None or len(df) < 6:
            return None
        close = df["close"].to_numpy(dtype=float)
        return float(close[-1] / close[-6] - 1.0)

    # ============================================================
    # 波动率类因子 (5 个)
    # ============================================================
    def alpha30(self, df: pd.DataFrame) -> float | None:
        """Alpha30 (T04 修正): STD(CLOSE/DELAY(CLOSE,5)-1, 20)

        T04 FIX: 原公式 WMA((CLOSE-DELAY(CLOSE,5))/DELAY(CLOSE,5), 20) 与 alpha25 完全相同。
        改为: 5 日收益率的 20 日标准差 (真正的波动率, 非 WMA)。

        含义: 5 日收益率的 20 日波动率
        高值: 5 日收益波动剧烈
        低值: 5 日收益平稳
        """
        if df is None or len(df) < self.lookback + 5:
            return None
        close = df["close"].to_numpy(dtype=float)
        ret5 = close[5:] / close[:-5] - 1.0
        if len(ret5) < self.lookback:
            return None
        return float(ret5[-self.lookback:].std())

    def alpha33(self, df: pd.DataFrame) -> float | None:
        """Alpha33: 1 - RANK(STD(RET,20))

        含义: 1 - 收益率 20 日标准差排名
        高值: 波动率低 (相对历史平静)
        低值: 波动率高 (相对历史剧烈)
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        close = df["close"].to_numpy(dtype=float)
        ret1 = close[1:] / close[:-1] - 1.0
        if len(ret1) < self.lookback:
            return None
        std20 = ret1[-self.lookback:].std()
        # 简化: 用 std 本身反向归一化 (越大越接近 0, 越小越接近 1)
        return float(1.0 - min(std20, 1.0))

    def alpha40(self, df: pd.DataFrame) -> float | None:
        """Alpha40 (T04 修正): SUM(IF(CLOSE>DELAY(CLOSE,1), VOL/DELAY(VOL,1), 0), 20)

        T04 FIX: 原公式 SUM(IF(CLOSE>DELAY(CLOSE,1), 1, 0), 20) 与 alpha6 完全线性相关。
        改为: 上涨日成交量增长率之和, 区分于 alpha6 (上涨天数占比)。

        含义: 上涨日成交量相对前一日增长率之和
        高值: 上涨时持续放量
        """
        if df is None or len(df) < self.lookback + 2:
            return None
        if "volume" not in df.columns:
            return None
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        # 上涨日 (close[t] > close[t-1]) 的成交量增长率
        up = close[1:] > close[:-1]
        vol_ratio = np.zeros_like(vol[1:])
        valid = vol[:-1] > 1e-12
        vol_ratio[valid] = vol[1:][valid] / vol[:-1][valid] - 1.0
        # 取最近 lookback 期
        if len(vol_ratio) < self.lookback:
            return None
        tail_up = up[-self.lookback:]
        tail_ratio = vol_ratio[-self.lookback:]
        return float((tail_up * tail_ratio).sum())

    def alpha43(self, df: pd.DataFrame) -> float | None:
        """Alpha43: SUM(IF(CLOSE>DELAY(CLOSE,1), STD(CLOSE,20), 0), 20) / SUM(IF(CLOSE<=DELAY(CLOSE,1), STD(CLOSE,20), 0), 20)

        含义: 上涨日波动率之和 / 下跌日波动率之和
        高值: 上涨日波动大 (健康上涨)
        低值: 下跌日波动大 (恐慌下跌)
        """
        if df is None or len(df) < self.lookback * 2 + 1:
            return None
        close = df["close"].to_numpy(dtype=float)
        # 滚动 20 日 std
        std_series = np.array([
            close[max(0, i - self.lookback):i + 1].std() if i >= 1 else 0.0
            for i in range(len(close))
        ])
        up = close[1:] > close[:-1]
        down = ~up
        # 对齐: std_series[1:] 对应每个 ret 日
        tail_std_up = std_series[1:][up][-self.lookback:]
        tail_std_down = std_series[1:][down][-self.lookback:]
        denom = tail_std_down.sum()
        if abs(denom) < 1e-12:
            return 0.0
        return float(tail_std_up.sum() / denom)

    def alpha57(self, df: pd.DataFrame) -> float | None:
        """Alpha57: SUM(CLOSE-DELAY(CLOSE,5)) / SUM(DELAY(CLOSE,5), 5)

        含义: 5 日累计收益 / 5 日前价格均值
        近似 5 日累计收益率
        """
        if df is None or len(df) < 10:
            return None
        close = df["close"].to_numpy(dtype=float)
        if len(close) < 10:
            return None
        # 最近 5 日的 (close - close[t-5])
        recent = close[-5:]
        delayed = close[-10:-5]
        diff = recent - delayed
        denom = delayed.sum()
        if abs(denom) < 1e-12:
            return 0.0
        return float(diff.sum() / denom)

    # ============================================================
    # 反转/极端类因子 (5 个)
    # ============================================================
    def alpha4(self, df: pd.DataFrame) -> float | None:
        """Alpha4: RANK(CLOSE)

        含义: 收盘价在历史中的排名 (反转因子)
        高值: 价格处于历史高位 (反转下跌风险)
        低值: 价格处于历史低位 (反转上涨机会)
        """
        if df is None or len(df) < 2:
            return None
        close = df["close"].to_numpy(dtype=float)
        ranks = self._rank(close)
        return float(ranks[-1])

    def alpha19(self, df: pd.DataFrame) -> float | None:
        """Alpha19 (T04 修正): MEAN(VOLUME, 5) / MEAN(VOLUME, 20)

        T04 FIX: 原公式 1/STD 与 alpha33 (1-STD排名) 高度相关。
        改为: 短期成交量均值 / 长期成交量均值 (成交量动量)

        含义: 短期成交量相对长期的变化
        高值: 近 5 日放量
        低值: 近 5 日缩量
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        if "volume" not in df.columns:
            return None
        vol = df["volume"].to_numpy(dtype=float)
        if len(vol) < self.lookback:
            return None
        mean5 = vol[-5:].mean()
        mean20 = vol[-self.lookback:].mean()
        if abs(mean20) < 1e-12:
            return 0.0
        return float(mean5 / mean20)

    def alpha24(self, df: pd.DataFrame) -> float | None:
        """Alpha24: SUM(IF(CLOSE>DELAY(CLOSE,1), ABS(CLOSE/DELAY(CLOSE,1)-1)*VOLUME, 0), 20) / SUM(IF(CLOSE<=DELAY(CLOSE,1), ABS(CLOSE/DELAY(CLOSE,1)-1)*VOLUME, 0), 20)

        含义: 上涨日成交额弹性 / 下跌日成交额弹性
        高值: 上涨时单位成交额推动的涨幅大于下跌时
        """
        if df is None or len(df) < self.lookback + 1:
            return None
        if "volume" not in df.columns:
            return None
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        ret_abs = np.abs(close[1:] / close[:-1] - 1.0)
        elastic = ret_abs * vol[1:]
        up = close[1:] > close[:-1]
        down = ~up
        tail_up = elastic[up][-self.lookback:]
        tail_down = elastic[down][-self.lookback:]
        denom = tail_down.sum()
        if abs(denom) < 1e-12:
            return 0.0
        return float(tail_up.sum() / denom)

    def alpha26(self, df: pd.DataFrame) -> float | None:
        """Alpha26 (T04 修正): SUM(IF(CLOSE<OPEN, ABS(CLOSE-OPEN)/OPEN, 0), 20)

        T04 FIX: 原公式 (下跌日弹性/上涨日弹性) 与 alpha24 完美负相关 (ρ=-0.906)。
        改为: 阴线日振幅累计 (基于当日 open vs close, 非前后日)

        含义: 过去 20 日阴线振幅累计
        高值: 阴线多且振幅大 (空头主导)
        低值: 阳线多或振幅小
        """
        if df is None or len(df) < self.lookback:
            return None
        if "open" not in df.columns:
            return None
        close = df["close"].to_numpy(dtype=float)
        open_ = df["open"].to_numpy(dtype=float)
        bearish = close < open_
        # 阴线振幅 = |CLOSE - OPEN| / OPEN
        amp = self._safe_divide(np.abs(close - open_), open_)
        bearish_amp = np.where(bearish, amp, 0.0)
        if len(bearish_amp) < self.lookback:
            return None
        return float(bearish_amp[-self.lookback:].sum())

    def alpha131(self, df: pd.DataFrame) -> float | None:
        """Alpha131 (T04 修正): RANK(DELAY(CLOSE, 5))

        T04 FIX: 原公式 WMA(CLOSE-DELAY(CLOSE,5), 20) 与 alpha25 高度相关。
        改为: 5 日前收盘价在历史中的排名 (反转因子)。

        含义: 5 日前价格的历史排名
        高值: 5 日前价格处于历史高位 (可能反转下跌)
        低值: 5 日前价格处于历史低位 (可能反转上涨)
        """
        if df is None or len(df) < 6:
            return None
        close = df["close"].to_numpy(dtype=float)
        if len(close) < 6:
            return None
        close[-6]  # 5 日前的收盘价
        ranks = self._rank(close)
        # 找到 delayed_close_5 在当前序列中的排名
        idx_5_ago = len(close) - 6
        return float(ranks[idx_5_ago])

    # ============================================================
    # 其他类因子 (1 个, 已实现)
    # ============================================================
    def alpha144(self, df: pd.DataFrame) -> float | None:
        """
        Alpha144:
        SUMIF(ABS(CLOSE/DELAY(CLOSE,1)-1)/AMOUNT, 20, CLOSE<DELAY(CLOSE,1))
        / COUNT(CLOSE<DELAY(CLOSE,1), 20)

        含义: 过去 N 个交易日内, 下跌日"收益率绝对值/成交额"的平均值。
        高值: 下跌放量、单位成交额推动的价格跌幅大
        低值: 下跌缩量或承接较好
        """
        if df is None or len(df) < self.lookback + 1:
            return None

        close = df["close"].to_numpy(dtype=float)
        amount = df["amount"].to_numpy(dtype=float)

        ret = np.zeros_like(close)
        ret[1:] = close[1:] / close[:-1] - 1.0

        down_mask = close < np.roll(close, 1)
        down_mask[0] = False

        tail_ret = ret[-self.lookback:]
        tail_amount = amount[-self.lookback:]
        tail_down = down_mask[-self.lookback:]

        if not np.any(tail_down):
            return 0.0

        efficiency = np.where(
            tail_down,
            np.abs(tail_ret) / np.where(tail_amount > 0, tail_amount, np.nan),
            np.nan,
        )
        value = np.nanmean(efficiency)

        return float(value) if np.isfinite(value) else 0.0

    # ============================================================
    # 批量计算
    # ============================================================
    def compute(self, df: pd.DataFrame) -> dict[str, float | None]:
        """批量计算所有 21 个因子

        Args:
            df: 需包含 close (必需) / open / high / low / volume / amount (按需) 列, 按时间升序

        Returns:
            Dict[str, float | None]: {alpha_name: value}
        """
        return {
            # 量价类
            "alpha6": self.alpha6(df),
            "alpha12": self.alpha12(df),
            "alpha54": self.alpha54(df),
            "alpha85": self.alpha85(df),
            "alpha101": self.alpha101(df),
            # 动量类
            "alpha22": self.alpha22(df),
            "alpha25": self.alpha25(df),
            "alpha28": self.alpha28(df),
            "alpha132": self.alpha132(df),
            "alpha178": self.alpha178(df),
            # 波动率类
            "alpha30": self.alpha30(df),
            "alpha33": self.alpha33(df),
            "alpha40": self.alpha40(df),
            "alpha43": self.alpha43(df),
            "alpha57": self.alpha57(df),
            # 反转/极端类
            "alpha4": self.alpha4(df),
            "alpha19": self.alpha19(df),
            "alpha24": self.alpha24(df),
            "alpha26": self.alpha26(df),
            "alpha131": self.alpha131(df),
            # 其他
            "alpha144": self.alpha144(df),
        }
