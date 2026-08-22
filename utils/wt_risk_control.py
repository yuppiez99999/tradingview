"""
WonderTrader风格风控模块

实现多层次风控体系：
- 组合盘资金风控
- 通道流量风控
- 账户资金风控
- 离合器机制（策略风险时断开信号执行）

适用于增强现有系统的风控能力。
"""

import json
import logging
import math
import os
from datetime import datetime
from typing import Any, Optional, cast

logger = logging.getLogger(__name__)

# scipy.stats 前向声明 (模块级) — 根除 student_t 局部 try import ignore
scipy_stats: Optional[type]
try:
    from scipy import stats as _scipy_stats
    scipy_stats = _scipy_stats
except ImportError:
    scipy_stats = None

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# G11 蒙特卡洛 CVaR 安全默认 (system_config.json risk_management.cvar 缺失时)
_CVAR_CONFIG_DEFAULT = {
    "method": "monte_carlo",
    "distribution": "student_t",
    "dof": 5,
    "confidence_level": 0.95,
    "n_paths": 50000,
    "horizon_days": 1,
    "seed": 42,
}


def _load_cvar_config() -> dict:
    """读取 system_config.json 的 risk_management.cvar 段 (G11).

    失败 (文件缺失/JSON损坏/段不存在) 一律返回安全默认, fail-open 不阻断主流程.
    """
    try:
        cfg_path = os.path.join(_PROJECT_ROOT, "config", "system_config.json")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cvar_cfg = cfg.get("risk_management", {}).get("cvar", {})
        merged = dict(_CVAR_CONFIG_DEFAULT)
        merged.update(cvar_cfg)
        return merged
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
        logger.warning("[CVaR] 读取 system_config.json risk_management.cvar 失败, 使用默认配置: %s", exc)
        return dict(_CVAR_CONFIG_DEFAULT)


class RiskControl:
    """多层次风控管理器"""

    # 类级可选属性显式注解 — 根除 __init__ 中 =0 / =0.0 单态窄化推断
    config: dict[str, Any]
    daily_trades: int
    daily_volume: float
    daily_pnl: float
    daily_loss: float
    max_equity: float
    current_equity: float
    trading_enabled: bool
    circuit_breaker_tripped: bool
    circuit_breaker_reason: str

    def __init__(self, config: Optional[dict] = None):
        self.config = cast(dict[str, Any], config) if config is not None else {
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
        self.daily_volume = 0.0
        self.daily_pnl = 0.0
        self.daily_loss = 0.0
        self.max_equity = 0.0
        self.current_equity = 0.0
        self.trading_enabled = True
        self.circuit_breaker_tripped = False
        self.circuit_breaker_reason = ""

    def reset_daily(self) -> None:
        """重置每日统计"""
        self.daily_trades = 0
        self.daily_volume = 0.0
        self.daily_pnl = 0.0
        self.daily_loss = 0.0

    def update_equity(self, equity: float) -> None:
        """更新权益并计算回撤"""
        self.current_equity = equity
        if equity > self.max_equity:
            self.max_equity = equity

    def check_circuit_breaker(self) -> tuple[bool, str]:
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

    def check_position_concentration(self, code: str, position_value: float, total_equity: float) -> tuple[bool, str]:
        """检查单标的集中度"""
        if not self.config["position_limit_enabled"]:
            return True, ""

        pct = position_value / total_equity if total_equity > 0 else 0
        if pct >= self.config["max_position_concentration_pct"]:
            return False, f"单标的集中度 {pct:.2%} >= {self.config['max_position_concentration_pct']:.2%}"

        return True, ""

    def check_single_trade(self, trade_amount: float, total_equity: float) -> tuple[bool, str]:
        """检查单笔交易限额"""
        pct = trade_amount / total_equity if total_equity > 0 else 0
        if pct >= self.config["max_single_trade_pct"]:
            return False, f"单笔交易占比 {pct:.2%} >= {self.config['max_single_trade_pct']:.2%}"

        return True, ""

    def check_daily_trade_count(self) -> tuple[bool, str]:
        """检查每日交易次数"""
        if self.daily_trades >= self.config["max_daily_trades"]:
            return False, f"当日交易次数 {self.daily_trades} >= 上限 {self.config['max_daily_trades']}"

        return True, ""

    def check_daily_volume(self, volume: float) -> tuple[bool, str]:
        """检查每日成交量"""
        if self.daily_volume + volume >= self.config["max_daily_volume"]:
            return False, "当日成交量接近上限"

        return True, ""

    def record_trade(self, amount: float, volume: float, pnl: float = 0.0) -> None:
        """记录交易"""
        self.daily_trades += 1
        self.daily_volume += volume
        self.daily_pnl += pnl
        if pnl < 0:
            self.daily_loss += abs(pnl)

    def get_risk_status(self) -> dict:
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
    ) -> tuple[bool, list[str]]:
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
        self.stop_loss_orders: dict = {}

    def set_stop_loss(self, code: str, avg_cost: float, qty: int) -> None:
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

    def check_stop_loss(self, code: str, current_price: float) -> tuple[str, Optional[dict]]:
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

    def update_stop_loss(self, code: str, new_avg_cost: float) -> None:
        """更新止损价格（加仓后）"""
        if code in self.stop_loss_orders:
            order = self.stop_loss_orders[code]
            order["avg_cost"] = new_avg_cost
            order["stop_price"] = new_avg_cost * (1 - self.stop_loss_pct)
            order["take_profit_price"] = new_avg_cost * (1 + self.take_profit_pct)

    def remove_stop_loss(self, code: str) -> None:
        """移除止损单"""
        if code in self.stop_loss_orders:
            del self.stop_loss_orders[code]

    def get_stop_loss_status(self) -> dict:
        """获取所有止损单状态"""
        return self.stop_loss_orders


class PortfolioRiskAnalyzer:
    """组合风险分析器"""

    def __init__(self):
        pass

    @staticmethod
    def _normalize_positions(positions: dict) -> dict:
        """标准化持仓字段，兼容 qty / shares"""
        normalized = {}
        for code, pos in positions.items():
            normalized[code] = {
                "qty": pos.get("qty", pos.get("shares", 0)),
                "avg_cost": pos.get("avg_cost", 0.0),
            }
        return normalized

    @staticmethod
    def calculate_var(positions: dict, volatility: float = 0.02, confidence_level: float = 0.95) -> float:
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
        return float(total_value * volatility * z_score)
    @staticmethod
    def calculate_cvar(
        positions: dict,
        volatility: float = 0.02,
        confidence_level: float = 0.95,
        method: str = "analytic",
        n_paths: int = 50000,
        horizon_days: int = 1,
        seed: int = 42,
        dist: str = "normal",
        dof: int = 5,
    ) -> float:
        """计算条件在险价值(CVaR)

        Args:
            method: "analytic" 解析正态近似 (默认, 轻量); "monte_carlo" 蒙特卡洛模拟
                    组合日收益路径 (支持肥尾, 工业级 G11 增强).
            n_paths: 蒙特卡洛路径数 (仅 method="monte_carlo").
            horizon_days: 持有期(交易日), 组合净值路径模拟长度.
            seed: 随机种子, 保证可复现 (使用 np.random.default_rng, 无全局种子污染).
            dist: 蒙特卡洛分布 "normal" | "student_t" (仅 method="monte_carlo";
                  student_t 需做方差缩放校正保持目标 volatility, 交付肥尾风险).
            dof: Student-t 自由度 (仅 dist="student_t").
        """
        normalized = PortfolioRiskAnalyzer._normalize_positions(positions)
        total_value = sum(pos["qty"] * pos["avg_cost"] for pos in normalized.values())
        if method == "monte_carlo":
            return PortfolioRiskAnalyzer._cvar_monte_carlo(
                total_value, volatility, confidence_level, n_paths, horizon_days, seed,
                dist=dist, dof=dof,
            )
        # 默认解析正态 CVaR (对正态假设精确)
        z_score = 1.645 if confidence_level == 0.95 else 2.33 if confidence_level == 0.99 else 1.28
        cvar_factor = volatility * (
            z_score * math.exp(-(z_score**2) / 2) / (math.sqrt(2 * math.pi) * (1 - confidence_level))
        )
        return float(total_value * cvar_factor)

    @staticmethod
    def _cvar_monte_carlo(
        total_value: float,
        volatility: float,
        confidence_level: float,
        n_paths: int,
        horizon_days: int,
        seed: int,
        dist: str = "normal",
        dof: int = 5,
    ) -> float:
        """蒙特卡洛模拟组合收益路径, 取左尾条件均值 (G11 增强).

        分布说明:
        - "normal": 标准正态, 与解析正态 CVaR 在大样本下收敛.
        - "student_t": 肥尾分布, 对极端损失更敏感. 关键校正: Student-t(dof) 的
          方差为 dof/(dof-2), 直接用会放大目标波动率, 故采样后对样本除以
          sqrt(dof/(dof-2)) 做方差缩放, 保证 period_vol 与设定 volatility 一致.
        """
        try:
            import numpy as np
        except ImportError:
            # numpy 不可用时回退解析 (不阻断主流程)
            return PortfolioRiskAnalyzer._cvar_analytic(
                total_value, volatility, confidence_level
            )
        if dist == "student_t":
            if scipy_stats is None:
                # scipy 缺失 → fail-open 降级正态
                logger.warning("[CVaR] scipy 未安装, student_t 采样降级正态")
                rng = np.random.default_rng(seed)
                period_vol = volatility * math.sqrt(horizon_days)
                returns = rng.normal(0.0, period_vol, size=n_paths)
            else:
                try:
                    rng = np.random.default_rng(seed)
                    period_vol = volatility * math.sqrt(horizon_days)
                    # 方差缩放校正: 保持目标 period_vol 一致
                    scale = math.sqrt(dof / (dof - 2)) if dof > 2 else 1.0
                    samples = scipy_stats.t.rvs(dof, size=n_paths, random_state=rng) / scale
                    returns = samples * period_vol
                except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
                    # scipy 采样异常 → fail-open 降级正态
                    logger.warning("[CVaR] Student-t 采样失败, 降级正态: %s", exc)
                    rng = np.random.default_rng(seed)
                    period_vol = volatility * math.sqrt(horizon_days)
                    returns = rng.normal(0.0, period_vol, size=n_paths)
        else:
            rng = np.random.default_rng(seed)
            # 日波动率缩放至持有期
            period_vol = volatility * math.sqrt(horizon_days)
            returns = rng.normal(0.0, period_vol, size=n_paths)
        # 左尾分位数阈值
        alpha = 1.0 - confidence_level
        threshold = np.quantile(returns, alpha)
        tail = returns[returns <= threshold]
        if tail.size == 0:
            return total_value * abs(threshold)
        cvar_return = float(-tail.mean())  # 条件在险收益(正值)
        return total_value * cvar_return

    @staticmethod
    def _cvar_analytic(total_value: float, volatility: float, confidence_level: float) -> float:
        """解析正态 CVaR (numpy 不可用时回退)."""
        z_score = 1.645 if confidence_level == 0.95 else 2.33 if confidence_level == 0.99 else 1.28
        cvar_factor = volatility * (
            z_score * math.exp(-(z_score**2) / 2) / (math.sqrt(2 * math.pi) * (1 - confidence_level))
        )
        return float(total_value * cvar_factor)
    @staticmethod
    def calculate_position_concentration(positions: dict) -> dict:
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
    def analyze_sector_distribution(positions: dict, sector_map: dict) -> dict:
        """分析行业分布"""
        normalized = PortfolioRiskAnalyzer._normalize_positions(positions)
        sectors: dict[str, dict[str, Any]] = {}
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
                sectors[sector]["percentage"] = sectors[sector]["value"] / total_value
        return dict(sorted(sectors.items(), key=lambda x: -x[1]["value"]))

    def analyze_portfolio(self, positions: dict, total_built: float, target: float) -> dict:
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
        # G11: 读取 system_config.json risk_management.cvar, 默认启用蒙特卡洛(肥尾) CVaR
        # 观测路径 fail-open: 配置读取/计算异常均降级解析正态, 不静默
        try:
            cvar_cfg = _load_cvar_config()
            cvar_method = cvar_cfg.get("method", "monte_carlo")
            cvar_dist = cvar_cfg.get("distribution", "student_t")
            cvar_dof = int(cvar_cfg.get("dof", 5))
            cvar_conf = float(cvar_cfg.get("confidence_level", 0.95))
            cvar_paths = int(cvar_cfg.get("n_paths", 50000))
            cvar_horizon = int(cvar_cfg.get("horizon_days", 1))
            cvar_seed = int(cvar_cfg.get("seed", 42))
            cvar_95 = self.calculate_cvar(
                positions,
                confidence_level=cvar_conf,
                method=cvar_method,
                n_paths=cvar_paths,
                horizon_days=cvar_horizon,
                seed=cvar_seed,
                dist=cvar_dist,
                dof=cvar_dof,
            )
            cvar_method_used = cvar_method if cvar_method != "monte_carlo" else f"monte_carlo_{cvar_dist}"
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
            # 观测路径 fail-open: 计算异常则降级解析正态, 标注来源, 不静默
            logger.warning("[CVaR] analyze_portfolio 蒙特卡洛失败, 降级 analytic: %s", exc)
            cvar_95 = self.calculate_cvar(positions, confidence_level=0.95, method="analytic")
            cvar_method_used = "analytic_fallback"

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
            "cvar_method": cvar_method_used,
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
        positions: dict,
        sector_map: Optional[dict] = None,
    ) -> str:
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


def create_risk_control(config: Optional[dict] = None) -> RiskControl:
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
        stop_loss_manager.set_stop_loss(code, pos["avg_cost"], pos["qty"])
    report = RiskReportGenerator.generate_risk_report(risk_control, stop_loss_manager, positions)
    logger.info(report)
