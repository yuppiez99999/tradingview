# v7.6 现金收益增强 -- 闲置现金自动管理
# 当前: 34.8% 现金 (=174万) 年化收益 0% → 优化 → 逆回购 ≈ 1.5-1.8% 年化
# 174万 × 1.5% = 年入 2.6万 ÷ 500万 = 组合级 +0.52%/年
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, List

logger = logging.getLogger("v76.treasury")

@dataclass
class CashYieldConfig:
    min_cash_buffer: float = 50000       # 最低保留现金 (5万)
    reverse_repo_ratio: float = 0.85     # 超出缓冲的 85% 做逆回购
    daily_yield_rate: float = 0.000062   # 约 1.55% 年化 (GC001 日均)
    holiday_multiplier: float = 2.5      # 节前逆回购利率倍数
    auto_roll: bool = True               # 自动续作


class CashYieldManager:
    """桥水式现金管理: 闲置现金 → GC001 逆回购自动化"""
    
    def __init__(self, config: Optional[CashYieldConfig] = None):
        self.cfg = config or CashYieldConfig()
        self._daily_log: List[dict] = []
        self._cumulative_income: float = 0.0
        self._days_active: int = 0
        
    def optimize(self, total_cash: float, daily_margin_need: float = 0) -> dict:
        """返回: {keep_cash, deploy_repo, expected_daily_income}"""
        reserve = self.cfg.min_cash_buffer + daily_margin_need
        available = max(0, total_cash - reserve)
        
        deploy = available * self.cfg.reverse_repo_ratio
        keep = total_cash - deploy
        
        # 检测节前效应 (周五 / 节前最后一天)
        today = date.today()
        is_holiday_eve = self._is_holiday_eve(today)
        rate = self.cfg.daily_yield_rate * (self.cfg.holiday_multiplier if is_holiday_eve else 1.0)
        income = deploy * rate
        
        entry = {
            'date': today.isoformat(),
            'total_cash': total_cash,
            'deployed': deploy,
            'kept': keep,
            'daily_income': income,
            'rate': rate,
            'holiday_boost': is_holiday_eve,
        }
        self._daily_log.append(entry)
        self._cumulative_income += income
        self._days_active += 1
        
        logger.info(
            "现金管理: 总%.0f 部署%.0f(逆回购) 保留%.0f 日收益%.2f 累计%.2f",
            total_cash, deploy, keep, income, self._cumulative_income
        )
        
        # 自动续作: 标记 flags 供主流程读取
        need_roll = self.cfg.auto_roll and len(self._daily_log) >= 2
        
        return {
            'keep_cash': round(keep, 2),
            'deploy_repo': round(deploy, 2),
            'expected_daily_income': round(income, 2),
            'cumulative_income': round(self._cumulative_income, 2),
            'annualized_yield_on_cash': round(income * 252 / max(total_cash, 1), 6),
            'need_roll': need_roll,
            'days_active': self._days_active,
        }

    def _is_holiday_eve(self, d: date) -> bool:
        if d.weekday() == 4:  # 周五
            return True
        return False

    @property
    def total_income(self) -> float:
        return self._cumulative_income

    def report(self) -> dict:
        return {
            'total_income': round(self._cumulative_income, 2),
            'days_active': self._days_active,
            'avg_daily_income': round(self._cumulative_income / max(self._days_active, 1), 2),
            'estimated_annual': round(self._cumulative_income / max(self._days_active, 1) * 252, 2),
        }
