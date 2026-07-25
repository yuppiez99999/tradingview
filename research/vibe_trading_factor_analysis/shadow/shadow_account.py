# -*- coding: utf-8 -*-
"""ShadowAccount - Stage 7 影子账户（CIO 视角 v1.0 + P2.1c 风险管理层）

90 日纸面交易 + 蒙特卡洛压力测试 + live DSR 复算。
锁定参数（DECISION v1.0）：
  - 观察期 90 日（比原方案 60d 严格 50%）
  - max_drawdown < 12%
  - live_DSR > 0.5（高于历史 DSR > 0）
  - 蒙特卡洛 1000 次 bootstrap

通过条件（三者全部满足）：
  1. live_DSR > 0.5
  2. max_drawdown < 12%
  3. monte_carlo_p95_dd < 18%（尾部风险约束）

P2.1c 改进：可选风险管理层（risk_managed=True）
  - 波动率缩放：目标年化波动率 15%（对齐生产 MAX_DRAWDOWN_LIMIT=15%）
  - 回撤去杠杆：当回撤 > 5% 时降低敞口至 50%
  - 设计依据：单因子 Shadow 失败根因是无风险管理导致的回撤过大（23.3%），
    而生产 V9（含风险管理）回撤仅 9.95%。Shadow 应测试因子+风险管理组合，
    与生产使用方式一致。
"""
from __future__ import annotations

import logging
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("shadow_account")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.validators.dsr_validator import DSRValidator


# ============== CIO 锁定参数（DECISION v1.0）==============
SHADOW_OBSERVATION_DAYS = 90          # 观察期 90d（比原方案 60d 严格 50%）
LIVE_DSR_THRESHOLD = 0.5              # 影子账户 live DSR 准入门槛
MAX_DRAWDOWN_THRESHOLD = 0.12         # 最大回撤约束 12%
MONTE_CARLO_P95_DRAWDOWN = 0.18       # 蒙特卡洛 95% 分位回撤 18%
MONTE_CARLO_TRIALS = 1000             # 蒙特卡洛 bootstrap 次数
MIN_OBS_DAYS = 60                     # 最少观察 60 日才评估
LONG_SHORT_TOP_N = 20                 # 多空 TopN 持仓
DAILY_TRANSACTION_COST = 0.0008       # 单边万八（A股印花税+佣金估算）
MAX_LEVERAGE = 1.0                    # 最大杠杆（无杠杆）

# ============== P2.1c 风险管理层参数 ==============
TRADING_DAYS_PER_YEAR = 252            # 交易日/年
RISK_MANAGED_TARGET_VOL = 0.15        # 目标年化波动率 15%（对齐生产）
RISK_MANAGED_VOL_LOOKBACK = 20        # 波动率回看窗口 20 日
RISK_MANAGED_DD_DERISK_THRESHOLD = 0.05  # 回撤 > 5% 触发去杠杆
RISK_MANAGED_DD_DERISK_FACTOR = 0.5      # 去杠杆至 50% 敞口
RISK_MANAGED_SCALER_CAP = 2.0          # 缩放因子上限（避免过度加杠杆）


@dataclass
class ShadowResult:
    """影子账户结果（CIO v1.0 + P2.1c 风险管理）

    包含：90日 PnL 序列、live DSR、最大回撤、蒙特卡洛压力测试。
    P2.1c 新增：风险管理层启用时记录缩放统计。
    """
    factor_name: str
    # 核心指标
    live_dsr: float = 0.0
    sr_observed: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    daily_pnl: List[float] = field(default_factory=list)
    n_obs_days: int = 0
    # 蒙特卡洛压力测试
    monte_carlo_p95_dd: float = 0.0
    monte_carlo_mean_dd: float = 0.0
    monte_carlo_trials: int = 0
    # 多重检验
    n_trials: int = 0
    # P2.1c 风险管理层字段
    risk_managed: bool = False
    raw_max_drawdown: float = 0.0           # 未缩放原始回撤（对照）
    raw_total_return: float = 0.0           # 未缩放原始收益（对照）
    realized_vol: float = 0.0              # 实际年化波动率（缩放后）
    raw_realized_vol: float = 0.0           # 原始年化波动率
    avg_scaler: float = 1.0                 # 平均缩放因子
    derisk_triggered_days: int = 0          # 触发去杠杆的日数
    # 准入结论
    pass_shadow: bool = False
    fail_reasons: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ShadowAccount:
    """影子账户 - Stage 7

    90 日纸面交易模拟，复算 live DSR，蒙特卡洛压力测试。
    通过条件：live_DSR > 0.5 AND max_dd < 12% AND mc_p95_dd < 18%

    用法：
        >>> account = ShadowAccount()
        >>> result = account.run_shadow(
        ...     factor_values_history=...,    # 90 日因子值历史
        ...     forward_returns_history=...,  # 90 日 forward returns
        ...     n_trials=13,                 # 已测试候选因子数
        ...     factor_name="VT_MOM_ILLIQUID_60D"
        ... )
        >>> if result.pass_shadow:
        ...     # 进入 FactorCommittee
        ...     pass
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        c = config or {}
        # P2.2 v6.2d：保存原始 config，支持因子特定覆盖时复用
        self.config = dict(c)
        self.observation_days = int(c.get("observation_days", SHADOW_OBSERVATION_DAYS))
        self.live_dsr_threshold = float(c.get("live_dsr_threshold", LIVE_DSR_THRESHOLD))
        self.max_dd_threshold = float(c.get("max_dd_threshold", MAX_DRAWDOWN_THRESHOLD))
        self.mc_p95_dd_threshold = float(c.get("mc_p95_dd_threshold", MONTE_CARLO_P95_DRAWDOWN))
        self.mc_trials = int(c.get("mc_trials", MONTE_CARLO_TRIALS))
        self.long_short_top_n = int(c.get("long_short_top_n", LONG_SHORT_TOP_N))
        self.transaction_cost = float(c.get("transaction_cost", DAILY_TRANSACTION_COST))
        self.dsr_validator = DSRValidator(config=c.get("dsr_config"))
        self._rng = np.random.default_rng(seed=42)  # 可复现蒙特卡洛
        # P2.1c 风险管理层参数
        self.risk_managed = bool(c.get("risk_managed", False))
        self.target_vol = float(c.get("target_vol", RISK_MANAGED_TARGET_VOL))
        self.vol_lookback = int(c.get("vol_lookback", RISK_MANAGED_VOL_LOOKBACK))
        self.dd_derisk_threshold = float(c.get("dd_derisk_threshold", RISK_MANAGED_DD_DERISK_THRESHOLD))
        self.dd_derisk_factor = float(c.get("dd_derisk_factor", RISK_MANAGED_DD_DERISK_FACTOR))
        self.scaler_cap = float(c.get("scaler_cap", RISK_MANAGED_SCALER_CAP))
        logger.info(
            "[ShadowAccount] 初始化 | obs=%d dsr>%.2f dd<%.2f mc_p95<%.2f trials=%d risk_managed=%s",
            self.observation_days, self.live_dsr_threshold,
            self.max_dd_threshold, self.mc_p95_dd_threshold, self.mc_trials,
            self.risk_managed,
        )
        if self.risk_managed:
            logger.info(
                "[ShadowAccount] 风险管理层 | target_vol=%.2f vol_lookback=%d dd_derisk>%.2f→×%.2f scaler_cap=%.1f",
                self.target_vol, self.vol_lookback,
                self.dd_derisk_threshold, self.dd_derisk_factor, self.scaler_cap,
            )

    def run_shadow(
        self,
        factor_values_history: List[Dict[str, float]],
        forward_returns_history: List[Dict[str, float]],
        n_trials: int,
        factor_name: str = "candidate",
    ) -> ShadowResult:
        """执行 90 日影子账户纸面交易

        Args:
            factor_values_history: 每日因子值列表，每个元素为 {symbol: factor_value}
            forward_returns_history: 每日 forward return 列表，每个元素为 {symbol: fwd_ret}
            n_trials: 已测试候选因子总数（用于 DSR 多重检验）
            factor_name: 因子名称

        Returns:
            ShadowResult: 影子账户结果
        """
        r = ShadowResult(factor_name=factor_name, n_trials=n_trials)

        # ============ Step 1: 数据校验 ============
        n_days = min(len(factor_values_history), len(forward_returns_history))
        if n_days < MIN_OBS_DAYS:
            r.fail_reasons.append(f"obs_days {n_days} < {MIN_OBS_DAYS}")
            r.reason = "; ".join(r.fail_reasons)
            logger.warning("[ShadowAccount] %s 观察日不足 | %d < %d", factor_name, n_days, MIN_OBS_DAYS)
            return r

        # 取最近 90 日
        use_days = min(n_days, self.observation_days)
        fv_hist = factor_values_history[-use_days:]
        fr_hist = forward_returns_history[-use_days:]
        r.n_obs_days = use_days

        # ============ Step 2: 每日纸面交易 PnL 模拟 ============
        daily_pnl = []
        daily_factor_returns = []  # 因子多空组合日收益率，用于 DSR
        prev_holdings: Optional[set] = None

        for day_idx in range(use_days):
            fv = fv_hist[day_idx]
            fr = fr_hist[day_idx]

            # 构建当日 TopN 多空组合
            long_syms, short_syms = self._build_long_short(fv)
            if not long_syms or not short_syms:
                daily_pnl.append(0.0)
                daily_factor_returns.append(0.0)
                prev_holdings = None
                continue

            # 计算多空 PnL（等权）
            long_pnl = self._compute_avg_return(long_syms, fr)
            short_pnl = self._compute_avg_return(short_syms, fr)
            gross_pnl = long_pnl - short_pnl

            # 交易成本（换手率）
            cur_holdings = set(long_syms) | set(short_syms)
            turnover_ratio = self._compute_turnover(prev_holdings, cur_holdings)
            cost = turnover_ratio * self.transaction_cost
            net_pnl = gross_pnl - cost

            daily_pnl.append(float(net_pnl))
            daily_factor_returns.append(float(net_pnl))  # 用净 PnL 作为 DSR 输入
            prev_holdings = cur_holdings

        r.daily_pnl = daily_pnl

        # ============ Step 3: 计算核心指标 ============
        if not daily_pnl:
            r.fail_reasons.append("无有效交易数据")
            r.reason = "; ".join(r.fail_reasons)
            return r

        # 原始（未缩放）指标
        r.raw_total_return = float(np.prod([1.0 + p for p in daily_pnl]) - 1.0)
        r.raw_max_drawdown = self._compute_max_drawdown(daily_pnl)
        r.raw_realized_vol = float(np.std(daily_pnl, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))

        # ============ P2.1c 风险管理层：波动率缩放 + 回撤去杠杆 ============
        if self.risk_managed:
            daily_pnl, daily_factor_returns, rm_stats = self._apply_risk_management(
                daily_pnl, daily_factor_returns,
            )
            r.daily_pnl = daily_pnl
            r.realized_vol = rm_stats["realized_vol"]
            r.avg_scaler = rm_stats["avg_scaler"]
            r.derisk_triggered_days = rm_stats["derisk_triggered_days"]
            logger.info(
                "[ShadowAccount] %s 风险管理 | raw_dd=%.3f→rm_dd=%.3f raw_vol=%.3f→rm_vol=%.3f derisk_days=%d",
                factor_name, r.raw_max_drawdown, rm_stats["rm_max_dd"],
                r.raw_realized_vol, rm_stats["realized_vol"], rm_stats["derisk_triggered_days"],
            )

        # 总收益
        r.total_return = float(np.prod([1.0 + p for p in daily_pnl]) - 1.0)

        # 最大回撤
        r.max_drawdown = self._compute_max_drawdown(daily_pnl)

        # live DSR 复算
        dsr_result = self.dsr_validator.validate(
            factor_returns=daily_factor_returns,
            n_trials=n_trials,
            factor_name=factor_name,
        )
        r.live_dsr = float(dsr_result.dsr_value)
        r.sr_observed = float(dsr_result.sr_observed)
        r.risk_managed = self.risk_managed

        # ============ Step 4: 蒙特卡洛压力测试 ============
        mc_p95_dd, mc_mean_dd = self._monte_carlo_stress_test(daily_pnl)
        r.monte_carlo_p95_dd = float(mc_p95_dd)
        r.monte_carlo_mean_dd = float(mc_mean_dd)
        r.monte_carlo_trials = self.mc_trials

        # ============ Step 5: 三重准入判定 ============
        if r.live_dsr <= self.live_dsr_threshold:
            r.fail_reasons.append(f"live_DSR={r.live_dsr:.3f} <= {self.live_dsr_threshold}")
        if r.max_drawdown >= self.max_dd_threshold:
            r.fail_reasons.append(f"max_dd={r.max_drawdown:.3f} >= {self.max_dd_threshold}")
        if r.monte_carlo_p95_dd >= self.mc_p95_dd_threshold:
            r.fail_reasons.append(
                f"mc_p95_dd={r.monte_carlo_p95_dd:.3f} >= {self.mc_p95_dd_threshold}"
            )

        r.pass_shadow = len(r.fail_reasons) == 0
        r.reason = "pass" if r.pass_shadow else "; ".join(r.fail_reasons)

        logger.info(
            "[ShadowAccount] %s | days=%d dsr=%.3f sr=%.2f dd=%.3f mc_p95=%.3f %s",
            factor_name, use_days, r.live_dsr, r.sr_observed,
            r.max_drawdown, r.monte_carlo_p95_dd,
            "✓ PASS" if r.pass_shadow else "✗ FAIL: " + r.reason,
        )
        return r

    # ============================================================
    # 私有方法
    # ============================================================

    def _build_long_short(
        self, factor_values: Dict[str, float]
    ) -> Tuple[List[str], List[str]]:
        """构建 TopN 多空组合

        剔除 NaN/inf，按因子值排序，取前 N 做多、后 N 做空。
        """
        valid = [(s, v) for s, v in factor_values.items() if math.isfinite(v)]
        if len(valid) < 2 * self.long_short_top_n:
            # 标的不足，按一半多一半空
            if not valid:
                return [], []
            n_each = max(1, len(valid) // 4)
        else:
            n_each = self.long_short_top_n

        sorted_syms = sorted(valid, key=lambda x: x[1])
        short_syms = [s for s, _ in sorted_syms[:n_each]]
        long_syms = [s for s, _ in sorted_syms[-n_each:]]
        return long_syms, short_syms

    @staticmethod
    def _compute_avg_return(symbols: List[str], forward_returns: Dict[str, float]) -> float:
        """计算等权组合平均收益"""
        rets = [forward_returns[s] for s in symbols if s in forward_returns and math.isfinite(forward_returns[s])]
        if not rets:
            return 0.0
        return float(np.mean(rets))

    @staticmethod
    def _compute_turnover(prev: Optional[set], cur: set) -> float:
        """计算换手率 = 不重叠标的数 / 总持仓数"""
        if prev is None:
            return 1.0  # 首日全换手
        if not cur:
            return 0.0
        union = prev | cur
        if not union:
            return 0.0
        diff = len(prev.symmetric_difference(cur))
        return float(diff / len(union))

    @staticmethod
    def _compute_max_drawdown(daily_pnl: List[float]) -> float:
        """计算最大回撤"""
        if not daily_pnl:
            return 0.0
        cumulative = [1.0]
        for p in daily_pnl:
            cumulative.append(cumulative[-1] * (1.0 + p))
        peak = cumulative[0]
        max_dd = 0.0
        for v in cumulative:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
        return float(max_dd)

    def _apply_risk_management(
        self,
        daily_pnl: List[float],
        daily_factor_returns: List[float],
    ) -> Tuple[List[float], List[float], Dict[str, float]]:
        """P2.1c 风险管理层：波动率缩放 + 回撤去杠杆

        两层保护：
            1. 波动率缩放（Vol Targeting）：
               - 使用过去 vol_lookback 日 PnL 计算实现波动率
               - 缩放因子 = min(target_vol / realized_vol, scaler_cap)
               - 当实现波动率高于目标时降低敞口，低于目标时加杠杆（上限 2x）
            2. 回撤去杠杆（Drawdown De-risking）：
               - 跟踪累积净值回撤
               - 当回撤 > dd_derisk_threshold 时，敞口降至 dd_derisk_factor
               - 回撤恢复后敞口自动恢复

        设计依据：
            - 生产 V9 含风险管理，回撤 9.95%；单因子无风险管理回撤 23.3%
            - Shadow 应测试因子+风险管理组合，与生产使用方式一致
            - 风险管理不能挽救坏因子（IC_IR<0.3 的因子即使加风险管理也过不了 DSR）
            - 风险管理能将好因子的高波动降至可接受范围

        Args:
            daily_pnl: 原始日 PnL 序列
            daily_factor_returns: 原始日因子收益序列（用于 DSR）

        Returns:
            scaled_pnl: 缩放后日 PnL 序列
            scaled_factor_returns: 缩放后日因子收益序列
            stats: {realized_vol, avg_scaler, derisk_triggered_days, rm_max_dd}
        """
        n = len(daily_pnl)
        scaled_pnl: List[float] = []
        scaled_factor_returns: List[float] = []

        # 累积净值（用于计算回撤，基于缩放后 PnL）
        cumulative = [1.0]
        peak = 1.0

        scalers: List[float] = []
        derisk_days = 0

        for i in range(n):
            # ---- 1. 波动率缩放（基于过去 vol_lookback 日的 PnL）----
            if i >= self.vol_lookback:
                recent = daily_pnl[i - self.vol_lookback:i]
                realized_vol_daily = float(np.std(recent, ddof=1))
                realized_vol_annual = realized_vol_daily * math.sqrt(TRADING_DAYS_PER_YEAR)
                if realized_vol_annual > 1e-9:
                    vol_scaler = min(self.target_vol / realized_vol_annual, self.scaler_cap)
                else:
                    vol_scaler = 1.0
            else:
                # 预热期：不缩放
                vol_scaler = 1.0

            # ---- 2. 回撤去杠杆（基于昨日累积净值，避免前视偏差）----
            # 在开始今日交易前，检查当前回撤
            current_value = cumulative[-1]  # 昨日累积净值
            current_dd = (peak - current_value) / peak if peak > 0 else 0.0

            if current_dd > self.dd_derisk_threshold:
                dd_scaler = self.dd_derisk_factor
                derisk_days += 1
            else:
                dd_scaler = 1.0

            # ---- 3. 合并缩放因子并应用于今日 PnL ----
            combined_scaler = vol_scaler * dd_scaler
            scalers.append(combined_scaler)

            scaled_pnl_i = daily_pnl[i] * combined_scaler
            scaled_pnl.append(scaled_pnl_i)
            scaled_factor_returns.append(daily_factor_returns[i] * combined_scaler)

            # 更新累积净值与峰值
            new_value = cumulative[-1] * (1.0 + scaled_pnl_i)
            cumulative.append(new_value)
            if new_value > peak:
                peak = new_value

        # 统计
        rm_max_dd = self._compute_max_drawdown(scaled_pnl)
        rm_vol = float(np.std(scaled_pnl, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)) if n > 1 else 0.0
        avg_scaler = float(np.mean(scalers)) if scalers else 1.0

        stats = {
            "realized_vol": rm_vol,
            "avg_scaler": avg_scaler,
            "derisk_triggered_days": derisk_days,
            "rm_max_dd": rm_max_dd,
        }
        return scaled_pnl, scaled_factor_returns, stats

    def _monte_carlo_stress_test(self, daily_pnl: List[float]) -> Tuple[float, float]:
        """蒙特卡洛 bootstrap 压力测试

        对 daily_pnl 做有放回重采样 1000 次，计算每次最大回撤，
        返回 (P95 分位回撤, 平均回撤)。
        """
        if len(daily_pnl) < 10:
            return 1.0, 1.0  # 数据不足，返回极端值

        arr = np.array(daily_pnl, dtype=float)
        n = len(arr)
        dds = np.zeros(self.mc_trials, dtype=float)

        for trial in range(self.mc_trials):
            sample = self._rng.choice(arr, size=n, replace=True)
            dds[trial] = self._compute_max_drawdown(sample.tolist())

        p95 = float(np.percentile(dds, 95))
        mean_dd = float(np.mean(dds))
        return p95, mean_dd


# ============================================================
# 便捷函数
# ============================================================

def quick_shadow(
    factor_values_history: List[Dict[str, float]],
    forward_returns_history: List[Dict[str, float]],
    n_trials: int,
    factor_name: str = "candidate",
) -> ShadowResult:
    """快速影子账户测试（使用默认配置，无风险管理）"""
    account = ShadowAccount()
    return account.run_shadow(
        factor_values_history=factor_values_history,
        forward_returns_history=forward_returns_history,
        n_trials=n_trials,
        factor_name=factor_name,
    )


def quick_shadow_risk_managed(
    factor_values_history: List[Dict[str, float]],
    forward_returns_history: List[Dict[str, float]],
    n_trials: int,
    factor_name: str = "candidate",
    target_vol: float = RISK_MANAGED_TARGET_VOL,
) -> ShadowResult:
    """快速影子账户测试（启用 P2.1c 风险管理层）

    与 quick_shadow 的区别：
        - 启用波动率缩放（目标年化波动率 15%）
        - 启用回撤去杠杆（回撤 > 5% 时敞口降至 50%）

    适用场景：
        - 单因子 Alpha 信号强但回撤过大（如 VT_MICRO_VOL_SKEW_INV）
        - 验证因子在风险管理约束下是否仍能通过 Shadow
    """
    account = ShadowAccount(config={"risk_managed": True, "target_vol": target_vol})
    return account.run_shadow(
        factor_values_history=factor_values_history,
        forward_returns_history=forward_returns_history,
        n_trials=n_trials,
        factor_name=factor_name,
    )
