"""
v7.6 SignalFusion — 真正 IC-based 动态权重 + 信号相关性去冗余

P0 修复:
1. 动态权重改用真正 rank_IC (Spearman corr(signal_t, return_t+1))
2. 信号间相关性矩阵建模 — 去冗余, 防隐式杠杆
3. IC_IR 加权 (IC 均值 / IC 标准差), 非简单命中率
"""
import numpy as np
import pandas as pd
import logging
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from dataclasses import dataclass, field
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class ICRecord:
    """单次 IC 记录"""
    date: pd.Timestamp
    signal_name: str
    rank_ic: float
    sign_ic: float


class SignalFusion:
    """
    v7.6 信号融合器 — IC-based 动态权重

    融合多源信号：
    - Alpha 因子信号 (SignalGenerator)
    - ML 模型预测 (可选)
    - AI/LLM 情绪分析 (可选)
    - 康波周期 / 宏观信号 (继承 v7.4)
    - 因果验证结果 (继承 v7.3)
    - Qlib 深度学习信号 (v7.5 + Qlib 集成)

    P0 修复:
    - _fuse_dynamic: 用真正 rank_IC 替代假命中率
    - 信号相关性矩阵: 去冗余, 高相关信号降权
    - IC_IR: IC均值/IC标准差, 衡量信号稳定性
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None,
                 ic_lookback: int = 20,
                 min_ic_samples: int = 5):
        """
        Args:
            weights: 各信号源初始权重
            ic_lookback: IC 计算回望窗口 (交易日)
            min_ic_samples: 最少 IC 样本数才启用动态权重
        """
        self.weights = weights or {
            'alpha': 0.45,
            'ml': 0.10,
            'qlib': 0.10,
            'ai': 0.05,
            'macro': 0.25,
            'causal': 0.05,
        }
        self._normalize_weights()
        self._signals: Dict[str, pd.Series] = {}
        self._fusion_history: List[Dict] = []

        # IC 追踪
        self._ic_lookback = ic_lookback
        self._min_ic_samples = min_ic_samples
        self._forward_returns: Optional[pd.Series] = None
        self._ic_history: Dict[str, deque] = {}  # {signal_name: deque([(date, rank_ic), ...])}
        self._last_dynamic_weights: Dict[str, float] = {}

    def _normalize_weights(self) -> None:
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

    # ---------- 前置收益注入 (IC 计算必需) ----------
    def inject_forward_returns(self, forward_returns: pd.Series) -> None:
        """注入 T+1 收益率, 用于计算真正 IC

        Args:
            forward_returns: index=日期, values=T+1收益率
                             通常 = close.pct_change().shift(-1)
        """
        self._forward_returns = forward_returns
        logger.info(f"注入前置收益 {len(forward_returns)} 天, IC 计算就绪")

    def _compute_rank_ic(self, signal: pd.Series, forward_ret: pd.Series) -> Optional[Tuple[float, float]]:
        """计算 Spearman rank IC 和 sign IC

        rank_ic = corr(rank(signal_t), rank(return_t+1))
        sign_ic = corr(sign(signal_t), sign(return_t+1))

        Returns:
            (rank_ic, sign_ic) 或 None (样本不足)
        """
        common = signal.dropna().index.intersection(forward_ret.dropna().index)
        if len(common) < self._min_ic_samples:
            return None

        s = signal.loc[common]
        r = forward_ret.loc[common]

        # Spearman rank IC
        from scipy.stats import spearmanr
        rank_ic, _ = spearmanr(s.values, r.values)
        if np.isnan(rank_ic):
            rank_ic = 0.0

        # Sign IC
        sign_s = np.sign(s.values)
        sign_r = np.sign(r.values)
        # 避免除零
        if np.std(sign_s) > 0 and np.std(sign_r) > 0:
            sign_ic = float(np.corrcoef(sign_s, sign_r)[0, 1])
        else:
            sign_ic = 0.0

        return float(rank_ic), sign_ic

    def _update_ic_history(self) -> None:
        """更新各信号的 IC 历史 (滚动窗口)"""
        if self._forward_returns is None:
            logger.debug("无前置收益, 跳过 IC 更新")
            return

        for name, signal in self._signals.items():
            if len(signal) < self._min_ic_samples + 1:
                continue

            # 滚动计算每日 IC
            if name not in self._ic_history:
                self._ic_history[name] = deque(maxlen=self._ic_lookback)

            # 计算最新 IC
            result = self._compute_rank_ic(signal, self._forward_returns)
            if result is not None:
                rank_ic, sign_ic = result
                latest_date = signal.index[-1] if hasattr(signal.index, '__getitem__') else None
                self._ic_history[name].append((latest_date, rank_ic, sign_ic))

    def _compute_signal_correlation(self) -> pd.DataFrame:
        """计算信号间相关性矩阵 (用于去冗余)"""
        if len(self._signals) < 2:
            return pd.DataFrame()

        # 对齐所有信号
        signals_df = pd.DataFrame(self._signals)
        return signals_df.corr(method='spearman')

    def _compute_ic_ir(self, signal_name: str) -> float:
        """计算信号的 IC 信息比率 = IC均值 / IC标准差

        高 IC_IR = 信号既有预测力又稳定
        """
        history = self._ic_history.get(signal_name)
        if not history or len(history) < self._min_ic_samples:
            return 0.0

        ics = [h[1] for h in history]
        mean_ic = np.mean(ics)
        std_ic = np.std(ics)

        if std_ic < 1e-8:
            return 0.0

        return float(mean_ic / std_ic)

    def _compute_dynamic_weights_ic(self) -> Dict[str, float]:
        """用 IC_IR + 信号相关性计算动态权重

        算法:
        1. 计算每个信号的 IC_IR
        2. 用 IC_IR 的 softmax 作为基础权重
        3. 对高相关信号对降权 (去冗余)
        4. 与初始权重取加权平均 (收缩)
        """
        if self._forward_returns is None:
            return dict(self.weights)

        self._update_ic_history()

        # 1. 计算 IC_IR
        ic_irs = {}
        for name in self._signals:
            ir = self._compute_ic_ir(name)
            ic_irs[name] = ir

        # 如果 IC 历史不足, 回退到初始权重
        valid_irs = {k: v for k, v in ic_irs.items() if abs(v) > 1e-6}
        if len(valid_irs) < 2:
            return dict(self.weights)

        # 2. softmax(IC_IR) 基础权重
        # 只给 IC_IR > 0 的信号正权重
        ir_values = np.array([max(ic_irs.get(k, 0.0), 0.0) for k in self._signals.keys()])
        if ir_values.sum() < 1e-8:
            # 所有 IC_IR ≤ 0, 回退初始权重
            return dict(self.weights)

        # softmax with temperature
        temperature = 1.0
        exp_ir = np.exp(ir_values / temperature)
        softmax_w = exp_ir / exp_ir.sum()
        ic_weights = dict(zip(self._signals.keys(), softmax_w))

        # 3. 信号相关性去冗余
        corr_matrix = self._compute_signal_correlation()
        if not corr_matrix.empty and len(corr_matrix) > 1:
            for name in self._signals:
                if name not in corr_matrix.columns:
                    continue
                # 计算与其他信号的平均相关性
                other_corrs = [corr_matrix.loc[name, o] for o in self._signals
                               if o != name and o in corr_matrix.columns]
                if other_corrs:
                    avg_corr = np.mean(other_corrs)
                    # 高相关 → 降权 (相关 0.8 → 降 40%)
                    redundancy_factor = 1.0 - 0.5 * max(avg_corr, 0.0)
                    ic_weights[name] *= redundancy_factor

        # 4. 与初始权重收缩 (70% IC权重 + 30% 初始权重)
        adjusted = {}
        for name in self._signals:
            ic_w = ic_weights.get(name, 0.0)
            init_w = self.weights.get(name, 0.0) / max(sum(self.weights.values()), 1e-8)
            adjusted[name] = 0.7 * ic_w + 0.3 * init_w

        # 归一化
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}

        self._last_dynamic_weights = adjusted
        return adjusted

    # ---------- 信号注入 ----------
    def inject_alpha_signal(self, signal: pd.Series, name: str = 'alpha') -> None:
        self._signals[name] = signal

    def inject_ml_signal(self, signal: pd.Series, name: str = 'ml') -> None:
        self._signals[name] = signal

    def inject_ai_signal(self, signal: pd.Series, source: str = 'ai') -> None:
        self._signals[source] = signal

    def inject_macro_signal(self, signal: pd.Series, source: str = 'macro') -> None:
        self._signals[source] = signal

    def inject_causal_signal(self, signal: pd.Series, source: str = 'causal') -> None:
        self._signals[source] = signal

    def inject_qlib_signal(self, signal: pd.Series, name: str = 'qlib') -> None:
        """v7.5 + Qlib 集成：注入 Qlib 模型预测信号"""
        self._signals[name] = signal

    def inject_custom_signal(self, signal: pd.Series, name: str) -> None:
        self._signals[name] = signal

    # ---------- 融合 ----------
    def fuse(self, method: str = 'weighted') -> pd.Series:
        """
        信号融合

        Args:
            method: 'weighted' (加权平均) | 'vote' (投票) | 'dynamic' (动态权重)

        Returns:
            融合后信号 Series
        """
        if not self._signals:
            logger.warning("无信号可融合")
            return pd.Series(0.0, index=[0])

        if method == 'weighted':
            return self._fuse_weighted()
        elif method == 'vote':
            return self._fuse_vote()
        elif method == 'dynamic':
            return self._fuse_dynamic()
        else:
            logger.warning(f"未知融合方法 {method}，使用加权平均")
            return self._fuse_weighted()

    def _fuse_weighted(self) -> pd.Series:
        """加权平均融合"""
        # 对齐所有信号
        all_signals = []
        for name, signal in self._signals.items():
            w = self.weights.get(name, 0.0)
            if w > 0:
                all_signals.append(signal * w)

        if not all_signals:
            return pd.Series(0.0, index=[0])

        # 找到公共 index
        common_idx = all_signals[0].index
        for s in all_signals[1:]:
            common_idx = common_idx.intersection(s.index)

        if len(common_idx) == 0:
            return pd.Series(0.0, index=[0])

        fused = sum(s.reindex(common_idx).fillna(0) for s in all_signals)

        # 记录
        self._fusion_history.append({
            'method': 'weighted',
            'ts': datetime.now().isoformat(),
            'signal_mean': float(fused.mean()),
            'signal_std': float(fused.std()),
            'weight_used': dict(self.weights)
        })

        return fused

    def _fuse_vote(self) -> pd.Series:
        """投票法：信号方向 ≥ N/2 则采纳"""
        if not self._signals:
            return pd.Series(0.0, index=[0])

        # 对齐（只保留有权重的信号，避免空信号浪费计算）
        sig_list = [
            signal for name, signal in self._signals.items()
            if self.weights.get(name, 0.0) > 0 and signal is not None and len(signal) > 0
        ]
        if not sig_list:
            return pd.Series(0.0, index=[0])

        common_idx = sig_list[0].index
        for s in sig_list[1:]:
            common_idx = common_idx.intersection(s.index)

        if len(common_idx) == 0:
            return pd.Series(0.0, index=[0])

        signs = np.array([s.reindex(common_idx).fillna(0).values for s in sig_list])
        # 多数投票
        vote_threshold = len(sig_list) / 2
        positive_votes = (signs > 0).sum(axis=0)
        negative_votes = (signs < 0).sum(axis=0)

        result = np.where(positive_votes > vote_threshold, 1.0,
                          np.where(negative_votes > vote_threshold, -1.0, 0.0))
        return pd.Series(result, index=common_idx)

    def _fuse_dynamic(self) -> pd.Series:
        """IC-based 动态权重融合

        P0 修复: 用真正 rank_IC 替代假命中率

        流程:
        1. 注入 forward_returns 后, 计算 IC_IR
        2. softmax(IC_IR) → IC 权重
        3. 信号相关性去冗余
        4. 与初始权重收缩 (70/30)
        5. 加权融合

        若未注入 forward_returns, 回退到静态权重
        """
        if self._forward_returns is None:
            logger.warning("未注入 forward_returns, 动态权重回退到静态权重")
            return self._fuse_weighted()

        # 计算 IC-based 动态权重
        adjusted = self._compute_dynamic_weights_ic()

        if not adjusted:
            return self._fuse_weighted()

        # 临时设置权重并融合
        orig_weights = dict(self.weights)
        self.weights = adjusted
        fused = self._fuse_weighted()
        self.weights = orig_weights

        return fused

    # ---------- 信号解释 ----------
    def explain(self) -> dict:
        """返回融合信号成分解释 (含 IC 信息)"""
        ic_info = {}
        for name in self._signals:
            ir = self._compute_ic_ir(name)
            history = self._ic_history.get(name, [])
            ics = [h[1] for h in history] if history else []
            ic_info[name] = {
                'ic_ir': round(ir, 4),
                'mean_ic': round(float(np.mean(ics)), 4) if ics else None,
                'std_ic': round(float(np.std(ics)), 4) if ics else None,
                'n_samples': len(ics),
            }

        return {
            'weights': dict(self.weights),
            'dynamic_weights': dict(self._last_dynamic_weights) if self._last_dynamic_weights else None,
            'ic_info': ic_info,
            'signal_correlation': self._compute_signal_correlation().round(3).to_dict() if len(self._signals) > 1 else None,
            'signals': {name: {'mean': float(s.mean()), 'std': float(s.std())}
                        for name, s in self._signals.items()},
            'fusion_history': self._fusion_history[-5:] if self._fusion_history else [],
        }

    # ---------- 宏观 / 康波 ----------
    def apply_kondratieff_filter(self, signal: pd.Series, cycle_phase: str = 'neutral') -> pd.Series:
        """
        康波周期过滤（继承 v7.4）
        - 'expansion': 放大做多信号，衰减做空信号
        - 'recession': 衰减做多信号，放大做空信号
        - 'neutral': 不变
        """
        if cycle_phase == 'expansion':
            return signal * np.where(signal > 0, 1.2, 0.8)
        elif cycle_phase == 'recession':
            return signal * np.where(signal < 0, 1.2, 0.8)
        else:
            return signal
