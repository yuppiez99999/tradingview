# -*- coding: utf-8 -*-
"""
v7.5 尾部风险对冲引擎 — 7.4 移植版

来源:
    - _archive_dead_code/tail_risk_hedge.py (883 行)
    - _archive_dead_code/protective_put_manager.py (658 行)

核心能力 (与 7.4 对齐):
    1. 4 状态机: normal → warning → crisis → recovery
       (来源: tail_risk_hedge.py:138-148 analyze_market_regime)
    2. 5 级市场状态对冲比例: normal 30% / yellow 50% / orange 70% / red 85% / extreme 100%
       (来源: protective_put_manager.py:119-125 REGIME_HEDGE_RATIOS)
    3. 三层 OTM Put 阶梯: bs_loss>50% → [10%, 15%, 20%] / >35% → [8%, 12%] / 其他 → [10%]
       (来源: protective_put_manager.py:202-207)
    4. recovery 状态自动降低保护比例至 30%
       (来源: tail_risk_hedge.py:264-265)
    5. VIX>35 时 Put Delta 自动放大 1.5 倍
       (来源: protective_put_manager.py:422-423)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger('v7.5.tail_risk_hedge')


# ============================================================
# 4 状态机 (7.4 tail_risk_hedge.py:138-148)
# ============================================================

class MarketRegime:
    """市场状态机"""
    NORMAL = "normal"        # tail_risk_score < 0.3
    WARNING = "warning"      # 0.3 ≤ score < 0.6
    CRISIS = "crisis"        # score ≥ 0.8
    RECOVERY = "recovery"    # 0.6 ≤ score < 0.8 (缓解期)


# ============================================================
# 5 级市场状态 (7.4 protective_put_manager.py:119-125)
# ============================================================

REGIME_HEDGE_RATIOS = {
    "normal":  0.30,    # 正常: 覆盖 30%
    "yellow":  0.50,    # 黄色: 50%
    "orange":  0.70,    # 橙色: 70%
    "red":     0.85,    # 红色: 85%
    "extreme": 1.00,    # 极端: 全覆盖
}


@dataclass
class TailRiskConfig:
    """尾部风险对冲配置"""
    # tail_risk_hedge.py:67-76
    max_protection_ratio: float = 0.30       # 危机期最大保护比例 30%
    warning_protection_ratio: float = 0.21   # 警告期 70% (30% * 0.7)
    recovery_protection_ratio: float = 0.09  # 恢复期 30% (30% * 0.3)
    recovery_threshold: float = 0.02         # 风险缓解阈值

    # protective_put_manager.py:82-125
    annual_budget_pct: float = 0.025         # 年度对冲预算 2.5%
    min_hedge_ratio: float = 0.30            # 最低覆盖 30%
    max_hedge_ratio: float = 1.0             # 最高全覆盖
    expiry_months: int = 3                   # 期权期限 3 个月

    # 主副标的分配 (protective_put_manager.py:215-230)
    main_weight: float = 0.70                # 主标的 70%
    sub_weight: float = 0.30                 # 副标的 30%


# ============================================================
# TailRiskHedger 主类
# ============================================================

class TailRiskHedger:
    """尾部风险对冲引擎 — 7.4 移植版

    实现了:
        - 4 状态机 (normal/warning/crisis/recovery)
        - 5 级市场对冲比例
        - 三层 OTM Put 阶梯
        - recovery 自动回补
        - VIX>35 时 Delta 放大 1.5 倍
    """

    def __init__(self, config: Optional[TailRiskConfig] = None):
        self.config = config or TailRiskConfig()
        self.current_regime = MarketRegime.NORMAL
        self.current_protection_ratio = 0.0
        self.history: List[Dict] = []

    # ---------- 1. 状态机判定 ----------
    def analyze_market_regime(self,
                              vix: float,
                              hwm_drawdown: float,
                              portfolio_volatility: float = 0.0,
                              var_95: float = 0.0,
                              cvar_95: float = 0.0) -> str:
        """
        7.4 移植: 4 状态机判定 (tail_risk_hedge.py:138-148)

        综合评分 = f(dd, vix, vol, var, cvar)
            score > 0.8 → crisis
            0.6 ≤ score < 0.8 → warning
            0.3 ≤ score < 0.6 → recovery
            score < 0.3 → normal

        Returns:
            MarketRegime 字符串
        """
        # 综合评分 (简化版, 加权 dd + vix)
        dd_score = min(1.0, hwm_drawdown * 3)              # dd 33% 满分
        vix_score = min(1.0, max(0.0, (vix - 18) / 62))   # vix 18起评 → 80满分
        vol_score = min(1.0, portfolio_volatility * 5)     # vol 20% 满分
        var_score = min(1.0, var_95 * 10)                  # var 10% 满分

        # 加权综合 (dd 40% + vix 35% + vol 15% + var 10%)
        tail_score = (dd_score * 0.40 + vix_score * 0.35 +
                      vol_score * 0.15 + var_score * 0.10)

        if tail_score > 0.8:
            regime = MarketRegime.CRISIS
        elif tail_score > 0.6:
            regime = MarketRegime.WARNING
        elif tail_score > 0.3:
            regime = MarketRegime.RECOVERY
        else:
            regime = MarketRegime.NORMAL

        self.current_regime = regime
        return regime

    # ---------- 2. 保护比例 (5 级 + recovery 衰减) ----------
    def calculate_protection_ratio(self,
                                   vix: float,
                                   hwm_drawdown: float,
                                   bs_loss: float = 0.0) -> float:
        """
        7.4 移植: 计算当前保护比例

        来源:
            - tail_risk_hedge.py:260-267 (4 状态机衰减)
            - protective_put_manager.py:119-125 (5 级市场)
            - protective_put_manager.py:422-423 (VIX>35 Delta 放大 1.5 倍)
        """
        regime = self.current_regime

        # 1. 基础保护比例 (基于状态机)
        if regime == MarketRegime.CRISIS:
            base = self.config.max_protection_ratio  # 0.30
        elif regime == MarketRegime.WARNING:
            base = self.config.warning_protection_ratio  # 0.21
        elif regime == MarketRegime.RECOVERY:
            base = self.config.recovery_protection_ratio  # 0.09
        else:
            base = 0.0

        # 2. 黑天鹅损失加深保护 (bs_loss 越大, 保护越深)
        if bs_loss > 0.50:
            base = max(base, 0.30)  # 至少 30%
        elif bs_loss > 0.35:
            base = max(base, 0.20)

        # 3. VIX>35 时 Delta 放大 1.5 倍 (protective_put_manager.py:422-423)
        if vix > 35:
            base = min(1.0, base * 1.5)

        # 4. 5 级市场状态映射 (覆盖更细)
        if vix >= 80 or hwm_drawdown >= 0.20:
            market_state = "extreme"
            base = max(base, REGIME_HEDGE_RATIOS["extreme"])
        elif vix >= 60 or hwm_drawdown >= 0.15:
            market_state = "red"
            base = max(base, REGIME_HEDGE_RATIOS["red"])
        elif vix >= 40 or hwm_drawdown >= 0.10:
            market_state = "orange"
            base = max(base, REGIME_HEDGE_RATIOS["orange"])
        elif vix >= 18 or hwm_drawdown >= 0.05:
            market_state = "yellow"
            base = max(base, REGIME_HEDGE_RATIOS["yellow"])
        else:
            market_state = "normal"

        # 保存当前5级市场状态 (供compute_hedge动作判定使用)
        self._market_state = market_state

        # 5. 限制在 [min, max]
        ratio = max(self.config.min_hedge_ratio if regime != MarketRegime.NORMAL else 0.0,
                    min(self.config.max_hedge_ratio, base))

        self.current_protection_ratio = ratio
        return ratio

    # ---------- 3. 三层 OTM Put 阶梯 ----------
    def build_otm_ladder(self,
                         bs_loss: float,
                         vix: float,
                         spot_price: float) -> List[Dict]:
        """
        7.4 移植: 三层 OTM Put 阶梯构建

        来源: protective_put_manager.py:202-207
            bs_loss > 0.50 → [10%, 15%, 20%] OTM (三层深度虚值)
            bs_loss > 0.35 → [8%, 12%] OTM (两层中等虚值)
            其他           → [10%] OTM (单层标准)

        Returns:
            [{'otm_pct': 0.10, 'weight': 0.4, 'strike': 0.90*spot}, ...]
        """
        if bs_loss > 0.50:
            otm_pcts = [0.10, 0.15, 0.20]
            weights = [0.40, 0.35, 0.25]
        elif bs_loss > 0.35:
            otm_pcts = [0.08, 0.12]
            weights = [0.55, 0.45]
        else:
            otm_pcts = [0.10]
            weights = [1.0]

        # VIX>35 时整体 Delta 放大 1.5 倍 (增加权重)
        if vix > 35:
            scale = 1.5
            weights = [min(1.0, w * scale) for w in weights]

        ladder = []
        for otm, w in zip(otm_pcts, weights):
            ladder.append({
                "otm_pct": otm,
                "strike": round(spot_price * (1 - otm), 4),
                "weight": round(w, 3),
                "delta_target": -0.20 * (1 + otm * 5),  # OTM 越深 delta 越小
            })
        return ladder

    # ---------- 4. 主副标的双层分配 ----------
    def allocate_main_sub(self,
                          total_budget: float,
                          main_symbol: str = "510300",
                          sub_symbol: str = "588000") -> Dict[str, float]:
        """
        7.4 移植: 主副标的双层资金分配

        来源: protective_put_manager.py:215-230
            主标的 70% (沪深300ETF, Beta=0.90)
            副标的 30% (科创50ETF, Beta=1.15, 高 Beta)
        """
        return {
            main_symbol: total_budget * self.config.main_weight,
            sub_symbol: total_budget * self.config.sub_weight,
        }

    # ---------- 5. 期权展期判断 ----------
    def should_roll(self,
                    days_to_expiry: int,
                    current_vix: float,
                    emergency_level: int = 0) -> Dict:
        """
        7.4 移植: 期权展期判断

        来源: protective_put_manager.py:583-603
            到期前 5 天评估
            VIX<18 且 emergency_level=0 → 降低对冲比例
            恶化信号 → 加深 OTM 深度
        """
        if days_to_expiry > 5:
            return {"action": "HOLD", "reason": f"距到期 {days_to_expiry} 天"}

        if current_vix < 18 and emergency_level == 0:
            return {
                "action": "REDUCE_HEDGE",
                "reason": f"VIX={current_vix} 风险缓解, 降低对冲比例",
                "new_ratio": self.config.min_hedge_ratio,
            }

        if current_vix > 40:
            return {
                "action": "DEEPEN_OTM",
                "reason": f"VIX={current_vix} 风险加剧, 加深 OTM",
                "new_otm_ladder": self.build_otm_ladder(
                    bs_loss=0.5, vix=current_vix, spot_price=1.0
                ),
            }

        return {"action": "ROLL", "reason": "标准展期"}

    # ---------- 6. 完整对冲决策 ----------
    def compute_hedge(self,
                      vix: float,
                      hwm_drawdown: float,
                      portfolio_value: float,
                      spot_price: float = 1.0,
                      bs_loss: float = 0.0,
                      portfolio_volatility: float = 0.0,
                      var_95: float = 0.0,
                      days_to_expiry: int = 90) -> Dict:
        """
        完整尾部风险对冲决策 (整合 4 状态机 + 5 级 + OTM 阶梯)
        """
        # 1. 状态机
        regime = self.analyze_market_regime(
            vix=vix,
            hwm_drawdown=hwm_drawdown,
            portfolio_volatility=portfolio_volatility,
            var_95=var_95,
        )

        # 2. 保护比例
        protection_ratio = self.calculate_protection_ratio(
            vix=vix, hwm_drawdown=hwm_drawdown, bs_loss=bs_loss,
        )

        # 3. OTM 阶梯
        otm_ladder = self.build_otm_ladder(
            bs_loss=bs_loss, vix=vix, spot_price=spot_price,
        )

        # 4. 预算分配
        total_budget = portfolio_value * self.config.annual_budget_pct / 12  # 月度预算
        symbol_alloc = self.allocate_main_sub(total_budget)

        # 5. 展期判断
        roll_decision = self.should_roll(days_to_expiry, vix)

        # 6. 综合动作
        if protection_ratio <= 0:
            action = "NO_HEDGE"
        elif regime == MarketRegime.CRISIS:
            action = "EMERGENCY_PUT"     # 危机期紧急 Put
        elif regime == MarketRegime.WARNING:
            action = "BARE_PUT"          # 警告期裸 Put
        elif regime == MarketRegime.RECOVERY:
            action = "PUT_SPREAD"        # 恢复期 Put Spread (省成本)
        elif getattr(self, '_market_state', 'normal') in ('yellow', 'orange'):
            action = "PUT_SPREAD"        # ★v7.6: VIX≥18黄区触发 — 轻量建仓期权保护
        else:
            action = "NO_HEDGE"

        result = {
            "action": action,
            "regime": regime,
            "protection_ratio": round(protection_ratio, 4),
            "budget_total": round(total_budget, 0),
            "budget_allocation": {k: round(v, 0) for k, v in symbol_alloc.items()},
            "otm_ladder": otm_ladder,
            "roll_decision": roll_decision,
            "vix": vix,
            "hwm_drawdown": hwm_drawdown,
            "bs_loss": bs_loss,
        }

        self.history.append(result)
        logger.info(
            f"尾部风险对冲: regime={regime}, action={action}, "
            f"保护比例={protection_ratio:.2%}, OTM层数={len(otm_ladder)}, "
            f"预算={total_budget:.0f}"
        )
        return result
