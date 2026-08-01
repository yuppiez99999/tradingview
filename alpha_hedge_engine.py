"""
亚洲宏观多策略专户 - 期权量化执行引擎 (集成版)
=============================================

整合 QMT Framework 架构 + THSRealBroker 真实下单
核心目标: 严格控制最大回撤 < 15%，自动化收取 Theta 时间价值
"""

import logging
import math
import os
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("alpha_hedge_engine")

# 回撤分级熔断（顶级对冲基金整改：回撤必须是硬控制而非软预测）
try:
    from utils.drawdown_breaker import DrawdownCircuitBreaker, DrawdownDecision
except Exception as e:
    DrawdownCircuitBreaker = None
    DrawdownDecision = None
    logger.debug(f"DrawdownBreaker 模块加载失败, 回撤熔断降级: {e}")

# 全局风控熔断（保证金三级协议）
try:
    from utils.kill_switch import KillSwitch
except Exception as e:
    KillSwitch = None
    import logging as _logging

    _logging.getLogger("alpha_hedge_engine").error("KillSwitch 模块加载失败, 风控熔断协议不可用: %s", e)

# B1.3: 从 config/risk_params.yaml 统一读取回撤上限 (fail-safe 兜底 0.15)
try:
    from utils.risk_params import get_max_drawdown_limit as _get_max_drawdown_limit
    _DEFAULT_MAX_DRAWDOWN_LIMIT = _get_max_drawdown_limit()
except Exception as e:
    _DEFAULT_MAX_DRAWDOWN_LIMIT = 0.15


class RiskControl:
    """统一风控检查器，集成 DrawdownCircuitBreaker + KillSwitch 两套熔断协议"""

    def __init__(self, margin_limit=0.50, max_drawdown_limit=None, fat_finger_limit=500000):
        # B1.3: 默认值从 config/risk_params.yaml 读取
        if max_drawdown_limit is None:
            max_drawdown_limit = _DEFAULT_MAX_DRAWDOWN_LIMIT
        self.margin_limit = margin_limit
        self.max_drawdown_limit = max_drawdown_limit
        self.fat_finger_limit = fat_finger_limit
        self.drawdown_breaker = (
            DrawdownCircuitBreaker(max_drawdown=max_drawdown_limit) if DrawdownCircuitBreaker else None
        )
        # 集成 utils/kill_switch 的三级保证金熔断协议 (L1=50%, L2=65%, L3=75%)
        self.kill_switch = KillSwitch(margin_limit=margin_limit) if KillSwitch else None

    def check_drawdown(self, current_drawdown: float):
        """组合层面回撤分级熔断检查（current_drawdown 为负值，如 -0.10）。

        Returns:
            DrawdownDecision；熔断模块不可用时返回 None
        """
        if self.drawdown_breaker is None:
            return None
        decision = self.drawdown_breaker.evaluate(current_drawdown)
        if decision.level.value in ("FORCE_HEDGE", "HALT"):
            logger.error(
                "【回撤熔断】当前回撤 %.2f%%，级别 %s：%s",
                current_drawdown * 100,
                decision.level.value,
                decision.action,
            )
        elif decision.level.value == "REDUCE":
            logger.warning(
                "【回撤减仓】当前回撤 %.2f%%，级别 %s：%s",
                current_drawdown * 100,
                decision.level.value,
                decision.action,
            )
        return decision

    def check_kill_switch(self, margin_usage: float) -> bool:
        """统一保证金熔断检查，使用 utils/kill_switch 三级协议。

        KillSwitch.check_margin_status 返回结构（已修复）:
        {level: int, can_open: bool, can_trade: bool, action: str, ...}
        """
        if self.kill_switch is None:
            # 降级：KillSwitch 不可用时, 用简单硬阈值防护
            if margin_usage >= 0.75:
                logger.error(
                    "【严重警报】保证金使用率超 75%%！触发系统强平并熔断所有开仓权限！(KillSwitch不可用, 降级防护)"
                )
                return False
            elif margin_usage >= 0.60:
                logger.warning(
                    "【警告】保证金使用率达 %.0f%%，系统已锁死开仓权限，仅允许平仓。(KillSwitch不可用, 降级防护)",
                    margin_usage * 100,
                )
                return False
            return True

        status = self.kill_switch.check_margin_status(margin_usage)

        # M13 修复: 使用 .get() 防止 KeyError 导致风控检查本身崩溃
        can_trade = status.get("can_trade", False)
        level = status.get("level", 0)
        action = status.get("action", "unknown")

        if not can_trade:
            logger.error("【KillSwitch-L%d】保证金%.1f%% 触发熔断: %s — 不可交易", level, margin_usage * 100, action)
            # 执行熔断协议（fail-fast: 无 broker_callback 时抛 RuntimeError）
            if level >= 1:
                try:
                    self.kill_switch.execute_kill_switch(level)
                    logger.error("【KillSwitch-L%d】熔断协议已执行", level)
                except RuntimeError as e:
                    logger.critical("【KillSwitch-L%d】熔断协议执行失败: %s", level, e)
            return False

        if not status.get("can_open", True):
            logger.warning("【KillSwitch-L%d】保证金%.1f%%: %s — 仅允许平仓", level, margin_usage * 100, action)
            return False

        return True

    def check_liquidity_spread(self, ask_price: float, bid_price: float) -> bool:
        if bid_price <= 0:
            return False
        spread_ratio = (ask_price - bid_price) / bid_price
        if spread_ratio > 0.05:
            logger.warning(f"【拦截】买卖价差大于 5% ({spread_ratio:.2%})，拒绝交易。")
            return False
        return True

    def check_fat_finger(self, order_amount: float) -> bool:
        if order_amount > self.fat_finger_limit:
            logger.warning(f"【拦截】单笔金额 {order_amount:.2f} 超过防胖手指限额 {self.fat_finger_limit:.2f}")
            return False
        return True


class OptionContractFinder:
    @staticmethod
    def find_call_contract(underlying_code: str, strike_price: float, month: str = "next") -> str:
        return f"{underlying_code[:6]}-C-{int(strike_price)}"

    @staticmethod
    def find_put_contract(underlying_code: str, strike_price: float, month: str = "far") -> str:
        return f"{underlying_code[:6]}-P-{int(strike_price)}"


class AlphaHedgeEngine:
    def __init__(self, account_id: str, broker=None, mode: str = "sim"):
        self.account_id = account_id
        self.mode = mode
        self.broker = broker

        self.total_aum = 5000000
        self.risk_control = RiskControl()

        self.spot_pool = {
            "dividend_etf": "512890.SH",
            "tech_etf": "588000.SH",
        }

        self._fills: List[Dict] = []
        self._lock = threading.RLock()

    def _execute_order(self, symbol: str, qty: int, side: str, price: float) -> Dict:
        if self.mode == "sim":
            fill = {
                "order_id": f"SIM-{int(time.time() * 1000)}",
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "price": price,
                "status": "FILLED",
                "timestamp": datetime.now().isoformat(),
            }
            with self._lock:
                self._fills.append(fill)
            return fill

        if self.broker:
            return self.broker.place_order(symbol, qty, side, price)

        return {"status": "FAILED", "reason": "无可用经纪商"}

    def execute_covered_call(self):
        logger.info("\n>>> 启动 Theta 引擎：备兑收租模块扫描中...")

        for _name, etf_code in self.spot_pool.items():
            available_volume = self._get_position_volume(etf_code)
            if available_volume == 0:
                logger.warning(f"[备兑开仓] {etf_code} 持仓为0, 跳过")
                continue

            current_price = self._get_last_price(etf_code)
            if current_price <= 0:
                logger.warning(f"[备兑开仓] {etf_code} 当前价格无效 ({current_price}), 跳过")
                continue
            target_strike = current_price * 1.05
            if target_strike <= 0:
                logger.warning(f"[备兑开仓] {etf_code} 目标行权价无效 ({target_strike}), 跳过")
                continue

            target_option = OptionContractFinder.find_call_contract(etf_code, target_strike, month="next")

            ask_price, bid_price = self._get_option_quotes(target_option)

            if not self.risk_control.check_liquidity_spread(ask_price, bid_price):
                continue

            order_volume = int(available_volume / 10000)
            # ER2 修复: 持仓不足静默跳过 — order_volume=0 时不应继续下单
            if order_volume <= 0:
                logger.warning(
                    f"[备兑开仓] {etf_code} 持仓不足 ({available_volume} 股 < 10000 股/张), 无法形成 1 张期权, 跳过"
                )
                continue
            order_amount = order_volume * ask_price * 10000

            if not self.risk_control.check_fat_finger(order_amount):
                continue

            logger.info(f"【执行】对 {etf_code} 卖出 {order_volume} 张 {target_option} @ {ask_price}")

            result = self._execute_order(target_option, order_volume, "SELL_OPEN", ask_price)
            logger.info(f"下单结果: {result.get('status', 'UNKNOWN')}")

    def tail_risk_monitor(self):
        logger.info("\n>>> 启动 Gamma 引擎：尾部风险监控中...")

        tech_etf = self.spot_pool["tech_etf"]

        is_breakdown_ma60 = self._check_tech_breakdown(tech_etf)
        iv_percentile = self._get_iv_percentile(tech_etf)

        logger.info(f"技术面破位: {is_breakdown_ma60}, IV分位: {iv_percentile:.2%}")

        if is_breakdown_ma60 or iv_percentile < 0.10:
            logger.info("【触发对冲】大盘破位或保险极度便宜，启动尾部防御买入 Put！")

            current_price = self._get_last_price(tech_etf)
            # ER2 修复: current_price 零值未保护 — 后续 target_strike 和除零都依赖此值
            if current_price <= 0:
                logger.warning(f"[尾部防御] {tech_etf} 当前价格无效 ({current_price}), 放弃买入 Put")
                return
            target_strike = current_price * 0.95
            # ER2 修复: target_strike 零值未保护 (current_price 极小或 NaN 时)
            if target_strike <= 0 or not math.isfinite(target_strike):
                logger.warning(
                    f"[尾部防御] {tech_etf} 目标行权价无效 ({target_strike}, "
                    f"current_price={current_price}), 放弃买入 Put"
                )
                return

            target_put = OptionContractFinder.find_put_contract(tech_etf, target_strike, month="far")

            ask_price, bid_price = self._get_option_quotes(target_put)

            if not self.risk_control.check_liquidity_spread(ask_price, bid_price):
                return

            # ER2 修复: ask_price 零值未保护 — 防 ZeroDivisionError
            if ask_price <= 0:
                logger.warning(f"[尾部防御] {target_put} 卖一价无效 ({ask_price}), 无法计算仓位, 放弃")
                return

            budget = 20000
            volume = int(budget / (ask_price * 10000))

            if volume <= 0:
                logger.warning(f"【警告】预算 {budget} 不足 (ask_price={ask_price}), 无法购买 Put 合约 {target_put}")
                return

            logger.info(f"【执行】买入 {volume} 张 {target_put} 作为下行保险 @ {ask_price}")

            result = self._execute_order(target_put, volume, "BUY_OPEN", ask_price)
            logger.info(f"下单结果: {result.get('status', 'UNKNOWN')}")

    def execute_options_order(self, symbol: str, qty: int, side: str, price: float) -> Dict:
        """执行期权订单，包含流动性/风控检查"""
        logger.info(f"\n>>> 执行期权订单: {side} {qty}手 {symbol} @ {price}")

        ask_price, bid_price = self._get_option_quotes(symbol)
        if not self.risk_control.check_liquidity_spread(ask_price, bid_price):
            return {"status": "REJECTED", "reason": "流动性不足"}

        multiplier = self._get_option_multiplier(symbol)
        order_amount = qty * price * multiplier
        if not self.risk_control.check_fat_finger(order_amount):
            return {"status": "REJECTED", "reason": "超过防胖手指限额"}

        margin_usage = self._get_margin_usage()
        if not self.risk_control.check_kill_switch(margin_usage):
            return {"status": "REJECTED", "reason": "风控熔断"}

        return self._execute_order(symbol, qty, side, price)

    def monitor_drawdown(self, current_drawdown: float):
        """回撤分级熔断监控：超过强制对冲阈值时自动买入尾部保险。

        Args:
            current_drawdown: 当前组合回撤（负数，如 -0.10 表示回撤 10%）
        Returns:
            熔断决策 dict，或 None
        """
        decision = self.risk_control.check_drawdown(current_drawdown)
        if decision is None:
            return None
        if decision.level.value in ("FORCE_HEDGE", "HALT"):
            logger.info(">>> 回撤熔断触发，启动尾部防御买入 Put...")
            try:
                self.tail_risk_monitor()
            except Exception as e:
                logger.error(f"【回撤熔断】尾部防御执行失败: {e}")
        return decision.to_dict()

    def _get_position_volume(self, symbol: str) -> int:
        # M14 修复: broker 为 None 时返回 0, 而非 1000000
        # (原逻辑返回 1000000 会导致 execute_covered_call 生成 100 张虚假备兑开仓订单)
        if not self.broker:
            return 0
        positions = self.broker.get_positions()
        # 兼容 get_positions() 的多种返回结构: Dict[symbol,int] / Dict[symbol,dict] / List[dict]
        if isinstance(positions, dict):
            pos = positions.get(symbol)
            if isinstance(pos, dict):
                # M14 修复: 找不到 volume 时返回 0, 而非 1000000
                return int(pos.get("volume", 0))
            if isinstance(pos, (int, float)):
                return int(pos)
            return 0
        if isinstance(positions, (list, tuple)):
            for p in positions:
                if isinstance(p, dict) and p.get("symbol") == symbol:
                    return int(p.get("volume", 0))
        return 0

    def _get_last_price(self, symbol: str) -> float:
        if self.broker and hasattr(self.broker, "get_price"):
            return self.broker.get_price(symbol)
        return 1.00

    def _get_option_quotes(self, symbol: str) -> tuple:
        if self.broker and hasattr(self.broker, "get_option_quotes"):
            return self.broker.get_option_quotes(symbol)
        return 0.105, 0.100

    def _get_margin_usage(self) -> float:
        if self.broker and hasattr(self.broker, "get_margin_usage"):
            return self.broker.get_margin_usage()
        return 0.45

    def _check_tech_breakdown(self, symbol: str) -> bool:
        """检查标的是否跌破60日均线（技术面破位信号）

        当 broker 提供 MA60 数据时使用真实数据,
        否则 raise NotImplementedError 拒绝返回假信号。
        """
        if self.broker and hasattr(self.broker, "check_breakdown"):
            return self.broker.check_breakdown(symbol)
        raise NotImplementedError(
            f"_check_tech_breakdown({symbol}): broker 不提供技术面数据, "
            f"拒绝返回硬编码 False 假信号。请接入提供 MA60 数据的 broker。"
        )

    def _get_iv_percentile(self, symbol: str) -> float:
        """获取隐含波动率历史分位

        当 broker 提供 IV 数据时使用真实数据,
        否则 raise NotImplementedError 拒绝返回假数据。
        """
        if self.broker and hasattr(self.broker, "get_iv_percentile"):
            return self.broker.get_iv_percentile(symbol)
        raise NotImplementedError(
            f"_get_iv_percentile({symbol}): broker 不提供 IV 数据, "
            f"拒绝返回硬编码 0.08 假数据。请接入提供 IV 分位数据的 broker。"
        )

    def _get_option_multiplier(self, symbol: str) -> int:
        if symbol.startswith("y"):
            return 10
        if symbol.startswith("512") or symbol.startswith("588"):
            return 10000
        return 10000

    def run_daily_routine(self, current_drawdown: Optional[float] = None):
        logger.info(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动宏观对冲专户执行引擎...")
        # M17 修复: 区分可恢复异常 (log 并继续) 和致命异常 (log 后 re-raise)
        # 原逻辑捕获所有异常后继续执行, 调用者无法感知失败
        try:
            # 顶级对冲基金整改：盘前先评估组合回撤，触发熔断时优先尾部防御
            if current_drawdown is not None:
                self.monitor_drawdown(current_drawdown)
            self.execute_covered_call()
            self.tail_risk_monitor()
            logger.info(">>> 本次轮询执行完毕。状态：安全。")
        except NotImplementedError as e:
            # 可恢复: broker 不提供某些接口, 降级处理
            logger.warning(f"【降级】功能不可用, 跳过: {e}")
        except (KeyError, ValueError, TypeError) as e:
            # 致命: 数据结构异常, 调用者应感知
            logger.error(f"【致命异常】数据/类型错误: {e!s}", exc_info=True)
            raise
        except Exception as e:
            # 其他未知异常: 记录后 re-raise, 不再静默吞掉
            logger.error(f"【系统异常】{e!s}", exc_info=True)
            raise


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # C6 修复: mode 独立于 broker 是否加载, 通过显式参数控制
    # 原 logic: broker 加载成功 → mode="real", 但使用 MockAccount 虚假数据 → 可能基于虚假数据真实下单
    explicit_mode = os.environ.get("ALPHA_HEDGE_MODE", "sim").lower()  # 默认 sim, 需显式设置才进 real

    broker = None
    if explicit_mode == "real":
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("ths_real_broker", "v8.3_institutional/ths_real_broker.py")
            ths_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ths_module)
            THSRealBroker = ths_module.THSRealBroker

            # C6 修复: real 模式必须使用真实账户, 不再使用 MockAccount
            # 请通过 THSRealBroker 的标准初始化方式接入真实账户
            # 以下仅为 broker 加载校验, 实际账户接入应由调用方完成
            if not hasattr(THSRealBroker, "get_positions"):
                logger.error("[C6] THSRealBroker 缺少 get_positions 接口, 降级为 sim 模式")
                explicit_mode = "sim"
            elif not hasattr(THSRealBroker, "get_price"):
                logger.error("[C6] THSRealBroker 缺少 get_price 接口, 降级为 sim 模式")
                explicit_mode = "sim"
            else:
                # 注意: 此处不创建 MockAccount, 真实使用时由调用方传入真实 account
                logger.warning("[C6] real 模式需调用方提供真实 account, 当前未实例化 broker")
                explicit_mode = "sim"  # 安全降级: 未提供真实 account 时不进 real
        except Exception as e:
            logger.warning(f"THSRealBroker 不可用: {e}，将使用模拟模式")
            explicit_mode = "sim"
    else:
        logger.info("ALPHA_HEDGE_MODE=sim (默认), 使用模拟模式")

    engine = AlphaHedgeEngine(account_id="YOUR_ACCOUNT", broker=broker, mode=explicit_mode)

    logger.info("\n" + "=" * 60)
    logger.info("亚洲宏观多策略专户 - 期权量化执行引擎")
    logger.info("=" * 60)

    # NOTE: 硬编码测试订单已移除，避免 mode="real" 时意外下单。
    # 如需测试，请用 mode="sim" 并手动调用 execute_options_order。
    # 示例: engine = AlphaHedgeEngine(..., mode="sim")
    #       engine.execute_options_order("y2608-C-9000", 1, "BUY_OPEN", 512.5)

    logger.info("\n引擎已就绪 (mode=%s)，未执行任何订单。" % engine.mode)

    logger.info("\n" + "=" * 60)
    logger.info("执行完毕")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
