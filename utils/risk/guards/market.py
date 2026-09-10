"""风控守卫 Guard 6/7/8 — 大盘熔断 / 流动性危机 / 隔夜跳空 mixin

审计 item 11 (2026-09-10) 拆自 ``utils/risk_guard_integrator.py``: **零行为变更**,
仅搬移方法体与所属分隔注释, 逻辑/字段/落盘路径/日志文本逐字节保持原样。

契约: 路径与 logger 一律经 ``plan_context.XXX`` 属性式访问; 重量级引擎依赖保持方法体内惰性 import。
"""

from __future__ import annotations

from typing import Any

from utils.risk.guards.plan_context import PlanContextMixin


class MarketGuardMixin(PlanContextMixin):
    # ============================================================
    # Guard 6: 大盘熔断 (P1-H 新增, v8.6.6)
    # ============================================================
    def guard_market_circuit_breaker(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """大盘熔断 Guard - 沪深300 跌幅触发 L2/L3

        L2 (跌 5%): 禁止开仓, 保留平仓
        L3 (跌 7%): 全局平仓 + halt_all_trading

        委托 utils/market_circuit_breaker.py MarketCircuitBreaker 实现
        """
        try:
            from utils.market_circuit_breaker import MarketCircuitBreaker
        except ImportError:
            self._log("[大盘熔断] MarketCircuitBreaker 导入失败, 跳过")
            plan.setdefault("risk_guard", {})["market_circuit_breaker"] = {
                "status": "SKIP",
                "reason": "import_failed",
            }
            return plan

        try:
            mcb = MarketCircuitBreaker()
            status = mcb.check_market_status()
            plan = mcb.apply_to_plan(plan, status)

            if status["level"] >= 2:
                self._log(
                    f"[大盘熔断] L{status['level']} 触发: 沪深300 跌幅 {status['hs300_change_pct']:.2%}, "
                    f"数据源={status['data_source']}, 动作={status['actions']}"
                )
            else:
                self._log(
                    f"[大盘熔断] 正常: 沪深300 跌幅 {status['hs300_change_pct']:.2%} (数据源={status['data_source']})"
                )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"[大盘熔断] 检查崩溃: {e}")
            plan.setdefault("risk_guard", {})["market_circuit_breaker_error"] = str(e)
            # fail-closed: 大盘熔断崩溃时禁止建仓
            # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
            # 原代码只设 spot_build_allowed=False, 未设 build_allowed=False,
            # 下游执行器读 build_allowed 会绕过 Guard 崩溃限制, 在风控异常时仍允许开仓.
            # 修复: 同步设 build_allowed=False + 升级 circuit_level 到 WARNING (不覆盖 CRITICAL).
            ms = plan.setdefault("market_state", {})
            ms["spot_build_allowed"] = False
            ms["build_allowed"] = False
            if str(ms.get("circuit_level", "NORMAL")).upper() != "CRITICAL":
                ms["circuit_level"] = "WARNING"

        return plan

    # ============================================================
    # Guard 7: 流动性危机全局撤单 (P1-J 新增, v8.6.6)
    # ============================================================
    def guard_liquidity_crisis(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """流动性危机 Guard - 全市场涨跌停家数触发全局撤单

        触发条件: limit_up_count + limit_down_count > 2000 (真实数据)
        响应动作:
            1. 清空 plan['execution_plan']['morning_orders']
            2. 清空 plan['execution_plan']['afternoon_orders']
            3. 标记 plan['market_state']['liquidity_crisis'] = True

        v8.6.8 P1-LIVE-05 修复 (2026-07-26):
            原始 bug: 数据源不可用时 (_fetch_limit_counts 返回 fail_closed),
            FAIL_CLOSED_COUNT=2500 > 阈值 2000, 误触发全局撤单 + circuit_level=CRITICAL.
            但数据源不可用 ≠ 流动性危机, 只是网络故障/接口封禁/非交易日.

            修复原则 (与其他 Guard 对齐):
                - 数据源不可用 → 保守禁开仓 (WARNING), 不清空订单 (不 CRITICAL)
                - 真实数据显示流动性危机 → 全局撤单 (CRITICAL)
                - 崩溃 → 保守禁开仓 (WARNING), 不清空订单

            风控分级原则:
                - 数据源不可用: 不允许新开仓 (因为无法判断市场状态)
                - 但已有订单应该保留 (因为没有证据表明需要清仓)
                - 仅当真实数据显示极端行情时才清仓

        日志样本 (修复前):
            [RiskGuard] [流动性危机] 所有数据源不可用, fail-closed 返回 2500 触发撤单
            [RiskGuard] [流动性危机] 触发全局撤单: 涨停 2500 + 跌停 0 = 2500 > 2000, 数据源=fail_closed
        """
        LIMIT_COUNT_THRESHOLD = 2000  # 涨跌停家数阈值 (真实数据触发)
        # v8.6.8 P1-LIVE-05: 移除 FAIL_CLOSED_COUNT=2500, 数据源不可用时返回 0 + 标记 fail_closed

        try:
            limit_up, limit_down, data_source = self._fetch_limit_counts(pnl_report)
            total_limit = limit_up + limit_down
            is_data_unavailable = data_source == "fail_closed"

            plan.setdefault("risk_guard", {})["liquidity_crisis"] = {
                "limit_up": limit_up,
                "limit_down": limit_down,
                "total_limit": total_limit,
                "threshold": LIMIT_COUNT_THRESHOLD,
                "data_source": data_source,
                "data_unavailable": is_data_unavailable,
            }

            if is_data_unavailable:
                # v8.6.8 P1-LIVE-05: 数据源不可用 — 保守禁开仓, 但不清空订单
                # 风控原则: 无数据 ≠ 极端行情, 不应误清仓
                plan.setdefault("market_state", {})
                plan["market_state"]["spot_build_allowed"] = False
                # v8.6.8 P0-05 FIX (2026-07-26): L2 等效场景必须同步 build_allowed=False
                # 原代码只设 spot_build_allowed=False, 但 build_allowed 仍为 True
                # 与一致性校验和 overnight_gap L2 处理不对齐, 下游执行器读 build_allowed 会绕过风控
                plan["market_state"]["build_allowed"] = False
                # 不覆盖更高优先级 Guard 的 CRITICAL
                if plan["market_state"].get("circuit_level") != "CRITICAL":
                    plan["market_state"]["circuit_level"] = "WARNING"
                plan["risk_guard"]["liquidity_crisis"]["action"] = "DATA_UNAVAILABLE_NO_NEW_POSITIONS"
                plan["risk_guard"]["liquidity_crisis"]["triggered"] = False
                plan["risk_guard"]["liquidity_crisis"][
                    "note"
                ] = "数据源不可用, 保守禁开仓 (不清空订单). 生产环境请检查 akshare/astock_realtime 连通性"
                self._log(
                    f"[流动性危机] 数据源不可用, 保守禁开仓 (不清空订单). "
                    f"建议检查数据源连通性. data_source={data_source}"
                )
            elif total_limit > LIMIT_COUNT_THRESHOLD:
                # 真实数据触发全局撤单
                plan.setdefault("execution_plan", {})
                plan["execution_plan"]["morning_orders"] = []
                plan["execution_plan"]["afternoon_orders"] = []
                plan.setdefault("market_state", {})
                plan["market_state"]["liquidity_crisis"] = True
                plan["market_state"]["spot_build_allowed"] = False
                plan["market_state"]["circuit_level"] = "CRITICAL"
                # v8.6.8 P0-01 FIX (2026-07-26): circuit_level=CRITICAL 必须 build_allowed=False
                # 原代码遗漏此字段, 导致 trade_plan 同时出现
                #   market_state.build_allowed=true vs market_state.circuit_level=CRITICAL
                # 顶级对冲基金标准: 风控字段必须自洽, CRITICAL 时禁止任何新开仓
                plan["market_state"]["build_allowed"] = False
                plan["risk_guard"]["liquidity_crisis"]["action"] = "CANCEL_ALL_ORDERS"
                plan["risk_guard"]["liquidity_crisis"]["triggered"] = True

                self._log(
                    f"[流动性危机] 触发全局撤单: 涨停 {limit_up} + 跌停 {limit_down} = "
                    f"{total_limit} > {LIMIT_COUNT_THRESHOLD}, 数据源={data_source}"
                )
            else:
                plan["risk_guard"]["liquidity_crisis"]["action"] = "NORMAL"
                plan["risk_guard"]["liquidity_crisis"]["triggered"] = False
                self._log(
                    f"[流动性危机] 正常: 涨跌停 {total_limit} < {LIMIT_COUNT_THRESHOLD} "
                    f"(涨停 {limit_up} + 跌停 {limit_down}, 数据源={data_source})"
                )
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"[流动性危机] 检查崩溃: {e}")
            plan.setdefault("risk_guard", {})["liquidity_crisis_error"] = str(e)
            # v8.6.8 P1-LIVE-05: 崩溃时仅禁开仓, 不清空订单 (避免误清仓)
            # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
            # 原代码只设 spot_build_allowed=False, 未设 build_allowed=False,
            # 下游执行器读 build_allowed 会绕过 Guard 崩溃限制, 在风控异常时仍允许开仓.
            # 修复: 同步设 build_allowed=False + 升级 circuit_level 到 WARNING (不覆盖 CRITICAL).
            ms = plan.setdefault("market_state", {})
            ms["spot_build_allowed"] = False
            ms["build_allowed"] = False
            if str(ms.get("circuit_level", "NORMAL")).upper() != "CRITICAL":
                ms["circuit_level"] = "WARNING"
            if plan["market_state"].get("circuit_level") != "CRITICAL":
                plan["market_state"]["circuit_level"] = "WARNING"

        return plan

    def _fetch_limit_counts(self, pnl_report: dict[str, Any] | None = None) -> tuple[int, int, str]:
        """获取全市场涨跌停家数 (三层 fallback)

        Args:
            pnl_report: 当日盈亏报告 (用于 Layer 2 提取持仓代码)

        Returns:
            (limit_up_count, limit_down_count, data_source)
            - 真实数据: limit_up + limit_down > 2000 → 触发全局撤单 (CRITICAL)
            - 数据源不可用: 返回 (0, 0, "fail_closed"), 由调用方决定保守动作 (WARNING)
            v8.6.8 P1-LIVE-05: 移除 FAIL_CLOSED_COUNT=2500 误触发清仓
        """
        # v8.6.8 P1-LIVE-05: 移除 FAIL_CLOSED_COUNT=2500 (误触发 CRITICAL 全局撤单)
        # 数据源不可用时返回 0, 由 guard_liquidity_crisis 根据 data_source="fail_closed"
        # 做保守处理 (禁开仓 + WARNING, 但不清空订单)
        # Layer 1: akshare 全市场实时行情
        try:
            import akshare as ak

            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty and "涨跌幅" in df.columns:
                # 涨停: 涨幅 >= 9.5% (考虑浮点误差)
                # 跌停: 跌幅 <= -9.5%
                pct = df["涨跌幅"]
                limit_up = int((pct >= 9.5).sum())
                limit_down = int((pct <= -9.5).sum())
                return limit_up, limit_down, "akshare"
        except ImportError:
            self._log("[流动性危机] akshare 未安装, 尝试 Layer 2")
        except AttributeError as e:
            self._log(f"[流动性危机] akshare 获取失败: {e}, 尝试 Layer 2")

        # Layer 2: astock_realtime 持仓样本 (降级, 不准确)
        try:
            from utils.astock_realtime import get_realtime_quotes

            # v8.6.6 修复: 使用兼容层提取持仓代码 (支持完整格式和简化格式)
            codes = []
            if pnl_report:
                positions = self._extract_positions(pnl_report)
                codes = [p.get("code", p.get("symbol", "")) for p in positions if isinstance(p, dict)]

            # 清理代码格式 (astock_realtime 需要纯数字代码)
            clean_codes = []
            for c in codes[:50]:
                if not c:
                    continue
                # 提取纯数字部分 (如 "600519.SH" → "600519")
                num = "".join(ch for ch in str(c) if ch.isdigit())
                if num:
                    clean_codes.append(num)

            if clean_codes:
                quotes = get_realtime_quotes(clean_codes)
                limit_up = sum(1 for q in quotes.values() if q.get("change_pct", 0) >= 9.5)
                limit_down = sum(1 for q in quotes.values() if q.get("change_pct", 0) <= -9.5)
                # v8.6.13 P0 FIX (2026-08-01 AI 扫描):
                # 原代码用样本外推 (scale = 5000 // N), 严重失真:
                #   持仓 1 只且涨停 → scale=5000 → 外推 5000 涨停 > 2000 阈值 → 误触发全局撤单
                #   持仓 10 只中 5 只涨停 → scale=500 → 外推 2500 > 2000 → 误触发全局撤单
                # 持仓是精选标的 (集中某板块), 不代表全市场分布, 线性外推无统计学依据.
                # 修复: 移除外推, 直接返回样本内统计 + 标记 data_source="astock_sample",
                # 由 guard_liquidity_crisis 根据 data_source 做保守处理 (不触发 CRITICAL 全局撤单).
                self._log(
                    f"[流动性危机] Layer 2 持仓样本 (未外推): {len(clean_codes)} 只持仓 → "
                    f"涨停 {limit_up} + 跌停 {limit_down} (data_source=astock_sample, 不触发 CRITICAL)"
                )
                return limit_up, limit_down, "astock_sample"
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
            ImportError,
        ) as e:
            self._log(f"[流动性危机] astock_realtime 获取失败: {e}")

        # Layer 3: 数据源不可用 — 返回 0 + 标记 fail_closed
        # v8.6.8 P1-LIVE-05: 不再返回 2500 触发误清仓, 改为返回 0 让调用方保守处理
        self._log(
            "[流动性危机] 所有数据源不可用, 返回 (0, 0, fail_closed), 由 guard_liquidity_crisis 保守禁开仓 (不清空订单)"
        )
        return 0, 0, "fail_closed"

    # ============================================================
    # Guard 8: 隔夜跳空缺口 (P1-I 新增, v8.6.6)
    # ============================================================
    def guard_overnight_gap(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """隔夜跳空 Guard - 外盘隔夜风险触发 L1/L2/L3

        L1 (S&P500 跌 1% 或 ADR 偏离 2%): 预警
        L2 (S&P500 跌 2% 或 ADR 偏离 4%): 禁止开仓
        L3 (S&P500 跌 3% 或 ADR 偏离 6%): 全局平仓

        委托 utils/overnight_gap_monitor.py OvernightGapMonitor 实现
        """
        try:
            from utils.overnight_gap_monitor import OvernightGapMonitor
        except ImportError:
            self._log("[隔夜跳空] OvernightGapMonitor 导入失败, 跳过")
            plan.setdefault("risk_guard", {})["overnight_gap"] = {
                "status": "SKIP",
                "reason": "import_failed",
            }
            return plan

        try:
            ogm = OvernightGapMonitor()
            risk = ogm.evaluate_overnight_risk()
            plan = ogm.apply_to_plan(plan, risk)

            # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
            # 原代码用 risk["level"] / risk['sp500_change_pct'] 等直接访问,
            # OvernightGapMonitor 返回结构变化或缺字段时会 KeyError, 导致
            # guard_overnight_gap 崩溃, 被外层 try/except 捕获后 fail-closed 禁建仓.
            # 修复: 统一用 .get() 加默认值, 字段缺失时降级为 0/unknown 不崩溃.
            if not isinstance(risk, dict):
                self._log(f"[隔夜跳空] evaluate_overnight_risk 返回非 dict: {type(risk).__name__}")
                risk = {}
            risk_level = int(risk.get("level", 0) or 0)
            sp500_pct = float(risk.get("sp500_change_pct", 0) or 0)
            adr_pct = float(risk.get("adr_deviation_pct", 0) or 0)
            data_src = str(risk.get("data_source", "unknown"))
            trigger = str(risk.get("trigger", "unknown"))

            if risk_level >= 2:
                self._log(
                    f"[隔夜跳空] L{risk_level} 触发: S&P500 {sp500_pct:.2%}, "
                    f"ADR偏离 {adr_pct:.2%}, 数据源={data_src}, "
                    f"触发={trigger}"
                )
            elif risk_level == 1:
                self._log(f"[隔夜跳空] L1 预警: S&P500 {sp500_pct:.2%}, " f"ADR偏离 {adr_pct:.2%}")
            else:
                self._log(f"[隔夜跳空] 正常: S&P500 {sp500_pct:.2%} (数据源={data_src})")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"[隔夜跳空] 检查崩溃: {e}")
            plan.setdefault("risk_guard", {})["overnight_gap_error"] = str(e)
            # fail-closed: 隔夜跳空崩溃时禁止建仓
            # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
            # 原代码只设 spot_build_allowed=False, 未设 build_allowed=False,
            # 下游执行器读 build_allowed 会绕过 Guard 崩溃限制, 在风控异常时仍允许开仓.
            # 修复: 同步设 build_allowed=False + 升级 circuit_level 到 WARNING (不覆盖 CRITICAL).
            ms = plan.setdefault("market_state", {})
            ms["spot_build_allowed"] = False
            ms["build_allowed"] = False
            if str(ms.get("circuit_level", "NORMAL")).upper() != "CRITICAL":
                ms["circuit_level"] = "WARNING"

        return plan

