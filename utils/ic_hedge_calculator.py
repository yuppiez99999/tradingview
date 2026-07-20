# -*- coding: utf-8 -*-
"""
IC 期货对冲量计算器 v1.0
============================

功能:
    - 根据多头组合市值与 portfolio beta 计算需要做空的 IC (中证500股指期货) 合约数
    - 支持目标 beta (默认 0.05, 最大净敞口 0.10)
    - 自动校验基差对冲成本, 贴水 > 1.5% 时减少对冲量 (避免高成本对冲)
    - 计算所需保证金与维持率
    - 输出对冲指令 (可被 daily_workflow 消费)

对冲公式:
    所需对冲名义市值 = 多头组合市值 × (portfolio_beta - target_beta)
    IC 合约数 = round(所需对冲名义市值 / IC 合约乘数 / IC 点位)

IC 合约规格 (中金所):
    - 合约乘数: 200 元/点
    - 保证金比例: 12% (2024 起)
    - 最小变动价位: 0.2 点
    - 交易单位: 1 手 = 200 × 点位

用法:
    from utils.ic_hedge_calculator import ICHedgeCalculator
    calc = ICHedgeCalculator()
    result = calc.calculate(
        long_market_value=1_400_000,
        portfolio_beta=0.85,
        target_beta=0.05,
        ic_price=5500.0,
    )
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger("ic_hedge")

# ============================================================
# IC 合约规格 (中证500股指期货)
# ============================================================
IC_MULTIPLIER = 200.0          # 合约乘数: 200 元/点
IC_MARGIN_RATE = 0.12           # 保证金比例: 12%
IC_MIN_TICK = 0.2               # 最小变动价位
IC_MAX_CONTRACTS = 3            # v10.0 限制: 最多 3 张 (quant_neutral_account.short_contracts_max)
IC_BASIS_THRESHOLD = 0.015      # 贴水 1.5% 触发减仓
IC_BASIS_REDUCE_PCT = 0.30      # 贴水超限时减少 30% 对冲量
DEFAULT_TARGET_BETA = 0.05      # 目标 beta
MAX_NET_EXPOSURE = 0.10         # 最大净敞口
MARGIN_MAINTENANCE_MIN = 0.60   # 维持保证金最低 60%


@dataclass
class ICHedgeResult:
    """IC 对冲计算结果"""
    target_contracts: int = 0           # 目标做空合约数
    hedge_notional: float = 0.0         # 对冲名义市值
    required_margin: float = 0.0        # 所需保证金
    margin_usage_ratio: float = 0.0      # 保证金占用率
    net_beta: float = 0.0                # 对冲后净 beta
    net_exposure: float = 0.0            # 净敞口
    basis_warning: bool = False          # 基差警告 (贴水过高)
    adjusted_contracts: int = 0          # 基差调整后实际合约数
    ic_price: float = 0.0                # IC 点位
    long_market_value: float = 0.0       # 多头组合市值
    portfolio_beta: float = 0.0          # 原始组合 beta
    target_beta: float = 0.0              # 目标 beta
    feasible: bool = True                # 是否可行
    reason: str = ""                     # 不可行原因


class ICHedgeCalculator:
    """IC (中证500) 股指期货对冲量计算器

    适用场景:
        - 量化市场中性策略的 beta 对冲
        - 股票多头组合的系统性风险对冲
        - v10.0 quant_neutral_account 专用 (70 万资金, 140 万名义)
    """

    def __init__(
        self,
        multiplier: float = IC_MULTIPLIER,
        margin_rate: float = IC_MARGIN_RATE,
        max_contracts: int = IC_MAX_CONTRACTS,
        basis_threshold: float = IC_BASIS_THRESHOLD,
        basis_reduce_pct: float = IC_BASIS_REDUCE_PCT,
    ):
        self.multiplier = multiplier
        self.margin_rate = margin_rate
        self.max_contracts = max_contracts
        self.basis_threshold = basis_threshold
        self.basis_reduce_pct = basis_reduce_pct

    # ------------------------------------------------------------
    # 核心计算
    # ------------------------------------------------------------
    def calculate(
        self,
        long_market_value: float,
        portfolio_beta: float,
        target_beta: float,
        ic_price: float,
        available_margin: Optional[float] = None,
        basis: Optional[float] = None,
    ) -> ICHedgeResult:
        """计算 IC 做空合约数

        Args:
            long_market_value: 多头组合市值 (元)
            portfolio_beta: 多头组合 beta
            target_beta: 目标 beta (通常 0.05)
            ic_price: IC 期货点位
            available_margin: 可用保证金 (元, 可选, 用于校验)
            basis: 基差 = (现货 - 期货) / 现货, 正值=贴水, 负值=升水

        Returns:
            ICHedgeResult: 对冲计算结果
        """
        result = ICHedgeResult(
            long_market_value=long_market_value,
            portfolio_beta=portfolio_beta,
            target_beta=target_beta,
            ic_price=ic_price,
        )

        # 1. 异常参数校验
        if long_market_value <= 0 or ic_price <= 0:
            result.feasible = False
            result.reason = "参数错误: 多头市值或 IC 价格 <= 0"
            return result

        if portfolio_beta <= target_beta:
            # 已达标, 无需对冲
            result.target_contracts = 0
            result.adjusted_contracts = 0
            result.net_beta = portfolio_beta
            result.net_exposure = portfolio_beta
            result.reason = "组合 beta 已达标, 无需对冲"
            return result

        # 2. 计算所需对冲名义市值
        # 需要消除的 beta = portfolio_beta - target_beta
        # 对冲名义 = 多头市值 × (portfolio_beta - target_beta)
        beta_to_hedge = portfolio_beta - target_beta
        hedge_notional = long_market_value * beta_to_hedge
        result.hedge_notional = hedge_notional

        # 3. 计算所需 IC 合约数
        # 使用 ceil 向上取整: 宁可过度对冲也不要敞口超限 (hedge fund 实务)
        contract_value = self.multiplier * ic_price  # 1 张 IC 合约名义价值
        raw_contracts = hedge_notional / contract_value
        target_contracts = max(1, math.ceil(raw_contracts))  # 至少 1 张, 向上取整

        # 4. 上限控制 (v10.0: 最多 3 张)
        capped = False
        if target_contracts > self.max_contracts:
            target_contracts = self.max_contracts
            capped = True

        result.target_contracts = target_contracts

        # 5. 基差调整 (贴水过高时减少对冲量, 转向 ETF + 个股直接组合)
        adjusted = target_contracts
        if basis is not None and basis > self.basis_threshold:
            result.basis_warning = True
            # 贴水过高, 减少 30% 对冲量
            adjusted = max(0, int(round(target_contracts * (1 - self.basis_reduce_pct))))
            if adjusted == 0 and target_contracts > 0:
                # 完全不对冲, 转向 ETF 组合
                result.reason = (
                    f"基差贴水 {basis*100:.2f}% > {self.basis_threshold*100:.0f}%, "
                    f"减少 {self.basis_reduce_pct*100:.0f}% 对冲量, "
                    "建议转向 ETF + 个股直接组合"
                )
            logger.warning(
                f"[IC对冲] 基差警告: 贴水 {basis*100:.2f}%, "
                f"对冲量 {target_contracts} → {adjusted} 张"
            )

        result.adjusted_contracts = adjusted

        # 6. 计算所需保证金
        actual_contracts = adjusted
        required_margin = actual_contracts * contract_value * self.margin_rate
        result.required_margin = required_margin

        # 7. 保证金占用率 (若提供可用保证金)
        if available_margin is not None and available_margin > 0:
            result.margin_usage_ratio = required_margin / available_margin
            if result.margin_usage_ratio > MARGIN_MAINTENANCE_MIN:
                # 超过维持保证金最低线, 减少合约
                while actual_contracts > 0 and required_margin / available_margin > MARGIN_MAINTENANCE_MIN:
                    actual_contracts -= 1
                    required_margin = actual_contracts * contract_value * self.margin_rate
                result.adjusted_contracts = actual_contracts
                result.required_margin = required_margin
                result.margin_usage_ratio = required_margin / available_margin
                if actual_contracts == 0:
                    result.feasible = False
                    result.reason = (
                        f"可用保证金不足: 需要 ¥{required_margin:,.0f}, "
                        f"维持率要求 ≥ {MARGIN_MAINTENANCE_MIN*100:.0f}%"
                    )

        # 8. 计算对冲后净 beta 和净敞口
        if actual_contracts > 0:
            actual_hedge_notional = actual_contracts * contract_value
            # 净 beta = portfolio_beta - 实际对冲名义 / 多头市值
            result.net_beta = max(0, portfolio_beta - actual_hedge_notional / long_market_value)
            result.net_exposure = result.net_beta
        else:
            result.net_beta = portfolio_beta
            result.net_exposure = portfolio_beta

        # 9. 净敞口超限警告
        if result.net_exposure > MAX_NET_EXPOSURE:
            logger.warning(
                f"[IC对冲] 净敞口 {result.net_exposure:.2%} 超过上限 {MAX_NET_EXPOSURE:.0%}, "
                "需减少多头仓位或增加对冲"
            )

        if capped:
            result.reason = (
                f"已达最大合约数上限 {self.max_contracts} 张, "
                f"建议增加 ETF + 个股直接组合以降低 beta"
            )

        return result

    # ------------------------------------------------------------
    # 对冲指令生成
    # ------------------------------------------------------------
    def build_hedge_order(
        self,
        result: ICHedgeResult,
        trade_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """根据计算结果生成 IC 对冲指令

        Args:
            result: ICHedgeResult 对象
            trade_date: 交易日期

        Returns:
            指令字典, 可写入 trade_instructions/{date}_instructions.json
        """
        trade_date = trade_date or date.today()

        if result.adjusted_contracts == 0:
            return {
                "action": "skip",
                "reason": result.reason or "无需对冲",
                "trade_date": trade_date.isoformat(),
            }

        contract_value = self.multiplier * result.ic_price
        return {
            "action": "open_short" if result.target_contracts > 0 else "skip",
            "symbol": "IC",
            "exchange": "CFFEX",
            "direction": "short",
            "contracts": result.adjusted_contracts,
            "price": result.ic_price,
            "notional_value": result.adjusted_contracts * contract_value,
            "required_margin": result.required_margin,
            "multiplier": self.multiplier,
            "trade_date": trade_date.isoformat(),
            "rationale": {
                "long_market_value": result.long_market_value,
                "portfolio_beta": result.portfolio_beta,
                "target_beta": result.target_beta,
                "net_beta_after_hedge": result.net_beta,
                "basis_warning": result.basis_warning,
                "reason": result.reason,
            },
        }

    # ------------------------------------------------------------
    # 调仓指令 (从当前持仓数 → 目标持仓数)
    # ------------------------------------------------------------
    def build_rebalance_order(
        self,
        current_contracts: int,
        target_result: ICHedgeResult,
        trade_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """生成调仓指令

        Args:
            current_contracts: 当前持有的空头合约数
            target_result: 目标计算结果
            trade_date: 交易日期

        Returns:
            调仓指令
        """
        trade_date = trade_date or date.today()
        target = target_result.adjusted_contracts
        delta = target - current_contracts

        if delta == 0:
            return {
                "action": "hold",
                "symbol": "IC",
                "current": current_contracts,
                "target": target,
                "trade_date": trade_date.isoformat(),
            }
        elif delta > 0:
            # 增加空头
            return {
                "action": "add_short",
                "symbol": "IC",
                "exchange": "CFFEX",
                "direction": "short",
                "delta_contracts": delta,
                "current": current_contracts,
                "target": target,
                "price": target_result.ic_price,
                "trade_date": trade_date.isoformat(),
            }
        else:
            # 减少空头 (买入平仓)
            return {
                "action": "reduce_short",
                "symbol": "IC",
                "exchange": "CFFEX",
                "direction": "close_short",
                "delta_contracts": abs(delta),
                "current": current_contracts,
                "target": target,
                "price": target_result.ic_price,
                "trade_date": trade_date.isoformat(),
            }

    # ------------------------------------------------------------
    # 摘要输出
    # ------------------------------------------------------------
    def summary(self, result: ICHedgeResult) -> str:
        """生成对冲结果摘要"""
        lines = [
            "=" * 60,
            "IC 期货对冲计算结果",
            "=" * 60,
            f"多头组合市值: ¥{result.long_market_value:,.0f}",
            f"组合 Beta: {result.portfolio_beta:.3f}",
            f"目标 Beta: {result.target_beta:.3f}",
            f"IC 价格: {result.ic_price:.1f} 点",
            "",
            f"目标做空合约: {result.target_contracts} 张",
            f"调整后合约: {result.adjusted_contracts} 张",
            f"对冲名义市值: ¥{result.hedge_notional:,.0f}",
            f"所需保证金: ¥{result.required_margin:,.0f}",
            f"保证金占用率: {result.margin_usage_ratio:.1%}",
            "",
            f"对冲后净 Beta: {result.net_beta:.3f}",
            f"净敞口: {result.net_exposure:.3f}",
        ]

        if result.basis_warning:
            lines.append("")
            lines.append("[警告] 基差贴水过高, 已减少对冲量")

        if not result.feasible:
            lines.append("")
            lines.append(f"[不可行] {result.reason}")
        elif result.reason:
            lines.append("")
            lines.append(f"[说明] {result.reason}")

        lines.append("=" * 60)
        return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="IC 期货对冲量计算器")
    parser.add_argument("--long-value", type=float, default=1_400_000, help="多头组合市值")
    parser.add_argument("--portfolio-beta", type=float, default=0.85, help="组合 beta")
    parser.add_argument("--target-beta", type=float, default=DEFAULT_TARGET_BETA, help="目标 beta")
    parser.add_argument("--ic-price", type=float, default=5500.0, help="IC 期货价格")
    parser.add_argument("--available-margin", type=float, default=None, help="可用保证金")
    parser.add_argument("--basis", type=float, default=None, help="基差 (正=贴水, 负=升水)")
    args = parser.parse_args()

    calc = ICHedgeCalculator()
    result = calc.calculate(
        long_market_value=args.long_value,
        portfolio_beta=args.portfolio_beta,
        target_beta=args.target_beta,
        ic_price=args.ic_price,
        available_margin=args.available_margin,
        basis=args.basis,
    )

    print(calc.summary(result))

    print("\n对冲指令:")
    order = calc.build_hedge_order(result)
    print(json.dumps(order, ensure_ascii=False, indent=2, default=str))
