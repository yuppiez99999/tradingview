"""
Theta 引擎 - 常态化备兑收租 (Covered Call Overlay)
=================================================

对冲基金视角核心模块:
    - 400万现货持仓为担保, 无需额外保证金
    - 每月滚动卖出 DTE 20-40 天, OTM 5-8% 的认购期权
    - 预期年化增厚 6%-9% 现金流

核心逻辑:
    1. 月度生成 Covered Call 计划 (目标ETF+行权价+到期日+预期权利金)
    2. 到期前 5 个交易日自动滚仓
    3. 风控: 最大OTM 10%, DTE 15-45天, 自动止损

用法:
    from utils.theta_engine import ThetaEngine
    engine = ThetaEngine()
    plan = engine.generate_monthly_plan()
    engine.check_rollover()
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml

# T4.2 收尾 — 切换到统一 BS 定价内核 (Single Source of Truth)
# 之前 L206-207 用 `spot * iv_estimate * otm_pct * 0.5` 简化估算, 现统一调用
# utils/fineng/pricing/black_scholes.py, 与 greek_hedge_manager / protective_put_engine 对齐
from utils.fineng.pricing.black_scholes import bs_call_price

logger = logging.getLogger("theta_engine")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "configs" / "portfolio.yaml"
PLAN_DIR = BASE_DIR / "reports" / "theta_plans"


class ThetaEngine:
    """Theta 引擎 - 备兑期权生成与滚仓管理"""

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        PLAN_DIR.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        """加载 portfolio.yaml 配置 (P1-Q8: 通过 ConfigManager 统一加载)

        优先级:
            1. 显式传入的 config_path (向后兼容测试场景)
            2. ConfigManager 自动解析 (v8.3 唯一事实源 > configs/ 历史回退)

        Returns:
            hedge.theta_engine 配置字典, 加载失败返回空 dict (fail-safe)
        """
        # 路径 1: 调用方显式指定了 config_path (测试场景, 向后兼容)
        if self.config_path != CONFIG_PATH:
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                theta_cfg = cfg.get("hedge", {}).get("theta_engine", {}) if isinstance(cfg, dict) else {}
                if not theta_cfg.get("enabled", False):
                    logger.warning("Theta 引擎未启用 (显式路径)")
                return theta_cfg
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
                logger.error(f"加载配置失败 (显式路径 {self.config_path}): {e}")
                return {}

        # 路径 2: 通过 ConfigManager 统一加载 (P1-Q8, 生产路径)
        try:
            from utils.config_manager import get_config

            portfolio_cfg = get_config("portfolio")
            theta_cfg = portfolio_cfg.get("hedge", {}).get("theta_engine", {})
            if theta_cfg:
                if not theta_cfg.get("enabled", False):
                    logger.warning("Theta 引擎未启用")
                return theta_cfg  # type: ignore
                # ConfigManager 全部失败, 回退到旧路径 (保底)
            with open(self.config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            theta_cfg = cfg.get("hedge", {}).get("theta_engine", {}) if isinstance(cfg, dict) else {}
            if not theta_cfg.get("enabled", False):
                logger.warning("Theta 引擎未启用 (回退路径)")
            return theta_cfg
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"ConfigManager 加载失败, 回退到旧路径: {e}")
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("hedge", {}).get("theta_engine", {}) if isinstance(cfg, dict) else {}
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e2: # P2 模块 fail-safe, 待后续精确化
                logger.error(f"加载配置彻底失败: {e2}")
                return {}

    def _get_etf_spots(self) -> dict[str, float]:
        """获取目标 ETF 现价 (Wind MCP > 新浪 HTTP > 兜底)"""
        spots = {}
        target_codes = [t["code"] for t in self.config.get("target_etfs", [])]

        # 尝试 Wind MCP
        try:
            from wind_mcp_fetcher import wind_get_etf_quote

            for code in target_codes:
                try:
                    price = wind_get_etf_quote(code)
                    if price and price > 0:
                        spots[code] = float(price)
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
                    pass
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
            pass

        # 回退: 新浪 HTTP
        if len(spots) < len(target_codes):
            try:
                import requests

                for code in target_codes:
                    if code in spots:
                        continue
                    prefix = "sh" if code.startswith("5") else "sz"
                    url = f"http://hq.sinajs.cn/list={prefix}{code}"
                    headers = {"Referer": "https://finance.sina.com.cn"}
                    r = requests.get(url, headers=headers, timeout=5)
                    if r.status_code == 200:
                        parts = r.text.split(",")
                        if len(parts) > 3:
                            price = float(parts[3])
                            if price > 0:
                                spots[code] = price
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
                logger.warning(f"新浪行情获取失败: {e}")

        return spots

    def generate_monthly_plan(self) -> dict:
        """生成月度 Covered Call 计划

        Returns:
            {
                "generate_date": "2026-07-12",
                "expiry_date": "2026-08-14",
                "dte": 33,
                "positions": [
                    {
                        "code": "588080",
                        "spot_price": 1.025,
                        "strike": 1.080,  # OTM 5.4%
                        "strike_pct": 0.054,
                        "contracts": 20,
                        "est_premium": 0.008,  # 每份权利金
                        "est_premium_total": 16000,  # 总权利金
                        "annualized_return": 0.094  # 年化收益
                    },
                    ...
                ],
                "total_est_premium": 95000,
                "total_collateral": 3000000,
                "portfolio_yield": 0.024  # 月度组合收益率 2.4%
            }
        """
        if not self.config.get("enabled", False):
            logger.warning("Theta 引擎未启用, 跳过生成")
            return {"status": "disabled"}

        rolling = self.config.get("rolling", {})
        dte_range = rolling.get("dte_range", [20, 40])
        otm_range = rolling.get("strike_otm_pct", [0.05, 0.08])

        # 目标到期日 (取 DTE 中值)
        target_dte = (dte_range[0] + dte_range[1]) // 2
        expiry_date = datetime.now() + timedelta(days=target_dte)
        # 跳过到周末
        while expiry_date.weekday() >= 5:
            expiry_date += timedelta(days=1)

        spots = self._get_etf_spots()
        if not spots:
            logger.error("无法获取 ETF 现价, 无法生成计划")
            return {"status": "error", "reason": "no_price_data"}

        positions = []
        total_premium = 0
        total_collateral = 0

        for etf in self.config.get("target_etfs", []):
            code = etf["code"]
            weight = etf["weight_in_pool"]
            collateral = self.config.get("underlying_collateral", 3000000) * weight
            total_collateral += collateral

            if code not in spots:
                logger.warning(f"{code}: 无法获取现价, 跳过")
                continue

            spot = spots[code]
            # OTM 比例 (5-8% 区间中值 6.5%)
            otm_pct = (otm_range[0] + otm_range[1]) / 2
            strike = round(spot * (1 + otm_pct), 3)

            # 估算权利金 (简化: OTM 越深权利金越低, IV 越高权利金越高)
            # 科技类 ETF IV 约 25-35%, 红利类约 12-18%
            iv_estimate = 0.30 if code in ("588080", "512760", "515030") else 0.20
            # T4.2 收尾 — 调用统一 BS 定价内核 (替换简化估算 spot*iv*otm*0.5)
            # Covered Call 卖出认购, 权利金 = BS Call 价格
            T_years = target_dte / 365.0  # noqa: N806  # 剩余期限 (年, ACT/365)
            r = self.config.get("risk_free_rate", 0.02)  # 无风险利率, 默认 2%
            est_premium = bs_call_price(
                S=spot, K=strike, T=T_years, r=r, sigma=iv_estimate
            )
            # 边界保护: 极端 OTM 时 BS 价格可能趋近 0, 设最低价保底 (与 protective_put_engine 一致)
            est_premium = max(est_premium, 0.0001)

            # 合约数 (ETF 期权合约乘数 10000)
            contracts = int(collateral / (spot * 10000))

            est_premium_total = est_premium * contracts * 10000
            total_premium += est_premium_total

            # 年化收益率
            days_to_expiry = (expiry_date - datetime.now()).days
            annualized = (est_premium_total / collateral) * (365 / days_to_expiry)

            positions.append(
                {
                    "code": code,
                    "spot_price": spot,
                    "strike": strike,
                    "strike_otm_pct": round(otm_pct, 4),
                    "contracts": contracts,
                    "est_premium_per_unit": round(est_premium, 4),
                    "est_premium_total": round(est_premium_total, 2),
                    "collateral": collateral,
                    "annualized_return": round(annualized, 4),
                    "iv_estimate": iv_estimate,
                }
            )

        portfolio_yield = total_premium / total_collateral if total_collateral > 0 else 0

        plan = {
            "generate_date": datetime.now().strftime("%Y-%m-%d"),
            "expiry_date": expiry_date.strftime("%Y-%m-%d"),
            "dte": (expiry_date - datetime.now()).days,
            "positions": positions,
            "total_est_premium": round(total_premium, 2),
            "total_collateral": total_collateral,
            "portfolio_yield_monthly": round(portfolio_yield, 4),
            "portfolio_yield_annualized": round(portfolio_yield * 12, 4),
            "target_monthly_range": self.config.get("expected_enhancement", {}).get(
                "monthly_theta_target", [0.005, 0.008]
            ),
        }

        # 保存计划
        plan_file = PLAN_DIR / f"theta_plan_{datetime.now():%Y%m%d}.json"
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)

        logger.info(
            f"Theta 月度计划生成: {len(positions)} 个头寸, "
            f"预期权利金 {total_premium:.0f}, 月度收益 {portfolio_yield:.2%}"
        )

        return plan

    def check_rollover(self) -> list[dict]:
        """检查到期前 5 个交易日的头寸, 生成滚仓计划

        Returns:
            需要滚仓的头寸列表
        """
        # 加载最近计划
        plans = sorted(PLAN_DIR.glob("theta_plan_*.json"), reverse=True)
        if not plans:
            return []

        with open(plans[0], encoding="utf-8") as f:
            latest_plan = json.load(f)

        expiry_date = datetime.strptime(latest_plan["expiry_date"], "%Y-%m-%d")
        days_to_expiry = (expiry_date - datetime.now()).days

        # 到期前 5 个交易日 (约 7 天)
        if days_to_expiry > 7:
            logger.info(f"距到期 {days_to_expiry} 天, 暂无需滚仓")
            return []

        rollover_positions = []
        for pos in latest_plan.get("positions", []):
            rollover_positions.append(
                {
                    "code": pos["code"],
                    "old_expiry": latest_plan["expiry_date"],
                    "old_strike": pos["strike"],
                    "action": "close_and_open_new",
                    "reason": f"距到期 {days_to_expiry} 天",
                }
            )

        logger.info(f"触发滚仓: {len(rollover_positions)} 个头寸需要滚仓")
        return rollover_positions

    def get_theta_statistics(self) -> dict:
        """获取 Theta 引擎统计 (累计权利金/月度收益率)

        Returns:
            {
                "total_plans": 12,
                "total_premium_collected": 980000,
                "avg_monthly_yield": 0.0072,
                "avg_annualized_yield": 0.086,
                "target_met": true  # 是否达到 6-9% 年化目标
            }
        """
        plans = sorted(PLAN_DIR.glob("theta_plan_*.json"))
        if not plans:
            return {
                "total_plans": 0,
                "total_premium_collected": 0,
                "avg_monthly_yield": 0,
                "avg_annualized_yield": 0,
                "target_met": False,
            }

        total_premium = 0
        monthly_yields = []

        for plan_file in plans:
            try:
                with open(plan_file, encoding="utf-8") as f:
                    plan = json.load(f)
                total_premium += plan.get("total_est_premium", 0)
                monthly_yields.append(plan.get("portfolio_yield_monthly", 0))
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
                continue

        avg_monthly = sum(monthly_yields) / len(monthly_yields) if monthly_yields else 0
        avg_annual = avg_monthly * 12

        target_range = self.config.get("expected_enhancement", {}).get("annual_cashflow_boost", [0.06, 0.09])
        target_met = target_range[0] <= avg_annual <= target_range[1]

        return {
            "total_plans": len(plans),
            "total_premium_collected": round(total_premium, 2),
            "avg_monthly_yield": round(avg_monthly, 4),
            "avg_annualized_yield": round(avg_annual, 4),
            "target_range": target_range,
            "target_met": target_met,
        }


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Theta 引擎 - Covered Call 生成")
    parser.add_argument("--generate", action="store_true", help="生成月度计划")
    parser.add_argument("--rollover", action="store_true", help="检查滚仓")
    parser.add_argument("--stats", action="store_true", help="查看统计")
    args = parser.parse_args()

    engine = ThetaEngine()

    if args.generate:
        plan = engine.generate_monthly_plan()
        logger.info(json.dumps(plan, ensure_ascii=False, indent=2))

    if args.rollover:
        positions = engine.check_rollover()
        if positions:
            logger.info(f"需要滚仓的头寸: {len(positions)} 个")
            for p in positions:
                logger.info(f"  {p['code']}: {p['reason']}")
        else:
            logger.info("暂无头寸需要滚仓")

    if args.stats or (not any([args.generate, args.rollover, args.stats])):
        stats = engine.get_theta_statistics()
        logger.info("\n=== Theta 引擎统计 ===")
        logger.info(f"总计划数: {stats['total_plans']}")
        logger.info(f"累计预期权利金: {stats['total_premium_collected']:,.0f}")
        logger.info(f"平均月度收益率: {stats['avg_monthly_yield']:.2%}")
        logger.info(f"年化收益率: {stats['avg_annualized_yield']:.2%}")
        logger.info(
            f"目标区间: {stats['target_range'][0]:.0%}-{stats['target_range'][1]:.0%}, "
            f"达标: {'是' if stats['target_met'] else '否'}"
        )
