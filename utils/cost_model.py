"""
统一成本模型 (Single Source of Truth for Trading Costs)
========================================================

顶级对冲基金标准：全系统只保留一套成本假设，回测/预测必须扣除成本后再报净收益。

此前系统的两套预测脚本成本假设相互矛盾：
- annualized_return_forecast.py 取 ~0.45% (3x换手 * 15bps)，严重低估
- five_year_projection.py 取 2.8% (1.5%交易 + 0.5%滑点 + 0.8%管理费)

本模块将真实成本拆解为可审计的组成部分，给出保守的年度总成本，供所有
预测/回测脚本统一引用，禁止各模块自行拍脑袋设定成本。

成本组成 (A股量化专户，月度再平衡 + 期权覆盖 + 期货对冲)：
- 佣金 commission:         双边，约 0.02% / 边
- 印花税 stamp_duty:       仅卖出，A股 0.05%，按约一半交易为卖出计
- 冲击成本 impact:         高贝塔个股双边，约 0.05% / 边
- 期权覆盖损耗 option:     备兑+Covered Call滚仓 + 尾部Put权利金损耗，年化 ~0.30%
- 期货基差/滚仓 futures:   指数期货对冲滚仓损耗，年化 ~0.15%

年度再平衡次数默认 12 (月度)。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostAssumption:
    """可审计的成本假设（单位：bps，1bps = 0.01%）"""

    commission_bps: float = 2.0  # 单边佣金 (2 bps = 0.02%)
    stamp_duty_bps: float = 5.0  # 印花税（仅卖出，A股 5 bps = 0.05%）
    impact_per_side_bps: float = 5.0  # 单边市场冲击/滑点 (5 bps = 0.05%)
    option_overlay_bps: float = 30.0  # 期权覆盖年化损耗 (30 bps = 0.30%)
    futures_basis_bps: float = 15.0  # 期货对冲年化滚仓损耗 (15 bps = 0.15%)
    rebalance_per_year: int = 12  # 年度再平衡次数
    sell_ratio: float = 0.5  # 卖出占比（印花税估算用）

    @property
    def commission_annual_bps(self) -> float:
        # 每次再平衡双边
        return self.commission_bps * 2 * self.rebalance_per_year

    @property
    def stamp_annual_bps(self) -> float:
        return self.stamp_duty_bps * self.rebalance_per_year * self.sell_ratio

    @property
    def impact_annual_bps(self) -> float:
        return self.impact_per_side_bps * 2 * self.rebalance_per_year

    @property
    def annual_total_bps(self) -> float:
        return (
            self.commission_annual_bps
            + self.stamp_annual_bps
            + self.impact_annual_bps
            + self.option_overlay_bps
            + self.futures_basis_bps
        )

    @property
    def annual_total_cost(self) -> float:
        """年化总成本（小数，如 0.024 = 2.4%）"""
        return self.annual_total_bps / 10000.0

    def breakdown(self) -> dict[str, float]:
        """返回各组成部分（小数形式）便于报告与审计"""
        return {
            "commission": self.commission_annual_bps / 10000.0,
            "stamp_duty": self.stamp_annual_bps / 10000.0,
            "market_impact": self.impact_annual_bps / 10000.0,
            "option_overlay": self.option_overlay_bps / 10000.0,
            "futures_basis": self.futures_basis_bps / 10000.0,
            "total": self.annual_total_cost,
        }

    def net_return(self, gross_return: float) -> float:
        return gross_return - self.annual_total_cost


# 全系统默认成本假设：保守、可审计、约 2.43%/年
DEFAULT_COST_MODEL = CostAssumption()


def get_cost_model() -> CostAssumption:
    """所有预测/回测脚本统一调用此函数获取成本模型，禁止本地硬编码。"""
    return DEFAULT_COST_MODEL
