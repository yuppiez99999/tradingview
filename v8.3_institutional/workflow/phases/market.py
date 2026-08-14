# -*- coding: utf-8 -*-
"""Phase 2: 市场状态评估 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L1182-L1284 (phase_market) + L1286-L1309 (_scan_anysearch_news)
"""
from __future__ import annotations

import logging

from workflow.context import WorkflowContext, get_dw_module

logger = logging.getLogger("v75.daily_workflow")

# === 从 daily_workflow 模块获取模块级符号 ===
# 注: _dw 在 import 时获取一次 (是模块对象引用, 不变);
#     CircuitLevel 必须在 phase 函数内动态查找, 与拆分前 daily_workflow.py 中
#     phase_market 内联调用的语义一致 (测试通过 monkeypatch.setattr(dw, ...) patch).
_dw = get_dw_module()


class _SafeLevel:
    """CircuitBreaker.check 抛异常时的降级 level (fail-open, 不阻断建仓)。

    name=NORMAL, value=0 → level < LEVEL_3 → build_allowed=True
    """
    name = "NORMAL"
    value = 0

    def __ge__(self, other):
        # 比较时视为低于 LEVEL_3 (LEVEL_3.value=3)
        try:
            return 0 >= int(getattr(other, "value", 0))
        except Exception:
            return False


def phase_market(ctx: WorkflowContext):
    """市场状态评估"""
    # 动态查找模块级符号 (兼容 monkeypatch 对 daily_workflow 模块的 patch)
    CircuitLevel = getattr(_dw, "CircuitLevel", None) if _dw else None

    logger.info("=" * 60)
    logger.info("Phase 2: 市场状态评估")
    logger.info("=" * 60)

    # 懒初始化（支持单独运行该 phase）
    if not hasattr(ctx, "cb"):
        try:
            from risk.circuit_breaker import CircuitBreaker
            ctx.cb = CircuitBreaker()
        except Exception as e:
            logger.warning(f"CircuitBreaker 初始化失败，使用模拟模式: {e}")
            ctx.cb = None

    # G13 修复 (2026-08-06): VIX 从 VixDataSource 获取真实值, 非硬编码
    _vix_value = 18.5  # 默认值 (正常偏低), 实盘应从数据源获取
    try:
        from utils.alpha.vix_data_source import fetch_vix
        _vix_fetched = fetch_vix(use_cache=True)
        if _vix_fetched is not None and 5.0 <= _vix_fetched <= 150.0:
            _vix_value = float(_vix_fetched)
    except Exception:  # noqa: BLE001  # VIX 获取 fail-open, 用默认值
        pass

    # 模拟市场数据 (实盘应从 Wind 获取)
    market_data = {
        "vix": _vix_value,
        "portfolio_drop": 0.0,          # 当日无跌
        "index_return_20d": 0.02,       # 20日 +2%
        "index_return_60d": 0.05,       # 60日 +5%
    }

    # 熔断级别判定 (cb.check 抛异常时降级到 _SafeLevel, 不崩溃)
    try:
        level = ctx.cb.check(
            portfolio_drop=market_data["portfolio_drop"],
            vix=market_data["vix"],
        )
    except Exception as e:
        logger.warning(f"CircuitBreaker.check 异常, 降级到 SAFE_LEVEL: {e}")
        level = _SafeLevel()

    actions = ctx.cb.allowed_actions()

    logger.info(f"VIX: {market_data['vix']}, 跌幅: {market_data['portfolio_drop']:.2%}")
    logger.info(f"熔断级别: {level.name}")
    logger.info(f"允许操作: open_new={actions['open_new']}, "
                f"force_reduce={actions['force_reduce_pct']}")

    ctx.state["phases"]["market"] = {
        "status": "PASS",
        "vix": market_data["vix"],
        "circuit_level": level.name,
        "actions": actions,
    }

    # LEVEL_3+ 暂停建仓; CircuitLevel 不可用时 (拆分环境降级) 用 level.value >= 3 判断
    _level_value = getattr(level, "value", 0)
    try:
        _level_value = int(_level_value)
    except Exception:
        _level_value = 0
    _is_level3_plus = False
    if CircuitLevel is not None:
        try:
            _is_level3_plus = level >= CircuitLevel.LEVEL_3
        except Exception:
            _is_level3_plus = _level_value >= 3
    else:
        _is_level3_plus = _level_value >= 3
    if _is_level3_plus:
        logger.warning("市场熔断 LEVEL_3+, 暂停建仓")
        ctx.state["phases"]["market"]["build_allowed"] = False
    else:
        ctx.state["phases"]["market"]["build_allowed"] = True

    # === 对冲基金视角: 数据质量监控 ===
    if ctx.data_quality_monitor is not None:
        try:
            # 检查持仓数据质量
            positions = ctx._get_portfolio_positions_for_stress_test() if hasattr(ctx, "_get_portfolio_positions_for_stress_test") else []
            data_for_check = {}
            for pos in positions:
                code = pos.get("code", "")
                if code:
                    data_for_check[code] = {
                        "close": pos.get("price", pos.get("amount", 0)),
                        "volume": pos.get("volume", 0),
                        "timestamp": ctx.trade_date,
                    }
            if data_for_check:
                expected_symbols = [p.get("code") for p in positions if p.get("code")]
                dq_report = ctx.data_quality_monitor.check_market_data(
                    data_for_check, expected_symbols=expected_symbols
                )
                ctx.state["phases"]["market"]["data_quality"] = {
                    "score": dq_report.overall_score,
                    "critical": dq_report.critical_count,
                    "error": dq_report.error_count,
                    "warning": dq_report.warning_count,
                    "passed": dq_report.passed,
                }
                if not dq_report.passed:
                    logger.warning(
                        "[DataQuality] 数据质量未通过: %.1f/100 (critical=%d, error=%d)",
                        dq_report.overall_score,
                        dq_report.critical_count,
                        dq_report.error_count,
                    )
                else:
                    logger.info(
                        "[DataQuality] 数据质量通过: %.1f/100",
                        dq_report.overall_score,
                    )
        except Exception as e:
            logger.error(f"[DataQuality] 数据质量检查失败: {e}", exc_info=True)

    # === AnySearch 实时新闻扫描 (v7.8: 作为 iFinD 的 fallback) ===
    _scan_anysearch_news(ctx)

    return level


def _scan_anysearch_news(ctx: WorkflowContext):
    """使用 AnySearch 扫描实时财经新闻，作为 iFinD 的补充数据源"""
    try:
        from utils.anysearch_connector import AnySearchConnector
        conn = AnySearchConnector()
        if conn.available:
            conn.connect()
            news = conn.get_finance_news()
            if news:
                logger.info(f"[AnySearch] 获取到 {len(news)} 条财经新闻")
                for i, item in enumerate(news[:3], 1):
                    title = item.get('title', '')[:40]
                    url = item.get('url', '')
                    logger.info(f"  [{i}] {title} -> {url}")
                ctx.state["phases"]["market"]["anysearch_news_count"] = len(news)
                ctx.state["phases"]["market"]["anysearch_news"] = news[:3]
            else:
                logger.info("[AnySearch] 未获取到新闻")
        else:
            logger.info("[AnySearch] 不可用，跳过")
    except ImportError:
        logger.info("[AnySearch] 模块未安装，跳过")
    except Exception as e:
        logger.warning(f"[AnySearch] 新闻扫描失败: {e}")
