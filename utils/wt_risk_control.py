"""
WonderTrader风格风控模块

实现多层次风控体系：
- 组合盘资金风控
- 通道流量风控
- 账户资金风控
- 离合器机制（策略风险时断开信号执行）

适用于增强现有系统的风控能力。
"""

import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class RiskControl:
    """多层次风控管理器"""

    def __init__(self, config: Optional[Dict] = None):  # type: ignore
        self.config = config or {
            "max_daily_loss_pct": 0.03,
            "max_portfolio_drawdown_pct": 0.05,
            "max_position_concentration_pct": 0.3,
            "max_single_trade_pct": 0.1,
            "max_daily_trades": 100,
            "max_daily_volume": 1000000000,
            "circuit_breaker_enabled": True,
            "stop_loss_enabled": True,
            "position_limit_enabled": True,
        }

        self.daily_trades = 0
        self.daily_volume = 0
        self.daily_pnl = 0.0
        self.daily_loss = 0.0
        self.max_equity = 0.0
        self.current_equity = 0.0
        self.trading_enabled = True
        self.circuit_breaker_tripped = False
        self.circuit_breaker_reason = ""

    def reset_daily(self):
        """重置每日统计"""
        self.daily_trades = 0
        self.daily_volume = 0
        self.daily_pnl = 0.0
        self.daily_loss = 0.0

    def update_equity(self, equity: float):
        """更新权益并计算回撤"""
        self.current_equity = equity
        if equity > self.max_equity:
            self.max_equity = equity

    def check_circuit_breaker(self) -> Tuple[bool, str]:
        """检查熔断机制"""
        if not self.config["circuit_breaker_enabled"]:
            return True, ""

        if self.circuit_breaker_tripped:
            return False, self.circuit_breaker_reason

        if self.max_equity > 0:
            drawdown = (self.max_equity - self.current_equity) / self.max_equity
            if drawdown >= self.config["max_portfolio_drawdown_pct"]:
                self.circuit_breaker_tripped = True
                self.circuit_breaker_reason = (
                    f"组合回撤 {drawdown:.2%} >= {self.config['max_portfolio_drawdown_pct']:.2%}"
                )
                return False, self.circuit_breaker_reason

        if self.daily_loss >= self.config["max_daily_loss_pct"] * self.max_equity:
            self.circuit_breaker_tripped = True
            self.circuit_breaker_reason = f"当日亏损 ¥{self.daily_loss:,.0f} >= 上限"
            return False, self.circuit_breaker_reason

        return True, ""

    def check_position_concentration(self, code: str, position_value: float, total_equity: float) -> Tuple[bool, str]:
        """检查单标的集中度"""
        if not self.config["position_limit_enabled"]:
            return True, ""

        pct = position_value / total_equity if total_equity > 0 else 0
        if pct >= self.config["max_position_concentration_pct"]:
            return False, f"单标的集中度 {pct:.2%} >= {self.config['max_position_concentration_pct']:.2%}"

        return True, ""

    def check_single_trade(self, trade_amount: float, total_equity: float) -> Tuple[bool, str]:
        """检查单笔交易限额"""
        pct = trade_amount / total_equity if total_equity > 0 else 0
        if pct >= self.config["max_single_trade_pct"]:
            return False, f"单笔交易占比 {pct:.2%} >= {self.config['max_single_trade_pct']:.2%}"

        return True, ""

    def check_daily_trade_count(self) -> Tuple[bool, str]:
        """检查每日交易次数"""
        if self.daily_trades >= self.config["max_daily_trades"]:
            return False, f"当日交易次数 {self.daily_trades} >= 上限 {self.config['max_daily_trades']}"

        return True, ""

    def check_daily_volume(self, volume: float) -> Tuple[bool, str]:
        """检查每日成交量"""
        if self.daily_volume + volume >= self.config["max_daily_volume"]:
            return False, "当日成交量接近上限"

        return True, ""

    def record_trade(self, amount: float, volume: float, pnl: float = 0.0):
        """记录交易"""
        self.daily_trades += 1
        self.daily_volume += volume  # type: ignore
        self.daily_pnl += pnl
        if pnl < 0:
            self.daily_loss += abs(pnl)

    def get_risk_status(self) -> Dict:
        """获取风控状态"""
        drawdown = (self.max_equity - self.current_equity) / self.max_equity if self.max_equity > 0 else 0

        return {
            "trading_enabled": self.trading_enabled,
            "circuit_breaker_tripped": self.circuit_breaker_tripped,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "max_daily_loss_pct": self.config["max_daily_loss_pct"],
            "max_portfolio_drawdown_pct": self.config["max_portfolio_drawdown_pct"],
            "current_drawdown": drawdown,
            "daily_trades": self.daily_trades,
            "daily_trades_limit": self.config["max_daily_trades"],
            "daily_volume": self.daily_volume,
            "daily_volume_limit": self.config["max_daily_volume"],
            "daily_pnl": self.daily_pnl,
            "daily_loss": self.daily_loss,
            "max_equity": self.max_equity,
            "current_equity": self.current_equity,
        }

    def pre_trade_check(
        self, code: str, trade_amount: float, volume: float, position_value: float, total_equity: float
    ) -> Tuple[bool, List[str]]:
        """交易前风控检查

        Args:
            code: 标的代码
            trade_amount: 交易金额
            volume: 成交量
            position_value: 当前持仓价值
            total_equity: 总权益

        Returns:
            (是否通过, 拒绝原因列表)
        """
        reasons = []

        ok, reason = self.check_circuit_breaker()
        if not ok:
            reasons.append(reason)

        ok, reason = self.check_position_concentration(code, position_value + trade_amount, total_equity)
        if not ok:
            reasons.append(reason)

        ok, reason = self.check_single_trade(trade_amount, total_equity)
        if not ok:
            reasons.append(reason)

        ok, reason = self.check_daily_trade_count()
        if not ok:
            reasons.append(reason)

        ok, reason = self.check_daily_volume(volume)
        if not ok:
            reasons.append(reason)

        return len(reasons) == 0, reasons


class StopLossManager:
    """止损管理器"""

    def __init__(self, stop_loss_pct: float = 0.05, take_profit_pct: float = 0.10):
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.stop_loss_orders = {}  # type: ignore

    def set_stop_loss(self, code: str, avg_cost: float, qty: int):
        """设置止损单"""
        stop_price = avg_cost * (1 - self.stop_loss_pct)
        take_profit_price = avg_cost * (1 + self.take_profit_pct)

        self.stop_loss_orders[code] = {
            "avg_cost": avg_cost,
            "qty": qty,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "status": "active",
            "created_at": datetime.now().isoformat(),
        }

    def check_stop_loss(self, code: str, current_price: float) -> Tuple[str, Optional[Dict]]:
        """检查止损条件

        Returns:
            (action: 'stop_loss'/'take_profit'/'none', order_info)
        """
        if code not in self.stop_loss_orders:
            return "none", None

        order = self.stop_loss_orders[code]
        if order["status"] != "active":
            return "none", None

        if current_price <= order["stop_price"]:
            order["status"] = "triggered_stop_loss"
            order["trigger_price"] = current_price
            order["triggered_at"] = datetime.now().isoformat()
            return "stop_loss", order

        if current_price >= order["take_profit_price"]:
            order["status"] = "triggered_take_profit"
            order["trigger_price"] = current_price
            order["triggered_at"] = datetime.now().isoformat()
            return "take_profit", order

        return "none", None

    def update_stop_loss(self, code: str, new_avg_cost: float):
        """更新止损价格（加仓后）"""
        if code in self.stop_loss_orders:
            order = self.stop_loss_orders[code]
            order["avg_cost"] = new_avg_cost
            order["stop_price"] = new_avg_cost * (1 - self.stop_loss_pct)
            order["take_profit_price"] = new_avg_cost * (1 + self.take_profit_pct)

    def remove_stop_loss(self, code: str):
        """移除止损单"""
        if code in self.stop_loss_orders:
            del self.stop_loss_orders[code]

    def get_stop_loss_status(self) -> Dict:
        """获取所有止损单状态"""
        return self.stop_loss_orders


class PortfolioRiskAnalyzer:
    """组合风险分析器"""

    def __init__(self):
        pass

    @staticmethod
    def _normalize_positions(positions: Dict) -> Dict:
        """标准化持仓字段，兼容 qty / shares"""
        normalized = {}
        for code, pos in positions.items():
            normalized[code] = {
                "qty": pos.get("qty", pos.get("shares", 0)),
                "avg_cost": pos.get("avg_cost", 0.0),
            }
        return normalized

    @staticmethod
    def calculate_var(positions: Dict, volatility: float = 0.02, confidence_level: float = 0.95) -> float:
        """计算在险价值(VaR)

        Args:
            positions: 持仓字典 {code: {'qty': int, 'avg_cost': float}}
            volatility: 波动率（默认2%）
            confidence_level: 置信水平（默认95%）

        Returns:
            VaR值
        """
        normalized = PortfolioRiskAnalyzer._normalize_positions(positions)
        total_value = sum(pos["qty"] * pos["avg_cost"] for pos in normalized.values())
        z_score = 1.645 if confidence_level == 0.95 else 2.33 if confidence_level == 0.99 else 1.28
        return total_value * volatility * z_score  # type: ignore

    @staticmethod
    def calculate_cvar(positions: Dict, volatility: float = 0.02, confidence_level: float = 0.95) -> float:
        """计算条件在险价值(CVaR)"""
        normalized = PortfolioRiskAnalyzer._normalize_positions(positions)
        total_value = sum(pos["qty"] * pos["avg_cost"] for pos in normalized.values())
        z_score = 1.645 if confidence_level == 0.95 else 2.33 if confidence_level == 0.99 else 1.28
        cvar_factor = volatility * (
            z_score * math.exp(-(z_score**2) / 2) / (math.sqrt(2 * math.pi) * (1 - confidence_level))
        )
        return total_value * cvar_factor  # type: ignore

    @staticmethod
    def calculate_position_concentration(positions: Dict) -> Dict:
        """计算持仓集中度"""
        normalized = PortfolioRiskAnalyzer._normalize_positions(positions)
        total_value = sum(pos["qty"] * pos["avg_cost"] for pos in normalized.values())
        if total_value == 0:
            return {}

        concentration = {}
        for code, pos in normalized.items():
            pos_value = pos["qty"] * pos["avg_cost"]
            concentration[code] = {
                "value": pos_value,
                "percentage": pos_value / total_value,
                "qty": pos["qty"],
                "avg_cost": pos["avg_cost"],
            }

        return dict(sorted(concentration.items(), key=lambda x: -x[1]["percentage"]))

    @staticmethod
    def analyze_sector_distribution(positions: Dict, sector_map: Dict) -> Dict:
        """分析行业分布"""
        normalized = PortfolioRiskAnalyzer._normalize_positions(positions)
        sectors = {}
        total_value = 0

        for code, pos in normalized.items():
            code_num = code.split(".")[0] if "." in code else code
            sector = sector_map.get(code_num, "未知")

            if sector not in sectors:
                sectors[sector] = {"value": 0, "count": 0}

            pos_value = pos["qty"] * pos["avg_cost"]
            sectors[sector]["value"] += pos_value
            sectors[sector]["count"] += 1
            total_value += pos_value

        for sector in sectors:
            if total_value > 0:
                sectors[sector]["percentage"] = sectors[sector]["value"] / total_value  # type: ignore

        return dict(sorted(sectors.items(), key=lambda x: -x[1]["value"]))

    def analyze_portfolio(self, positions: Dict, total_built: float, target: float) -> Dict:
        """组合级风险分析

        参数:
            positions: 持仓字典
            total_built: 已建仓金额
            target: 目标建仓金额

        返回:
            风险分析结果字典
        """
        # 持仓集中度
        concentration = self.calculate_position_concentration(positions)

        # VaR/CVaR
        var_95 = self.calculate_var(positions, confidence_level=0.95)
        var_99 = self.calculate_var(positions, confidence_level=0.99)
        cvar_95 = self.calculate_cvar(positions, confidence_level=0.95)

        # 建仓进度
        build_progress = total_built / target if target > 0 else 0

        # 集中度风险评分 (0-100)
        concentration_risk = 0.0
        if concentration:
            top_pct = next(iter(concentration.values())).get("percentage", 0)
            concentration_risk = min(top_pct * 200, 100)

        # 综合风险评分 (0-100)
        risk_score = min(
            concentration_risk * 0.4 + (var_95 / max(target, 1)) * 10000 * 0.3 + (1 - build_progress) * 30 * 0.3,
            100,
        )

        return {
            "risk_score": round(risk_score, 2),
            "concentration_risk": round(concentration_risk, 2),
            "var_95": round(var_95, 2),
            "var_99": round(var_99, 2),
            "cvar_95": round(cvar_95, 2),
            "build_progress": round(build_progress, 4),
            "total_positions": len(positions),
            "sector_distribution": {},
        }


class RiskReportGenerator:
    """风险报告生成器"""

    @staticmethod
    def generate_risk_report(
        risk_control: RiskControl,
        stop_loss_manager: StopLossManager,
        positions: Dict,
        sector_map: Optional[Dict] = None,
    ) -> str:  # type: ignore
        """生成风险报告"""
        risk_status = risk_control.get_risk_status()
        analyzer = PortfolioRiskAnalyzer()
        concentration = analyzer.calculate_position_concentration(positions)
        sectors = analyzer.analyze_sector_distribution(positions, sector_map or {})

        lines = [
            "# 风险监控报告",
            "",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
            "## 交易状态",
            "",
            f"- **交易开关**: {'✅ 开启' if risk_status['trading_enabled'] else '❌ 关闭'}",
            f"- **熔断状态**: {'❌ 已触发' if risk_status['circuit_breaker_tripped'] else '✅ 正常'}",
            f"- **熔断原因**: {risk_status['circuit_breaker_reason'] or '无'}",
            "",
            "## 回撤监控",
            "",
            f"- **最大权益**: ¥{risk_status['max_equity']:,.0f}",
            f"- **当前权益**: ¥{risk_status['current_equity']:,.0f}",
            f"- **当前回撤**: {risk_status['current_drawdown']:.2%}",
            f"- **回撤上限**: {risk_status['max_portfolio_drawdown_pct']:.2%}",
            "",
            "## 当日统计",
            "",
            f"- **交易次数**: {risk_status['daily_trades']} / {risk_status['daily_trades_limit']}",
            f"- **成交量**: ¥{risk_status['daily_volume']:,.0f} / ¥{risk_status['daily_volume_limit']:,.0f}",
            f"- **当日盈亏**: {'+' if risk_status['daily_pnl'] >= 0 else ''}¥{risk_status['daily_pnl']:,.2f}",
            f"- **当日亏损**: ¥{risk_status['daily_loss']:,.2f}",
            "",
            "## 持仓集中度",
            "",
        ]

        if concentration:
            lines.append("| 标的 | 持仓金额 | 占比 | 数量 | 成本 |")
            lines.append("|------|---------|------|------|------|")
            for code, info in concentration.items():
                lines.append(
                    f"| {code} | ¥{info['value']:,.0f} | {info['percentage']:.2%} | {info['qty']:,} | {info['avg_cost']:.4f} |"
                )
        else:
            lines.append("- 无持仓")

        lines.extend(
            [
                "",
                "## 行业分布",
                "",
            ]
        )

        if sectors:
            lines.append("| 行业 | 持仓金额 | 占比 | 标的数 |")
            lines.append("|------|---------|------|--------|")
            for sector, info in sectors.items():
                lines.append(f"| {sector} | ¥{info['value']:,.0f} | {info['percentage']:.2%} | {info['count']} |")
        else:
            lines.append("- 无数据")

        stop_loss_orders = stop_loss_manager.get_stop_loss_status()
        lines.extend(
            [
                "",
                "## 止损止盈状态",
                "",
            ]
        )

        if stop_loss_orders:
            lines.append("| 标的 | 成本 | 止损价 | 止盈价 | 状态 |")
            lines.append("|------|------|--------|--------|------|")
            for code, order in stop_loss_orders.items():
                status_map = {
                    "active": "✅ 生效",
                    "triggered_stop_loss": "❌ 止损触发",
                    "triggered_take_profit": "💰 止盈触发",
                }
                lines.append(
                    f"| {code} | {order['avg_cost']:.4f} | {order['stop_price']:.4f} | {order['take_profit_price']:.4f} | {status_map.get(order['status'], order['status'])} |"
                )
        else:
            lines.append("- 无止损单")

        return "\n".join(lines)


def create_risk_control(config: Optional[Dict] = None) -> RiskControl:  # type: ignore
    """创建风控管理器"""
    return RiskControl(config)


def create_stop_loss_manager(stop_loss_pct: float = 0.05, take_profit_pct: float = 0.10) -> StopLossManager:
    """创建止损管理器"""
    return StopLossManager(stop_loss_pct, take_profit_pct)


if __name__ == "__main__":
    risk_control = RiskControl()
    stop_loss_manager = StopLossManager()

    risk_control.update_equity(1000000)
    risk_control.max_equity = 1000000

    positions = {
        "588080.SH": {"qty": 10000, "avg_cost": 2.26},
        "512880.SH": {"qty": 20000, "avg_cost": 1.13},
        "510050.SH": {"qty": 5000, "avg_cost": 3.09},
    }

    for code, pos in positions.items():
        stop_loss_manager.set_stop_loss(code, pos["avg_cost"], pos["qty"])  # type: ignore

    report = RiskReportGenerator.generate_risk_report(risk_control, stop_loss_manager, positions)
    print(report)
