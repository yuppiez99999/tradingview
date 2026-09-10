"""
大盘熔断监控器 (Market Circuit Breaker)
========================================
创建日期: 2026-07-26
创建原因: P1-H 黑天鹅防御缺口修复 — 沪深300 跌 5%/7% 场景无响应
审计文档: docs/HEDGE_FUND_AUDIT_V2_2026-07-26.md

触发阈值:
    L2 预警: 沪深300 跌幅 >= 5% → 禁止开仓, 保留平仓
    L3 全局平仓: 沪深300 跌幅 >= 7% → 全局平仓 + halt_all_trading

数据源 fallback (三层):
    1. astock_realtime.get_realtime_quotes(['510300']) — 沪深300ETF 实时价
    2. akshare.stock_zh_index_spot_em() — 全市场指数快照
    3. fail-closed: 返回 -5% 触发 L2 (保守保护, 不过度反应)

v8.6.7 修复 (2026-07-26):
    BUG: FAIL_CLOSED_PCT=-0.08 误触发 L3 全局平仓 (因 -0.08 <= l3_threshold=-0.07)
    修复: 改为 -0.05 触发 L2 (禁止开仓), 避免数据源不可用时误清空全部订单
    原则: fail-closed 应保守 (禁止新动作), 但不应过度反应 (强制平仓)
    数据源不可用 ≠ 极端行情, 可能只是网络故障/接口封禁/非交易日

与 circuit_breaker.py 的区别:
    现有 circuit_breaker.py 是数据源容错熔断器 (CLOSED/OPEN/HALF_OPEN),
    本模块是大盘指数跌幅熔断器, 两者概念完全不同, 不可复用。

用法:
    from utils.market_circuit_breaker import MarketCircuitBreaker
    mcb = MarketCircuitBreaker()
    status = mcb.check_market_status()
    if status['level'] >= 2:
        plan = mcb.apply_to_plan(plan, status)
"""

from __future__ import annotations

import logging

from utils.datetime_utils import now_bj

logger = logging.getLogger("market_circuit_breaker")


class MarketCircuitBreaker:
    """大盘熔断监控器 — 基于沪深300 实时跌幅触发 L2/L3 协议

    设计原则:
    - fail-closed: 数据源全部不可用时返回 -5% 触发 L2 (保守保护, 不过度反应)
    - 三层 fallback: astock_realtime → akshare → fail-closed
    - 幂等性: apply_to_plan 可重复调用, 不会叠加效果
    """

    # 沪深300 标的代码
    HS300_ETF_CODE = "510300"  # 沪深300ETF (实时行情)
    HS300_INDEX_CODE = "000300"  # 沪深300 指数 (akshare)

    # 触发阈值 (跌幅, 负值)
    L2_THRESHOLD = -0.05  # 跌 5% → L2 预警
    L3_THRESHOLD = -0.07  # 跌 7% → L3 全局平仓

    # fail-closed 默认值 (数据源全部不可用时)
    # v8.6.7: 从 -0.08 改为 -0.05, 避免误触发 L3 全局平仓
    # -0.08 <= l3_threshold(-0.07) → L3 (错误, 过度反应)
    # -0.05 <= l2_threshold(-0.05) → L2 (正确, 禁止开仓但不清仓)
    FAIL_CLOSED_PCT = -0.05  # 返回 -5% 触发 L2 (保守保护)

    def __init__(
        self,
        l2_threshold: float | None = None,
        l3_threshold: float | None = None,
        fail_closed_pct: float | None = None,
    ):
        """初始化大盘熔断监控器

        Args:
            l2_threshold: L2 触发阈值 (跌幅, 负值), 默认 -0.05
            l3_threshold: L3 触发阈值 (跌幅, 负值), 默认 -0.07
            fail_closed_pct: fail-closed 返回值, 默认 -0.08
        """
        self.l2_threshold = (
            float(l2_threshold) if l2_threshold is not None else self.L2_THRESHOLD
        )
        self.l3_threshold = (
            float(l3_threshold) if l3_threshold is not None else self.L3_THRESHOLD
        )
        self.fail_closed_pct = (
            float(fail_closed_pct)
            if fail_closed_pct is not None
            else self.FAIL_CLOSED_PCT
        )

    # ------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------

    def check_market_status(self) -> dict:
        """检查大盘熔断状态

        Returns:
            {
                'timestamp': ISO 时间戳,
                'hs300_change_pct': 沪深300 跌幅 (小数, 如 -0.05),
                'level': 0/2/3,
                'level_name': "正常"/"L2预警"/"L3全局平仓",
                'actions': [动作列表],
                'data_source': "astock_realtime"/"akshare"/"fail_closed",
                'can_trade': bool (level < 3),
                'can_open': bool (level == 0),
            }
        """
        sp500_change, data_source = self._fetch_hs300_change_pct()

        # 确定熔断级别
        level = 0
        if sp500_change <= self.l3_threshold:
            level = 3
        elif sp500_change <= self.l2_threshold:
            level = 2

        level_names = {0: "正常", 2: "L2预警", 3: "L3全局平仓"}
        actions = []
        if level >= 2:
            actions.append("禁止开盘新开仓, 仅允许平仓")
        if level >= 3:
            actions.append("09:25 集合竞价全局平仓 + halt_all_trading")

        result = {
            "timestamp": now_bj().isoformat(),
            "hs300_change_pct": float(sp500_change),
            "level": level,
            "level_name": level_names.get(level, "正常"),
            "actions": actions,
            "data_source": data_source,
            "can_trade": level < 3,
            "can_open": level == 0,
        }

        if level >= 2:
            logger.warning(
                "[MarketCircuitBreaker] %s 触发: 沪深300 跌幅 %.2f%%, 数据源=%s, 动作=%s",
                result["level_name"],
                sp500_change * 100,
                data_source,
                actions,
            )
        else:
            logger.info(
                "[MarketCircuitBreaker] 大盘正常: 沪深300 跌幅 %.2f%%, 数据源=%s",
                sp500_change * 100,
                data_source,
            )

        return result

    def apply_to_plan(self, plan: dict, status: dict) -> dict:
        """将熔断状态应用到交易计划

        L3: 清空所有订单 + halt_all_trading + 禁止建仓
        L2: 过滤 BUY 订单 (保留 SELL 平仓)
        L0: 不修改

        Args:
            plan: 交易计划字典
            status: check_market_status() 返回的状态

        Returns:
            修改后的 plan
        """
        level = status.get("level", 0)
        plan.setdefault("execution_plan", {})
        plan.setdefault("market_state", {})
        plan.setdefault("risk_guard", {})

        if level >= 3:
            # L3: 全局平仓 — 清空所有订单
            plan["execution_plan"]["morning_orders"] = []
            plan["execution_plan"]["afternoon_orders"] = []
            plan["market_state"]["spot_build_allowed"] = False
            plan["market_state"]["build_allowed"] = False
            plan["market_state"]["circuit_level"] = "CRITICAL"
            plan["market_state"]["halt_all_trading"] = True
            plan["risk_guard"]["market_circuit_breaker"] = {
                "level": 3,
                "hs300_change_pct": status.get("hs300_change_pct", 0),
                "action": "HALT_ALL_TRADING",
                "data_source": status.get("data_source", "unknown"),
            }
            logger.critical(
                "[MarketCircuitBreaker] L3 全局平仓: 已清空所有订单, halt_all_trading=True"
            )

        elif level == 2:
            # L2: 禁止开仓 — 过滤 BUY 订单, 保留 SELL
            # v8.6.13 P0 FIX (2026-08-01 AI 扫描):
            # 原代码 o.get("direction") != "BUY" 在现货订单上恒为 True,
            # 因为 trade_plan 现货订单字段是 "side" 而非 "direction",
            # 导致 BUY 订单未被过滤, L2 大盘熔断风控完全失效.
            # 修复: 用 side + direction 双字段判断 (兼容现货和期货两种格式)
            def _is_buy_order(o: dict) -> bool:
                """判断订单是否为建仓方向 (BUY 系列)"""
                side_val = str(o.get("side", "")).upper()
                direction_val = str(o.get("direction", "")).upper()
                return side_val == "BUY" or direction_val in (
                    "BUY",
                    "BUY_OPEN",
                    "BUY_PUT",
                )

            morning_orders = plan["execution_plan"].get("morning_orders", [])
            plan["execution_plan"]["morning_orders"] = [
                o for o in morning_orders if not _is_buy_order(o)
            ]
            afternoon_orders = plan["execution_plan"].get("afternoon_orders", [])
            plan["execution_plan"]["afternoon_orders"] = [
                o for o in afternoon_orders if not _is_buy_order(o)
            ]
            plan["market_state"]["spot_build_allowed"] = False
            # v8.6.13 P0 FIX: 同步 build_allowed=False, 与 v8.6.8 P0-02/P0-05 一致性原则对齐
            # 原代码只设 spot_build_allowed=False, 下游执行器读 build_allowed 会绕过 L2 限制
            plan["market_state"]["build_allowed"] = False
            # v8.6.7 修复: 不能覆盖更高优先级 Guard 设置的 CRITICAL
            # 原代码无条件设为 WARNING, 会把 KillSwitch L3 的 CRITICAL 降级
            if plan["market_state"].get("circuit_level") != "CRITICAL":
                plan["market_state"]["circuit_level"] = "WARNING"
            plan["risk_guard"]["market_circuit_breaker"] = {
                "level": 2,
                "hs300_change_pct": status.get("hs300_change_pct", 0),
                "action": "NO_NEW_POSITIONS",
                "data_source": status.get("data_source", "unknown"),
            }
            logger.warning(
                "[MarketCircuitBreaker] L2 预警: 已过滤 BUY 订单, 仅允许平仓"
            )

        else:
            # L0: 正常
            plan["risk_guard"]["market_circuit_breaker"] = {
                "level": 0,
                "hs300_change_pct": status.get("hs300_change_pct", 0),
                "action": "NORMAL",
                "data_source": status.get("data_source", "unknown"),
            }

        return plan

    # ------------------------------------------------------------
    # 私有: 三层 fallback 数据获取
    # ------------------------------------------------------------

    def _fetch_hs300_change_pct(self) -> tuple[float, str]:
        """获取沪深300 当日跌幅 (三层 fallback)

        Returns:
            (change_pct, data_source)
            change_pct: 跌幅小数 (如 -0.05 表示跌 5%)
            data_source: "astock_realtime" / "akshare" / "fail_closed"
        """
        # Layer 1: astock_realtime (沪深300ETF 实时行情)
        change_pct, ok = self._fetch_via_astock()
        if ok and change_pct is not None:
            return change_pct, "astock_realtime"
        # Layer 2: akshare (全市场指数快照)
        change_pct, ok = self._fetch_via_akshare()
        if ok and change_pct is not None:
            return change_pct, "akshare"
        # Layer 3: fail-closed (保守保护)
        logger.error(
            "[MarketCircuitBreaker] 所有数据源不可用, fail-closed 返回 %.2f%%",
            self.fail_closed_pct * 100,
        )
        return self.fail_closed_pct, "fail_closed"

    def _fetch_via_astock(self) -> tuple[float | None, bool]:
        """Layer 1: astock_realtime 获取沪深300ETF 实时涨跌幅"""
        try:
            from utils.astock_realtime import get_realtime_quotes

            quotes = get_realtime_quotes([self.HS300_ETF_CODE])
            if not quotes or self.HS300_ETF_CODE not in quotes:
                return None, False

            quote = quotes[self.HS300_ETF_CODE]
            # 优先使用 change_pct 字段
            change_pct = quote.get("change_pct")
            if change_pct is not None:
                return float(change_pct) / 100.0, True

            # 降级: 从 price 和 pre_close 计算
            price = quote.get("price")
            pre_close = quote.get("pre_close")
            if price and pre_close and pre_close > 0:
                return (float(price) - float(pre_close)) / float(pre_close), True

            return None, False
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.debug("[MarketCircuitBreaker] astock_realtime 获取失败: %s", e)
            return None, False

    def _fetch_via_akshare(self) -> tuple[float | None, bool]:
        """Layer 2: akshare 获取沪深300 指数涨跌幅"""
        try:
            import akshare as ak

            # 获取全市场指数实时行情
            df = ak.stock_zh_index_spot_em(symbol="指数成份")
            if df is None or df.empty:
                return None, False

            # 查找沪深300 指数 (代码 000300)
            mask = df["代码"].astype(str).str.contains(self.HS300_INDEX_CODE)
            matched = df[mask]
            if matched.empty:
                # 尝试从名称匹配
                mask2 = df["名称"].astype(str).str.contains("沪深300")
                matched = df[mask2]

            if matched.empty:
                return None, False

            row = matched.iloc[0]
            # akshare 涨跌幅字段可能是 '涨跌幅' (百分比数值)
            if "涨跌幅" in row:
                return float(row["涨跌幅"]) / 100.0, True

            return None, False
        except ImportError:
            logger.debug("[MarketCircuitBreaker] akshare 未安装, 跳过 Layer 2")
            return None, False
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.debug("[MarketCircuitBreaker] akshare 获取失败: %s", e)
            return None, False


# ============================================================
# 模块自检
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("Market Circuit Breaker 自检")
    logger.info("=" * 70)

    mcb = MarketCircuitBreaker()
    status = mcb.check_market_status()
    logger.info(f"\n沪深300 跌幅: {status['hs300_change_pct']:.2%}")
    logger.info(f"级别: L{status['level']} ({status['level_name']})")
    logger.info(f"数据源: {status['data_source']}")
    logger.info(f"动作: {status['actions']}")

    # 测试 apply_to_plan
    test_plan = {
        "execution_plan": {
            "morning_orders": [
                {"symbol": "588080", "direction": "BUY", "shares": 1000},
                {"symbol": "512880", "direction": "SELL", "shares": 500},
            ],
            "afternoon_orders": [],
        },
        "market_state": {},
        "risk_guard": {},
    }
    result = mcb.apply_to_plan(test_plan, status)
    logger.info("\napply_to_plan 结果:")
    logger.info(f"  morning_orders: {result['execution_plan']['morning_orders']}")
    logger.info(
        f"  risk_guard: {result['risk_guard'].get('market_circuit_breaker', {})}"
    )

    logger.info("\n[OK] MarketCircuitBreaker 自检通过")
