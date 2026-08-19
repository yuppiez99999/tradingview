"""
CLI 模式集合 — v5.10 P0-9 重构
================================================
从主文件迁移的 CLI 模式处理器。
每个子模块职责单一，便于独立测试和维护。

v8.7.3 修复 (2026-08-04):
    - 容错导入: 每个模式用 try/except 包裹, 单个模式导入失败不影响其他
    - 修复 core.context 缺失 (创建 core/context.py 聚合层)
    - 修复 utils.cli_helpers 缺失 (创建降级实现)

模块划分:
  - hypothesis.py             : 假设验证
  - etf_flow.py               : ETF资金流向监控
  - portfolio_optimization.py : 投资组合优化
  - kommo_monitor.py          : 康波周期监控
  - commodity_fundamentals.py : 大宗商品基本面分析
  - risk_monitor.py           : 风险监控
  - futures_options_scan.py   : 期货期权扫描
  - kondratiev_analysis.py    : 康波周期+十五五交叠分析
  - macro_analysis.py         : 宏观综合分析
  - fifteen_five_analysis.py  : 十五五规划适配分析
  - social_security_analysis.py: 社保基金ETF风格追踪
  - ai_decision.py            : AI盘中实时决策
  - daily_workflow.py         : 每日三阶段交易工作流
  - unified_monitor.py        : 统一监控模式
  - ai_hedge_mode.py          : AI Hedge Fund 多分析师联合决策
  - live_monitoring.py        : 实时监控
  - report_generation.py      : 报告生成
  - rebalance.py              : 再平衡
  - backtest.py               : 回测
  - quick_check.py            : 快速检查
  - enhanced_training.py      : ML增强训练
  - enhanced_prediction.py    : ML增强预测
"""

import logging

_logger = logging.getLogger("cli.modes")

# ============================================================
# 容错导入: 每个模式独立导入, 失败则跳过
# ============================================================

# 模式导入映射: (函数名, 模块路径, 函数名)
_MODE_IMPORTS = [
    ("run_hypothesis_test", "cli.modes.hypothesis", "run_hypothesis_test"),
    ("run_etf_flow_monitor", "cli.modes.etf_flow", "run_etf_flow_monitor"),
    ("run_portfolio_optimization", "cli.modes.portfolio_optimization", "run_portfolio_optimization"),
    ("run_kommo_monitor", "cli.modes.kommo_monitor", "run_kommo_monitor"),
    ("run_commodity_fundamentals", "cli.modes.commodity_fundamentals", "run_commodity_fundamentals"),
    ("run_risk_monitor", "cli.modes.risk_monitor", "run_risk_monitor"),
    ("run_futures_options_scan", "cli.modes.futures_options_scan", "run_futures_options_scan"),
    ("run_kondratiev_analysis", "cli.modes.kondratiev_analysis", "run_kondratiev_analysis"),
    ("run_macro_analysis", "cli.modes.macro_analysis", "run_macro_analysis"),
    ("run_fifteen_five_analysis", "cli.modes.fifteen_five_analysis", "run_fifteen_five_analysis"),
    ("run_social_security_analysis", "cli.modes.social_security_analysis", "run_social_security_analysis"),
    ("run_ai_decision", "cli.modes.ai_decision", "run_ai_decision"),
    ("run_daily_workflow", "cli.modes.daily_workflow", "run_daily_workflow"),
    ("run_unified_monitor", "cli.modes.unified_monitor", "run_unified_monitor"),
    ("run_ai_hedge_mode", "cli.modes.ai_hedge_mode", "run_ai_hedge_mode"),
    ("run_hedge_mode", "cli.modes.hedge_mode", "run_hedge_mode"),
    ("run_hedge_detail_mode", "cli.modes.hedge_detail_mode", "run_hedge_detail_mode"),
    ("run_hedge_rebalance_joint", "cli.modes.hedge_rebalance_joint", "run_hedge_rebalance_joint"),
    ("run_live_monitoring", "cli.modes.live_monitoring", "run_live_monitoring"),
    ("run_report_generation", "cli.modes.report_generation", "run_report_generation"),
    ("run_rebalance", "cli.modes.rebalance", "run_rebalance"),
    ("run_backtest", "cli.modes.backtest", "run_backtest"),
    ("run_quick_check", "cli.modes.quick_check", "run_quick_check"),
    ("run_enhanced_training_mode", "cli.modes.enhanced_training", "run_enhanced_training_mode"),
    ("run_enhanced_prediction_mode", "cli.modes.enhanced_prediction", "run_enhanced_prediction_mode"),
    ("run_ml_signal_mode", "cli.modes.ml_signal", "run_ml_signal_mode"),
    ("run_model_training", "cli.modes.model_training", "run_model_training"),
    ("run_stress_test_mode", "cli.modes.stress_test", "run_stress_test_mode"),
    ("run_stop_loss_config_mode", "cli.modes.stop_loss_config", "run_stop_loss_config_mode"),
    ("run_ml_significance_mode", "cli.modes.ml_significance", "run_ml_significance_mode"),
    ("run_kronos_predict_mode", "cli.modes.kronos_predict", "run_kronos_predict_mode"),
    ("run_gemma_analyze_mode", "cli.modes.gemma_analyze", "run_gemma_analyze_mode"),
    ("run_dcf_mode", "cli.modes.dcf_mode", "run_dcf_mode"),
    ("run_comps_mode", "cli.modes.comps_mode", "run_comps_mode"),
]

__all__: list = []
_failed_modes: list = []

for _func_name, _module_path, _func_in_module in _MODE_IMPORTS:
    try:
        import importlib

        _mod = importlib.import_module(_module_path)
        _func = getattr(_mod, _func_in_module)
        globals()[_func_name] = _func
        __all__.append(_func_name)
    except Exception as _e:  # noqa: BLE001
        _failed_modes.append((_module_path, str(_e)))
        _logger.warning("跳过 %s: %s", _module_path, _e)

if _failed_modes:
    _logger.info(
        "CLI 模式加载完成: %d 个成功, %d 个跳过 (缺失依赖)",
        len(__all__),
        len(_failed_modes),
    )
