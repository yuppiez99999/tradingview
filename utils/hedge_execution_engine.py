"""
对冲执行引擎 (Hedge Execution Engine)
=====================================
修改原因: P0 对冲引擎实质化 - 对冲信号→实际订单的桥梁
修改日期: 2026-07-21

核心问题:
    greek_hedge_manager.py 计算出对冲需求但从未真正执行。
    本模块负责:
    1. 读取当前持仓和市场数据
    2. 调用 GreekHedgeManager 计算对冲需求
    3. 生成具体的对冲执行订单 (期货空头 + 认沽期权)
    4. 将订单写入 trade_plans/ 和 reports/ 作为执行计划

与以下模块协作:
    - utils/greek_hedge_manager.py: Greeks暴露计算
    - utils/drawdown_controller.py: 回撤触发加码对冲
    - utils/vol_target_controller.py: 波动率目标→缩仓
    - v7.5_institutional/generate_daily_trade_plan.py: 合成次日计划

用法:
    from utils.hedge_execution_engine import HedgeExecutionEngine
    engine = HedgeExecutionEngine()
    orders = engine.generate_hedge_orders()
    engine.write_to_trade_plan(orders, "2026-07-22")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, cast

logger = logging.getLogger("hedge_execution_engine")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
# v8.6.8 P0-01 FIX (2026-07-26): 路径与生产环境对齐
# 原路径 v7.5_institutional/ 为旧版本, 生产 daily_workflow + risk_guard_integrator
# 均使用 v8.3_institutional/, 导致 write_to_trade_plan() 写入孤立文件, 生产 trade_plan
# 缺失 execution_status / execution_notes 字段, 无法被下游执行器识别
REPORTS_DIR = BASE_DIR / "v8.3_institutional" / "reports"
TRADE_PLANS_DIR = BASE_DIR / "v8.3_institutional" / "trade_plans"


class HedgeExecutionEngine:
    """对冲执行引擎 - 将对冲信号转化为可执行订单

    设计原则:
        1. Beta对冲: IF期货空头覆盖组合50% Beta暴露
        2. 尾部保护: OTM 5% 认沽期权保护下行风险
        3. 动态调整: 根据回撤级别和波动率自动加码
        4. 成本控制: 年化对冲成本 < 2.5% 总资本
    """

    # 对冲参数
    IF_MULTIPLIER = 300  # IF期货合约乘数
    IF_MARGIN_RATE = 0.12  # IF保证金比例
    TARGET_BETA = 0.30  # 目标组合Beta (从1.05降到0.30)
    HEDGE_REBALANCE_THRESHOLD = 0.10  # Beta偏离10%触发再平衡

    # 期权参数
    PUT_OTM_PCT = 0.05  # 认沽虚值程度 5%
    MAX_ANNUAL_OPTION_COST_PCT = 0.025  # 最大年化期权成本 2.5%
    PUT_ROLL_DTE = 5  # 到期前5天滚仓

    # 懒加载可选实例属性 — 根除 __init__ 中 = None 触发的 None 单例推断
    _hedge_manager: Optional[Any]
    _post_trade_attribution: Optional[Any]
    positions_data: dict
    positions_file: Path

    def __init__(self, positions_file: str | None = None):
        self.positions_file = Path(positions_file) if positions_file else CONFIG_DIR / "positions.json"
        self.positions_data = self._load_positions()
        self._hedge_manager = None
        # T3.5: 执行后归因 (延迟初始化, 仅在 on_fill 调用时创建)
        self._post_trade_attribution = None

    # ============================================================
    # T3.5: 执行后归因接口
    # ============================================================
    def on_fill(self, fill, estimate=None):
        """成交后回调: 将成交记录传给 TCA 归因引擎 (T3.5)

        设计原则:
            - 解耦: 不影响 hedge_engine 的主流程 (生成对冲订单)
            - 可选: 若 TCA 归因不可用, 仅记录日志, 不阻断
            - 透传: 将 fill + estimate 透传给 PostTradeAttribution.record()

        Args:
            fill: FillRecord 或兼容字典 (symbol/side/shares/price/timestamp?)
            estimate: T3.4 的 PreTradeEstimate (可选, 用于预估 vs 实际对比)

        Returns:
            PostTradeAttribution 实例 (可用于后续归因查询)
        """
        try:
            if self._post_trade_attribution is None:
                from utils.tca_post_trade_attribution import PostTradeAttribution

                self._post_trade_attribution = PostTradeAttribution(save_to_file=True)

            # 字典 → FillRecord 转换 (兼容上层传入的字典)
            from utils.tca_post_trade_attribution import FillRecord as PTAFillRecord

            if isinstance(fill, dict):
                fill = PTAFillRecord(
                    symbol=str(fill.get("symbol", "")),
                    side=str(fill.get("side", "BUY")).upper(),
                    shares=int(fill.get("shares", 0)),
                    price=float(fill.get("price", 0.0)),
                    timestamp=fill.get("timestamp", ""),
                    broker=fill.get("broker", ""),
                    venue=fill.get("venue", ""),
                    order_id=fill.get("order_id", ""),
                )
            elif not isinstance(fill, PTAFillRecord):
                # tca_engine.FillRecord 或其他兼容类型 → 转换
                fill = PTAFillRecord(
                    symbol=str(getattr(fill, "symbol", "")),
                    side=str(getattr(fill, "side", "BUY")).upper(),
                    shares=int(getattr(fill, "shares", 0)),
                    price=float(getattr(fill, "price", 0.0)),
                    timestamp=getattr(fill, "timestamp", ""),
                    broker=getattr(fill, "broker", ""),
                    venue=getattr(fill, "venue", ""),
                    order_id=getattr(fill, "order_id", ""),
                )

            # 局部变量化 + None 守卫 — mypy 不收窄 self._post_trade_attribution 跨方法
            pta = self._post_trade_attribution
            if pta is not None:
                pta.record(fill, estimate)
            logger.info(
                "[HedgeEngine] on_fill 已记录: %s %s %d@%.4f",
                fill.symbol,
                fill.side,
                fill.shares,
                fill.price,
            )
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            # fail-safe: 归因失败不影响对冲主流程
            logger.error("[HedgeEngine] on_fill 归因失败 (fail-safe): %s", e)
        return self._post_trade_attribution

    def get_post_trade_attribution(self):
        """获取 PostTradeAttribution 实例 (用于归因查询)"""
        return self._post_trade_attribution

    def _load_positions(self) -> dict:
        """加载持仓配置"""
        try:
            with open(self.positions_file, encoding="utf-8") as f:
                return cast(dict, json.load(f))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"加载持仓失败: {e}")
            return {}

    def _get_hedge_manager(self):
        """懒加载 GreekHedgeManager"""
        if self._hedge_manager is None:
            from utils.greek_hedge_manager import GreekHedgeManager

            hm = GreekHedgeManager(
                target_delta=0.0,  # 目标Delta中性
                target_gamma=0.0,
                max_vega=100000.0,
                max_theta_burn=-10000.0,
            )
            self._hedge_manager = hm
            return hm
        return self._hedge_manager

    def calc_portfolio_beta(self) -> float:
        """计算当前组合Beta暴露 (基于持仓市值加权)

        Beta假设:
            - 宽基ETF: beta=1.0
            - 科技股: beta=1.3
            - 防御/红利: beta=0.6
            - 黄金: beta=0.1
            - 国债: beta=0.05
            - 医药: beta=0.9
            - 新能源: beta=1.2
            - 资源: beta=1.1
        """
        SECTOR_BETA = {
            "宽基": 1.0,
            "科技": 1.3,
            "金融": 1.1,
            "防御": 0.6,
            "资源": 1.1,
            "新能源": 1.2,
            "医药": 0.9,
            "顺周期": 0.8,
            "制造": 1.1,
            "成长": 1.2,
            "国债": 0.05,
        }

        positions = self.positions_data.get("positions", {})
        total_value = 0.0
        weighted_beta = 0.0

        for _code, pos in positions.items():
            shares = pos.get("actual_shares", pos.get("shares", 0))
            price = pos.get("est_price", 0)
            if shares <= 0 or price <= 0:
                continue
            market_value = shares * price
            sector = pos.get("sector", "宽基")
            beta = SECTOR_BETA.get(sector, 1.0)
            weighted_beta += market_value * beta
            total_value += market_value

        if total_value <= 0:
            return 1.0
        return weighted_beta / total_value

    def calc_portfolio_market_value(self) -> float:
        """计算组合当前市值 (不含对冲头寸)"""
        positions = self.positions_data.get("positions", {})
        total_value = 0.0
        for _code, pos in positions.items():
            shares = pos.get("actual_shares", pos.get("shares", 0))
            price = pos.get("est_price", 0)
            if shares > 0 and price > 0:
                total_value += shares * price
        return total_value

    def generate_futures_hedge_orders(
        self,
        portfolio_value: float | None = None,
        portfolio_beta: float | None = None,
        target_beta: float | None = None,
        drawdown_level: int = 0,
    ) -> list[dict]:
        """生成期货对冲订单

        逻辑:
            需要对冲的Beta = (current_beta - target_beta) * portfolio_value
            IF合约数 = hedge_notional / (IF价格 * IF乘数 * IF_beta)

        回撤加码:
            Level 1: 对冲比例 40% → 50%
            Level 2: 对冲比例 → 60%
            Level 3: 对冲比例 → 80%
            Level 4: 对冲比例 → 80% (后续逐步平仓)
        """
        if portfolio_value is None:
            portfolio_value = self.calc_portfolio_market_value()
        if portfolio_beta is None:
            portfolio_beta = self.calc_portfolio_beta()
        if target_beta is None:
            target_beta = self.TARGET_BETA

        # 回撤加码: 根据级别调整target_beta
        DD_BETA_TARGETS = {0: 0.30, 1: 0.25, 2: 0.20, 3: 0.10, 4: 0.05}
        if drawdown_level > 0:
            target_beta = DD_BETA_TARGETS.get(drawdown_level, target_beta)

        # 需要对冲掉的Beta
        beta_to_hedge = max(portfolio_beta - target_beta, 0)
        if beta_to_hedge < 0.05:
            logger.info(f"Beta已在目标范围内 ({portfolio_beta:.3f} ≈ {target_beta}), 无需对冲")
            return []

        # 对冲所需名义价值
        hedge_notional = portfolio_value * beta_to_hedge

        # IF期货当前估算价格 (沪深300指数 ÷ 1)
        # 实际应从行情获取, 这里用合理估值
        if_price = self._get_if_price()
        if if_price <= 0:
            if_price = 4200  # 默认沪深300指数水平

        # 每张IF合约对冲的名义价值
        if_notional_per_contract = if_price * self.IF_MULTIPLIER  # 约 126万/张

        # 需要空头合约数
        contracts_needed = round(hedge_notional / if_notional_per_contract)
        contracts_needed = max(contracts_needed, 1)  # 至少1张

        # 保证金需求
        margin_required = contracts_needed * if_notional_per_contract * self.IF_MARGIN_RATE

        orders = [
            {
                "order_id": f"HEDGE_IF_{datetime.now():%Y%m%d_%H%M%S}",
                "type": "FUTURES",
                "instrument": "IF",
                "exchange": "CFFEX",
                "direction": "SELL",
                "contracts": contracts_needed,
                "multiplier": self.IF_MULTIPLIER,
                "est_price": if_price,
                "notional": contracts_needed * if_notional_per_contract,
                "margin_required": margin_required,
                "execution_window": "09:45-10:30",
                "order_type": "LIMIT",
                "price_buffer": 0.002,  # 限价下偏0.2%
                "rationale": {
                    "portfolio_value": portfolio_value,
                    "portfolio_beta": round(portfolio_beta, 4),
                    "target_beta": target_beta,
                    "beta_to_hedge": round(beta_to_hedge, 4),
                    "hedge_notional": round(hedge_notional, 0),
                    "drawdown_level": drawdown_level,
                },
                "risk_limits": {
                    "max_contracts": 10,  # 500万组合最多10张IF
                    "max_margin_pct": 0.30,  # 最大保证金占对冲资金30%
                },
                "status": "PENDING",
            }
        ]

        logger.info(
            f"期货对冲订单: IF空头 {contracts_needed} 张, "
            f"保证金 ¥{margin_required:,.0f}, "
            f"Beta {portfolio_beta:.3f} → {target_beta:.3f}"
        )
        return orders

    def generate_put_protection_orders(
        self,
        portfolio_value: float | None = None,
        drawdown_level: int = 0,
    ) -> list[dict]:
        """生成认沽期权保护订单

        详见 utils/protective_put_engine.py (P3模块)
        这里生成基础Put保护需求, 具体执行由 protective_put_engine 完成
        """
        if portfolio_value is None:
            portfolio_value = self.calc_portfolio_market_value()

        # 年度期权预算 = 总资本 * 2.5%
        total_capital = self.positions_data.get("meta", {}).get("total_capital", 5_000_000)
        annual_budget = total_capital * self.MAX_ANNUAL_OPTION_COST_PCT
        quarterly_budget = annual_budget / 4  # 每季度预算

        # 回撤加码
        if drawdown_level >= 2:
            quarterly_budget *= 2.0  # Level 2+ 期权预算翻倍
        elif drawdown_level >= 1:
            quarterly_budget *= 1.5  # Level 1 预算增加50%

        # 目标ETF的认沽保护
        hedge_positions = self.positions_data.get("hedge_positions", {})
        put_orders = []

        # v9.0 新结构: Put 配置嵌套在 risk_reversal_collar.put_protection 下
        # v8.x 旧结构: Put 配置直接放在 hedge_positions 顶层 (如 ETF_put_options)
        put_protection_cfg = hedge_positions.get("risk_reversal_collar", {}).get("put_protection", {})
        if put_protection_cfg:
            put_entries = put_protection_cfg
        else:
            put_entries = hedge_positions

        for key, hedge_pos in put_entries.items():
            # P0-E 修复 (2026-07-26 v8.6.5): 跳过 description/hedge_mode/budget_summary 等非字典字段
            # 原始 bug: hedge_positions 包含 "description": "200万纯期权对冲..." 等字符串字段
            # 遍历时 hedge_pos 是字符串, hedge_pos.get("instrument", "") 抛
            # 'str' object has no attribute 'get', 导致每次 EOD 对冲订单生成都失败
            if not isinstance(hedge_pos, dict):
                continue
            if "put" not in key.lower() and "Put" not in hedge_pos.get("instrument", ""):
                continue

            contracts = hedge_pos.get("target_contracts", 0)
            premium_budget = hedge_pos.get("premium_budget", 0)
            instrument = hedge_pos.get("instrument", "")

            if contracts <= 0:
                continue

            put_orders.append(
                {
                    "order_id": f"HEDGE_PUT_{key}_{datetime.now():%Y%m%d}",
                    "type": "OPTIONS",
                    "instrument": instrument,
                    "exchange": hedge_pos.get("exchange", "SSE"),
                    "direction": "BUY_PUT",
                    "contracts": contracts,
                    "strike_rule": hedge_pos.get("strike", "OTM_5%"),
                    "premium_budget": premium_budget,
                    "execution_window": "09:30-10:00",
                    "order_type": "LIMIT",
                    "rationale": {
                        "hedge_key": key,
                        "reason": hedge_pos.get("reason", ""),
                        "framework": hedge_pos.get("framework", []),
                        "drawdown_level": drawdown_level,
                    },
                    "status": "PENDING",
                }
            )

        logger.info(f"认沽保护订单: {len(put_orders)} 组, 总预算 ¥{sum(o['premium_budget'] for o in put_orders):,.0f}")
        return put_orders

    def generate_covered_call_orders(self) -> list[dict]:
        """生成 Covered Call 备兑开仓订单

        逻辑:
            1. 读取 hedge_positions.covered_call_overlay 配置
            2. 对每个标的, 从 positions 中读取 ETF 持仓数量
            3. 计算可备兑张数 = 持仓份额 // 10000 (1张=10000份)
            4. 根据 strike_rule 计算 OTM 行权价
            5. 估算月度权利金收入 (OTM 5% ≈ 1.0%, OTM 8% ≈ 0.6%)

        前置条件:
            - 证券账户已开通一级期权权限
            - ETF 持仓 >= 10000 份 (1张备兑担保)
            - hedge_positions.covered_call_overlay.enabled = true

        Returns:
            Covered Call 备兑开仓订单列表
        """
        hedge_positions = self.positions_data.get("hedge_positions", {})
        cc_cfg = hedge_positions.get("covered_call_overlay", {})

        if not cc_cfg.get("enabled", False):
            logger.info("Covered Call 备兑未启用, 跳过")
            return []

        underlyings = cc_cfg.get("underlyings", [])
        positions = self.positions_data.get("positions", {})

        # OTM 程度 → 月度权利金率估算 (基于 ETF 期权隐含波动率经验值)
        # 经验值: 上交所/深交所 ETF 期权, 30天到期, IV ~20-25%
        OTM_PREMIUM_RATE = {
            0.05: 0.010,  # OTM 5%: 月度权利金约 1.0%
            0.065: 0.008,  # OTM 6.5%: 月度权利金约 0.8%
            0.08: 0.006,  # OTM 8%: 月度权利金约 0.6%
            0.10: 0.004,  # OTM 10%: 月度权利金约 0.4%
        }

        cc_orders: list[dict] = []
        for u in underlyings:
            if not isinstance(u, dict):
                continue

            code = u.get("code", "")
            direction = u.get("direction", "SELL_CALL")
            strike_rule = u.get("strike_rule", "OTM_5pct_to_8pct")

            # 从持仓中获取 ETF 数据
            etf_pos = positions.get(code, {})
            if not etf_pos:
                logger.warning(f"Covered Call 标的 {code} 未在持仓中找到, 跳过")
                continue

            shares = int(etf_pos.get("shares", 0))
            etf_price = float(etf_pos.get("est_price", 0))
            etf_name = etf_pos.get("name", code)

            if shares < 10000 or etf_price <= 0:
                logger.warning(
                    f"Covered Call 标的 {code} 持仓不足: {shares} 份 < 10000 份 (1张), 跳过"
                )
                continue

            # 可备兑张数 (1张 = 10000 份)
            max_contracts = shares // 10000
            target_contracts = u.get("target_contracts", max_contracts)
            contracts = min(target_contracts, max_contracts)

            if contracts <= 0:
                continue

            # 解析 strike_rule 获取 OTM 百分比
            otm_pct = 0.05  # 默认 OTM 5%
            if "5pct" in strike_rule and "8pct" in strike_rule:
                otm_pct = 0.065  # 取中间值
            elif "10pct" in strike_rule:
                otm_pct = 0.10
            elif "8pct" in strike_rule:
                otm_pct = 0.08
            elif "5pct" in strike_rule:
                otm_pct = 0.05

            # 计算行权价 (OTM Call: 行权价 > 现价)
            strike_price = round(etf_price * (1 + otm_pct), 3)

            # 估算月度权利金
            premium_rate = OTM_PREMIUM_RATE.get(otm_pct, 0.008)
            premium_per_contract = round(etf_price * 10000 * premium_rate, 0)
            total_premium = premium_per_contract * contracts

            cc_orders.append(
                {
                    "order_id": f"HEDGE_CC_{code.replace('.', '_')}_{datetime.now():%Y%m%d}",
                    "type": "OPTIONS",
                    "instrument": f"{code.split('.')[0]} Call",
                    "exchange": "SSE" if code.endswith(".SH") else "SZSE",
                    "direction": "SELL_CALL_COVERED",
                    "underlying": code,
                    "underlying_name": etf_name,
                    "contracts": contracts,
                    "strike_rule": strike_rule,
                    "otm_pct": otm_pct,
                    "est_strike_price": strike_price,
                    "est_underlying_price": etf_price,
                    "est_premium_per_contract": premium_per_contract,
                    "est_total_premium": total_premium,
                    "collateral": f"{shares}份 {code} ETF备兑担保",
                    "execution_window": "09:35-10:05",
                    "order_type": "LIMIT",
                    "rationale": {
                        "underlying_shares": shares,
                        "max_covered_contracts": max_contracts,
                        "target_contracts": target_contracts,
                        "otm_pct": otm_pct,
                        "monthly_income_estimate": total_premium,
                        "annual_income_estimate": total_premium * 12,
                        "permission_level": "LEVEL_1",
                    },
                    "status": "PENDING",
                }
            )

            logger.info(
                f"Covered Call: {code} {etf_name} 卖出 {contracts} 张 "
                f"OTM {otm_pct*100:.1f}% Call (行权价 {strike_price}), "
                f"月度权利金收入估算 ¥{total_premium:,.0f}"
            )

        total_income = sum(o["est_total_premium"] for o in cc_orders)
        logger.info(
            f"Covered Call 订单: {len(cc_orders)} 组, "
            f"月度权利金收入估算 ¥{total_income:,.0f}, "
            f"年化估算 ¥{total_income * 12:,.0f}"
        )
        return cc_orders

    def generate_hedge_orders(self, drawdown_level: int = 0) -> dict[str, Any]:
        """生成完整对冲执行计划 (期货 + 期权)

        Returns:
            {
                "generated_at": "...",
                "portfolio_status": {...},
                "futures_orders": [...],
                "options_orders": [...],
                "total_margin_required": float,
                "total_premium_budget": float,
                "hedge_effectiveness": {...},
            }
        """
        portfolio_value = self.calc_portfolio_market_value()
        portfolio_beta = self.calc_portfolio_beta()
        total_capital = self.positions_data.get("meta", {}).get("total_capital", 5_000_000)
        hedge_capital = self.positions_data.get("meta", {}).get("hedge_capital", 2_000_000)

        # v8.6.8 P0-01 FIX (2026-07-26): 检查 hedge_mode, OPTIONS_ONLY 模式跳过期货订单
        # 原代码无视 portfolio.yaml/positions.json 的 hedge_mode=OPTIONS_ONLY 配置,
        # 始终生成 IF 期货空头订单, 导致:
        #   1. 与 portfolio.yaml "OPTIONS_ONLY" 模式声明冲突
        #   2. 期货保证金 172K + 期权权利金 1.65M = 1.82M > 1M 预算, 误判超支
        #   3. trade_plan.futures_options_hedge.hedge_mode=OPTIONS_ONLY 与 hedge_execution.futures_orders=[IF...] 矛盾
        hedge_positions_cfg = self.positions_data.get("hedge_positions", {}) or {}
        hedge_mode = str(hedge_positions_cfg.get("hedge_mode", "MIXED")).upper()
        options_only_mode = hedge_mode in ("OPTIONS_ONLY", "COVERED_CALL_PUT_PROTECT", "MULTI_STRATEGY_OPTIONS")

        if options_only_mode:
            # 纯期权对冲模式: 不生成期货空头订单, Beta 风险通过 ETF Put 组合管理
            futures_orders = []
            target_beta_after = portfolio_beta  # 维持原 Beta, 由 Put 提供尾部保护
            logger.info(
                f"[P0-01] hedge_mode={hedge_mode}, 跳过 IF 期货订单生成, "
                f"组合 Beta {portfolio_beta:.3f} 由 ETF Put 组合保护"
            )
        else:
            # 混合对冲模式 (原行为): 生成期货 + 期权
            futures_orders = self.generate_futures_hedge_orders(
                portfolio_value=portfolio_value,
                portfolio_beta=portfolio_beta,
                drawdown_level=drawdown_level,
            )
            # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
            # 原代码 else 分支没有初始化 target_beta_after, 当 generate_futures_hedge_orders
            # 返回空列表 (beta_to_hedge < 0.05, 无需对冲) 时, L462 的 if futures_orders 不进入,
            # target_beta_after 未定义, 后续 L470 round(target_beta_after, 4) 抛 NameError,
            # EOD 对冲执行中断, 次日 trade_plan 缺失 hedge_execution 字段.
            # 修复: 在 else 分支预初始化为 portfolio_beta (无需对冲则维持原 Beta).
            target_beta_after = portfolio_beta

        # 生成期权保护
        options_orders = self.generate_put_protection_orders(
            portfolio_value=portfolio_value,
            drawdown_level=drawdown_level,
        )

        # 生成 Covered Call 备兑开仓订单 (一级权限, 收取权利金抵消 Put 成本)
        covered_call_orders = self.generate_covered_call_orders()

        # 汇总 (净成本 = 保证金 + Put权利金支出 - Call权利金收入)
        total_margin = sum(o.get("margin_required", 0) for o in futures_orders)
        total_premium = sum(o.get("premium_budget", 0) for o in options_orders)
        total_call_income = sum(o.get("est_total_premium", 0) for o in covered_call_orders)
        total_cost = total_margin + total_premium - total_call_income

        # v8.6.8 P0-01 FIX: 预算阈值与 positions.json budget_summary 设计对齐
        # 原公式 hedge_capital * 0.7 (30% 缓冲) 过于保守, 导致设计内预算 (825K) 也被误判超支
        # 正确做法: 读取 budget_summary.buffer_for_roll_margin 作为缓冲, 预算阈值 = hedge_capital - buffer
        # 若 budget_summary 不存在, 降级使用 0.85 (15% 缓冲, 与 budget_summary 17.5% 接近)
        budget_summary = hedge_positions_cfg.get("budget_summary", {}) or {}
        buffer_for_roll = float(budget_summary.get("buffer_for_roll_margin", hedge_capital * 0.15))
        budget_threshold = max(hedge_capital - buffer_for_roll, 0)
        budget_ok = total_cost <= budget_threshold

        if not budget_ok:
            logger.warning(
                f"[P0-01] 对冲预算超支: total_cost=¥{total_cost:,.0f} > "
                f"threshold=¥{budget_threshold:,.0f} (hedge_capital=¥{hedge_capital:,.0f} "
                f"- buffer=¥{buffer_for_roll:,.0f}), 订单将标记为 CANCELLED_OVER_BUDGET"
            )

        # 对冲效果预估 (若期货订单被生成则取其 target_beta, 否则维持原 Beta)
        if futures_orders:
            target_beta_after = futures_orders[0]["rationale"]["target_beta"]

        result = {
            "generated_at": datetime.now().isoformat(),
            "drawdown_level": drawdown_level,
            "portfolio_status": {
                "market_value": round(portfolio_value, 0),
                "portfolio_beta_before": round(portfolio_beta, 4),
                "target_beta_after": round(target_beta_after, 4),
                "total_capital": total_capital,
                "hedge_capital": hedge_capital,
            },
            "futures_orders": futures_orders,
            "options_orders": options_orders,
            "covered_call_orders": covered_call_orders,
            "cost_summary": {
                "total_margin_required": round(total_margin, 0),
                "total_premium_budget": round(total_premium, 0),
                "total_call_income": round(total_call_income, 0),
                "total_cost": round(total_cost, 0),
                "hedge_capital_usage_pct": round(total_cost / hedge_capital, 4) if hedge_capital > 0 else 0,
                "within_budget": budget_ok,
                "budget_threshold": round(budget_threshold, 0),
                "buffer_for_roll": round(buffer_for_roll, 0),
                "hedge_mode": hedge_mode,
            },
            "hedge_effectiveness": {
                "beta_reduction": round(portfolio_beta - target_beta_after, 4),
                "downside_protection": f"OTM {self.PUT_OTM_PCT * 100:.0f}% Put",
                "covered_call_income_monthly": round(total_call_income, 0),
                "covered_call_income_annual": round(total_call_income * 12, 0),
                "estimated_annual_cost_pct": round((total_premium - total_call_income * 12) / total_capital, 4),
            },
        }

        return result

    def write_to_trade_plan(self, hedge_result: dict, trade_date: str) -> str:
        """将对冲订单写入交易计划文件

        Args:
            hedge_result: generate_hedge_orders() 返回的完整结果
            trade_date: 目标交易日 YYYY-MM-DD

        Returns:
            写入的文件路径
        """
        TRADE_PLANS_DIR.mkdir(parents=True, exist_ok=True)
        date_compact = trade_date.replace("-", "")
        plan_path = TRADE_PLANS_DIR / f"trade_plan_{date_compact}.json"

        # 如果已有计划文件, 合并对冲字段
        if plan_path.exists():
            try:
                with open(plan_path, encoding="utf-8") as f:
                    plan = json.load(f)
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
                plan = {}
        else:
            plan = {"trade_date": trade_date}

        # v8.6.8 P0-01 FIX (2026-07-26): 超预算订单必须标为 CANCELLED_OVER_BUDGET
        # 原代码无视 within_budget 一律写 status=PENDING, 导致:
        #   1. trade_plan.hedge_execution.cost_summary.within_budget=false 与 orders[].status=PENDING 矛盾
        #   2. 顶级对冲基金标准: 超预算订单不应进入执行队列, 必须在生成阶段拦截
        # 修复: within_budget=false 时, 所有订单 status 改为 CANCELLED_OVER_BUDGET,
        #       execution_status 改为 CANCELLED, 防止下游执行器误读
        cost_summary = hedge_result.get("cost_summary", {})
        within_budget = bool(cost_summary.get("within_budget", True))

        if within_budget:
            execution_status = "PENDING"
            order_status = "PENDING"
            execution_notes = [
                "期货: 09:45-10:30 完成IF空头开仓 (若存在)",
                "期权: 09:30-10:00 完成认沽期权买入",
                "确认: 盘后核实对冲比例是否达标",
            ]
        else:
            execution_status = "CANCELLED"
            order_status = "CANCELLED_OVER_BUDGET"
            execution_notes = [
                f"[P0-01] 预算超支, 全部对冲订单已拦截: "
                f"total_cost=¥{cost_summary.get('total_cost', 0):,.0f} > "
                f"threshold=¥{cost_summary.get('budget_threshold', 0):,.0f}",
                "下游执行器 (daily_workflow/SOR) 必须跳过 CANCELLED_OVER_BUDGET 订单",
                "需调整 hedge_positions 配置 (减少 contracts 或 premium_budget) 后重新生成",
            ]

        # 深拷贝订单并改写 status
        futures_orders_copy = [dict(o, status=order_status) for o in hedge_result.get("futures_orders", [])]
        options_orders_copy = [dict(o, status=order_status) for o in hedge_result.get("options_orders", [])]
        covered_call_orders_copy = [dict(o, status=order_status) for o in hedge_result.get("covered_call_orders", [])]

        # 写入对冲执行字段
        plan["hedge_execution"] = {
            "generated_at": hedge_result["generated_at"],
            "drawdown_level": hedge_result.get("drawdown_level", 0),
            "portfolio_beta_before": hedge_result["portfolio_status"]["portfolio_beta_before"],
            "target_beta_after": hedge_result["portfolio_status"]["target_beta_after"],
            "portfolio_status": hedge_result["portfolio_status"],
            "futures_orders": futures_orders_copy,
            "options_orders": options_orders_copy,
            "covered_call_orders": covered_call_orders_copy,
            "cost_summary": hedge_result["cost_summary"],
            "hedge_effectiveness": hedge_result["hedge_effectiveness"],
            "execution_status": execution_status,
            "execution_notes": execution_notes,
        }

        # v8.6.8 P0-01: 同步 futures_options_hedge 字段一致性
        # 原 trade_plan 已有 futures_options_hedge.hedge_mode=OPTIONS_ONLY 字段, 但 hedge_execution
        # 仍生成 IF 期货订单, 两处字段自相矛盾; 现在 hedge_execution 也已对齐 hedge_mode
        foh = plan.setdefault("futures_options_hedge", {})
        foh["hedge_mode"] = cost_summary.get("hedge_mode", foh.get("hedge_mode", "MIXED"))
        foh["orders"] = futures_orders_copy + options_orders_copy + covered_call_orders_copy
        foh["orders_count"] = len(foh["orders"])
        foh["loaded"] = True

        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)

        logger.info(f"对冲执行计划已写入: {plan_path} (status={execution_status})")
        return str(plan_path)

    def write_hedge_report(self, hedge_result: dict, trade_date: str) -> str:
        """将对冲报告写入 reports/ 目录"""
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        trade_date.replace("-", "")
        report_path = REPORTS_DIR / f"hedge_execution_fill_{trade_date}.json"

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(hedge_result, f, ensure_ascii=False, indent=2)

        logger.info(f"对冲执行报告已保存: {report_path}")
        return str(report_path)

    def _get_if_price(self) -> float:
        """获取IF期货当前价格 (沪深300指数)

        尝试顺序: Wind MCP > 新浪 > 默认值
        """
        # 尝试从持仓文件获取沪深300ETF价格反推指数
        positions = self.positions_data.get("positions", {})
        hs300_pos = positions.get("510300.SH", {})
        if hs300_pos:
            etf_price = hs300_pos.get("est_price", 0)
            if etf_price > 0:
                # 510300 ETF ≈ 沪深300指数 / 1000
                return float(etf_price) * 1000  # e.g. 4.65 → 4650

        # 尝试新浪接口
        try:
            import requests

            session = requests.Session()
            session.trust_env = False
            resp = session.get(
                "https://hq.sinajs.cn/list=sh000300",
                headers={"Referer": "https://finance.sina.com.cn"},
                timeout=5,
            )
            if resp.status_code == 200:
                content = resp.text
                start = content.find('"') + 1
                end = content.find('"', start)
                fields = content[start:end].split(",")
                if len(fields) > 3:
                    return float(fields[3])
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            pass

        return 4200  # 默认值


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="对冲执行引擎")
    parser.add_argument("--date", default=None, help="目标交易日 YYYY-MM-DD")
    parser.add_argument("--drawdown-level", type=int, default=0, choices=[0, 1, 2, 3, 4], help="当前回撤级别")
    parser.add_argument("--dry-run", action="store_true", help="仅计算不写入")
    args = parser.parse_args()

    if args.date is None:
        from datetime import timedelta

        today = datetime.now()
        # 下一个交易日
        d = today + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        trade_date = d.strftime("%Y-%m-%d")
    else:
        trade_date = args.date

    engine = HedgeExecutionEngine()
    result = engine.generate_hedge_orders(drawdown_level=args.drawdown_level)

    logger.info(json.dumps(result, ensure_ascii=False, indent=2))

    if not args.dry_run:
        engine.write_to_trade_plan(result, trade_date)
        engine.write_hedge_report(result, trade_date)
        logger.info(f"\n✅ 对冲执行计划已写入 trade_plan 和 reports (日期: {trade_date})")
    else:
        logger.info("\n[DRY-RUN] 未写入文件")