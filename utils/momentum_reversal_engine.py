"""
动量反转引擎 (Momentum & Reversal Engine)

世界顶级量化基金 Alpha 来源 (AQR / Renaissance / Bridgewater):
- 时间序列动量 (Time-Series Momentum, TSMOM) — 标的自身历史收益信号
- 截面动量 (Cross-Sectional Momentum, XSMOM) — 相对其他标的的强弱
- 反转信号 (Reversal) — 短期过度反应的反转
- 信号融合 (Signal Fusion) — 多周期加权融合
- 仓位生成 (Position Sizing) — 信号转目标仓位

公式核心:
    TSMOM(k) = sign(R_t-k:t]) × |R_t-k:t| / σ_k
    XSMOM(k) = (R_i,k - mean(R_j,k)) / std(R_j,k)
    Reversal(k) = -R_t-k:t (短期反转)
    Combined Signal = Σ w_i × Signal_i

参考:
- Moskowitz, T. & Ooi, J. & Pedersen, L. (2012) "Time Series Momentum"
- Asness, A. et al. (2013) "Value and Momentum Everywhere"
- Jegadeesh, N. & Titman, S. (1993) "Returns to Buying Winners"
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ============================================================
# 数据结构
# ============================================================


@dataclass
class MomentumSignal:
    """动量信号"""

    symbol: str
    # 时间序列动量
    tsmom_20d: float = 0.0
    tsmom_60d: float = 0.0
    tsmom_120d: float = 0.0
    tsmom_252d: float = 0.0
    # 截面动量
    xsmom_20d: float = 0.0
    xsmom_60d: float = 0.0
    xsmom_120d: float = 0.0
    # 反转信号
    reversal_5d: float = 0.0
    reversal_20d: float = 0.0
    # 融合信号
    combined_signal: float = 0.0
    # 信号强度 [-1, 1]
    signal_strength: float = 0.0
    # 信号置信度 [0, 1]
    confidence: float = 0.0
    # 目标仓位 (信号转权重)
    target_weight: float = 0.0


@dataclass
class MomentumResult:
    """动量引擎结果"""

    signals: dict[str, MomentumSignal] = field(default_factory=dict)
    # 平均信号强度
    avg_signal_strength: float = 0.0
    # 信号一致性 (多少标的方向一致)
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    # 策略建议
    top_long_candidates: list[str] = field(default_factory=list)
    top_short_candidates: list[str] = field(default_factory=list)
    # 诊断
    strategy_state: str = "NEUTRAL"  # TRENDING / REVERSING / NEUTRAL


# ============================================================
# 动量反转引擎
# ============================================================


class MomentumReversalEngine:
    """动量反转信号引擎

    用法:
        engine = MomentumReversalEngine()
        result = engine.generate_signals(
            price_data={
                "600519": {"closes": [...], "volumes": [...]},
                "000858": {"closes": [...], "volumes": [...]},
            }
        )
        # result.signals["600519"].combined_signal, .target_weight
    """

    def __init__(
        self,
        # 信号融合权重
        tsmom_weights: dict[int, float] | None = None,
        xsmom_weights: dict[int, float] | None = None,
        reversal_weights: dict[int, float] | None = None,
        # 类别权重
        tsmom_category_weight: float = 0.4,
        xsmom_category_weight: float = 0.3,
        reversal_category_weight: float = 0.3,
        # 仓位参数
        max_position: float = 0.10,  # 单标的最大仓位
        signal_threshold: float = 0.2,  # 信号阈值
        vol_target: float = 0.15,  # 目标波动率
    ):
        # 默认权重: 多周期加权
        self.tsmom_weights = tsmom_weights or {20: 0.2, 60: 0.4, 120: 0.3, 252: 0.1}
        self.xsmom_weights = xsmom_weights or {20: 0.3, 60: 0.4, 120: 0.3}
        self.reversal_weights = reversal_weights or {5: 0.6, 20: 0.4}

        self.tsmom_cat_w = float(tsmom_category_weight)
        self.xsmom_cat_w = float(xsmom_category_weight)
        self.reversal_cat_w = float(reversal_category_weight)

        self.max_position = float(max_position)
        self.signal_threshold = float(signal_threshold)
        self.vol_target = float(vol_target)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def generate_signals(
        self,
        price_data: dict[str, dict[str, list[float]]],
    ) -> MomentumResult:
        """生成所有标的的动量反转信号

        Args:
            price_data: {symbol: {"closes": [...], "volumes": [...]}}

        Returns:
            MomentumResult
        """
        result = MomentumResult()

        # DataFrame 不支持 bool() 求值, 用 empty 属性检查
        if price_data is None or (hasattr(price_data, "empty") and price_data.empty) or len(price_data) == 0:
            return result

        # 1. 计算时间序列动量 (TSMOM)
        tsmom_signals = self._calc_tsmom(price_data)

        # 2. 计算截面动量 (XSMOM)
        xsmom_signals = self._calc_xsmom(price_data)

        # 3. 计算反转信号
        reversal_signals = self._calc_reversal(price_data)

        # 4. 融合信号
        for sym in price_data:
            sig = MomentumSignal(symbol=sym)

            # TSMOM 融合
            tsmom_combined = 0.0
            for window, weight in self.tsmom_weights.items():
                tsmom_combined += weight * tsmom_signals.get(f"{sym}_{window}", 0.0)
            sig.tsmom_20d = tsmom_signals.get(f"{sym}_20", 0.0)
            sig.tsmom_60d = tsmom_signals.get(f"{sym}_60", 0.0)
            sig.tsmom_120d = tsmom_signals.get(f"{sym}_120", 0.0)
            sig.tsmom_252d = tsmom_signals.get(f"{sym}_252", 0.0)

            # XSMOM 融合
            xsmom_combined = 0.0
            for window, weight in self.xsmom_weights.items():
                xsmom_combined += weight * xsmom_signals.get(f"{sym}_{window}", 0.0)
            sig.xsmom_20d = xsmom_signals.get(f"{sym}_20", 0.0)
            sig.xsmom_60d = xsmom_signals.get(f"{sym}_60", 0.0)
            sig.xsmom_120d = xsmom_signals.get(f"{sym}_120", 0.0)

            # Reversal 融合
            rev_combined = 0.0
            for window, weight in self.reversal_weights.items():
                rev_combined += weight * reversal_signals.get(f"{sym}_{window}", 0.0)
            sig.reversal_5d = reversal_signals.get(f"{sym}_5", 0.0)
            sig.reversal_20d = reversal_signals.get(f"{sym}_20", 0.0)

            # 类别融合
            sig.combined_signal = (
                self.tsmom_cat_w * tsmom_combined
                + self.xsmom_cat_w * xsmom_combined
                + self.reversal_cat_w * rev_combined
            )

            # 信号强度 [-1, 1]
            sig.signal_strength = max(-1.0, min(1.0, sig.combined_signal))

            # 置信度
            sig.confidence = self._calc_confidence(sig)

            # 目标仓位
            sig.target_weight = self._calc_target_weight(sig, price_data.get(sym, {}))

            result.signals[sym] = sig

        # 5. 策略诊断
        self._diagnose(result)

        return result

    # ------------------------------------------------------------
    # 时间序列动量 (TSMOM)
    # ------------------------------------------------------------

    def _calc_tsmom(
        self,
        price_data: dict[str, dict[str, list[float]]],
    ) -> dict[str, float]:
        """计算时间序列动量

        TSMOM(k) = sign(R_t-k:t) × |R_t-k:t| / σ_k × scaling
        """
        signals: dict[str, float] = {}

        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if not closes:
                continue

            for window in self.tsmom_weights.keys():
                key = f"{sym}_{window}"
                if len(closes) > window:
                    ret = closes[-1] / closes[-window] - 1
                    # 波动率调整
                    window_closes = closes[-window:]
                    rets = np.diff(window_closes)
                    vol = float(np.std(rets)) if len(rets) > 1 else 0.02
                    vol_annual = vol * np.sqrt(252)
                    # Sharpe-like 信号
                    if vol_annual > 0:
                        signals[key] = float(np.sign(ret) * abs(ret) / vol_annual * 0.5)
                    else:
                        signals[key] = 0.0
                else:
                    signals[key] = 0.0

        return signals

    # ------------------------------------------------------------
    # 截面动量 (XSMOM)
    # ------------------------------------------------------------

    def _calc_xsmom(
        self,
        price_data: dict[str, dict[str, list[float]]],
    ) -> dict[str, float]:
        """计算截面动量

        XSMOM(k) = (R_i,k - mean(R_j,k)) / std(R_j,k)
        """
        signals: dict[str, float] = {}

        for window in self.xsmom_weights.keys():
            # 收集所有标的的 k 日收益
            returns: dict[str, float] = {}
            for sym, data in price_data.items():
                closes = data.get("closes", [])
                if len(closes) > window:
                    returns[sym] = closes[-1] / closes[-window] - 1

            if len(returns) < 2:
                for sym in price_data:
                    signals[f"{sym}_{window}"] = 0.0
                continue

            # 截面 Z-score
            ret_values = list(returns.values())
            mean_ret = float(np.mean(ret_values))
            std_ret = float(np.std(ret_values))

            for sym, ret in returns.items():
                key = f"{sym}_{window}"
                if std_ret > 0:
                    signals[key] = float((ret - mean_ret) / std_ret * 0.3)  # 缩放
                else:
                    signals[key] = 0.0

            # 没有数据的标的设为 0
            for sym in price_data:
                key = f"{sym}_{window}"
                if key not in signals:
                    signals[key] = 0.0

        return signals

    # ------------------------------------------------------------
    # 反转信号
    # ------------------------------------------------------------

    def _calc_reversal(
        self,
        price_data: dict[str, dict[str, list[float]]],
    ) -> dict[str, float]:
        """计算短期反转信号

        Reversal(k) = -R_t-k:t × volume_weight
        """
        signals: dict[str, float] = {}

        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])

            for window in self.reversal_weights.keys():
                key = f"{sym}_{window}"
                if len(closes) > window:
                    ret = closes[-1] / closes[-window] - 1
                    # 成交量加权: 放量反转信号更强
                    if len(vols) > window:
                        recent_vol = float(np.mean(vols[-window:]))
                        avg_vol = float(np.mean(vols[-min(len(vols), 60) :])) if len(vols) > 0 else 1
                        vol_ratio = recent_vol / max(avg_vol, 1e-10)
                        vol_weight = min(vol_ratio, 2.0)  # 限制 2x
                    else:
                        vol_weight = 1.0

                    signals[key] = float(-ret * vol_weight * 0.5)
                else:
                    signals[key] = 0.0

        return signals

    # ------------------------------------------------------------
    # 置信度计算
    # ------------------------------------------------------------

    def _calc_confidence(self, sig: MomentumSignal) -> float:
        """计算信号置信度

        基于信号一致性 (TSMOM/XSMOM/Reversal 方向是否一致)
        """
        tsmom_avg = (sig.tsmom_20d + sig.tsmom_60d + sig.tsmom_120d + sig.tsmom_252d) / 4
        xsmom_avg = (sig.xsmom_20d + sig.xsmom_60d + sig.xsmom_120d) / 3
        rev_avg = (sig.reversal_5d + sig.reversal_20d) / 2

        # 三个子信号方向
        tsmom_dir = 1 if tsmom_avg > 0 else (-1 if tsmom_avg < 0 else 0)
        xsmom_dir = 1 if xsmom_avg > 0 else (-1 if xsmom_avg < 0 else 0)
        rev_dir = 1 if rev_avg > 0 else (-1 if rev_avg < 0 else 0)

        # 一致性评分 (3 个方向都一致 = 1.0)
        directions = [tsmom_dir, xsmom_dir, rev_dir]
        if all(d == directions[0] for d in directions) and directions[0] != 0:
            confidence = 1.0
        elif sum(1 for d in directions if d == max(directions, key=directions.count)) >= 2:
            confidence = 0.6
        else:
            confidence = 0.3

        # 信号强度加成
        confidence *= min(abs(sig.combined_signal) / 0.5, 1.0)

        return float(max(0.0, min(1.0, confidence)))

    # ------------------------------------------------------------
    # 目标仓位计算
    # ------------------------------------------------------------

    def _calc_target_weight(
        self,
        sig: MomentumSignal,
        price_data: dict[str, list[float]],
    ) -> float:
        """根据信号计算目标仓位

        Position = signal_strength × confidence × vol_target / vol_actual × max_position
        """
        if abs(sig.signal_strength) < self.signal_threshold:
            return 0.0

        # 波动率调整
        closes = price_data.get("closes", [])
        if len(closes) > 20:
            rets = np.diff(closes[-21:])
            vol_actual = float(np.std(rets) * np.sqrt(252))
        else:
            vol_actual = 0.25  # 默认 25%

        # 波动率调整因子
        vol_adj = self.vol_target / max(vol_actual, 0.05)

        # 仓位 = 信号 × 置信度 × 波动率调整 × 最大仓位限制
        weight = sig.signal_strength * sig.confidence * vol_adj * self.max_position

        # 限制仓位
        return float(max(-self.max_position, min(self.max_position, weight)))

    # ------------------------------------------------------------
    # 策略诊断
    # ------------------------------------------------------------

    def _diagnose(self, result: MomentumResult) -> None:
        """策略状态诊断"""
        if not result.signals:
            return

        strengths = [s.signal_strength for s in result.signals.values()]
        result.avg_signal_strength = float(np.mean(strengths))

        result.bullish_count = sum(1 for s in result.signals.values() if s.signal_strength > 0.2)
        result.bearish_count = sum(1 for s in result.signals.values() if s.signal_strength < -0.2)
        result.neutral_count = len(result.signals) - result.bullish_count - result.bearish_count

        # 排序获取 top long/short
        sorted_signals = sorted(
            result.signals.values(),
            key=lambda s: s.signal_strength,
            reverse=True,
        )
        result.top_long_candidates = [s.symbol for s in sorted_signals[:5] if s.signal_strength > 0.2]
        result.top_short_candidates = [s.symbol for s in sorted_signals[-5:] if s.signal_strength < -0.2]

        # 策略状态判断
        total = len(result.signals)
        if total == 0:
            result.strategy_state = "NEUTRAL"
        elif result.bullish_count / total > 0.6:
            result.strategy_state = "TRENDING_UP"
        elif result.bearish_count / total > 0.6:
            result.strategy_state = "TRENDING_DOWN"
        elif abs(result.avg_signal_strength) < 0.1:
            result.strategy_state = "REVERSING"
        else:
            result.strategy_state = "NEUTRAL"

    # ------------------------------------------------------------
    # 工具: 信号质量过滤
    # ------------------------------------------------------------

    def filter_low_confidence(
        self,
        result: MomentumResult,
        min_confidence: float = 0.4,
    ) -> MomentumResult:
        """过滤低置信度信号"""
        filtered = MomentumResult()
        filtered.signals = {sym: sig for sym, sig in result.signals.items() if sig.confidence >= min_confidence}
        self._diagnose(filtered)
        return filtered

    def get_position_adjustment(
        self,
        result: MomentumResult,
        current_positions: dict[str, float],
    ) -> dict[str, float]:
        """获取仓位调整建议

        Args:
            result: 信号结果
            current_positions: {symbol: current_weight}

        Returns:
            {symbol: delta_weight} 需要调整的权重
        """
        adjustments: dict[str, float] = {}

        for sym, sig in result.signals.items():
            current = current_positions.get(sym, 0.0)
            target = sig.target_weight
            delta = target - current
            if abs(delta) > 0.001:  # 调整阈值 0.1%
                adjustments[sym] = float(delta)

        return adjustments
