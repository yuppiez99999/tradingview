"""
亚洲宏观多策略专户 - 期权量化执行引擎 (集成版)
=============================================

整合 QMT Framework 架构 + THSRealBroker 真实下单
核心目标: 严格控制最大回撤 < 15%，自动化收取 Theta 时间价值
"""

import logging
import math
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("alpha_hedge_engine")

# 回撤分级熔断（顶级对冲基金整改：回撤必须是硬控制而非软预测）
try:
    from utils.drawdown_breaker import DrawdownCircuitBreaker, DrawdownDecision
except Exception as e:
    DrawdownCircuitBreaker = None
    DrawdownDecision = None
    logger.debug("DrawdownBreaker 模块加载失败, 回撤熔断降级: %s", e)

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
except Exception:
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
        # BUG-2 修复: NaN 报价 fail-open — NaN > 0.05 为 False 导致返回 True
        # 入口校验 ask/bid 为有限正数
        if not (math.isfinite(ask_price) and math.isfinite(bid_price)
                and bid_price > 0 and ask_price > 0):
            logger.warning("【拦截】买卖报价无效 (ask=%s, bid=%s), 拒绝交易", ask_price, bid_price)
            return False
        spread_ratio = (ask_price - bid_price) / bid_price
        if spread_ratio > 0.05:
            logger.warning("【拦截】买卖价差大于 5%% (%.2f%%)，拒绝交易。", spread_ratio * 100)
            return False
        return True

    def check_fat_finger(self, order_amount: float) -> bool:
        if order_amount > self.fat_finger_limit:
            logger.warning("【拦截】单笔金额 %.2f 超过防胖手指限额 %.2f", order_amount, self.fat_finger_limit)
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
    def __init__(self, account_id: str, broker=None, mode: str = "sim",
                 total_aum: float = None):
        self.account_id = account_id
        self.mode = mode
        self.broker = broker

        # BUG-4 修复: AUM 不再硬编码 500万, 优先从 broker 账户信息读取
        if total_aum is not None:
            self.total_aum = total_aum
        elif broker and hasattr(broker, "get_total_aum"):
            try:
                self.total_aum = broker.get_total_aum()
            except Exception as e:
                logger.warning("从 broker 获取 AUM 失败 (%s), 使用默认值 5000000", e)
                self.total_aum = 5000000
        else:
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
                logger.warning("[备兑开仓] %s 持仓为0, 跳过", etf_code)
                continue

            current_price = self._get_last_price(etf_code)
            # BUG-1 修复: NaN 绕过校验 — NaN <= 0 为 False, NaN 会传播到 int(NaN) 崩溃
            if current_price is None or current_price <= 0 or not math.isfinite(current_price):
                logger.warning("[备兑开仓] %s 当前价格无效 (%s), 跳过", etf_code, current_price)
                continue
            target_strike = current_price * 1.05
            if target_strike <= 0 or not math.isfinite(target_strike):
                logger.warning("[备兑开仓] %s 目标行权价无效 (%s), 跳过", etf_code, target_strike)
                continue

            target_option = OptionContractFinder.find_call_contract(etf_code, target_strike, month="next")

            ask_price, bid_price = self._get_option_quotes(target_option)

            if not self.risk_control.check_liquidity_spread(ask_price, bid_price):
                continue

            order_volume = int(available_volume / 10000)
            # ER2 修复: 持仓不足静默跳过 — order_volume=0 时不应继续下单
            if order_volume <= 0:
                logger.warning(
                    "[备兑开仓] %s 持仓不足 (%s 股 < 10000 股/张), 无法形成 1 张期权, 跳过",
                    etf_code, available_volume,
                )
                continue
            order_amount = order_volume * ask_price * 10000

            if not self.risk_control.check_fat_finger(order_amount):
                continue

            logger.info("【执行】对 %s 卖出 %s 张 %s @ %s", etf_code, order_volume, target_option, ask_price)

            result = self._execute_order(target_option, order_volume, "SELL_OPEN", ask_price)
            logger.info("下单结果: %s", result.get('status', 'UNKNOWN'))

    def tail_risk_monitor(self):
        logger.info("\n>>> 启动 Gamma 引擎：尾部风险监控中...")

        tech_etf = self.spot_pool["tech_etf"]

        is_breakdown_ma60 = self._check_tech_breakdown(tech_etf)
        iv_percentile = self._get_iv_percentile(tech_etf)

        logger.info("技术面破位: %s, IV分位: %.2f%%", is_breakdown_ma60, iv_percentile * 100)

        if is_breakdown_ma60 or iv_percentile < 0.10:
            logger.info("【触发对冲】大盘破位或保险极度便宜，启动尾部防御买入 Put！")

            current_price = self._get_last_price(tech_etf)
            # ER2 修复: current_price 零值未保护 — 后续 target_strike 和除零都依赖此值
            if current_price <= 0:
                logger.warning("[尾部防御] %s 当前价格无效 (%s), 放弃买入 Put", tech_etf, current_price)
                return
            target_strike = current_price * 0.95
            # ER2 修复: target_strike 零值未保护 (current_price 极小或 NaN 时)
            if target_strike <= 0 or not math.isfinite(target_strike):
                logger.warning(
                    "[尾部防御] %s 目标行权价无效 (%s, current_price=%s), 放弃买入 Put",
                    tech_etf, target_strike, current_price,
                )
                return

            target_put = OptionContractFinder.find_put_contract(tech_etf, target_strike, month="far")

            ask_price, bid_price = self._get_option_quotes(target_put)

            if not self.risk_control.check_liquidity_spread(ask_price, bid_price):
                return

            # ER2 修复: ask_price 零值未保护 — 防 ZeroDivisionError
            if ask_price <= 0:
                logger.warning("[尾部防御] %s 卖一价无效 (%s), 无法计算仓位, 放弃", target_put, ask_price)
                return

            budget = 20000
            volume = int(budget / (ask_price * 10000))

            if volume <= 0:
                logger.warning("【警告】预算 %s 不足 (ask_price=%s), 无法购买 Put 合约 %s", budget, ask_price, target_put)
                return

            logger.info("【执行】买入 %s 张 %s 作为下行保险 @ %s", volume, target_put, ask_price)

            result = self._execute_order(target_put, volume, "BUY_OPEN", ask_price)
            logger.info("下单结果: %s", result.get('status', 'UNKNOWN'))

    def execute_options_order(self, symbol: str, qty: int, side: str, price: float) -> Dict:
        """执行期权订单，包含流动性/风控检查"""
        logger.info("\n>>> 执行期权订单: %s %s手 %s @ %s", side, qty, symbol, price)

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
        """回撤分级熔断监控：超过强制对冲阈值时真正买入尾部保险。

        P0-C3 修复: 原实现仅在 FORCE_HEDGE/HALT 时记录日志 (fail-open 熔断,
        既不禁买也不执行尾部对冲)。现改为:
          - FORCE_HEDGE: 真正调用 tail_risk_monitor 买入 Put 尾部保护
          - HALT:       执行尾部防御后返回 breach_hard_limit=True, 由调用方禁止新建多头

        Args:
            current_drawdown: 当前组合回撤（负数，如 -0.10 表示回撤 10%）
        Returns:
            熔断决策 dict (含 allow_new_buy / breach_hard_limit)，或 None
        """
        decision = self.risk_control.check_drawdown(current_drawdown)
        if decision is None:
            return None
        level = decision.level.value
        if level in ("FORCE_HEDGE", "HALT"):
            logger.info(">>> 回撤熔断(%s)触发，启动尾部防御买入 Put...", level)
            try:
                # P0-C3: 真正执行尾部对冲（买入 Put 下行保险），而非仅记录日志
                self.tail_risk_monitor()
                logger.info("【回撤熔断】尾部防御已执行 (级别 %s, allow_new_buy=%s)",
                            level, getattr(decision, "allow_new_buy", True))
            except NotImplementedError as e:
                # broker 不提供尾部防御所需接口: fail-closed, 保留熔断决策但提示
                logger.error("【回撤熔断】尾部防御不可用(%s)，HALT 仍禁止新建多头", e)
            except Exception as e:
                # P0-C3: 尾部防御执行异常不应放行——保留熔断决策 (fail-closed)
                logger.error("【回撤熔断】尾部防御执行异常: %s", e, exc_info=True)
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
        """获取标的最新价格; 无 broker 时返回 NaN (调用方自行判断)。"""
        if self.broker and hasattr(self.broker, "get_price"):
            price = self.broker.get_price(symbol)
            if price and price > 0:
                return price
        # M2修复: 取消硬编码兜底 1.00 (错误兜底会扭曲组合估值和风险计算)
        logger.warning("无法获取 %s 的最新价格 (broker 不可用或无数据), 返回 NaN", symbol)
        return float("nan")

    def _get_option_quotes(self, symbol: str) -> tuple:
        """获取期权买卖报价; 无 broker 时返回 (NaN, NaN) (调用方自行判断)。"""
        if self.broker and hasattr(self.broker, "get_option_quotes"):
            ask, bid = self.broker.get_option_quotes(symbol)
            if ask > 0 and bid > 0:
                return ask, bid
        logger.warning("无法获取 %s 的期权报价 (broker 不可用或无数据)", symbol)
        return float("nan"), float("nan")

    def _get_margin_usage(self) -> float:
        if self.broker and hasattr(self.broker, "get_margin_usage"):
            return self.broker.get_margin_usage()
        logger.warning("无法获取当前保证金使用率 (broker 不可用), 返回 NaN")
        return float("nan")

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
        """获取期权合约乘数。

        BUG-3 修复: 原实现仅按 symbol.startswith("y") 判定商品期权(乘数10),
        其余一律返回 10000(ETF期权)。非 y 开头的商品期权(m/c/ag/au/sr/cu 等)
        会误用 10000 乘数, 导致下单金额/张数成倍算错。

        现改为按标的代码前缀映射各交易所合约乘数:
        - ETF期权(510050/510300/588000/159919等): 10000
        - 股指期权(IO/MO/HO): 100
        - 商品期权: 按品种映射 (铜5/铝5/锌5/黄金1000/白银15/豆粕10等)
        """
        if not symbol:
            return 10000

        s = symbol.lower().strip()

        # ETF 期权 (沪市 5开头 / 深市 1开头)
        if s.startswith("51") or s.startswith("15") or s.startswith("58"):
            return 10000

        # 股指期权: IO(沪深300) / MO(中证500) / HO(上证50) — 乘数 100
        if s.startswith("io") or s.startswith("mo") or s.startswith("ho"):
            return 100

        # 商品期权按品种映射 (大商所 DCE / 郑商所 CZCE / 上期所 SHFE)
        _COMMODITY_MULTIPLIER = {
            # 大商所 DCE
            "m": 10,    # 豆粕
            "c": 10,    # 玉米
            "i": 100,   # 铁矿石
            "p": 10,    # 棕榈油
            "pp": 5,    # 聚丙烯
            "v": 5,     # PVC
            "l": 5,     # 聚乙烯
            "a": 10,    # 豆一
            "b": 10,    # 豆二
            "y": 10,    # 豆油
            "cs": 10,   # 玉米淀粉
            "eg": 10,   # 乙二醇
            "pg": 20,   # LPG
            # 郑商所 CZCE
            "sr": 10,   # 白糖
            "cf": 5,    # 棉花
            "ta": 5,    # PTA
            "ma": 10,   # 甲醇
            "oi": 10,   # 菜油
            "rm": 10,   # 菜粕
            "fg": 20,   # 玻璃
            "sf": 5,    # 硅铁
            "sm": 5,    # 锰硅
            "ap": 10,   # 苹果
            "cj": 5,    # 红枣
            "ur": 20,   # 尿素
            "sa": 20,   # 纯碱
            "pf": 5,    # 短纤
            "pk": 5,    # 花生
            "sh": 30,   # 烧碱
            "px": 5,    # 对二甲苯
            # 上期所 SHFE
            "cu": 5,    # 铜
            "al": 5,    # 铝
            "zn": 5,    # 锌
            "pb": 5,    # 铅
            "ni": 1,    # 镍
            "sn": 1,    # 锡
            "au": 1000, # 黄金
            "ag": 15,   # 白银
            "rb": 10,   # 螺纹钢
            "wr": 10,   # 线材
            "hc": 10,   # 热轧卷板
            "ss": 5,    # 不锈钢
            "ao": 20,   # 氧化铝
            "br": 5,    # 丁二烯橡胶
            # 上海国际能源交易中心 INE
            "sc": 1000, # 原油
            "lu": 10,   # 低硫燃料油
            "nr": 10,   # 20号胶
            "bc": 5,    # 国际铜
            "ec": 50,   # 集运指数
        }

        # 尝试从合约代码提取品种前缀 (如 "m2509-C-3200" -> "m", "ag2509-P-5800" -> "ag")
        for prefix in sorted(_COMMODITY_MULTIPLIER.keys(), key=len, reverse=True):
            if s.startswith(prefix):
                return _COMMODITY_MULTIPLIER[prefix]

        # 兜底: 无法识别的品种返回 ETF 期权默认乘数, 但记录警告
        logger.warning("[期权乘数] 无法识别合约品种 %s, 使用默认乘数 10000", symbol)
        return 10000

    def run_daily_routine(self, current_drawdown: Optional[float] = None):
        logger.info("[%s] 启动宏观对冲专户执行引擎...", time.strftime('%Y-%m-%d %H:%M:%S'))
        # M17 修复: 区分可恢复异常 (log 并继续) 和致命异常 (log 后 re-raise)
        # 原逻辑捕获所有异常后继续执行, 调用者无法感知失败
        try:
            # 顶级对冲基金整改：盘前先评估组合回撤，触发熔断时优先尾部防御
            drawdown_decision = None
            if current_drawdown is not None:
                drawdown_decision = self.monitor_drawdown(current_drawdown)

            # P0-C3: 消费熔断决策——HALT/禁买时跳过新建多头(备兑开仓), 只做去风险
            # BUG-5 验证: DrawdownDecision.to_dict() 确认含 level/allow_new_buy/breach_hard_limit
            # 三重判断: 任一为真即 halt (防字段缺失导致绕过)
            halt = bool(
                drawdown_decision
                and (drawdown_decision.get("level") == "HALT"
                     or drawdown_decision.get("allow_new_buy") is False
                     or drawdown_decision.get("breach_hard_limit"))
            )
            if halt:
                logger.error(
                    "【回撤熔断】HALT 生效: 禁止新建多头, 跳过备兑开仓 (execute_covered_call)。"
                    "尾部防御已在上一步执行。当前回撤: %.2f%%",
                    (current_drawdown or 0.0) * 100,
                )
                # 不做任何建仓动作；tail_risk_monitor 已在 monitor_drawdown 中执行防御
                # 仅记录去风险状态，避免在熔断期新开任何头寸
            else:
                self.execute_covered_call()
                self.tail_risk_monitor()
            logger.info(">>> 本次轮询执行完毕。状态：%s。",
                        "熔断保护中" if halt else "安全")
        except NotImplementedError as e:
            # 可恢复: broker 不提供某些接口, 降级处理
            logger.warning("【降级】功能不可用, 跳过: %s", e)
        except (KeyError, ValueError, TypeError) as e:
            # 致命: 数据结构异常, 调用者应感知
            logger.error("【致命异常】数据/类型错误: %s", e, exc_info=True)
            raise
        except Exception as e:
            # 其他未知异常: 记录后 re-raise, 不再静默吞掉
            logger.error("【系统异常】%s", e, exc_info=True)
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
            THSRealBroker = ths_module.THSRealBroker  # noqa: N806

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
            logger.warning("THSRealBroker 不可用: %s，将使用模拟模式", e)
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

    logger.info("\n引擎已就绪 (mode=%s)，未执行任何订单。", engine.mode)

    logger.info("\n" + "=" * 60)
    logger.info("执行完毕")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
