"""已废弃模式的占位 handler (方案A: cli/modes 模块化重构废弃回退)。

自 量化策略系统_统一入口_v8.6.py 字节级迁出 (2026-09-10, 审计 item 11 结构拆解)。
这些模式打印废弃提示并指向 v8.3_institutional/ 替代入口, 与迁出前行为一致。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from cli.handlers.support import logger


# ============================================================
# v5.10 P0-9 回退 (方案A·2026-08-04): cli/modes 模块化重构已废弃
# ============================================================
# 原计划从 cli.modes 导入 21 个模式处理器, 但 cli/modes 依赖
# core.context / engine.managers / utils.cli_helpers 等"幻影模块"
# (从未在 git 中存在), 导致主入口文件完全无法运行.
#
# 方案A: 废弃 cli/modes 目录, 21 个模式改为本地占位 handler,
# 打印废弃提示并指向 v8.3_institutional/ 替代入口.
# 13 个本地定义的模式 (run_live_monitoring / run_report_generation /
# run_rebalance / run_backtest / run_quick_check / run_model_training /
# run_enhanced_training_mode / run_enhanced_prediction_mode /
# run_hypothesis_test / run_ai_hedge_mode / run_stress_test_mode /
# run_stop_loss_config_mode / run_ml_signal_mode) 保持可用.
# cli/modes 目录保留以备后续重建, 但不再被主入口 import.
def _deprecated_mode_stub(
    mode_name: str, flag: str, alt_entry: str = ""
) -> Callable[..., dict]:
    """生成已废弃模式的占位 handler (方案A: cli/modes 废弃回退)。

    Args:
        mode_name: 模式中文名 (用于日志展示)
        flag: 对应的命令行 flag (如 '--daily')
        alt_entry: 替代入口命令 (无则提示参考 v8.3_institutional/)
    """

    def _stub(args: argparse.Namespace) -> dict:
        logger.warning(
            f"⚠️ {flag} {mode_name} 模式已废弃 (cli/modes 模块化重构回退, 方案A)。"
        )
        if alt_entry:
            logger.info(f"💡 替代入口: {alt_entry}")
        else:
            logger.info(
                "💡 该模式暂未迁移到独立入口, 请参考 v8.3_institutional/ 目录相关脚本。"
            )
        logger.info(
            "   生产入口: py -3.8 v8.3_institutional/daily_workflow.py --phase all"
        )
        return {"deprecated": True, "mode": mode_name, "flag": flag}

    _stub.__name__ = f'run_deprecated_{flag.strip("-")}'
    _stub.__doc__ = (
        f"[已废弃·方案A] {mode_name} — cli/modes 已废弃, 见 v8.3_institutional/"
    )
    return _stub


# 21 个废弃模式占位 (原 cli.modes 导入, 现回退为占位 handler)
run_daily_workflow = _deprecated_mode_stub(
    "每日工作流", "--daily", "py -3.8 v8.3_institutional/daily_workflow.py --phase all"
)
run_risk_monitor = _deprecated_mode_stub("风险监控", "--risk")
# run_etf_flow_monitor / run_social_security_analysis 已恢复为真实实现 (见 get_etf_flow_data 下方)
run_portfolio_optimization = _deprecated_mode_stub("投资组合优化", "--portfolio-opt")
run_kommo_monitor = _deprecated_mode_stub("康波周期监控", "--kommo-monitor")
run_commodity_fundamentals = _deprecated_mode_stub("大宗商品基本面", "--commodity-fund")
run_kondratiev_analysis = _deprecated_mode_stub("康波+十五五交叠", "--kondratiev")
run_fifteen_five_analysis = _deprecated_mode_stub("十五五规划适配", "--fifteen-five")
# run_social_security_analysis 已恢复为真实实现 (见 get_etf_flow_data 下方)
run_macro_analysis = _deprecated_mode_stub("宏观综合分析", "--macro-analysis")
run_ai_decision = _deprecated_mode_stub("AI盘中决策", "--ai-decision")
run_futures_options_scan = _deprecated_mode_stub("期货期权扫描", "--futures-options")
run_unified_monitor = _deprecated_mode_stub("统一监控", "--unified-monitor")
run_hedge_mode = _deprecated_mode_stub("对冲分析", "--hedge")
run_hedge_rebalance_joint = _deprecated_mode_stub(
    "对冲+再平衡联动", "--hedge-rebalance"
)
run_hedge_detail_mode = _deprecated_mode_stub("期货对冲明细", "--hedge-detail")
run_ml_significance_mode = _deprecated_mode_stub("ML显著性验证", "--ml-significance")
run_kronos_predict_mode = _deprecated_mode_stub("Kronos K线预测", "--kronos")
run_gemma_analyze_mode = _deprecated_mode_stub("Gemma分析增强", "--gemma")
run_dcf_mode = _deprecated_mode_stub("DCF估值模型", "--dcf")
run_comps_mode = _deprecated_mode_stub("可比公司分析", "--comps")

