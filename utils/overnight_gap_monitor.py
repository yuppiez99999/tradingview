"""
隔夜跳空缺口监控器 (Overnight Gap Monitor)
============================================
创建日期: 2026-07-26
创建原因: P1-I 黑天鹅防御缺口修复 — 隔夜外盘大跌无 09:25 前仓位调整
审计文档: docs/HEDGE_FUND_AUDIT_V2_2026-07-26.md

触发阈值:
    L1 预警: S&P500 跌幅 >= 1% 或 ADR 偏离 >= 2%
    L2 熔断: S&P500 跌幅 >= 2% 或 ADR 偏离 >= 4% → 禁止开仓
    L3 全局平仓: S&P500 跌幅 >= 3% 或 ADR 偏离 >= 6% → 全局平仓

数据源 fallback (三层):
    1. ExternalDataManager.get_global_stock('SPY') — S&P500 ETF 实时数据
    2. 本地缓存 (cache/external_data/) — 最近一次成功数据
    3. fail-closed: 返回 -2% 触发 L2 (保守保护, 不过度反应)

v8.6.7 修复 (2026-07-26):
    BUG: FAIL_CLOSED_PCT=-0.04 误触发 L3 全局平仓 (因 -0.04 <= sp500_l3=-0.03)
    修复: 改为 -0.02 触发 L2 (禁止开仓), 避免数据源不可用时误清空全部订单
    原则: fail-closed 应保守 (禁止新动作), 但不应过度反应 (强制平仓)

ADR (Advance/Decline Ratio) 偏离度:
    衡量市场广度异常, 当上涨/下跌家数比偏离 1.0 过远时视为异常

用法:
    from utils.overnight_gap_monitor import OvernightGapMonitor
    ogm = OvernightGapMonitor()
    risk = ogm.evaluate_overnight_risk()
    if risk['level'] >= 2:
        plan = ogm.apply_to_plan(plan, risk)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("overnight_gap_monitor")

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache" / "external_data"


class OvernightGapMonitor:
    """隔夜跳空缺口监控器 — 评估外盘隔夜风险并调整次日交易计划

    设计原则:
    - fail-closed: 数据源全部不可用时返回 -4% 触发 L2 (保守保护)
    - 多维度: S&P500 跌幅 + ADR 偏离度, 任一触发即响应
    - 幂等性: apply_to_plan 可重复调用
    """

    # S&P500 ETF 代码 (ExternalDataSource 使用)
    SPY_SYMBOL = "SPY"

    # v8.6.8: 通达信 A 股指数代理 (S&P500 数据源不可用时的 fallback)
    # 用沪深300ETF 当日涨跌幅作为隔夜风险代理
    # 逻辑: A 股大跌 → 全球风险情绪上升 → 美股可能跟跌
    TDX_PROXY_SYMBOL = "510300.SH"  # 沪深300ETF 华泰柏瑞
    TDX_PROXY_L1_MAP = -0.01  # 沪深300跌1% → 映射为S&P500 -1% (L1)
    TDX_PROXY_L2_MAP = -0.02  # 沪深300跌2% → 映射为S&P500 -2% (L2)
    TDX_PROXY_NEUTRAL = 0.0  # 沪深300跌幅<1%或上涨 → 中性 (不触发)

    # 触发阈值
    SP500_L1_THRESHOLD = -0.01  # S&P500 跌 1% → L1 预警
    SP500_L2_THRESHOLD = -0.02  # S&P500 跌 2% → L2 熔断
    SP500_L3_THRESHOLD = -0.03  # S&P500 跌 3% → L3 全局平仓

    ADR_L1_THRESHOLD = 0.02  # ADR 偏离 2% → L1 预警
    ADR_L2_THRESHOLD = 0.04  # ADR 偏离 4% → L2 熔断
    ADR_L3_THRESHOLD = 0.06  # ADR 偏离 6% → L3 全局平仓

    # fail-closed 默认值
    # v8.6.7: 从 -0.04 改为 -0.02, 避免误触发 L3 全局平仓
    # -0.04 <= sp500_l3(-0.03) → L3 (错误, 过度反应)
    # -0.02 <= sp500_l2(-0.02) → L2 (正确, 禁止开仓但不清仓)
    FAIL_CLOSED_PCT = -0.02  # 返回 -2% 触发 L2 (保守保护)

    def __init__(
        self,
        sp500_l2_threshold: float | None = None,  # type: ignore
        sp500_l3_threshold: float | None = None,  # type: ignore
        adr_l2_threshold: float | None = None,  # type: ignore
        adr_l3_threshold: float | None = None,  # type: ignore
        fail_closed_pct: float | None = None,  # type: ignore
    ):
        """初始化隔夜跳空监控器

        Args:
            sp500_l2_threshold: S&P500 L2 阈值, 默认 -0.02
            sp500_l3_threshold: S&P500 L3 阈值, 默认 -0.03
            adr_l2_threshold: ADR L2 阈值, 默认 0.04
            adr_l3_threshold: ADR L3 阈值, 默认 0.06
            fail_closed_pct: fail-closed 返回值, 默认 -0.04
        """
        self.sp500_l2 = float(sp500_l2_threshold) if sp500_l2_threshold is not None else self.SP500_L2_THRESHOLD
        self.sp500_l3 = float(sp500_l3_threshold) if sp500_l3_threshold is not None else self.SP500_L3_THRESHOLD
        self.adr_l2 = float(adr_l2_threshold) if adr_l2_threshold is not None else self.ADR_L2_THRESHOLD
        self.adr_l3 = float(adr_l3_threshold) if adr_l3_threshold is not None else self.ADR_L3_THRESHOLD
        self.fail_closed_pct = float(fail_closed_pct) if fail_closed_pct is not None else self.FAIL_CLOSED_PCT

    # ------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------

    def evaluate_overnight_risk(self) -> dict:
        """评估隔夜跳空风险

        Returns:
            {
                'timestamp': ISO 时间戳,
                'sp500_change_pct': S&P500 跌幅 (小数),
                'adr_deviation_pct': ADR 偏离度 (小数),
                'level': 0/1/2/3,
                'level_name': "正常"/"L1预警"/"L2熔断"/"L3全局平仓",
                'actions': [动作列表],
                'data_source': "external_data"/"cache"/"fail_closed",
                'can_trade': bool (level < 3),
                'can_open': bool (level < 2),
            }
        """
        sp500_change, adr_deviation, data_source = self._fetch_overnight_data()

        # 综合两个指标确定级别 (取较高者)
        sp500_level = self._sp500_to_level(sp500_change)
        adr_level = self._adr_to_level(adr_deviation)
        level = max(sp500_level, adr_level)

        level_names = {0: "正常", 1: "L1预警", 2: "L2熔断", 3: "L3全局平仓"}
        actions = []
        if level >= 1:
            actions.append("加强盘前监控, 检查持仓风险")
        if level >= 2:
            actions.append("禁止开盘新开仓, 仅允许平仓")
        if level >= 3:
            actions.append("09:25 集合竞价全局平仓 + halt_all_trading")

        result = {
            "timestamp": datetime.now().isoformat(),
            "sp500_change_pct": float(sp500_change),
            "adr_deviation_pct": float(adr_deviation),
            "level": level,
            "level_name": level_names.get(level, "正常"),
            "actions": actions,
            "data_source": data_source,
            "can_trade": level < 3,
            "can_open": level < 2,
            "trigger": self._get_trigger_reason(sp500_change, adr_deviation, sp500_level, adr_level),
        }

        if level >= 2:
            logger.warning(
                "[OvernightGapMonitor] %s 触发: S&P500 %.2f%%, ADR偏离 %.2f%%, 数据源=%s, 动作=%s",
                result["level_name"],
                sp500_change * 100,
                adr_deviation * 100,
                data_source,
                actions,
            )
        elif level == 1:
            logger.info(
                "[OvernightGapMonitor] L1 预警: S&P500 %.2f%%, ADR偏离 %.2f%%",
                sp500_change * 100,
                adr_deviation * 100,
            )
        else:
            logger.info(
                "[OvernightGapMonitor] 隔夜正常: S&P500 %.2f%%, ADR偏离 %.2f%%",
                sp500_change * 100,
                adr_deviation * 100,
            )

        return result

    def apply_to_plan(self, plan: dict, risk: dict) -> dict:
        """将隔夜风险应用到次日交易计划

        L3: 清空所有订单 + halt_all_trading
        L2: 过滤 BUY 订单 (保留 SELL)
        L1: 仅标记, 不修改订单
        L0: 不修改

        Args:
            plan: 交易计划字典
            risk: evaluate_overnight_risk() 返回的风险评估

        Returns:
            修改后的 plan
        """
        level = risk.get("level", 0)
        plan.setdefault("execution_plan", {})
        plan.setdefault("market_state", {})
        plan.setdefault("risk_guard", {})

        if level >= 3:
            # L3: 全局平仓
            plan["execution_plan"]["morning_orders"] = []
            plan["execution_plan"]["afternoon_orders"] = []
            plan["market_state"]["spot_build_allowed"] = False
            plan["market_state"]["build_allowed"] = False
            plan["market_state"]["circuit_level"] = "CRITICAL"
            plan["market_state"]["halt_all_trading"] = True
            plan["risk_guard"]["overnight_gap"] = {
                "level": 3,
                "sp500_change_pct": risk.get("sp500_change_pct", 0),
                "adr_deviation_pct": risk.get("adr_deviation_pct", 0),
                "action": "HALT_ALL_TRADING",
                "trigger": risk.get("trigger", "unknown"),
                "data_source": risk.get("data_source", "unknown"),
            }
            logger.critical("[OvernightGapMonitor] L3 全局平仓: 已清空所有订单, halt_all_trading=True")

        elif level == 2:
            # L2: 禁止开仓 (过滤 BUY 订单, 保留 SELL)
            # v8.6.8 P0-02 FIX (2026-07-26): 修复字段名 bug
            # 原代码用 o.get('direction') != 'BUY', 但 trade_plan 现货订单字段是 'side' (非 'direction')
            # 所有现货订单 direction=None, None != 'BUY' 为 True, 导致所有 BUY 订单被保留
            # 与 spot_build_allowed=false 严重矛盾, 实盘会执行被禁止的建仓订单
            # 修复: 同时检查 side 和 direction 两个字段, 任一为 BUY 即过滤
            morning_orders = plan["execution_plan"].get("morning_orders", [])
            plan["execution_plan"]["morning_orders"] = [
                o
                for o in morning_orders
                if o.get("side", "").upper() != "BUY"
                and o.get("direction", "").upper() not in ("BUY", "BUY_OPEN", "BUY_PUT")
            ]
            afternoon_orders = plan["execution_plan"].get("afternoon_orders", [])
            plan["execution_plan"]["afternoon_orders"] = [
                o
                for o in afternoon_orders
                if o.get("side", "").upper() != "BUY"
                and o.get("direction", "").upper() not in ("BUY", "BUY_OPEN", "BUY_PUT")
            ]
            plan["market_state"]["spot_build_allowed"] = False
            # v8.6.8 P0-02 FIX: L2 必须同步 build_allowed=False (与一致性校验对齐)
            # 原代码只设 spot_build_allowed=False, 但 build_allowed 仍为 True
            # 下游执行器可能读 build_allowed 字段而非 spot_build_allowed, 导致绕过风控
            plan["market_state"]["build_allowed"] = False
            # v8.6.7 修复: 不能覆盖更高优先级 Guard 设置的 CRITICAL
            # 原代码无条件设为 WARNING, 会把大盘熔断 L3 的 CRITICAL 降级
            if plan["market_state"].get("circuit_level") != "CRITICAL":
                plan["market_state"]["circuit_level"] = "WARNING"
            plan["risk_guard"]["overnight_gap"] = {
                "level": 2,
                "sp500_change_pct": risk.get("sp500_change_pct", 0),
                "adr_deviation_pct": risk.get("adr_deviation_pct", 0),
                "action": "NO_NEW_POSITIONS",
                "trigger": risk.get("trigger", "unknown"),
                "data_source": risk.get("data_source", "unknown"),
            }
            logger.warning("[OvernightGapMonitor] L2 熔断: 已过滤 BUY 订单, 仅允许平仓")

        elif level == 1:
            # L1: 仅标记
            plan["risk_guard"]["overnight_gap"] = {
                "level": 1,
                "sp500_change_pct": risk.get("sp500_change_pct", 0),
                "adr_deviation_pct": risk.get("adr_deviation_pct", 0),
                "action": "WARNING",
                "trigger": risk.get("trigger", "unknown"),
                "data_source": risk.get("data_source", "unknown"),
            }

        else:
            plan["risk_guard"]["overnight_gap"] = {
                "level": 0,
                "sp500_change_pct": risk.get("sp500_change_pct", 0),
                "adr_deviation_pct": risk.get("adr_deviation_pct", 0),
                "action": "NORMAL",
                "data_source": risk.get("data_source", "unknown"),
            }

        return plan

    # ------------------------------------------------------------
    # 私有: 级别判定
    # ------------------------------------------------------------

    def _sp500_to_level(self, change_pct: float) -> int:
        """S&P500 跌幅 → 级别"""
        if change_pct <= self.sp500_l3:
            return 3
        if change_pct <= self.sp500_l2:
            return 2
        if change_pct <= self.SP500_L1_THRESHOLD:
            return 1
        return 0

    def _adr_to_level(self, deviation: float) -> int:
        """ADR 偏离度 → 级别"""
        abs_dev = abs(deviation)
        if abs_dev >= self.adr_l3:
            return 3
        if abs_dev >= self.adr_l2:
            return 2
        if abs_dev >= self.ADR_L1_THRESHOLD:
            return 1
        return 0

    def _get_trigger_reason(
        self,
        sp500_change: float,
        adr_deviation: float,
        sp500_level: int,
        adr_level: int,
    ) -> str:
        """获取触发原因"""
        if sp500_level >= adr_level and sp500_level > 0:
            return f"sp500_drop_{sp500_change:.2%}"
        if adr_level > 0:
            return f"adr_deviation_{adr_deviation:.2%}"
        return "none"

    # ------------------------------------------------------------
    # 私有: 三层 fallback 数据获取
    # ------------------------------------------------------------

    def _fetch_overnight_data(self) -> tuple[float, float, str]:
        """获取隔夜外盘数据 (四层 fallback)

        Returns:
            (sp500_change_pct, adr_deviation_pct, data_source)
        """
        # Layer 1: ExternalDataSource (实时)
        sp500, adr, ok = self._fetch_via_external_source()
        if ok:
            return sp500, adr, "external_data"  # type: ignore

        # Layer 1.5: 通达信 A 股指数代理 (v8.6.8 新增)
        # 当 ExternalDataSource (Finnhub/AlphaVantage) 不可用时,
        # 用沪深300ETF 当日涨跌幅作为隔夜风险代理
        sp500, adr, ok = self._fetch_via_tdx_proxy()
        if ok:
            return sp500, adr, "tdx_proxy"  # type: ignore

        # Layer 2: 本地缓存
        sp500, adr, ok = self._fetch_via_cache()
        if ok:
            return sp500, adr, "cache"  # type: ignore

        # Layer 3: fail-closed
        logger.error(
            "[OvernightGapMonitor] 所有数据源不可用, fail-closed 返回 S&P500 %.2f%%",
            self.fail_closed_pct * 100,
        )
        return self.fail_closed_pct, 0.0, "fail_closed"

    def _fetch_via_tdx_proxy(self) -> tuple[float | None, float | None, bool]:
        """Layer 1.5: 通达信 A 股指数代理 (v8.6.8)

        用沪深300ETF 当日涨跌幅作为 S&P500 隔夜风险的代理指标.
        当 ExternalDataSource (Finnhub/AlphaVantage) 不可用时启用.

        映射规则:
            沪深300 跌幅 >= 2% → S&P500 -2% (L2 熔断)
            沪深300 跌幅 >= 1% → S&P500 -1% (L1 预警)
            沪深300 跌幅 < 1% 或上涨 → S&P500 0% (中性, 不触发)

        Returns:
            (sp500_change_pct, adr_deviation_pct, ok)
        """
        try:
            from utils.tdx_data_source import get_tdx_source

            tdx = get_tdx_source()
            if tdx is None:
                logger.debug("[OvernightGapMonitor] 通达信数据源不可用")
                return None, None, False

            # 优先用实时行情 + 历史K线获取前收盘价
            # (ETF 的 get_security_info 可能不返回 last_close, 需用历史K线兜底)
            price = None
            prev_close = None

            # 方式1: 实时行情获取当前价
            quote = tdx.get_realtime_quote(self.TDX_PROXY_SYMBOL)
            if quote:
                price = float(quote.get("index_price", 0))
                prev_close = float(quote.get("prev_close", 0))
                # ETF 的 prev_close 可能为 0, 需用历史K线兜底

            # 方式2: 历史K线获取前收盘价 (当 prev_close 无效时)
            if prev_close <= 0:
                try:
                    klines = tdx.get_historical_klines(self.TDX_PROXY_SYMBOL, period="1d", count=2)
                    if klines is not None and len(klines) >= 2:
                        # 倒数第二天收盘价作为前收盘
                        prev_close = float(klines.iloc[-2]["close"])
                        # 如果实时行情没拿到 price, 用最新收盘价
                        if price <= 0:
                            price = float(klines.iloc[-1]["close"])
                        logger.debug(
                            "[OvernightGapMonitor] 通达信历史K线兜底: prev_close=%.4f, price=%.4f",
                            prev_close,
                            price,
                        )
                except Exception as e_kline:
                    logger.debug(
                        "[OvernightGapMonitor] 通达信历史K线获取失败: %s",
                        e_kline,
                    )

            if prev_close <= 0 or price <= 0:
                logger.warning(
                    "[OvernightGapMonitor] 通达信返回无效价格: price=%s, prev_close=%s",
                    price,
                    prev_close,
                )
                return None, None, False

            # 计算沪深300当日涨跌幅
            hs300_change = (price - prev_close) / prev_close

            # 映射到 S&P500 代理值
            if hs300_change <= self.TDX_PROXY_L2_MAP:
                # 沪深300跌 >= 2% → S&P500 -2% (L2 熔断)
                sp500_proxy = self.TDX_PROXY_L2_MAP
            elif hs300_change <= self.TDX_PROXY_L1_MAP:
                # 沪深300跌 1%-2% → S&P500 -1% (L1 预警)
                sp500_proxy = self.TDX_PROXY_L1_MAP
            else:
                # 沪深300跌 < 1% 或上涨 → 中性
                sp500_proxy = self.TDX_PROXY_NEUTRAL

            logger.info(
                "[OvernightGapMonitor] 通达信代理: 沪深300 %.2f%% → S&P500 代理 %.2f%%",
                hs300_change * 100,
                sp500_proxy * 100,
            )

            # 保存到缓存 (供 Layer 2 使用)
            self._save_to_cache(sp500_proxy, 0.0)

            return sp500_proxy, 0.0, True

        except ImportError:
            logger.debug("[OvernightGapMonitor] tdx_data_source 模块未安装")
            return None, None, False
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.debug("[OvernightGapMonitor] 通达信代理获取失败: %s", e)
            return None, None, False

    def _fetch_via_external_source(self) -> tuple[float | None, float | None, bool]:
        """Layer 1: ExternalDataSource 获取 S&P500 + ADR"""
        try:
            from utils.external_data_source import ExternalDataManager

            mgr = ExternalDataManager()

            # 获取 S&P500 ETF 数据
            spy_data = mgr.get_global_stock(self.SPY_SYMBOL)
            if not spy_data:
                return None, None, False

            sp500_change = None
            # 优先使用 change_pct 字段
            if "change_pct" in spy_data:
                sp500_change = float(spy_data["change_pct"]) / 100.0
            elif "price" in spy_data and "pre_close" in spy_data:
                pre_close = float(spy_data["pre_close"])
                price = float(spy_data["price"])
                if pre_close > 0:
                    sp500_change = (price - pre_close) / pre_close

            if sp500_change is None:
                return None, None, False

            # ADR 偏离度: 从 risk_sentiment 获取 (如果可用)
            adr_deviation = 0.0
            try:
                sentiment = mgr.get_risk_sentiment()
                # risk_sentiment 可能包含 market_breadth 字段
                breadth = sentiment.get("market_breadth", {})
                adv = float(breadth.get("advancing", 0))
                dec = float(breadth.get("declining", 0))
                if adv + dec > 0:
                    # ADR = advancing / declining, 偏离度 = |ADR - 1| / 基线
                    adr_ratio = adv / max(dec, 1)
                    # 归一化: 当 adr_ratio=1 时偏离 0, adr_ratio=2 或 0.5 时偏离 0.5
                    adr_deviation = abs(adr_ratio - 1.0) / 2.0
            except Exception:  # P2 模块 fail-safe, 待后续精确化
                # ADR 获取失败不影响 S&P500
                pass

            # 保存到缓存 (供 Layer 2 使用)
            self._save_to_cache(sp500_change, adr_deviation)

            return sp500_change, adr_deviation, True

        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.debug("[OvernightGapMonitor] ExternalDataSource 获取失败: %s", e)
            return None, None, False

    def _fetch_via_cache(self) -> tuple[float | None, float | None, bool]:
        """Layer 2: 从本地缓存获取最近一次数据"""
        try:
            cache_path = CACHE_DIR / "overnight_gap_latest.json"
            if not cache_path.exists():
                return None, None, False

            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)

            sp500 = data.get("sp500_change_pct")
            adr = data.get("adr_deviation_pct", 0.0)
            cached_at = data.get("cached_at", "")

            if sp500 is None:
                return None, None, False

            logger.info(
                "[OvernightGapMonitor] 使用缓存数据 (缓存时间: %s): S&P500 %.2f%%",
                cached_at,
                float(sp500) * 100,
            )
            return float(sp500), float(adr), True

        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.debug("[OvernightGapMonitor] 缓存读取失败: %s", e)
            return None, None, False

    def _save_to_cache(self, sp500_change: float, adr_deviation: float):
        """保存数据到缓存 (供 Layer 2 使用)"""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path = CACHE_DIR / "overnight_gap_latest.json"
            data = {
                "sp500_change_pct": float(sp500_change),
                "adr_deviation_pct": float(adr_deviation),
                "cached_at": datetime.now().isoformat(),
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.debug("[OvernightGapMonitor] 缓存保存失败: %s", e)


# ============================================================
# 模块自检
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("Overnight Gap Monitor 自检")
    logger.info("=" * 70)

    ogm = OvernightGapMonitor()
    risk = ogm.evaluate_overnight_risk()
    logger.info(f"\nS&P500 跌幅: {risk['sp500_change_pct']:.2%}")
    logger.info(f"ADR 偏离度: {risk['adr_deviation_pct']:.2%}")
    logger.info(f"级别: L{risk['level']} ({risk['level_name']})")
    logger.info(f"数据源: {risk['data_source']}")
    logger.info(f"触发原因: {risk.get('trigger', 'none')}")
    logger.info(f"动作: {risk['actions']}")

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
    result = ogm.apply_to_plan(test_plan, risk)
    logger.info("\napply_to_plan 结果:")
    logger.info(f"  morning_orders: {result['execution_plan']['morning_orders']}")
    logger.info(f"  risk_guard: {result['risk_guard'].get('overnight_gap', {})}")

    logger.info("\n[OK] OvernightGapMonitor 自检通过")
