"""上下文 / 环境感知装载簇 —— 自 ``daily_workflow.DailyWorkflow`` 迁出 (2026-09-11)。

包含信号融合配置、外部报告、市场状态、外部因子、iFinD 洞察、EDB 期货数据、
期货期权扫描器摘要与当日交易计划的装载逻辑。

为什么落点不是 ``v8.3_institutional/daily_workflow/`` 目录
----------------------------------------------------------
若创建带 ``__init__.py`` 的 ``daily_workflow/`` 包, 它会**抢先**于同名模块
``daily_workflow.py`` 被 import, 直接弄坏既有
``from daily_workflow import DailyWorkflow`` (4 个测试文件 + v87 release gate
都依赖该路径), 故落在 ``workflow_mixins/``。

拆解口径
--------
本文件只装**真实现**; 编排层 (``__init__`` / ``phase_*`` 委托 shim /
``_build_context`` / ``run`` / ``main``) 留在宿主。迁出方法只引用标准库与
typing 名字, 故无需宿主属性式间接层。logger 名字必须与宿主一致
(``v75.daily_workflow``), 否则日志分区会漂移。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("v75.daily_workflow")


class ContextLoaderMixin:
    """装载与环境感知实现 (由 DailyWorkflow 继承使用)。"""

    def _load_fusion_config(self) -> dict[str, Any]:
        """从 settings.yaml 加载信号融合配置

        Returns:
            融合配置字典，加载失败时返回默认值
        """
        import yaml as _yaml

        defaults = {
            "qlib_weight": 0.50,
            "ifind_weight": 0.30,
            "external_weight": 0.20,
            "fused_factor_min": 0.5,
            "fused_factor_max": 1.3,
            "regime_adaptive": True,
            "bull_weights": {"qlib": 0.50, "ifind": 0.30, "external": 0.20},
            "bear_weights": {"qlib": 0.40, "ifind": 0.40, "external": 0.20},
            "crisis_weights": {"qlib": 0.30, "ifind": 0.30, "external": 0.40},
            "ifind_circuit_breaker": {
                "enabled": True,
                "direction": "negative",
                "min_confidence": 0.85,
            },
            "ifind_factor_map": {
                "positive_high": 1.2,
                "positive_mid": 1.0,
                "neutral": 1.0,
                "negative_high": 0.0,
                "negative_mid": 0.5,
                "default": 1.0,
            },
            "qlib_factor_map": {
                "strong_long": 1.3,
                "long": 1.0,
                "neutral": 0.8,
                "short": 0.5,
                "strong_short": 0.0,
            },
            "macro_factors": {
                "vix_regime": True,
                "rate_surprise": True,
                "fx_stress": True,
                "credit_spread": True,
            },
            "model_cache": {"enabled": True, "retrain_days": 30},
            "lgb_confidence_gate": True,  # lgb_enhanced 信号置信度门控开关
        }
        try:
            cfg_path = os.path.join(
                os.path.dirname(__file__), "config", "settings.yaml"
            )
            with open(cfg_path, encoding="utf-8") as f:
                full = _yaml.safe_load(f)
            sf = full.get("signal_fusion", {})
            if sf:
                defaults.update(sf)
                logger.info(
                    "信号融合配置已加载: qlib=%.2f ifind=%.2f external=%.2f regime_adaptive=%s",
                    sf.get("qlib_weight", 0.5),
                    sf.get("ifind_weight", 0.3),
                    sf.get("external_weight", 0.2),
                    sf.get("regime_adaptive", True),
                )
        except Exception as exc:  # fail-safe
            logger.warning("加载 signal_fusion 配置失败，使用默认值: %s", exc)
        return defaults

    def _load_external_reports(self) -> dict[str, Any]:
        """加载 15_每日工作流报告，作为信号生成的外部输入

        Returns:
            {
                "loaded": bool,
                "loaded_count": int,
                "total_count": int,
                "reason": str,
                "reports": {...},
                "sentiment_score": float,
                "risk_events": [...],
                "boost_symbols": [...],
                "cut_symbols": [...],
            }
        """
        if self.external_report_loader is None:
            return {
                "loaded": False,
                "reason": "ExternalReportLoader not initialized",
                "loaded_count": 0,
                "total_count": 0,
            }

        try:
            return self.external_report_loader.load_reports(self.trade_date)
        except Exception as exc:  # fail-safe
            logger.error("加载外部报告失败: %s", exc, exc_info=True)
            return {
                "loaded": False,
                "reason": str(exc),
                "loaded_count": 0,
                "total_count": 0,
            }

    def _detect_market_regime(self) -> str:
        """检测当前市场状态：bull / bear / crisis

        Returns:
            市场状态字符串
        """
        try:
            market_phase = self.state.get("phases", {}).get("market", {})
            vix = float(market_phase.get("vix", 18.5))
            circuit_level = str(market_phase.get("circuit_level", "NORMAL")).upper()
            drawdown = float(market_phase.get("drawdown", 0.0))

            if (
                circuit_level in ("LEVEL_3", "LEVEL_4", "CRISIS")
                or vix >= 40
                or drawdown <= -0.10
            ):
                return "crisis"
            if (
                circuit_level in ("LEVEL_1", "LEVEL_2")
                or vix >= 30
                or drawdown <= -0.05
            ):
                return "bear"
            return "bull"
        except Exception:  # fail-safe
            return "bull"

    def _get_regime_weights(self, regime: str) -> dict[str, float]:
        """根据市场状态获取信号融合权重

        Args:
            regime: 市场状态 (bull/bear/crisis)

        Returns:
            权重字典 {"qlib": x, "ifind": y, "external": z}
        """
        fc = self.fusion_config
        if not fc.get("regime_adaptive", True):
            return {
                "qlib": float(fc.get("qlib_weight", 0.5)),
                "ifind": float(fc.get("ifind_weight", 0.3)),
                "external": float(fc.get("external_weight", 0.2)),
            }

        regime_key = f"{regime}_weights"
        weights = fc.get(regime_key, {})
        if not weights:
            return {
                "qlib": float(fc.get("qlib_weight", 0.5)),
                "ifind": float(fc.get("ifind_weight", 0.3)),
                "external": float(fc.get("external_weight", 0.2)),
            }

        return {
            "qlib": float(weights.get("qlib", 0.5)),
            "ifind": float(weights.get("ifind", 0.3)),
            "external": float(weights.get("external", 0.2)),
        }

    def _calculate_external_factor(self, external_reports: dict[str, Any]) -> float:
        """根据外部报告计算订单调整系数

        Args:
            external_reports: _load_external_reports() 返回结果

        Returns:
            调整系数，范围 [0.5, 1.3]
        """
        if not external_reports.get("loaded"):
            return 1.0

        sentiment = float(external_reports.get("sentiment_score", 0.0))
        risk_events = external_reports.get("risk_events", [])
        boost_symbols = external_reports.get("boost_symbols", [])
        cut_symbols = external_reports.get("cut_symbols", [])

        factor = 1.0

        # 情绪映射: [-1, 1] -> [0.5, 1.3]
        factor += sentiment * 0.3

        # 风险事件压制
        if len(risk_events) >= 3:
            factor -= 0.2
        elif len(risk_events) >= 1:
            factor -= 0.1

        # 建议加仓/减仓标的数量影响整体系数
        if len(boost_symbols) > len(cut_symbols):
            factor += 0.1
        elif len(cut_symbols) > len(boost_symbols):
            factor -= 0.1

        return max(0.5, min(1.3, round(factor, 2)))

    def _get_ifind_insights(self, symbols: list, name_map: dict) -> dict[str, Any]:
        """获取 iFinD 新闻研判 (带当日缓存)

        同一天内多次调用只请求一次 API，后续从缓存读取。
        优先只扫描交易计划内标的，避免扫到无关标的触发误熔断。

        Returns:
            {symbol: StockInsight} 字典
        """
        today = self.trade_date
        if self._ifind_cache_date == today and self._ifind_cache:
            return self._ifind_cache

        if self.ifind_analyzer is None:
            return {}

        # 优先使用交易计划内标的，避免误熔断
        if not symbols and self._ifind_planned_symbols:
            symbols = list(self._ifind_planned_symbols)
        if not symbols:
            return {}

        try:
            insights = self.ifind_analyzer.batch_analyze(
                symbols, name_map=name_map, size=4, days=3
            )
            self._ifind_cache = {item.symbol: item for item in insights}
            self._ifind_cache_date = today
            logger.info(
                "iFinD 新闻研判完成 (已缓存): %d 个标的", len(self._ifind_cache)
            )
            return self._ifind_cache
        except Exception:  # fail-safe
            logger.error("iFinD 批量研判失败", exc_info=True)
            return {}

    def _get_edb_futures_data(
        self, names: list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """获取 EDB 期货/商品数据 (委托至 workflow.phases.hedge)"""
        from workflow.phases.hedge import _get_edb_futures_data as _impl

        return _impl(self._build_context(), names)

    def _get_futures_scanner_summary(self) -> dict[str, dict[str, Any]]:
        """期货期权扫描器汇总 (委托至 workflow.phases.hedge)"""
        from workflow.phases.hedge import _get_futures_scanner_summary as _impl

        return _impl()

    def _load_trade_plan(self) -> dict[str, Any]:
        """加载交易计划文件

        优先加载 `trade_plans/trade_plan_{YYYYMMDD}.json`,
        若不存在则尝试无 dash 版本 `trade_plan_{YYYY-MM-DD}.json`,
        若仍不存在则回退到主计划 `auto_trade_plan_500w_2026-2030.json`。

        Returns:
            交易计划字典 (含 phase/execution_plan/hedge_config/risk_controls 等),
            若文件不存在返回空字典。
        """
        date_compact = self.trade_date.replace("-", "")
        candidates = [
            self.config.PLAN_DIR / f"trade_plan_{date_compact}.json",
            self.config.PLAN_DIR / f"trade_plan_{self.trade_date}.json",
        ]
        for path in candidates:
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        plan = json.load(f)
                    logger.info(
                        f"已加载交易计划: {path.name} "
                        f"(阶段: {plan.get('phase', {}).get('name', 'N/A')}, "
                        f"订单数: {plan.get('execution_plan', {}).get('total_orders', 0)})"
                    )
                    # 预提取交易计划内的标的，供 iFinD 新闻扫描优先使用
                    try:
                        exec_plan = plan.get("execution_plan", {})
                        planned = []
                        for order in exec_plan.get(
                            "morning_orders", []
                        ) + exec_plan.get("afternoon_orders", []):
                            code = order.get("code")
                            if code:
                                planned.append(str(code))
                        self._ifind_planned_symbols = planned
                    except Exception:  # fail-safe
                        self._ifind_planned_symbols = []
                    return plan
                except Exception as e:  # fail-safe
                    logger.error(f"加载交易计划失败 {path}: {e}")
                    return {}

        master_plan = self.config.PLAN_DIR / "auto_trade_plan_500w_2026-2030.json"
        if master_plan.exists():
            try:
                with open(master_plan, encoding="utf-8") as f:
                    plan = json.load(f)
                logger.info(f"已回退加载主计划: {master_plan.name}")
                self._ifind_planned_symbols = []
                return plan
            except Exception as e:  # fail-safe
                logger.error(f"加载主计划失败 {master_plan}: {e}")
                return {}

        logger.warning(
            f"未找到交易计划文件: trade_plan_{date_compact}.json, "
            f"将在 phase_signal 中降级为空计划"
        )
        return {}
