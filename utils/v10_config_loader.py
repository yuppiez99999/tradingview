# -*- coding: utf-8 -*-
"""
v10.0 投资计划配置加载器
==========================

加载 auto_trade_plan_v10_十五五.json 配置, 提供:
    - 6 账户资金分配 (stock_long/etf/futures/quant_neutral/options/cash)
    - 5 年度阶段切换 (2026-2030)
    - 风控参数 (VaR/回撤/集中度/止损)
    - 每日时间表 (07:00-16:00)
    - 再平衡规则 (定期/阈值/回撤/极端事件)

用法:
    from utils.v10_config_loader import V10ConfigLoader
    loader = V10ConfigLoader()
    config = loader.load()
    phase = loader.get_current_phase()
    allocation = loader.get_allocation()
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("v10_config")

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "trade_plans" / "auto_trade_plan_v10_十五五.json"


class V10ConfigLoader:
    """v10.0 投资计划配置加载器"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self._config: Optional[Dict] = None

    def load(self) -> Dict[str, Any]:
        """加载 v10.0 配置"""
        if self._config is not None:
            return self._config

        if not self.config_path.exists():
            logger.warning(f"v10.0 配置文件不存在: {self.config_path}")
            return {}

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
            logger.info(f"v10.0 配置加载成功: {self.config_path.name}")
            return self._config
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error(f"v10.0 配置加载失败: {e}")
            return {}

    def get_allocation(self) -> Dict[str, float]:
        """获取 6 账户资金分配"""
        cfg = self.load()
        meta = cfg.get("meta", {})
        return meta.get("allocation", {})  # type: ignore

    def get_hedge_fund_standard(self) -> Dict[str, Any]:
        """获取对冲基金标准风控参数"""
        cfg = self.load()
        return cfg.get("meta", {}).get("hedge_fund_standard", {})  # type: ignore

    def get_stock_positions(self) -> List[Dict]:
        """获取股票多头持仓列表"""
        cfg = self.load()
        return cfg.get("stock_long_account", {}).get("positions", [])  # type: ignore

    def get_etf_positions(self) -> List[Dict]:
        """获取 ETF 持仓列表"""
        cfg = self.load()
        return cfg.get("etf_account", {}).get("positions", [])  # type: ignore

    def get_futures_config(self) -> Dict:
        """获取期货账户配置"""
        cfg = self.load()
        return cfg.get("macro_hedge_account", {})  # type: ignore

    def get_quant_neutral_config(self) -> Dict:
        """获取量化中性策略配置"""
        cfg = self.load()
        return cfg.get("quant_neutral_account", {})  # type: ignore

    def get_options_config(self) -> Dict:
        """获取期权策略配置"""
        cfg = self.load()
        return cfg.get("options_account", {})  # type: ignore

    def get_cash_config(self) -> Dict:
        """获取现金管理配置"""
        cfg = self.load()
        return cfg.get("cash_management", {})  # type: ignore

    def get_current_phase(self, today: Optional[date] = None) -> Dict[str, Any]:
        """根据日期获取当前年度阶段

        Args:
            today: 当前日期 (默认今天)

        Returns:
            {
                "phase_key": "phase_2026",
                "name": "建仓期",
                "period": "2026-07-14 to 2026-12-31",
                "target_return": 0.08,
                "max_drawdown": 0.08,
                "actions": {...},
                "capital_deployment": {...},
                "daily_build_limit": 200000,
            }
        """
        cfg = self.load()
        execution_plan = cfg.get("execution_plan", {})
        today = today or date.today()

        # 年度阶段判断
        phases = [
            ("phase_2026", date(2026, 1, 1), date(2026, 12, 31)),
            ("phase_2027", date(2027, 1, 1), date(2027, 12, 31)),
            ("phase_2028", date(2028, 1, 1), date(2028, 12, 31)),
            ("phase_2029", date(2029, 1, 1), date(2029, 12, 31)),
            ("phase_2030", date(2030, 1, 1), date(2030, 12, 31)),
        ]

        for phase_key, start, end in phases:
            if start <= today <= end:
                phase_data = execution_plan.get(phase_key, {})
                return {"phase_key": phase_key, **phase_data}

        # 默认返回建仓期
        return {"phase_key": "phase_2026", **execution_plan.get("phase_2026", {})}

    def get_daily_schedule(self) -> Dict[str, Dict]:
        """获取每日时间表"""
        cfg = self.load()
        return cfg.get("daily_schedule", {})  # type: ignore

    def get_risk_automation(self) -> Dict[str, Any]:
        """获取风控自动化配置"""
        cfg = self.load()
        return cfg.get("risk_automation", {})  # type: ignore

    def get_rebalance_config(self) -> Dict[str, Any]:
        """获取再平衡配置"""
        cfg = self.load()
        return cfg.get("dynamic_rebalance", {})  # type: ignore

    def get_drawdown_config(self) -> Dict[str, Any]:
        """获取回撤控制配置"""
        risk = self.get_risk_automation()
        return risk.get("drawdown_control", {})  # type: ignore

    def get_var_config(self) -> Dict[str, Any]:
        """获取 VaR 监控配置"""
        risk = self.get_risk_automation()
        return risk.get("var_monitoring", {})  # type: ignore

    def get_concentration_limits(self) -> Dict[str, Any]:
        """获取集中度限制"""
        risk = self.get_risk_automation()
        return risk.get("concentration_limits", {})  # type: ignore

    def get_stress_test_scenarios(self) -> Dict[str, Any]:
        """获取压力测试场景"""
        risk = self.get_risk_automation()
        return risk.get("stress_test_scenarios", {})  # type: ignore

    def get_early_warning_signals(self) -> Dict[str, Any]:
        """获取早期预警信号"""
        risk = self.get_risk_automation()
        return risk.get("early_warning_signals", {})  # type: ignore

    def get_total_capital(self) -> float:
        """获取总资金"""
        cfg = self.load()
        return float(cfg.get("meta", {}).get("total_capital", 5_000_000))

    def get_max_leverage(self) -> float:
        """获取最大杠杆"""
        cfg = self.load()
        return float(cfg.get("meta", {}).get("max_leverage", 1.5))

    def get_target_annual_return(self) -> float:
        """获取目标年化收益"""
        cfg = self.load()
        return float(cfg.get("meta", {}).get("target_annual_return", 0.085))

    def get_target_max_drawdown(self) -> float:
        """获取目标最大回撤"""
        cfg = self.load()
        return float(cfg.get("meta", {}).get("target_max_drawdown", 0.15))

    def get_daily_build_limit(self) -> float:
        """获取每日建仓限额"""
        phase = self.get_current_phase()
        return float(phase.get("daily_build_limit", 200_000))

    def summary(self) -> str:
        """生成配置摘要"""
        alloc = self.get_allocation()
        phase = self.get_current_phase()
        total = self.get_total_capital()

        lines = [
            "=" * 60,
            "v10.0 投资计划配置摘要",
            "=" * 60,
            f"总资金: ¥{total:,.0f}",
            f"最大杠杆: {self.get_max_leverage():.1f}x",
            f"目标年化: {self.get_target_annual_return():.1%}",
            f"目标最大回撤: {self.get_target_max_drawdown():.1%}",
            "",
            "资金分配:",
        ]
        for account, amount in alloc.items():
            pct = amount / total * 100 if total > 0 else 0
            lines.append(f"  {account}: ¥{amount:,.0f} ({pct:.1f}%)")

        lines.extend(
            [
                "",
                f"当前阶段: {phase.get('phase_key', 'N/A')} - {phase.get('name', 'N/A')}",
                f"阶段周期: {phase.get('period', 'N/A')}",
                f"目标收益: {phase.get('target_return', 0):.1%}",
                f"最大回撤: {phase.get('max_drawdown', 0):.1%}",
                f"每日建仓限额: ¥{self.get_daily_build_limit():,.0f}",
                "",
                "持仓统计:",
                f"  股票: {len(self.get_stock_positions())} 只",
                f"  ETF: {len(self.get_etf_positions())} 只",
                f"  期货: {len(self.get_futures_config().get('positions', []))} 种",
                "=" * 60,
            ]
        )

        return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="v10.0 投资计划配置加载器")
    parser.add_argument("--summary", action="store_true", help="显示配置摘要")
    parser.add_argument("--phase", action="store_true", help="显示当前阶段")
    parser.add_argument("--schedule", action="store_true", help="显示每日时间表")
    parser.add_argument("--risk", action="store_true", help="显示风控配置")
    args = parser.parse_args()

    loader = V10ConfigLoader()

    if args.summary or not any(vars(args).values()):
        logger.info(loader.summary())

    if args.phase:
        phase = loader.get_current_phase()
        logger.info(json.dumps(phase, ensure_ascii=False, indent=2, default=str))

    if args.schedule:
        schedule = loader.get_daily_schedule()
        logger.info("\n每日时间表:")
        for time_slot, info in schedule.items():
            logger.info(f"  {time_slot}: {info.get('stage', '')} - {info.get('action', '')}")

    if args.risk:
        risk = loader.get_risk_automation()
        logger.info(json.dumps(risk, ensure_ascii=False, indent=2, default=str))
