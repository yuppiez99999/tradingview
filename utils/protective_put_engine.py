"""
认沽期权保护引擎 (Protective Put Engine)
==========================================
修改原因: P3 期权保护自动启动 - 认沽保护从未启动问题
修改日期: 2026-07-21

核心问题:
    theta_engine.py 生成备兑看涨计划正常工作,
    但认沽保护（protective put）从未启动。
    本模块自动选择 OTM 5% 认沽期权, 当组合市值>100万且无Put持仓时自动触发买入。

功能:
    1. 监控组合市值, >100万时启动保护
    2. 自动选择合适的认沽期权合约 (OTM 5%, DTE 30-60)
    3. 年化期权成本控制 < 总资本的2.5%（约12.5万/年）
    4. 到期前5天自动滚仓到下月
    5. 回撤加码: Level 2+ 时增加保护手数

保护策略:
    - 510050 (上证50ETF): 核心保护, 60张认沽 (主力Beta对冲)
    - 588080 (科创50ETF): 科技保护, 25张认沽
    - 159915 (创业板ETF): 成长保护, 25张认沽
    - 510300 (沪深300ETF): 系统性保护, 25张认沽 (替代原IF期货空头功能)

年化成本预算:
    对冲资本200万 × 预算使用率82.5% = 权利金预算165万
    滚仓/保证金缓冲 = 35万
    月度预算 ≈ 动态按市场波动率调整

用法:
    from utils.protective_put_engine import ProtectivePutEngine
    ppe = ProtectivePutEngine()
    orders = ppe.generate_put_orders()
    ppe.check_and_roll()
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

logger = logging.getLogger("protective_put_engine")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
REPORTS_DIR = BASE_DIR / "v8.3_institutional" / "reports"
TRADE_PLANS_DIR = BASE_DIR / "v8.3_institutional" / "trade_plans"
PUT_STATE_FILE = BASE_DIR / "cache" / "protective_put_state.json"


class ProtectionTarget(TypedDict):
    """认沽保护目标结构 — 根除 PROTECTION_TARGETS [index] 索引"""
    code: str
    name: str
    exchange: str
    contracts: int
    priority: int
    reason: str
    budget_pct: float


class ProtectivePutEngine:
    """认沽期权保护引擎 - 尾部风险自动保护

    设计原则 (参考 Universa/Taleb 尾部对冲):
        1. 始终保持尾部保护 (组合市值>100万时)
        2. OTM 5% 认沽: 平衡成本与保护效果
        3. DTE 30-60天: 避免短期Theta衰减过快
        4. 年化成本 < 2.5%: 保护成本不能吃掉收益
        5. 自动滚仓: 到期前5天换到下月合约
    """

    # 保护参数
    MIN_PORTFOLIO_VALUE = 1_000_000  # 最低保护启动市值 100万
    OTM_PCT = 0.05  # 虚值程度 5%
    TARGET_DTE_MIN = 25  # 最短到期天数
    TARGET_DTE_MAX = 60  # 最长到期天数
    PREFERRED_DTE = 45  # 首选到期天数
    ROLL_DTE_THRESHOLD = 5  # 到期前5天滚仓
    MAX_ANNUAL_COST_PCT = 0.025  # 最大年化成本 2.5%
    TOTAL_CAPITAL = 5_000_000  # 总资本

    # 保护目标 ETF 配置 (v8.4 OPTIONS_ONLY: 200万纯期权对冲, 无期货)
    PROTECTION_TARGETS: list[ProtectionTarget] = [
        {
            "code": "510050",
            "name": "上证50ETF",
            "exchange": "SSE",
            "contracts": 60,
            "priority": 1,
            "reason": "核心蓝筹保护+主力Beta对冲(替代原IF期货), 60张覆盖约50%组合Beta暴露",
            "budget_pct": 0.45,
        },
        {
            "code": "588080",
            "name": "科创50ETF",
            "exchange": "SSE",
            "contracts": 25,
            "priority": 2,
            "reason": "科技成长保护, 高Beta高波动需加大覆盖",
            "budget_pct": 0.20,
        },
        {
            "code": "159915",
            "name": "创业板ETF",
            "exchange": "SZSE",
            "contracts": 25,
            "priority": 3,
            "reason": "创新成长保护, 成长风格波动大",
            "budget_pct": 0.18,
        },
        {
            "code": "510300",
            "name": "沪深300ETF",
            "exchange": "SSE",
            "contracts": 25,
            "priority": 4,
            "reason": "系统性风险保护+Beta对冲补充(部分替代IF期货功能), 显著增量覆盖",
            "budget_pct": 0.17,
        },
    ]

    def __init__(self, total_capital: float | None = None):
        if total_capital is not None:
            self.TOTAL_CAPITAL = float(total_capital)
        self._load_state()

    def _load_state(self):
        """加载认沽保护状态 (已持仓/到期日等)"""
        self.state = {}
        PUT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if PUT_STATE_FILE.exists():
            try:
                with open(PUT_STATE_FILE, encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
                self.state = {}

    def _save_state(self):
        """保存状态"""
        try:
            with open(PUT_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
            logger.error(f"保存Put状态失败: {e}")

    def _get_portfolio_value(self) -> float:
        """获取当前组合市值"""
        try:
            with open(CONFIG_DIR / "positions.json", encoding="utf-8") as f:
                positions = json.load(f)
        except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
            return 0

        total = 0.0
        for _code, pos in positions.get("positions", {}).items():
            shares = pos.get("actual_shares", pos.get("shares", 0))
            price = pos.get("est_price", 0)
            if shares > 0 and price > 0:
                total += shares * price
        return total

    def _get_etf_spot_price(self, code: str) -> float:
        """获取ETF现价"""
        try:
            with open(CONFIG_DIR / "positions.json", encoding="utf-8") as f:
                positions = json.load(f)
        except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
            return 0

        # 尝试匹配 code.SH 或 code.SZ
        for key, pos in positions.get("positions", {}).items():
            if key.startswith(code) or pos.get("code", "").startswith(code):
                price_val = cast(dict[str, Any], pos).get("est_price", 0)
                return float(price_val) if isinstance(price_val, (int, float)) else 0.0
        return 0

    def _estimate_put_premium(self, spot: float, strike: float, dte: int, iv: float = 0.25) -> float:
        """估算认沽期权权利金 (Black-Scholes近似)

        对于OTM 5%的认沽期权, 使用简化公式:
        P ≈ spot * N(-d2) * exp(-r*T) - strike * N(-d1)
        简化为: P ≈ spot * iv * sqrt(T) * N(-d1) 的近似
        """
        if spot <= 0 or strike <= 0 or dte <= 0:
            return 0

        T = dte / 365.0
        r = 0.02  # 无风险利率
        sigma = iv

        # Black-Scholes d1, d2
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        # 标准正态CDF
        from scipy.stats import norm

        put_price = strike * math.exp(-r * T) * norm.cdf(-d2) - spot * norm.cdf(-d1)

        return float(max(put_price, 0.0001))  # 最低价

    def should_buy_protection(self) -> tuple[bool, str]:
        """判断是否需要买入认沽保护

        触发条件:
            1. 组合市值 > 100万
            2. 无任何有效Put持仓 (或已到期)
            3. 年化预算未超限
        """
        portfolio_value = self._get_portfolio_value()

        if portfolio_value < self.MIN_PORTFOLIO_VALUE:
            return False, f"组合市值 CNY{portfolio_value:,.0f} < 启动阈值 CNY{self.MIN_PORTFOLIO_VALUE:,.0f}"

        # 检查现有Put持仓
        existing_puts = self.state.get("active_puts", [])
        has_valid_put = False
        for put in existing_puts:
            expiry_str = put.get("expiry_date", "")
            if expiry_str:
                try:
                    expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
                    if expiry > datetime.now() + timedelta(days=self.ROLL_DTE_THRESHOLD):
                        has_valid_put = True
                        break
                except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
                    continue

        if has_valid_put:
            return False, "已有有效认沽保护持仓"

        # 年度预算检查
        annual_budget = self.TOTAL_CAPITAL * self.MAX_ANNUAL_COST_PCT
        ytd_spent = self.state.get("ytd_premium_spent", 0)
        if ytd_spent >= annual_budget:
            return False, f"年度期权预算已用完 (CNY{ytd_spent:,.0f} / CNY{annual_budget:,.0f})"

        return True, f"组合市值 CNY{portfolio_value:,.0f}, 无有效Put保护, 应立即建仓"

    def generate_put_orders(self, drawdown_level: int = 0) -> dict[str, Any]:
        """生成认沽期权买入订单

        Args:
            drawdown_level: 回撤级别 (0-4), Level 2+ 增加手数

        Returns:
            {
                "should_execute": bool,
                "reason": str,
                "orders": [...],
                "total_premium_est": float,
                "annual_budget_remaining": float,
            }
        """
        should_buy, reason = self.should_buy_protection()

        result = {
            "generated_at": datetime.now().isoformat(),
            "should_execute": should_buy,
            "reason": reason,
            "drawdown_level": drawdown_level,
            "orders": [],
            "total_premium_est": 0,
            "annual_budget_remaining": 0,
        }

        if not should_buy:
            return result

        # 年度预算
        annual_budget = self.TOTAL_CAPITAL * self.MAX_ANNUAL_COST_PCT
        ytd_spent = self.state.get("ytd_premium_spent", 0)
        remaining_budget = annual_budget - ytd_spent

        # 回撤加码
        contract_multiplier = 1.0
        if drawdown_level >= 3:
            contract_multiplier = 2.0
        elif drawdown_level >= 2:
            contract_multiplier = 1.5
        elif drawdown_level >= 1:
            contract_multiplier = 1.2

        orders = []
        total_premium = 0.0

        for target in self.PROTECTION_TARGETS:
            code = target["code"]
            spot = self._get_etf_spot_price(code)
            if spot <= 0:
                logger.warning(f"无法获取 {code} 现价, 跳过")
                continue

            # OTM 5% 行权价
            strike = round(spot * (1 - self.OTM_PCT), 4)

            # 合约数 (回撤加码)
            contracts = int(target["contracts"] * contract_multiplier)

            # 到期日选择: 下月第4个周三 (中国ETF期权到期日)
            expiry_date = self._calc_next_expiry()
            dte = (expiry_date - datetime.now()).days

            if dte < self.TARGET_DTE_MIN:
                # 太近了, 选下下月
                expiry_date = self._calc_next_expiry(months_ahead=2)
                dte = (expiry_date - datetime.now()).days

            # 估算权利金
            iv = 0.30 if target["code"] in ("588080", "159915") else 0.22
            premium_per_unit = self._estimate_put_premium(spot, strike, dte, iv)
            # ETF期权合约乘数 = 10000
            premium_total = premium_per_unit * contracts * 10000

            # 预算检查
            budget_alloc = remaining_budget * target["budget_pct"]
            if premium_total > budget_alloc * 1.5:
                # 超出预算, 减少手数
                contracts = max(1, int(contracts * budget_alloc / premium_total))
                premium_total = premium_per_unit * contracts * 10000

            orders.append(
                {
                    "order_id": f"PUT_{code}_{datetime.now():%Y%m%d}",
                    "type": "BUY_PUT",
                    "underlying": code,
                    "underlying_name": target["name"],
                    "exchange": target["exchange"],
                    "spot_price": spot,
                    "strike": strike,
                    "otm_pct": self.OTM_PCT,
                    "contracts": contracts,
                    "dte": dte,
                    "expiry_date": expiry_date.strftime("%Y-%m-%d"),
                    "iv_estimate": iv,
                    "premium_per_unit": round(premium_per_unit, 4),
                    "premium_total": round(premium_total, 2),
                    "multiplier": 10000,
                    "execution_window": "09:30-10:00",
                    "order_type": "LIMIT",
                    "price_buffer": 0.05,  # 权利金上浮5%确保成交
                    "priority": target["priority"],
                    "reason": target["reason"],
                    "status": "PENDING",
                }
            )
            total_premium += premium_total

        result["orders"] = orders
        result["total_premium_est"] = round(total_premium, 2)
        result["annual_budget_remaining"] = round(remaining_budget - total_premium, 2)
        result["annual_cost_pct"] = round((ytd_spent + total_premium) * 4 / self.TOTAL_CAPITAL, 4)

        return result

    def check_and_roll(self) -> dict[str, Any]:
        """检查现有Put是否需要滚仓

        Returns:
            {
                "needs_roll": bool,
                "expiring_puts": [...],
                "new_orders": [...],
            }
        """
        active_puts = self.state.get("active_puts", [])
        expiring = []

        for put in active_puts:
            expiry_str = put.get("expiry_date", "")
            if not expiry_str:
                continue
            try:
                expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
                days_left = (expiry - datetime.now()).days
                if days_left <= self.ROLL_DTE_THRESHOLD:
                    expiring.append(put)
            except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
                continue

        if not expiring:
            return {"needs_roll": False, "expiring_puts": [], "new_orders": []}

        # 需要滚仓: 平仓到期合约 + 买入新月合约
        logger.info(f"需要滚仓: {len(expiring)} 组认沽期权即将到期")

        # 生成新订单 (直接调用 generate_put_orders)
        new_orders = self.generate_put_orders()

        return {
            "needs_roll": True,
            "expiring_puts": expiring,
            "close_orders": [
                {
                    "type": "CLOSE_PUT",
                    "underlying": p.get("underlying"),
                    "contracts": p.get("contracts"),
                    "expiry_date": p.get("expiry_date"),
                    "action": "SELL_TO_CLOSE",
                }
                for p in expiring
            ],
            "new_orders": new_orders.get("orders", []),
        }

    def record_execution(self, orders: list[dict], actual_premium: float | None = None):
        """记录执行结果, 更新状态"""
        if actual_premium is None:
            actual_premium = sum(o.get("premium_total", 0) for o in orders)

        # 更新活跃Put列表
        active_puts = self.state.get("active_puts", [])
        for order in orders:
            if order.get("status") in ("FILLED", "PENDING"):
                active_puts.append(
                    {
                        "underlying": order.get("underlying"),
                        "strike": order.get("strike"),
                        "contracts": order.get("contracts"),
                        "expiry_date": order.get("expiry_date"),
                        "premium_paid": order.get("premium_total"),
                        "entry_date": datetime.now().strftime("%Y-%m-%d"),
                    }
                )

        # 更新年度已花费
        ytd_spent = self.state.get("ytd_premium_spent", 0) + actual_premium

        self.state = {
            "last_updated": datetime.now().isoformat(),
            "active_puts": active_puts,
            "ytd_premium_spent": ytd_spent,
            "annual_budget": self.TOTAL_CAPITAL * self.MAX_ANNUAL_COST_PCT,
            "budget_usage_pct": ytd_spent / (self.TOTAL_CAPITAL * self.MAX_ANNUAL_COST_PCT),
        }
        self._save_state()
        logger.info(f"Put保护执行记录已更新: 活跃{len(active_puts)}组, 年度已花费CNY{ytd_spent:,.0f}")

    def _calc_next_expiry(self, months_ahead: int = 1) -> datetime:
        """计算下一个期权到期日 (每月第4个周三)"""
        today = datetime.now()
        # 目标月份
        target_month = today.month + months_ahead
        target_year = today.year
        if target_month > 12:
            target_month -= 12
            target_year += 1

        # 找第4个周三
        day = 1
        wednesday_count = 0
        while True:
            d = datetime(target_year, target_month, day)
            if d.weekday() == 2:  # 周三
                wednesday_count += 1
                if wednesday_count == 4:
                    return d
            day += 1
            if day > 28:
                # 安全回退
                return datetime(target_year, target_month, 28)

    def get_protection_status(self) -> dict[str, Any]:
        """获取当前保护状态摘要"""
        portfolio_value = self._get_portfolio_value()
        active_puts = self.state.get("active_puts", [])
        ytd_spent = self.state.get("ytd_premium_spent", 0)
        annual_budget = self.TOTAL_CAPITAL * self.MAX_ANNUAL_COST_PCT

        # 计算保护覆盖率
        total_notional_protected = 0
        for put in active_puts:
            strike = put.get("strike", 0)
            contracts = put.get("contracts", 0)
            total_notional_protected += strike * contracts * 10000

        coverage = total_notional_protected / portfolio_value if portfolio_value > 0 else 0

        return {
            "portfolio_value": portfolio_value,
            "protection_active": len(active_puts) > 0,
            "active_put_groups": len(active_puts),
            "notional_protected": total_notional_protected,
            "coverage_pct": round(coverage, 4),
            "ytd_premium_spent": ytd_spent,
            "annual_budget": annual_budget,
            "budget_usage_pct": round(ytd_spent / annual_budget, 4) if annual_budget > 0 else 0,
            "needs_action": portfolio_value >= self.MIN_PORTFOLIO_VALUE and len(active_puts) == 0,
        }


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="认沽期权保护引擎")
    parser.add_argument("--action", choices=["check", "generate", "roll", "status"], default="status", help="执行动作")
    parser.add_argument("--drawdown-level", type=int, default=0, help="回撤级别")
    args = parser.parse_args()

    ppe = ProtectivePutEngine()

    if args.action == "status":
        status = ppe.get_protection_status()
        logger.info(json.dumps(status, ensure_ascii=False, indent=2))
    elif args.action == "check":
        should, reason = ppe.should_buy_protection()
        logger.info(f"需要买入保护: {'是' if should else '否'}")
        logger.info(f"原因: {reason}")
    elif args.action == "generate":
        result = ppe.generate_put_orders(drawdown_level=args.drawdown_level)
        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.action == "roll":
        result = ppe.check_and_roll()
        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
