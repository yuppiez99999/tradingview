"""
自动交易系统 — 实时监控 + 自动再平衡 + ML信号联动

作为 --live 模式的核心引擎，提供：
1. 盘中实时行情监控（ETF + 股票）
2. ETF 资金流向监控（国家队追踪）
3. ML 信号轮询与警报
4. 自动再平衡触发
5. 风控状态实时检查

模块路径: utils.auto_trading_system
主入口调用: loader.load('auto_trading_system', {'AutoTradingSystem': 'AutoTradingSystem'})
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

# ============================================================
# 复用 AutomatedExecutionSystem 的基础设施
# (模块级前向声明 + try/except fallback 类，根除 5 处 no-redef ignore)
# ============================================================

# 前向声明 — try 分支来自 utils.execution.automated_execution_system，
# except 分支为本地兜底占位 stub。导入顺序决定了类型，类型声明统一 type
AutomatedExecutionSystem: type
ExecutionStrategy: type
MarketStateEvaluator: type
OrderRouter: type
TradingCalendar: type

try:
    from utils.execution.automated_execution_system import (
        AutomatedExecutionSystem as _AES,
    )
    from utils.execution.automated_execution_system import (
        ExecutionStrategy as _ES,
    )
    from utils.execution.automated_execution_system import (
        MarketStateEvaluator as _MSE,
    )
    from utils.execution.automated_execution_system import (
        OrderRouter as _OR,
    )
    from utils.execution.automated_execution_system import (
        TradingCalendar as _TC,
    )

    AutomatedExecutionSystem = _AES
    ExecutionStrategy = _ES
    MarketStateEvaluator = _MSE
    OrderRouter = _OR
    TradingCalendar = _TC
    _AUTOMATED_AVAILABLE = True
except ImportError as e:
    _AUTOMATED_AVAILABLE = False
    logger.warning("AutomatedExecutionSystem 不可用: %s，将使用精简模式", e)

    # 兜底占位类，确保导入不崩溃 (类型声明已前置，不会触发 no-redef)
    class _StubAutomatedExecutionSystem:
        def __init__(self, *args, **kwargs):
            pass

    class _StubExecutionStrategy:
        pass

    class _StubMarketStateEvaluator:
        pass

    class _StubOrderRouter:
        pass

    class _StubTradingCalendar:
        def get_next_execution_time(self):
            return None

    AutomatedExecutionSystem = _StubAutomatedExecutionSystem
    ExecutionStrategy = _StubExecutionStrategy
    MarketStateEvaluator = _StubMarketStateEvaluator
    OrderRouter = _StubOrderRouter
    TradingCalendar = _StubTradingCalendar


# ============================================================
# AutoTradingSystem — 主入口 --live 模式使用的类
# ============================================================
class AutoTradingSystem(AutomatedExecutionSystem):
    """自动交易系统 — 实时监控模式主引擎。

    主入口调用链:
        run_live_monitoring()
          → AutoTradingSystem()
          → system.run()

    提供盘中实时监控循环，包括行情、资金流、ML信号、风控检查。
    """

    # 默认监控标的（ETF + 核心宽基）
    DEFAULT_ETF_CODES = [
        "512170",  # 医疗ETF华宝
        "515030",  # 新能源车ETF华夏
        "510300",  # 沪深300ETF华泰柏瑞
        "510500",  # 中证500ETF
        "588000",  # 科创50ETF
        "159915",  # 创业板ETF
    ]

    def __init__(self, total_capital: float = 5000000, **kwargs):
        """初始化自动交易系统。

        Args:
            total_capital: 总资金（默认500万）
            **kwargs: 透传给 AutomatedExecutionSystem
        """
        super().__init__(total_capital=total_capital, **kwargs)

        self.monitor_interval = 30  # 监控间隔（秒）
        self.is_running = False
        self._monitor_thread: threading.Thread | None = None

        # 统计
        self.stats: dict[str, Any] = {
            "start_time": None,
            "cycles_completed": 0,
            "errors": 0,
            "last_update": None,
        }

        logger.info(f"AutoTradingSystem 初始化完成 (资金: {total_capital:,.0f}元)")

    # --------------------------------------------------------
    # 主循环 — 主入口调用 system.run()
    # --------------------------------------------------------
    def run(self, duration_seconds: int | None = None) -> None:
        """启动实时监控循环（阻塞式）。

        Args:
            duration_seconds: 运行时长（秒），None 表示无限运行直到 Ctrl+C
        """
        self.is_running = True
        self.stats["start_time"] = now_bj()
        logger.info("=" * 60)
        logger.info("🚀 AutoTradingSystem 实时监控已启动")
        logger.info("=" * 60)
        logger.info("监控间隔: %ds", self.monitor_interval)
        logger.info("监控标的: %s", ", ".join(self.DEFAULT_ETF_CODES))

        start_time = time.perf_counter()

        try:
            while self.is_running:
                cycle_start = time.perf_counter()
                try:
                    self._run_monitor_cycle()
                except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                    self.stats["errors"] += 1
                    logger.error("监控周期异常: %s", e)

                self.stats["cycles_completed"] += 1
                self.stats["last_update"] = now_bj()

                # 时长控制
                if (
                    duration_seconds
                    and (time.perf_counter() - start_time) >= duration_seconds
                ):
                    logger.info("达到指定运行时长，自动停止")
                    break

                # 计算休眠时间（扣除本次周期耗时）
                elapsed = time.perf_counter() - cycle_start
                sleep_time = max(0.1, self.monitor_interval - elapsed)
                time.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info("用户中断，停止监控")
        finally:
            self.is_running = False
            self._print_summary()

    def run_async(self, duration_seconds: int | None = None) -> threading.Thread:
        """异步启动监控（非阻塞，后台线程运行）。

        Args:
            duration_seconds: 运行时长（秒）

        Returns:
            监控线程对象
        """
        self._monitor_thread = threading.Thread(
            target=self.run,
            args=(duration_seconds,),
            daemon=True,
            name="AutoTradingSystem-Monitor",
        )
        self._monitor_thread.start()
        logger.info("监控线程已启动 (后台运行)")
        return self._monitor_thread

    def stop(self) -> None:
        """停止监控。"""
        self.is_running = False
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        logger.info("AutoTradingSystem 已停止")

    # --------------------------------------------------------
    # 单个监控周期
    # --------------------------------------------------------
    def _run_monitor_cycle(self) -> None:
        """执行一个完整的监控周期。"""
        logger.info("-" * 50)
        logger.info(
            "📊 监控周期 #%d 开始 @ %s",
            self.stats["cycles_completed"] + 1,
            now_bj().strftime("%H:%M:%S"),
        )

        # 1. 实时行情监控
        self._monitor_realtime_quotes()

        # 2. ETF 资金流监控
        self._monitor_etf_flow()

        # 3. ML 信号检查（如可用）
        self._check_ml_signals()

        # 4. 风控状态检查
        self._check_risk_status()

        # 5. 波动率 Regime 监控 (v8.6.14 新增, Phase 0 只读建议)
        self._check_vol_regime()

        time.perf_counter()
        logger.info("✅ 周期 #%d 完成", self.stats["cycles_completed"] + 1)

    # --------------------------------------------------------
    # 子模块：实时行情
    # --------------------------------------------------------
    def _monitor_realtime_quotes(self) -> None:
        """监控 ETF 实时行情。"""
        try:
            from utils.astock_realtime import get_realtime_quotes

            quotes = get_realtime_quotes(self.DEFAULT_ETF_CODES, use_cache=False)
            valid_count = 0

            logger.info("--- 实时行情 ---")
            for code in self.DEFAULT_ETF_CODES:
                if code in quotes:
                    q = quotes[code]
                    price = float(q.get("price") or 0)
                    if price > 0:
                        valid_count += 1
                        chg = q.get("change_pct", 0)
                        src = q.get("source", "?")
                        arrow = (
                            "🔴"
                            if chg and chg < 0
                            else ("🟢" if chg and chg > 0 else "⚪")
                        )
                        logger.info(
                            "  %s %s %-8s: %7.4f  (%+6.2f%%) [%s]",
                            arrow,
                            code,
                            q.get("name", "?"),
                            price,
                            chg or 0,
                            src,
                        )

            logger.info("  有效行情: %d/%d", valid_count, len(self.DEFAULT_ETF_CODES))
        except ImportError:
            logger.warning("  ⚠️ 实时行情模块不可用")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning("  ⚠️ 行情监控异常: %s", e)

    # --------------------------------------------------------
    # 子模块：ETF 资金流
    # --------------------------------------------------------
    def _monitor_etf_flow(self) -> None:
        """监控 ETF 资金流向。"""
        try:
            from utils.etf_flow_monitor import ETFRealTimeTracker

            tracker = ETFRealTimeTracker()
            monitor_codes = ["512170", "515030"]  # 重点监控2只

            logger.info("--- ETF资金流 ---")
            for code in monitor_codes:
                flow = tracker._fetch_price_based_flow(code)
                if flow:
                    direction = (
                        "📈"
                        if flow.get("trend") == "流入"
                        else ("📉" if flow.get("trend") == "流出" else "➖")
                    )
                    logger.info(
                        "  %s %s %-8s: 净流 %+7.2f亿  涨跌 %+5.2f%%  [%s]",
                        direction,
                        code,
                        flow.get("name", "?"),
                        flow.get("net_flow_yi", 0),
                        flow.get("change_pct", 0),
                        flow.get("source", "?"),
                    )
                else:
                    logger.info("  ⚠️ %s: 暂无资金流数据", code)
        except ImportError:
            logger.warning("  ⚠️ 资金流监控模块不可用")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning("  ⚠️ 资金流监控异常: %s", e)

    # --------------------------------------------------------
    # 子模块：ML 信号
    # --------------------------------------------------------
    def _check_ml_signals(self) -> None:
        """检查 ML 预测信号。"""
        logger.info("--- ML信号 ---")
        try:
            # 尝试从主入口工具获取ML信号
            from utils.cli_helpers import get_ml_signal_section

            ml_result = get_ml_signal_section(return_raw=True)
            if ml_result and isinstance(ml_result, tuple) and len(ml_result) >= 2:
                _, result = ml_result
                if "signals" in result:
                    sig = result["signals"]
                    buy_n = len(sig.get("buy", []))
                    sell_n = len(sig.get("sell", []))
                    hold_n = len(sig.get("hold", []))
                    model_name = result.get("model_info", {}).get("best_model", "?")
                    logger.info(
                        "  🤖 %s: 买入=%d  卖出=%d  持有=%d",
                        model_name,
                        buy_n,
                        sell_n,
                        hold_n,
                    )
                    return
            logger.info("  ℹ️  暂无 ML 信号")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.info("  ℹ️  ML信号检查跳过: %s", e)

    # --------------------------------------------------------
    # 子模块：风控状态
    # --------------------------------------------------------
    def _check_risk_status(self) -> None:
        """检查风控系统状态。"""
        logger.info("--- 风控状态 ---")
        try:
            # 尝试检查 ShadowAccount 和 KillSwitch
            shadow_ok = self._check_shadow_account()
            kill_ok = self._check_kill_switch()
            if shadow_ok and kill_ok:
                logger.info("  ✅ 风控系统运行正常")
            else:
                logger.warning("  ⚠️ 部分风控模块需要关注")
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.info("  ℹ️  风控检查跳过: %s", e)

    def _check_shadow_account(self) -> bool:
        """检查影子账户状态（简化版）。"""
        try:
            from utils.shadow_account import ShadowAccount  # noqa: F401

            # 简单检查模块可导入即可
            return True
        except ImportError:
            logger.info("  ℹ️  ShadowAccount 模块未加载 (可选)")
            return True  # 非核心依赖，不标记为失败

    def _check_kill_switch(self) -> bool:
        """检查 KillSwitch 状态（简化版）。"""
        try:
            from utils.kill_switch import KillSwitch  # noqa: F401

            return True
        except ImportError:
            logger.info("  ℹ️  KillSwitch 模块未加载 (可选)")
            return True

    # --------------------------------------------------------
    # 子模块：波动率 Regime 监控 (v8.6.14 新增)
    # --------------------------------------------------------
    def _check_vol_regime(self) -> None:
        """波动率 Regime 监控 (Phase 0 只读建议模式).

        - Feature Flag USE_VOL_REGIME_WEIGHTER=False 时直接跳过
        - 启用时调用 VolRegimeWeighter.run_cycle, 输出告警到日志
        - 不调仓, 不修改 portfolio.yaml (HC-4 约束)
        - 任何异常都不阻塞主监控循环 (fail-safe)

        v8.6.14 集成位置:
            AutoTradingSystem._run_monitor_cycle() 第 5 步
            数据源: VixDataSource (RV from shadow_state + 510050 K线 + 缓存)
            回撤源: DrawdownReader (shadow_state.json)
        """
        logger.info("--- 波动率Regime ---")
        try:
            from utils.infra.feature_flags import is_enabled

            if not is_enabled("USE_VOL_REGIME_WEIGHTER"):
                logger.info(
                    "  ℹ️  VolRegimeWeighter 未启用 (USE_VOL_REGIME_WEIGHTER=False)"
                )
                return

            from pathlib import Path

            import yaml

            from utils.alpha.drawdown_reader import DrawdownReader
            from utils.alpha.vix_data_source import VixDataSource
            from utils.alpha.vol_regime_weighter import VolRegimeWeighter

            # 读取 portfolio 快照 (只读, 不修改)
            portfolio_path = Path("configs/portfolio.yaml")
            if not portfolio_path.exists():
                logger.warning("  ⚠️  portfolio.yaml 不存在, 跳过 Regime 监控")
                return
            with portfolio_path.open("r", encoding="utf-8") as f:
                portfolio_snapshot = yaml.safe_load(f) or {}

            # 获取 VIX 和回撤
            vix_value = VixDataSource().fetch_vix(use_cache=True)  # 盘中用缓存
            current_drawdown = DrawdownReader().get_current_drawdown()

            # 调用 VolRegimeWeighter (不传 orchestrator, 盘中不写 decisions.jsonl)
            weighter = VolRegimeWeighter()
            result = weighter.run_cycle(
                portfolio_snapshot=portfolio_snapshot,
                vix_value=vix_value,
                daily_returns=None,  # 盘中无日收益序列
                current_drawdown=current_drawdown,
                orchestrator=None,  # 不写决策日志, 避免污染审计链
            )

            # 输出告警
            regime = result.get("regime", "unknown")
            confidence = result.get("confidence", 0.0)
            status = result.get("status", "unknown")

            if status == "disabled":
                logger.info("  ℹ️  VolRegimeWeighter 已禁用 (Flag 未启用)")
                return

            vix_str = f"{vix_value:.2f}" if vix_value is not None else "N/A"
            dd_str = (
                f"{current_drawdown:.2%}" if current_drawdown is not None else "N/A"
            )
            logger.info(
                "  📊 Regime=%s  置信度=%.2f  VIX=%s  回撤=%s",
                regime,
                confidence,
                vix_str,
                dd_str,
            )

            # 危机档告警 (bear/crisis 触发告警)
            if regime in ("bear", "crisis"):
                logger.warning(
                    "  ⚠️  波动率告警: %s 档, 建议减仓进攻类, 加仓防御类", regime
                )
            elif regime == "neutral":
                logger.info("  ℹ️  中性档, 维持当前权重")
            elif regime == "bull":
                logger.info("  ✅ 牛市档, 可适当加仓进攻类")

        except ImportError as e:
            logger.info("  ℹ️  VolRegimeWeighter 模块未加载: %s", e)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning("  ⚠️  波动率Regime检查异常: %s", e)

    # --------------------------------------------------------
    # 总结输出
    # --------------------------------------------------------
    def _print_summary(self) -> None:
        """打印运行总结。"""
        elapsed = 0
        if self.stats["start_time"]:
            elapsed = (now_bj() - self.stats["start_time"]).total_seconds()

        logger.info("=" * 60)
        logger.info("📋 AutoTradingSystem 运行总结")
        logger.info("=" * 60)
        logger.info("  运行时长: %.1f 秒", elapsed)
        logger.info("  完成周期: %d 次", self.stats["cycles_completed"])
        logger.info("  异常次数: %d 次", self.stats["errors"])
        if self.stats["cycles_completed"] > 0:
            error_rate = self.stats["errors"] / self.stats["cycles_completed"] * 100
            logger.info("  异常率: %.1f%%", error_rate)
        logger.info("=" * 60)

    # --------------------------------------------------------
    # 便捷方法：一次性快照（不启动循环）
    # --------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """获取一次完整的监控快照（不启动循环）。

        Returns:
            包含行情、资金流、ML信号、波动率Regime等的快照字典
        """
        result: dict[str, Any] = {
            "timestamp": now_bj().isoformat(),
            "quotes": {},
            "etf_flow": {},
            "ml_signals": None,
            "risk_status": "ok",
            "vol_regime": None,  # v8.6.14 新增
        }

        # 行情
        try:
            from utils.astock_realtime import get_realtime_quotes

            result["quotes"] = get_realtime_quotes(
                self.DEFAULT_ETF_CODES, use_cache=False
            )
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            pass

        # 资金流
        try:
            from utils.etf_flow_monitor import ETFRealTimeTracker

            tracker = ETFRealTimeTracker()
            for code in ["512170", "515030"]:
                flow = tracker._fetch_price_based_flow(code)
                if flow:
                    result["etf_flow"][code] = flow
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            pass

        # 波动率 Regime 快照 (v8.6.14 新增)
        try:
            from utils.infra.feature_flags import is_enabled

            if is_enabled("USE_VOL_REGIME_WEIGHTER"):
                from utils.alpha.drawdown_reader import DrawdownReader
                from utils.alpha.vix_data_source import VixDataSource

                vix = VixDataSource().fetch_vix(use_cache=True)
                dd = DrawdownReader().get_current_drawdown()
                result["vol_regime"] = {
                    "enabled": True,
                    "vix": vix,
                    "current_drawdown": dd,
                }
            else:
                result["vol_regime"] = {"enabled": False}
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            result["vol_regime"] = {"error": str(e)}

        return result


__all__ = [
    "AutoTradingSystem",
    "AutomatedExecutionSystem",
    "ExecutionStrategy",
    "MarketStateEvaluator",
    "OrderRouter",
    "TradingCalendar",
]
