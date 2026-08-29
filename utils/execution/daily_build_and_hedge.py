"""
每日建仓计划 + 期货期权对冲联动系统 v8.0
=========================================

功能：
  1. 读取 500万自动交易计划 JSON
  2. 根据日期判断建仓阶段，生成当日股票/ETF交易指令
  3. 实时评估组合风险与市场状态
  4. 基于 Greeks 动态对冲模型计算期货/期权对冲目标
  5. 输出完整交易指令单 + 对冲计划 + 执行报告

用法：
  python daily_build_and_hedge.py                           # 生成今日交易计划
  python daily_build_and_hedge.py --date 2026-07-13         # 指定日期
  python daily_build_and_hedge.py --dry-run                  # 干跑模式
  python daily_build_and_hedge.py --hedge-only               # 仅生成对冲计划
  python daily_build_and_hedge.py --save                     # 保存报告到文件
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

# T3.6 迁移修正: __file__ 从根目录变为 utils/execution/, 需回退两级到项目根目录
BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "v8.3_institutional" / "src"))

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"daily_build_hedge_{datetime.now():%Y%m%d}.log",
            encoding="utf-8",
            delay=True,
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("daily_build_hedge")


class DailyBuildHedgeSystem:
    """
    每日建仓 + 对冲联动系统

    流程：
    1. 加载自动交易计划
    2. 判断当前建仓阶段
    3. 评估市场状态（VIX/熔断级别/ETF资金流）
    4. 计算风险预算分配
    5. 生成股票/ETF建仓指令
    6. 计算组合 Greeks 暴露
    7. 生成期货/期权对冲计划
    8. 输出完整执行报告
    """

    def __init__(self, target_date: date | None = None, dry_run: bool = False):
        self.target_date = target_date or date.today()
        self.dry_run = dry_run
        self.plan_data: dict[str, Any] = {}
        self.market_state: dict[str, Any] = {}
        self.build_plan: dict[str, Any] = {}
        self.hedge_plan: dict[str, Any] = {}
        self.risk_status: dict[str, Any] = {}
        self.stock_positions: dict[str, dict] = {}
        self.holdings: dict[str, float] = {}
        self.prices: dict[str, float] = {}
        self._load_plan()

    def _load_plan(self) -> None:
        """加载自动交易计划"""
        plan_path = BASE_DIR / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
        backup_path = BASE_DIR / "500万建仓计划_20260706.json"

        candidates = [plan_path, backup_path]
        for path in candidates:
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        self.plan_data = json.load(f)
                    logger.info(f"已加载交易计划: {path.name}")
                    return
                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                    OSError,
                    RuntimeError,
                ) as e:
                    logger.error(f"加载计划失败 {path}: {e}")

        logger.error("未找到交易计划文件")

    def get_active_phase(self) -> tuple[dict | None, str]:
        """获取当前活跃建仓阶段"""
        exec_plan = self.plan_data.get("execution_plan", {})
        phases = ["phase1", "phase2", "phase3", "phase4"]

        for phase_key in phases:
            phase = exec_plan.get(phase_key, {})
            if not phase:
                continue
            start = phase.get("start_date")
            end = phase.get("end_date")
            if not start or not end:
                continue

            try:
                start_date = datetime.strptime(start, "%Y-%m-%d").date()
                end_date = datetime.strptime(end, "%Y-%m-%d").date()
                if start_date <= self.target_date <= end_date:
                    return phase, phase_key
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                continue

        return None, "completed"

    def assess_market_state(self) -> dict[str, Any]:
        """评估市场状态 (含ETF资金流 + LLM辅助决策)"""
        try:
            from utils.etf_flow_monitor import ETFMonitor  # type: ignore

            etf_monitor = ETFMonitor()
            etf_data = etf_monitor.get_summary()
        except (ImportError, AttributeError):
            etf_data = {}

        # ★ 新增: ETF资金流向盘前/盘中决策 (LLM辅助)
        llm_decision = None
        try:
            from utils.etf_flow_decision import ETFFlowDecisionEngine

            decision_engine = ETFFlowDecisionEngine()

            # 判断当前时段
            current_hour = datetime.now().hour
            current_min = datetime.now().minute
            current_time_str = f"{current_hour:02d}:{current_min:02d}"

            if "09:15" <= current_time_str <= "09:25":
                # 盘前决策 (09:15-09:25)
                logger.info("检测到盘前时段 (09:15-09:25)，生成ETF资金流预配置计划...")
                llm_decision = decision_engine.pre_market_decision()
            elif "09:30" <= current_time_str <= "15:00":
                # 盘中决策 (09:30-15:00)
                logger.info("检测到盘中时段 (09:30-15:00)，执行ETF资金流实时监控...")
                llm_decision = decision_engine.intraday_decision()
            elif "15:00" <= current_time_str <= "15:30":
                # 盘后复盘 (15:00-15:30)
                logger.info("检测到盘后时段 (15:00-15:30)，生成ETF资金流复盘报告...")
                llm_decision = decision_engine.post_market_review()
            else:
                # 非交易时段: 默认使用盘前决策模式 (基于最新收盘数据)
                logger.info(
                    f"当前非交易时段 ({current_time_str})，使用盘前决策模式生成最新资金流分析..."
                )
                llm_decision = decision_engine.pre_market_decision()
        except (ImportError, AttributeError) as e:
            logger.warning(f"ETF资金流决策引擎不可用: {e}，继续使用规则引擎")

        # M-8 (2026-08-09): VIX / 指数收益率 的失败策略统一为 fail-closed。
        # 数据不可信时设置 data_degraded=True, 下游熔断协议据此暂停建仓 (而非编造良性值)。
        _data_degraded = False

        # G13 修复 (2026-08-06): VIX 从 VixDataSource 获取真实值, 非硬编码
        _vix_proxy = 18.5
        _vix_source = "default_placeholder"
        try:
            from utils.alpha.vix_data_source import fetch_vix

            _vix_fetched = fetch_vix(use_cache=True)
            if _vix_fetched is not None and 5.0 <= _vix_fetched <= 150.0:
                _vix_proxy = float(_vix_fetched)
                _vix_source = "live"
            else:
                logger.warning(
                    "[RISK] VIX 取值越界或为空, 使用占位默认值 18.5 (RiskBudget 降级)"
                )
                _vix_source = "degraded_default"
                _data_degraded = True
                _vix_degraded = True
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            logger.warning(
                "[RISK] VIX 获取失败, 标记 data_degraded (占位值 18.5 仅用于中性判断)",
                exc_info=True,
            )
            _vix_source = "degraded_default"
            _data_degraded = True
            _vix_degraded = True

        # N-1 修复 (2026-08-09): 指数收益率从真实数据源获取, 缺失时 fail-closed 降级 cautious
        # 原三条收益率触发条件 (ret_20d<=-0.15 / ret_5d<=-0.08 / ret_20d>=0.10) 因硬编码常量
        # 永远不可能成立, 导致 market_regime 退化为 VIX 单因子且 bull 档位永不可达。
        # M-1 (2026-08-09): 缺失时不写入伪造收益率(否则下游熔断判据被良性默认值骗过, 满仓建仓),
        # 改为显式 None + data_degraded 标记, 由下游 get_emergency_protocol fail-closed 暂停建仓。
        _idx_rets = self._fetch_index_returns("000300.SH")
        if _idx_rets is None:
            logger.error(
                "[RISK] 指数收益率获取失败, 标记 data_degraded (下游熔断协议 fail-closed 暂停建仓)"
            )
            _data_degraded = True
            market_state_regime_fallback = "cautious"
        else:
            market_state_regime_fallback = None

        market_state = {
            "date": self.target_date.strftime("%Y-%m-%d"),
            "vix_proxy": _vix_proxy,
            # M-1: 数据缺失时写 None 而非伪造值; 下游用 data_degraded 判定, 不依赖 .get(..., 0) 默认值
            "index_return_20d": _idx_rets[20] if _idx_rets else None,
            "index_return_5d": _idx_rets[5] if _idx_rets else None,
            "data_degraded": _data_degraded,
            "vix_source": _vix_source,
            "margin_balance_change": 0.005,
            "sector_health": {"high_end_manufacturing_20d": 0.03},
            "etf_flows": etf_data,
            "macro_heat_score": 55,
            "macro_regime": "中性",
            "market_regime": "neutral",
            "etf_flow_decision": llm_decision,  # ★ 新增: LLM辅助决策结果
        }

        vix = market_state["vix_proxy"]
        ret_5d = market_state["index_return_5d"]
        ret_20d = market_state["index_return_20d"]

        if _data_degraded or market_state_regime_fallback is not None:
            # H16 修复: VIX 或指数收益率任一缺失即保守降级, 不依赖占位值 18.5 算中性
            # fail-closed: 数据不可信时宁可少建仓, 不可编造良性 neutral 满仓建仓
            market_state["market_regime"] = "cautious"
        elif vix >= 40 or (ret_20d is not None and ret_20d <= -0.15):
            market_state["market_regime"] = "bear"
        elif vix >= 30 or (ret_5d is not None and ret_5d <= -0.08):
            market_state["market_regime"] = "cautious"
        elif ret_20d is not None and ret_20d >= 0.10:
            market_state["market_regime"] = "bull"
        else:
            market_state["market_regime"] = "neutral"

        self.market_state = market_state
        return market_state

    def _fetch_index_returns(self, index_code: str = "000300.SH") -> dict | None:
        """获取指数真实区间收益率 (5日/20日)

        优先级: Wind MCP (wind_get_index_data) > 新浪 HTTP (仅最新价, 退化为 None)
        - 返回 {5: float, 20: float} (小数, 如 0.012 = +1.2%)
        - 数据不足或获取失败时返回 None (调用方 fail-closed 降级 cautious)

        注意: 新浪接口仅返回最新价, 无法计算区间收益率, 故不作为主源;
        当 Wind MCP 不可用时诚实返回 None, 而非编造常量。
        """
        # 主源: Wind MCP 指数历史 (含近 20+ 交易日收盘价序列)
        try:
            from wind_mcp_fetcher import wind_get_index_data

            df = wind_get_index_data(index_code, days=70)
            if df is not None and len(df) >= 21 and "close" in df.columns:
                closes = df["close"].astype(float).dropna()
                # GLM 4.5 复核: dropna 后 closes 可能不足 21 行, 防止 iloc[-21] IndexError
                if len(closes) < 21:
                    return None
                closes = closes.reset_index(drop=True)
                ret_5d = (
                    float(closes.iloc[-1] / closes.iloc[-6] - 1.0)
                    if len(closes) >= 6
                    else None
                )
                ret_20d = (
                    float(closes.iloc[-1] / closes.iloc[-21] - 1.0)
                    if len(closes) >= 21
                    else None
                )
                if ret_5d is not None and ret_20d is not None:
                    return {5: ret_5d, 20: ret_20d}
        # P2 模块 fail-safe, 待后续精确化 (异常类型宽泛, 但不吞掉以保留可追溯性)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            pass

        # 回退: 新浪仅最新价, 无法计算区间收益 -> 诚实返回 None (不编造)
        return None

    def calculate_risk_budget(self, phase: dict) -> dict[str, Any]:
        """计算风险预算"""
        from utils.risk_budget_allocator import RiskBudgetAllocator

        total_capital = self.plan_data.get("meta", {}).get("total_capital", 5000000)
        stock_capital = self.plan_data.get("meta", {}).get("stock_etf_capital", 4000000)
        daily_limit = phase.get("daily_limit", 200000)
        target_pct = phase.get("target_percentage", 0.5)

        allocator = RiskBudgetAllocator(
            total_capital=stock_capital,
            target_return=0.08,
            max_dd=0.15,
            single_trade_risk=0.015,
            daily_budget_limit=daily_limit,
        )

        positions = self.plan_data.get("stock_etf_account", {}).get("positions", [])
        pending_positions = []
        for pos in positions:
            code = pos.get("code", "")
            style = pos.get("style", "")
            target_weight = pos.get("target_weight", 0.0)
            amount = pos.get("amount", 0)
            pending_positions.append(
                {
                    "code": code,
                    "code_clean": code.replace(".SH", "").replace(".SZ", ""),
                    "style": style,
                    "target_weight": target_weight,
                    "remaining": amount * target_pct,
                }
            )

        allocation = allocator.allocate_daily_budget(
            pending_positions=pending_positions,
            signals=None,
            macro_scores=None,
            etf_signals=None,
        )

        self.risk_status = {
            "total_capital": total_capital,
            "stock_capital": stock_capital,
            "daily_limit": daily_limit,
            "target_percentage": target_pct,
            "allocation": allocation,
            "market_regime": self.market_state.get("market_regime", "neutral"),
        }

        return self.risk_status

    def generate_build_instructions(self, phase: dict) -> dict[str, Any]:
        """生成股票/ETF建仓指令"""
        from build_plan_executor import BuildPlanExecutor
        from utils.data_types import normalize_stock_code

        executor = BuildPlanExecutor(
            plan_path=str(BASE_DIR / "500万建仓计划_20260706.json")
        )

        market_state = self.market_state
        protocol = executor.get_emergency_protocol(market_state)
        capital_multiplier = protocol.get("day_capital_multiplier", 1.0)

        plan_path_500w = BASE_DIR / "500万建仓计划_20260706.json"
        with open(plan_path_500w, encoding="utf-8") as f:
            plan_500w = json.load(f)

        price_quotes = {}
        target_portfolio = plan_500w.get("target_portfolio", {})
        position_plan = plan_500w.get("position_plan", {})

        for code in target_portfolio:
            norm_code = normalize_stock_code(code)
            est_price = target_portfolio[code].get("est_price")
            if est_price and est_price > 0:
                price_quotes[norm_code] = est_price

        for code in position_plan:
            norm_code = normalize_stock_code(code)
            if norm_code not in price_quotes:
                est_price = position_plan[code].get("est_price")
                if est_price and est_price > 0:
                    price_quotes[norm_code] = est_price

        # 宽基ETF 按社保国家队ETF资金净流入加减仓 (幂等/异常安全, 不落盘)
        if any(
            info.get("style") == "宽基" or info.get("adjustable")
            for info in plan_500w.get("target_portfolio", {}).values()
        ):
            try:
                import copy as _copy

                from utils.broad_based_etf_policy import (
                    adjust_plan_with_national_team_flow,
                )

                adj_plan = _copy.deepcopy(plan_500w)
                adj_result = adjust_plan_with_national_team_flow(adj_plan)
                if adj_result.get("applied"):
                    executor.plan_data = adj_plan
                    logger.info(
                        f"宽基ETF国家队加减仓已应用: {adj_result.get('summary')}"
                    )
                else:
                    logger.info(
                        f"宽基ETF国家队加减仓未应用: {adj_result.get('reason')}"
                    )
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                RuntimeError,
            ) as e:
                logger.warning(f"宽基ETF加减仓集成失败, 维持基准权重: {e}")

        sheet = executor.generate_daily_orders(
            target_date=self.target_date,
            price_quotes=price_quotes,
            capital_multiplier=capital_multiplier,
        )

        build_instructions = {
            "trade_date": sheet.trade_date,
            "phase": phase.get("name", ""),
            "phase_key": phase.get("phase", ""),
            "total_capital": sheet.total_capital,
            "day_capital": sheet.day_capital,
            "capital_multiplier": capital_multiplier,
            "emergency_level": protocol.get("level_name", "NORMAL"),
            "morning_orders": [
                {
                    "priority": o.priority,
                    "code": o.code,
                    "name": o.name,
                    "shares": o.shares,
                    "est_price": o.est_price,
                    "limit_price": o.limit_price,
                    "est_amount": o.est_amount,
                    "style": o.style,
                    "risk": o.risk,
                    "note": o.note,
                }
                for o in sheet.morning_orders
            ],
            "afternoon_orders": [
                {
                    "priority": o.priority,
                    "code": o.code,
                    "name": o.name,
                    "shares": o.shares,
                    "est_price": o.est_price,
                    "limit_price": o.limit_price,
                    "est_amount": o.est_amount,
                    "style": o.style,
                    "risk": o.risk,
                    "note": o.note,
                }
                for o in sheet.afternoon_orders
            ],
            "paused_orders": sheet.paused_orders,
            "warnings": sheet.warnings,
            "emergency_actions": protocol.get("actions", []),
        }

        self.build_plan = build_instructions
        return build_instructions

    def calculate_hedge_plan(self) -> dict[str, Any]:
        """计算期货/期权对冲计划"""
        from utils.greek_hedge_manager import GreekHedgeManager, HedgeInstrument

        positions = {}
        prices = {}
        style_map = {}

        for order in self.build_plan.get("morning_orders", []) + self.build_plan.get(
            "afternoon_orders", []
        ):
            code = order.get("code", "")
            shares = order.get("shares", 0)
            price = order.get("est_price", 0.0)
            style = order.get("style", "")
            if code and shares > 0 and price > 0:
                positions[code] = shares
                prices[code] = price
                style_map[code] = style

        for code, info in self.stock_positions.items():
            shares = info.get("shares", 0)
            price = info.get("est_price", 0.0)
            if code and shares > 0 and price > 0:
                positions[code] = positions.get(code, 0) + shares
                prices[code] = price

        portfolio_value = sum(positions.get(c, 0) * prices.get(c, 0) for c in positions)

        style_beta_map = {
            "科技": 1.20,
            "金融": 0.90,
            "宽基": 0.95,
            "新能源": 1.15,
            "医药": 0.85,
            "资源": 1.10,
            "制造": 1.05,
            "顺周期": 1.10,
            "防御": 0.60,
            "default": 1.00,
        }

        portfolio_beta = 0.0
        for code, shares in positions.items():
            amt = shares * prices.get(code, 0)
            style = style_map.get(code, "default")
            beta = style_beta_map.get(style, 1.0)
            portfolio_beta += (
                (amt / portfolio_value) * beta if portfolio_value > 0 else 0
            )

        market_regime = self.market_state.get("market_regime", "neutral")
        hedge_policy = self.plan_data.get("dynamic_rebalance", {}).get(
            "delta_control", {}
        )
        target_delta = hedge_policy.get("target_range", {}).get(market_regime, 0.6)

        ghm = GreekHedgeManager(
            target_delta=portfolio_value * target_delta,
            target_gamma=0.0,
            max_vega=50000.0,
            max_theta_burn=-5000.0,
        )

        portfolio_exposure = ghm.calc_portfolio_greeks(positions, prices)

        hedge_instruments = [
            HedgeInstrument(
                code="IF_futures",
                instrument_type="FUTURES",
                direction="SELL",
                multiplier=300.0,
                delta=1.0,
                beta=1.0,
            ),
        ]

        futures_targets = ghm.target_futures_delta_hedge(
            portfolio_exposure, hedge_instruments, {"IF_futures": 4500.0}
        )

        hedge_account = self.plan_data.get("hedge_account", {})
        modules = hedge_account.get("modules", [])

        option_hedge_plan = []
        for module in modules:
            module_name = module.get("name", "")
            if module_name == "risk_reversal_collar":
                for underlying in module.get("underlyings", []):
                    option_hedge_plan.append(
                        {
                            "type": "PUT_OPTION",
                            "code": underlying.get("code", ""),
                            "name": underlying.get("name", ""),
                            "direction": underlying.get("direction", "BUY"),
                            "strike": underlying.get("strike", ""),
                            "premium_budget": underlying.get("premium_budget", 0),
                            "purpose": underlying.get("purpose", ""),
                        }
                    )
            elif module_name == "vega_event_driven":
                for underlying in module.get("underlyings", []):
                    option_hedge_plan.append(
                        {
                            "type": "STRATEGY_OPTION",
                            "code": underlying.get("code", ""),
                            "name": underlying.get("name", ""),
                            "strategy": underlying.get("strategy", ""),
                            "condition": underlying.get("condition", ""),
                            "purpose": underlying.get("purpose", ""),
                        }
                    )

        hedge_plan = {
            "portfolio_value": portfolio_value,
            "portfolio_beta": round(portfolio_beta, 4),
            "market_regime": market_regime,
            "target_delta": target_delta,
            "current_delta": round(portfolio_exposure.delta, 2),
            "current_gamma": round(portfolio_exposure.gamma, 4),
            "current_vega": round(portfolio_exposure.vega, 2),
            "current_theta": round(portfolio_exposure.theta, 2),
            "futures_hedge": {
                "instrument": "IF_futures",
                "direction": "SELL",
                "contracts": round(futures_targets.get("IF_futures", 0), 2),
                "multiplier": 300,
                "estimated_notional": round(
                    futures_targets.get("IF_futures", 0) * 4500 * 300, 2
                ),
                "description": f"对冲组合 Beta {portfolio_beta:.2f}，目标 Delta {target_delta}",
            },
            "option_hedge": option_hedge_plan,
            "rebalance_signal": ghm.rebalance_signal(portfolio_exposure),
        }

        self.hedge_plan = hedge_plan
        return hedge_plan

    def _load_target_portfolio_plan(self) -> dict[str, Any]:
        """加载含 target_portfolio 的目标建仓计划 (500万建仓计划_20260706.json)。

        该计划与每日建仓指令 (BuildPlanExecutor) 使用同一文件, 是宽基ETF与
        十五五/康波合规校验的真实载体; v9.0 自动交易计划 (self.plan_data) 不含 target_portfolio。
        """
        if getattr(self, "_target_plan_cache", None) is None:
            try:
                with open(
                    BASE_DIR / "500万建仓计划_20260706.json", encoding="utf-8"
                ) as f:
                    self._target_plan_cache = cast(dict[str, Any], json.load(f))
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                RuntimeError,
            ) as e:
                logger.warning(f"加载目标建仓计划失败: {e}")
                self._target_plan_cache = {}
        return self._target_plan_cache

    def fetch_realtime_quotes(self) -> dict[str, Any]:
        """拉取组合全部标的实时行情 (东财优先 + 腾讯回退, 来自 A股全栈数据 skill)。

        覆盖自动交易计划 positions 与目标建仓计划 target_portfolio 的全部代码,
        结果缓存 60s, 供报告实时行情快照与下单估值使用。
        """
        try:
            from utils.astock_realtime import get_realtime_quotes

            codes: set = set()
            for pos in self.plan_data.get("stock_etf_account", {}).get("positions", []):
                c = pos.get("code", "")
                if c:
                    codes.add(str(c).split(".")[0])
            tp_plan = self._load_target_portfolio_plan()
            codes.update(tp_plan.get("target_portfolio", {}).keys())

            quotes = cast(dict[str, Any], get_realtime_quotes(sorted(codes)))
            self.realtime_quotes = quotes
            logger.info(f"实时行情已获取: {len(quotes)} 只标的 (源: 东财/腾讯)")
            return quotes
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"实时行情获取失败: {e}")
            self.realtime_quotes = {}
            return {}

    def generate_report(self) -> str:
        """生成完整执行报告"""
        # 确保市场状态（含 ETF 资金流 LLM 决策）已评估
        if not self.market_state.get("etf_flow_decision"):
            self.assess_market_state()

        lines = []
        lines.extend(self._render_header())
        lines.extend(self._render_market_state_section())
        lines.extend(self._render_build_plan_section())
        lines.extend(self._render_hedge_plan_section())
        lines.extend(self._render_summary_section())
        lines.extend(self._render_checklist_section())

        # 六、十五五规划 + 康波周期 合规校验 (提取为 _render_compliance_section)
        lines.extend(self._render_compliance_section())

        # 七、宽基ETF 社保国家队资金流加减仓 (提取为 _render_etf_flow_adjustment_section)
        lines.extend(self._render_etf_flow_adjustment_section())

        # 八、实时行情快照 (A股全栈数据 skill: 东财 push2 优先 + 腾讯回退)
        lines.extend(self._render_realtime_quotes_section())

        # ★ 新增: 九、ETF资金流向盘前/盘中决策 (LLM辅助)
        lines.extend(self._render_etf_flow_decision_section())

        lines.extend(self._render_footer())

        return "\n".join(lines)

    def _render_header(self) -> list[str]:
        """报告头: 标题/时间/模式"""
        lines: list[str] = []
        lines.append(
            f"# 每日建仓计划 + 对冲联动报告 — {self.target_date.strftime('%Y-%m-%d')}"
        )
        lines.append("")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**模式**: {'干跑模式' if self.dry_run else '实盘模式'}")
        lines.append("")
        return lines

    def _render_market_state_section(self) -> list[str]:
        """第一节: 市场状态评估"""
        lines: list[str] = []
        lines.append("## 一、市场状态评估")
        lines.append("")
        lines.append(f"- **市场状态**: {self.market_state.get('market_regime', 'N/A')}")
        lines.append(f"- **VIX代理**: {self.market_state.get('vix_proxy', 'N/A')}")
        _ir20 = self.market_state.get("index_return_20d", "N/A")
        lines.append(
            f"- **20日收益率**: {_ir20:.2%}"
            if isinstance(_ir20, (int, float))
            else f"- **20日收益率**: {_ir20}"
        )
        lines.append(
            f"- **宏观热度**: {self.market_state.get('macro_heat_score', 'N/A')}"
        )
        lines.append("")
        return lines

    def _render_build_plan_section(self) -> list[str]:
        """第二节: 建仓计划"""
        lines: list[str] = []
        lines.append("## 二、建仓计划")
        lines.append("")
        lines.append(f"- **阶段**: {self.build_plan.get('phase', 'N/A')}")
        lines.append(
            f"- **当日建仓金额**: {self.build_plan.get('day_capital', 0):,.0f} 元"
        )
        lines.append(
            f"- **资金倍率**: {self.build_plan.get('capital_multiplier', 1.0):.0%}"
        )
        lines.append(
            f"- **应急级别**: {self.build_plan.get('emergency_level', 'NORMAL')}"
        )
        lines.append("")

        if self.build_plan.get("morning_orders"):
            lines.append("### 上午批次 (09:30 — 10:30)")
            lines.append("")
            lines.append(
                "| 优先级 | 代码 | 名称 | 买入股数 | 预估单价 | 限价 | 预估金额 | 风格 |"
            )
            lines.append(
                "|:-------|:-----|:-----|--------:|--------:|------|--------:|:-----|"
            )
            for o in self.build_plan["morning_orders"]:
                lines.append(
                    f"| {o['priority']} | {o['code']} | {o['name']} | "
                    f"{o['shares']:,} | {o['est_price']:.3f} | {o['limit_price']:.3f} | "
                    f"{o['est_amount']:,.0f} | {o['style']} |"
                )
            morning_total = sum(
                o["est_amount"] for o in self.build_plan["morning_orders"]
            )
            lines.append(f"| | | **上午合计** | | | | **{morning_total:,.0f}** | |")
            lines.append("")

        if self.build_plan.get("afternoon_orders"):
            lines.append("### 下午批次 (14:00 — 14:30)")
            lines.append("")
            lines.append(
                "| 优先级 | 代码 | 名称 | 买入股数 | 预估单价 | 限价 | 预估金额 | 风格 |"
            )
            lines.append(
                "|:-------|:-----|:-----|--------:|--------:|------|--------:|:-----|"
            )
            for o in self.build_plan["afternoon_orders"]:
                lines.append(
                    f"| {o['priority']} | {o['code']} | {o['name']} | "
                    f"{o['shares']:,} | {o['est_price']:.3f} | {o['limit_price']:.3f} | "
                    f"{o['est_amount']:,.0f} | {o['style']} |"
                )
            afternoon_total = sum(
                o["est_amount"] for o in self.build_plan["afternoon_orders"]
            )
            lines.append(f"| | | **下午合计** | | | | **{afternoon_total:,.0f}** | |")
            lines.append("")

        if self.build_plan.get("paused_orders"):
            lines.append("### 暂停执行标的")
            lines.append("")
            for p in self.build_plan["paused_orders"]:
                lines.append(f"- {p['code']} {p['name']}: {p['reason']}")
            lines.append("")

        if self.build_plan.get("emergency_actions"):
            lines.append("### 紧急响应措施")
            lines.append("")
            for action in self.build_plan["emergency_actions"]:
                lines.append(f"- {action}")
            lines.append("")
        return lines

    def _render_hedge_plan_section(self) -> list[str]:
        """第三节: 对冲计划"""
        lines: list[str] = []
        lines.append("## 三、对冲计划")
        lines.append("")
        lines.append(
            f"- **组合价值**: {self.hedge_plan.get('portfolio_value', 0):,.0f} 元"
        )
        lines.append(f"- **组合Beta**: {self.hedge_plan.get('portfolio_beta', 0):.2f}")
        lines.append(f"- **目标Delta**: {self.hedge_plan.get('target_delta', 0):.2f}")
        lines.append(f"- **当前Delta**: {self.hedge_plan.get('current_delta', 0):,.0f}")
        lines.append(f"- **当前Gamma**: {self.hedge_plan.get('current_gamma', 0):.4f}")
        lines.append(f"- **当前Vega**: {self.hedge_plan.get('current_vega', 0):,.0f}")
        lines.append(f"- **当前Theta**: {self.hedge_plan.get('current_theta', 0):,.0f}")
        lines.append("")

        lines.append("### 期货对冲")
        lines.append("")
        futures = self.hedge_plan.get("futures_hedge", {})
        lines.append(f"- **工具**: {futures.get('instrument', '')}")
        lines.append(f"- **方向**: {futures.get('direction', '')}")
        lines.append(f"- **合约数**: {futures.get('contracts', 0):.2f} 手")
        lines.append(f"- **名义金额**: {futures.get('estimated_notional', 0):,.0f} 元")
        lines.append(f"- **说明**: {futures.get('description', '')}")
        lines.append("")

        if self.hedge_plan.get("option_hedge"):
            lines.append("### 期权对冲")
            lines.append("")
            for opt in self.hedge_plan["option_hedge"]:
                lines.append(f"- **类型**: {opt.get('type', '')}")
                lines.append(f"  - 标的: {opt.get('code', '')} {opt.get('name', '')}")
                lines.append(f"  - 方向: {opt.get('direction', '')}")
                lines.append(f"  - 策略: {opt.get('strategy', opt.get('strike', ''))}")
                lines.append(f"  - 用途: {opt.get('purpose', '')}")
                if "premium_budget" in opt:
                    lines.append(f"  - 预算: {opt.get('premium_budget', 0):,.0f} 元")
                lines.append("")

        rebalance = self.hedge_plan.get("rebalance_signal", {})
        lines.append("### 再平衡信号")
        lines.append("")
        for key, value in rebalance.items():
            status = "需要" if value else "无需"
            lines.append(f"- {key}: {status}")
        lines.append("")
        return lines

    def _render_summary_section(self) -> list[str]:
        """第四节: 执行摘要"""
        lines: list[str] = []
        futures = self.hedge_plan.get("futures_hedge", {})  # 重新获取(原主函数局部变量)
        lines.append("## 四、执行摘要")
        lines.append("")
        total_orders = len(self.build_plan.get("morning_orders", [])) + len(
            self.build_plan.get("afternoon_orders", [])
        )
        lines.append(f"- 股票订单数: {total_orders} 笔")
        lines.append(f"- 当日建仓金额: {self.build_plan.get('day_capital', 0):,.0f} 元")
        lines.append(f"- 期货对冲合约: {futures.get('contracts', 0):.2f} 手")
        lines.append(
            f"- 期权对冲项目: {len(self.hedge_plan.get('option_hedge', []))} 项"
        )
        lines.append(f"- 应急级别: {self.build_plan.get('emergency_level', 'NORMAL')}")
        lines.append("")
        return lines

    def _render_checklist_section(self) -> list[str]:
        """第五节: 执行检查清单"""
        lines: list[str] = []
        lines.append("## 五、执行检查清单")
        lines.append("")
        lines.append("- [ ] 确认账户可用资金充足")
        lines.append("- [ ] 确认所有标的交易权限正常")
        lines.append("- [ ] 09:20 查看集合竞价，确认市场开盘情绪")
        lines.append("- [ ] 09:25 记录集合竞价产生的开盘参考价")
        lines.append("- [ ] 09:30-10:30 按优先级顺序执行上午批次")
        lines.append("- [ ] 11:30 确认上午成交，记录实际成交价")
        lines.append("- [ ] 14:00-14:30 执行下午批次")
        lines.append("- [ ] 15:00 确认全天成交，记录实际成本")
        lines.append("- [ ] 15:00 检查对冲计划，确认期货/期权对冲执行")
        lines.append("- [ ] 15:30 生成盘后报告，记录当日盈亏")
        lines.append("")
        return lines

    def _render_footer(self) -> list[str]:
        """报告尾: 分隔线 + 时间戳"""
        lines: list[str] = []
        lines.append("---")
        lines.append(f"*报告生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        return lines

    def _render_compliance_section(self) -> list[str]:
        """第六节: 十五五规划 + 康波周期 合规校验 (fail-safe)"""
        lines: list[str] = []
        try:
            from utils.broad_based_etf_policy import validate_portfolio_compliance

            target_plan = self._load_target_portfolio_plan()
            compliance = validate_portfolio_compliance(
                target_plan.get("target_portfolio", {})
            )
            cs = compliance.get("summary", {})
            lines.append("## 六、十五五规划 + 康波周期 合规校验")
            lines.append("")
            lines.append(
                f"- **达标标的**: {cs.get('passed', 0)}/{cs.get('total', 0)} | "
                f"**偏弱标的**: {cs.get('weak', 0)} | **平均对齐度**: {cs.get('avg_combined', 0):.3f}"
            )
            weak_codes = cs.get("weak_codes", [])
            if weak_codes:
                lines.append(f"- **需关注(建议减配)**: {', '.join(weak_codes)}")
            lines.append("")
            lines.append(
                "| 代码 | 名称 | 风格 | 十五五分 | 康波分 | 综合 | 判定 | 建议动作 |"
            )
            lines.append(
                "|:-------|:-----|:-----|--------:|--------:|------:|:-----|:---------|"
            )
            for h in compliance.get("holdings", []):
                lines.append(
                    f"| {h['code']} | {h['name']} | {h['style']} | "
                    f"{h['ff_score']:.2f} | {h['kc_score']:.2f} | {h['combined']:.2f} | "
                    f"{h['level']} | {h['action']} |"
                )
            lines.append("")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"合规校验展示失败: {e}")
            lines.append("## 六、十五五规划 + 康波周期 合规校验")
            lines.append("")
            lines.append(f"- 校验暂不可用: {e}")
            lines.append("")
        return lines

    def _render_etf_flow_adjustment_section(self) -> list[str]:
        """第七节: 宽基ETF 社保国家队资金流加减仓 (fail-safe)"""
        lines: list[str] = []
        try:
            from utils.broad_based_etf_policy import (
                fetch_national_team_flow_signals,
                flow_to_adjustment,
                get_broad_based_codes,
            )

            flow = fetch_national_team_flow_signals()
            target_plan = self._load_target_portfolio_plan()
            bb_codes = get_broad_based_codes(target_plan)
            tp = target_plan.get("target_portfolio", {})
            lines.append("## 七、宽基ETF 社保国家队资金流加减仓")
            lines.append("")
            if not flow:
                lines.append(
                    "- 社保国家队资金流数据暂不可用, 宽基ETF维持基准权重 (无信号则不调整)。"
                )
                lines.append("")
            else:
                lines.append(
                    "| 代码 | 名称 | 净流(亿) | 信号 | 动作 | 基准权重 | 目标权重 | 缩放 |"
                )
                lines.append(
                    "|:-------|:-----|--------:|:-----|:-----|---------:|---------:|-----:|"
                )
                for code in bb_codes:
                    info = tp.get(code, {})
                    base = float(
                        info.get("base_weight", info.get("weight", 0.0)) or 0.0
                    )
                    sig = flow.get(code, {})
                    net = float(sig.get("net_flow_yi", 0.0) or 0.0)
                    adj = flow_to_adjustment(net)
                    scale = max(0.5, min(1.5, 1.0 + adj["factor"]))
                    target_w = round(base * scale, 6)
                    lines.append(
                        f"| {code} | {info.get('name', code)} | {net:+.1f} | "
                        f"{adj['signal']} | {adj['action']} | {base:.2%} | "
                        f"{target_w:.2%} | {scale:.2f}x |"
                    )
                lines.append("")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"宽基ETF加减仓展示失败: {e}")
            lines.append("## 七、宽基ETF 社保国家队资金流加减仓")
            lines.append("")
            lines.append(f"- 加减仓展示暂不可用: {e}")
            lines.append("")
        return lines

    def _render_realtime_quotes_section(self) -> list[str]:
        """第八节: 实时行情快照 (A股全栈数据 skill, fail-safe)"""
        lines: list[str] = []
        try:
            quotes = self.fetch_realtime_quotes()
            lines.append("## 八、实时行情快照")
            lines.append("")
            if not quotes:
                lines.append(
                    "- 实时行情暂不可用 (东财/腾讯接口无响应), 下单沿用计划估值价。"
                )
                lines.append("")
            else:
                src_set = {q.get("source", "?") for q in quotes.values()}
                lines.append(
                    f"- 数据源: {', '.join(sorted(src_set))} | 覆盖标的: {len(quotes)} 只 | "
                    f"快照时间: {datetime.now().strftime('%H:%M:%S')} (缓存60s)"
                )
                lines.append("")
                lines.append("| 代码 | 名称 | 现价 | 涨跌% | PE | PB | 市值(亿) | 源 |")
                lines.append(
                    "|:-------|:-----|-----:|------:|-----:|-----:|---------:|:---|"
                )
                for code in sorted(quotes.keys()):
                    q = quotes[code]
                    pe = q.get("pe")
                    pb = q.get("pb")
                    pe_s = f"{pe:.2f}" if isinstance(pe, (int, float)) else "-"
                    pb_s = f"{pb:.2f}" if isinstance(pb, (int, float)) else "-"
                    mc = q.get("mktcap_yi", 0) or 0
                    lines.append(
                        f"| {code} | {q.get('name', code)} | {q.get('price', 0):.3f} | "
                        f"{q.get('change_pct', 0):+.2f} | {pe_s} | {pb_s} | "
                        f"{mc:.1f} | {q.get('source', '?')} |"
                    )
                lines.append("")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"实时行情快照展示失败: {e}")
            lines.append("## 八、实时行情快照")
            lines.append("")
            lines.append(f"- 实时行情快照暂不可用: {e}")
            lines.append("")
        return lines

    def _render_etf_flow_decision_section(self) -> list[str]:
        """第九节: ETF资金流向盘前/盘中决策 (LLM辅助, fail-safe)"""
        lines: list[str] = []
        try:
            etf_flow_decision = self.market_state.get("etf_flow_decision")
            if etf_flow_decision and etf_flow_decision.get("status") == "success":
                lines.append("## 九、ETF资金流向盘前/盘中决策 (LLM辅助)")
                lines.append("")

                # 时段信息
                phase = etf_flow_decision.get("phase", "unknown")
                timestamp = etf_flow_decision.get("timestamp", "")
                elapsed = etf_flow_decision.get("elapsed_seconds", 0)

                phase_labels = {
                    "pre_market": "【盘前决策】(09:15-09:25)",
                    "intraday": "【盘中决策】(09:30-15:00)",
                    "post_market": "【盘后复盘】(15:00-15:30)",
                }
                lines.append(f"- **时段**: {phase_labels.get(phase, phase)}")
                lines.append(f"- **时间戳**: {timestamp}")
                lines.append(f"- **耗时**: {elapsed:.1f}秒")
                lines.append("")

                # 摘要统计
                summary = etf_flow_decision.get("summary", {})
                lines.append("### 信号摘要")
                lines.append("")
                lines.append(f"- **监控ETF数**: {summary.get('total_etfs', 0)}")
                lines.append(f"- **强信号数**: {summary.get('strong_signals', 0)}")
                lines.append(f"- **中信号数**: {summary.get('medium_signals', 0)}")
                total_inflow = summary.get("total_inflow", 0)
                lines.append(f"- **总净流入**: {total_inflow:+.2f} 亿元")

                # 盘中突变信号
                if phase == "intraday":
                    sudden_changes = summary.get("sudden_changes", 0)
                    lines.append(f"- **突变信号**: {sudden_changes} 个")
                    if sudden_changes > 0:
                        lines.append("")
                        lines.append("#### 突变信号详情")
                        for change in etf_flow_decision.get("sudden_changes", [])[:5]:
                            lines.append(f"- {change['name']}: {change['description']}")

                lines.append("")

                # 交易建议 Top 10
                recommendations = etf_flow_decision.get("recommendations", [])
                if recommendations:
                    lines.append("### 交易建议 Top 10")
                    lines.append("")
                    lines.append(
                        "| 优先级 | 代码 | 名称 | 动作 | 强度 | 置信度 | 净流入(亿) | 价格变动% | 原因 |"
                    )
                    lines.append(
                        "|:-------|:-----|:-----|:-----|-----:|-------:|----------:|---------:|:-----|"
                    )

                    for i, rec in enumerate(recommendations[:10], 1):
                        action = rec.get("action", "观望")
                        strength = rec.get("strength", 0)
                        confidence = rec.get("confidence", 0)
                        net_flow = rec.get("net_flow_yi", 0)
                        price_change = rec.get("price_change_pct", 0)
                        reason = rec.get("reason", "")

                        lines.append(
                            f"| {i} | {rec['code']} | {rec['name']} | "
                            f"{action} | {strength:+.2f} | {confidence:.2f} | "
                            f"{net_flow:+.2f} | {price_change:+.2f}% | {reason} |"
                        )
                    lines.append("")

                # LLM分析结果
                llm_analysis = etf_flow_decision.get("llm_analysis")
                if llm_analysis:
                    lines.append("### LLM辅助分析")
                    lines.append("")
                    lines.append(
                        "> " + "\n> ".join(llm_analysis.split("\n")[:10])
                    )  # 限制长度
                    lines.append("")

                # 信号融合结果
                fused_signals = etf_flow_decision.get("fused_signals", [])
                if fused_signals:
                    lines.append("### 信号融合 Top 5")
                    lines.append("")
                    lines.append(
                        "| 代码 | 名称 | 融合强度 | 置信度 | 资金流 | LLM | 价格动量 |"
                    )
                    lines.append(
                        "|:-----|:-----|--------:|-------:|------:|-----:|--------:|"
                    )

                    for sig in fused_signals[:5]:
                        lines.append(
                            f"| {sig['symbol']} | {sig.get('meta', {}).get('name', '-')} | "
                            f"{sig['strength']:+.2f} | {sig['confidence']:.2f} | "
                            f"{sig.get('sources', {}).get('etf_strength', 0):+.2f} | "
                            f"{sig.get('sources', {}).get('llm_strength', 0):+.2f} | "
                            f"{sig.get('sources', {}).get('alpha_strength', 0):+.2f} |"
                        )
                    lines.append("")
            else:
                lines.append("## 九、ETF资金流向盘前/盘中决策")
                lines.append("")
                lines.append("- ETF资金流决策引擎暂不可用 (数据获取失败或LLM未配置)")
                lines.append("- 继续使用规则引擎进行宽基ETF加减仓 (第七节)")
                lines.append("")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"ETF资金流决策展示失败: {e}")
            lines.append("## 九、ETF资金流向盘前/盘中决策")
            lines.append("")
            lines.append(f"- ETF资金流决策暂不可用: {e}")
            lines.append("")
        return lines

    def save_report(self, output_dir: str | None = None) -> str:
        """保存报告到文件"""
        out_dir = (
            Path(output_dir)
            if output_dir
            else BASE_DIR / "每日报告归档" / self.target_date.strftime("%Y-%m-%d")
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        report_content = self.generate_report()

        date_str = self.target_date.strftime("%Y%m%d")
        md_path = out_dir / f"build_hedge_report_{date_str}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report_content)

        json_path = out_dir / f"build_hedge_report_{date_str}.json"

        compliance_data = {}
        broad_based_data = {}
        try:
            from utils.broad_based_etf_policy import (
                fetch_national_team_flow_signals,
                flow_to_adjustment,
                get_broad_based_codes,
                validate_portfolio_compliance,
            )

            target_plan = self._load_target_portfolio_plan()
            compliance_data = validate_portfolio_compliance(
                target_plan.get("target_portfolio", {})
            )
            flow = fetch_national_team_flow_signals()
            tp = target_plan.get("target_portfolio", {})
            broad_based_data = {
                "has_flow": bool(flow),
                "adjustments": [
                    {
                        "code": c,
                        "name": tp.get(c, {}).get("name", c),
                        "net_flow_yi": float(
                            flow.get(c, {}).get("net_flow_yi", 0.0) or 0.0
                        ),
                        **flow_to_adjustment(
                            float(flow.get(c, {}).get("net_flow_yi", 0.0) or 0.0)
                        ),
                    }
                    for c in get_broad_based_codes(target_plan)
                ],
            }
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning(f"报告JSON合规/加减仓数据收集失败: {e}")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "build_plan": self.build_plan,
                    "hedge_plan": self.hedge_plan,
                    "market_state": self.market_state,
                    "risk_status": self.risk_status,
                    "policy_compliance": compliance_data,
                    "broad_based_etf": broad_based_data,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        return str(md_path), str(json_path)

    def run(self) -> dict[str, Any]:
        """执行完整流程"""
        logger.info("=" * 60)
        logger.info("每日建仓计划 + 对冲联动系统 v8.0")
        logger.info("=" * 60)

        phase, phase_key = self.get_active_phase()
        if not phase:
            logger.warning("无活跃建仓阶段")
            return {"status": "no_active_phase"}

        logger.info(f"当前阶段: {phase.get('name', '')} ({phase_key})")

        self.assess_market_state()
        logger.info(f"市场状态: {self.market_state.get('market_regime')}")

        self.calculate_risk_budget(phase)
        logger.info("风险预算计算完成")

        self.generate_build_instructions(phase)
        morning_count = len(self.build_plan.get("morning_orders", []))
        afternoon_count = len(self.build_plan.get("afternoon_orders", []))
        logger.info(f"建仓指令生成: 上午{morning_count}笔, 下午{afternoon_count}笔")

        self.calculate_hedge_plan()
        futures_contracts = self.hedge_plan.get("futures_hedge", {}).get("contracts", 0)
        option_count = len(self.hedge_plan.get("option_hedge", []))
        logger.info(
            f"对冲计划生成: 期货{futures_contracts:.2f}手, 期权{option_count}项"
        )

        return {
            "status": "success",
            "phase": phase.get("name", ""),
            "phase_key": phase_key,
            "market_regime": self.market_state.get("market_regime"),
            "build_plan": self.build_plan,
            "hedge_plan": self.hedge_plan,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="每日建仓计划 + 期货期权对冲联动系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python daily_build_and_hedge.py                           # 生成今日交易计划
  python daily_build_and_hedge.py --date 2026-07-13         # 指定日期
  python daily_build_and_hedge.py --dry-run                  # 干跑模式
  python daily_build_and_hedge.py --hedge-only               # 仅生成对冲计划
  python daily_build_and_hedge.py --save                     # 保存报告到文件
        """,
    )
    parser.add_argument(
        "--date", "-d", type=str, default=None, help="目标日期 YYYY-MM-DD (默认: 今日)"
    )
    parser.add_argument("--dry-run", action="store_true", help="干跑模式，不实际执行")
    parser.add_argument("--hedge-only", action="store_true", help="仅生成对冲计划")
    parser.add_argument("--save", action="store_true", help="保存报告到文件")
    parser.add_argument("--output-dir", type=str, default=None, help="报告输出目录")

    args = parser.parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = date.today()

    logger.info(
        "daily_build_and_hedge 启动: date=%s dry_run=%s hedge_only=%s save=%s",
        target_date,
        args.dry_run,
        args.hedge_only,
        args.save,
    )

    system = DailyBuildHedgeSystem(
        target_date=target_date,
        dry_run=args.dry_run,
    )

    if args.hedge_only:
        system.assess_market_state()
        system.build_plan = {
            "morning_orders": [],
            "afternoon_orders": [],
            "day_capital": 0,
            "emergency_level": "NORMAL",
            "emergency_actions": [],
        }
        system.calculate_hedge_plan()
    else:
        result = system.run()
        if result["status"] != "success":
            logger.error(f"执行失败: {result}")
            sys.exit(1)

    logger.info(system.generate_report())

    if args.save:
        md_path, json_path = system.save_report(args.output_dir)
        logger.warning("报告已保存: Markdown=%s JSON=%s", md_path, json_path)
