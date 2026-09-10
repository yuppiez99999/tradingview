"""
量化策略系统 v5.10 — 康波周期 + 十五五规划 + 社保基金ETF追踪 优化版 + 对冲再平衡联动v5.10
整合所有核心模块的统一入口，基于 2026 年交易计划优化版

配置风格: 核心-卫星 + 动量择时 + 风险平价 + 尾部对冲
总资金: 500 万
- 股票和ETF基金: 400 万（80%）
- 对冲头寸: 100 万（20%）
目标: 年化收益 ≥ 8%，最大回撤 ≤ 15%
标的数量: 23 只（22 股票/ETF + 现金）

标的配置（2026 优化版）:
  - 核心宽基 ETF（30%）: 510300/510500/512100/588000/159915
  - 科技成长个股（25%）: 688041/300308/300274/002371/688981/600276/603019
  - 高端制造/顺周期（20%）: 600089/600875/601088/600219/600019
  - 资源/防御（20%）: 518880/000792/600900/000858/601318/600036
  - 现金缓冲（5%）: CASH

功能模块:
  1. 实时行情数据获取 (Wind / 通达信 / AKShare / yfinance / tushare / 新浪 多级回退) ⭐
  2. 自动交易与盘中再平衡
  3. 增强版再平衡引擎 (Excel数据驱动 - 5表联动)
  4. 每日报告生成 (含AI分析)
  6. 组合管理与仓位优化 (新增：等权重/风险平价/风险配比/因子配比)
  7. 策略注册表与假设验证机制
  8. 研究目标生命周期管理
  9. ETF国家队资金流向监控 (投资决策参考)
  10. 康波周期大宗商品监控 (新增：价格/宏观/库存三维度)
  11. 时序预测模型支持 (新增：Transformer骨架集成)
  12. LSEG金融数据集成 (新增：股票/债券/FX/期权/宏观指标)
  13. 康波周期+十五五交叠分析 (v5.1新增：周期阶段判定+行业轮动+商品信号)
  14. 十五五规划适配分析 (v5.1新增：持仓对标+政策对齐评分+权重调整)
  16. 期货期权扫描 (新增：期货市场+期权市场+套利机会)
  17. 统一监控模式 (新增：一键启动所有模块并行运行)
18. AI Hedge Fund - 19位大师级AI分析师联合决策 (v5.6新增)
19. ML模型预测信号 - GradientBoosting涨跌预测 (v5.6新增)
20. 对冲再平衡联动引擎 v5.10 - 组合自触发+多指数对冲+成本过滤 (v5.10新增)



运行模式:
  - 实时监控模式: 盘中实时行情监控 + 自动再平衡
  - 报告生成模式: 生成每日持仓报告
  - 回测模式: 历史数据回测验证
  - 风险监控模式: 止损止盈状态检查
  - ETF资金流向: 追踪国家队资金动向
  - 假设验证模式: 验证交易假设
  - 投资组合优化: 多策略资产配置对比 (新增)
  - 康波周期监控: 大宗商品全维度监控 (新增)
  - 大宗商品基本面: Wind数据综合分析 (新增)
  - 时序预测训练: Transformer模型训练 (新增)

使用方式:
  python "量化策略系统 v5.10.py" --daily --phase premarket   # 盘前交易计划
  python "量化策略系统 v5.10.py" --daily --phase intraday    # 盘中策略扫描
  python "量化策略系统 v5.10.py" --daily --phase postmarket  # 盘后综合报告
  python "量化策略系统 v5.10.py" --daily --phase all         # 全流程
  python "量化策略系统 v5.10.py" --rebalance      # 执行Excel再平衡
  python "量化策略系统 v5.10.py" --rebalance --sync-sl  # 同步止损止盈
  python "量化策略系统 v5.10.py" --live           # 实时监控模式
  python "量化策略系统 v5.10.py" --report         # 生成报告
  python "量化策略系统 v5.10.py" --etf-flow       # ETF资金流向监控
  python "量化策略系统 v5.10.py" --portfolio-opt  # 投资组合优化
  python "量化策略系统 v5.10.py" --kommo-monitor  # 康波周期监控
  python "量化策略系统 v5.10.py" --commodity-fund # 大宗商品基本面
  python "量化策略系统 v5.10.py" --train-model    # 时序预测训练
  python "量化策略系统 v5.10.py" --train-enhanced              # ML增强训练 v2.0 (四维优化)
  python "量化策略系统 v5.10.py" --train-enhanced --horizon 5  # T+5中期预测训练
  python "量化策略系统 v5.10.py" --train-enhanced --horizon 10 --optuna  # T+10+贝叶斯
  python "量化策略系统 v5.10.py" --kondratiev     # 康波周期+十五五交叠分析 (v5.1)
  python "量化策略系统 v5.10.py" --fifteen-five   # 十五五规划适配分析 (v5.1)
  python "量化策略系统 v5.10.py" --social-security # 社保基金ETF风格追踪 (v5.1)
  python "量化策略系统 v5.10.py" --macro-analysis  # 宏观综合分析（一键运行三大）(v5.1)
  python "量化策略系统 v5.10.py" --ml-signal       # ML模型预测信号
  python "量化策略系统 v5.10.py" --ml-enhanced     # ML增强预测 v2.0 (四维优化模型)

架构特点 (借鉴Vibe-Trading):
  - Connector-first: 统一数据源抽象，支持多连接器配置 (Wind/通达信/AKShare/yfinance/tushare/新浪) ⭐
  - 策略注册表: 中心化策略管理与版本控制
  - 假设验证: 支持统计检验与随机对照试验
  - 研究目标: 支持目标生命周期管理
  - 实时反馈: 长时间任务的进度可视化
  - Excel驱动: 5个Excel表格联动，配置即策略
  - 算力赛道: 康波第六轮核心驱动力配置
  - 投资组合优化: 等权重/风险平价/风险配比/因子配比/自定义配置 (新增)
  - 康波周期监控: 商品价格+宏观指标+产业库存三维度 (新增)
  - 时序预测: Transformer模型骨架集成 (新增)
  - LSEG集成: 国际金融市场全维度数据 (股票/债券/FX/期权/宏观) ⭐
"""

import argparse
import glob
import os
import sys
import time

from cli.handlers.deprecated_modes import (  # noqa: E402
    run_ai_decision,
    run_commodity_fundamentals,
    run_comps_mode,
    run_daily_workflow,
    run_dcf_mode,
    run_fifteen_five_analysis,
    run_futures_options_scan,
    run_gemma_analyze_mode,
    run_hedge_detail_mode,
    run_hedge_mode,
    run_hedge_rebalance_joint,
    run_kommo_monitor,
    run_kondratiev_analysis,
    run_kronos_predict_mode,
    run_macro_analysis,
    run_ml_significance_mode,
    run_portfolio_optimization,
    run_risk_monitor,
    run_unified_monitor,
)
from cli.handlers.helpers import (  # noqa: E402
    _check_commodity_module,
    _enforce_live_gate,
    _log_execution_summary,
    archive_report,
    get_etf_flow_data,
    get_ml_signal_section,
    get_stock_name,
    write_report_file,
)
from cli.handlers.support import (  # noqa: E402
    BASE_DIR,
    HARD_STOP_MAX_DRAWDOWN,
    LOG_DIR,
    ML_ENHANCED_PREDICTOR_AVAILABLE,
    ML_ENHANCED_TRAINER_AVAILABLE,
    TAIL_HEDGE_THRESHOLD,
    EnhancedPredictor,
    ETFFundFlowMonitor,
    ExcelDrivenRebalancingEngineV4,
    ProgressIndicator,
    SocialSecurityETFTracker,
    StrategyRegistry,
    auto_trading,
    config_hub,
    config_manager,
    connector_manager,
    daily_report,
    data_provider,
    generate_stress_report,
    graceful_fallback,
    load_portfolio_config,
    logger,
    pd,
    rebalance_engine,
    run_enhanced_training,
    stop_loss,
    strategy_registry,
)
from utils.datetime_utils import now_bj  # 业务时间(北京): 替代裸 datetime.now() (DTZ005)

"""
量化策略系统 v5.10 — 康波周期 + 十五五规划 + 社保基金ETF追踪 优化版 + 对冲再平衡联动v5.10
整合所有核心模块的统一入口，基于 2026 年交易计划优化版

配置风格: 核心-卫星 + 动量择时 + 风险平价 + 尾部对冲
总资金: 500 万
- 股票和ETF基金: 400 万（80%）
- 对冲头寸: 100 万（20%）
目标: 年化收益 ≥ 8%，最大回撤 ≤ 15%
标的数量: 23 只（22 股票/ETF + 现金）

标的配置（2026 优化版）:
  - 核心宽基 ETF（30%）: 510300/510500/512100/588000/159915
  - 科技成长个股（25%）: 688041/300308/300274/002371/688981/600276/603019
  - 高端制造/顺周期（20%）: 600089/600875/601088/600219/600019
  - 资源/防御（20%）: 518880/000792/600900/000858/601318/600036
  - 现金缓冲（5%）: CASH

功能模块:
  1. 实时行情数据获取 (Wind / 通达信 / AKShare / yfinance / tushare / 新浪 多级回退) ⭐
  2. 自动交易与盘中再平衡
  3. 增强版再平衡引擎 (Excel数据驱动 - 5表联动)
  4. 每日报告生成 (含AI分析)
  6. 组合管理与仓位优化 (新增：等权重/风险平价/风险配比/因子配比)
  7. 策略注册表与假设验证机制
  8. 研究目标生命周期管理
  9. ETF国家队资金流向监控 (投资决策参考)
  10. 康波周期大宗商品监控 (新增：价格/宏观/库存三维度)
  11. 时序预测模型支持 (新增：Transformer骨架集成)
  12. LSEG金融数据集成 (新增：股票/债券/FX/期权/宏观指标)
  13. 康波周期+十五五交叠分析 (v5.1新增：周期阶段判定+行业轮动+商品信号)
  14. 十五五规划适配分析 (v5.1新增：持仓对标+政策对齐评分+权重调整)
  16. 期货期权扫描 (新增：期货市场+期权市场+套利机会)
  17. 统一监控模式 (新增：一键启动所有模块并行运行)
18. AI Hedge Fund - 19位大师级AI分析师联合决策 (v5.6新增)
19. ML模型预测信号 - GradientBoosting涨跌预测 (v5.6新增)
20. 对冲再平衡联动引擎 v5.10 - 组合自触发+多指数对冲+成本过滤 (v5.10新增)



运行模式:
  - 实时监控模式: 盘中实时行情监控 + 自动再平衡
  - 报告生成模式: 生成每日持仓报告
  - 回测模式: 历史数据回测验证
  - 风险监控模式: 止损止盈状态检查
  - ETF资金流向: 追踪国家队资金动向
  - 假设验证模式: 验证交易假设
  - 投资组合优化: 多策略资产配置对比 (新增)
  - 康波周期监控: 大宗商品全维度监控 (新增)
  - 大宗商品基本面: Wind数据综合分析 (新增)
  - 时序预测训练: Transformer模型训练 (新增)

使用方式:
  python "量化策略系统 v5.10.py" --daily --phase premarket   # 盘前交易计划
  python "量化策略系统 v5.10.py" --daily --phase intraday    # 盘中策略扫描
  python "量化策略系统 v5.10.py" --daily --phase postmarket  # 盘后综合报告
  python "量化策略系统 v5.10.py" --daily --phase all         # 全流程
  python "量化策略系统 v5.10.py" --rebalance      # 执行Excel再平衡
  python "量化策略系统 v5.10.py" --rebalance --sync-sl  # 同步止损止盈
  python "量化策略系统 v5.10.py" --live           # 实时监控模式
  python "量化策略系统 v5.10.py" --report         # 生成报告
  python "量化策略系统 v5.10.py" --etf-flow       # ETF资金流向监控
  python "量化策略系统 v5.10.py" --portfolio-opt  # 投资组合优化
  python "量化策略系统 v5.10.py" --kommo-monitor  # 康波周期监控
  python "量化策略系统 v5.10.py" --commodity-fund # 大宗商品基本面
  python "量化策略系统 v5.10.py" --train-model    # 时序预测训练
  python "量化策略系统 v5.10.py" --train-enhanced              # ML增强训练 v2.0 (四维优化)
  python "量化策略系统 v5.10.py" --train-enhanced --horizon 5  # T+5中期预测训练
  python "量化策略系统 v5.10.py" --train-enhanced --horizon 10 --optuna  # T+10+贝叶斯
  python "量化策略系统 v5.10.py" --kondratiev     # 康波周期+十五五交叠分析 (v5.1)
  python "量化策略系统 v5.10.py" --fifteen-five   # 十五五规划适配分析 (v5.1)
  python "量化策略系统 v5.10.py" --social-security # 社保基金ETF风格追踪 (v5.1)
  python "量化策略系统 v5.10.py" --macro-analysis  # 宏观综合分析（一键运行三大）(v5.1)
  python "量化策略系统 v5.10.py" --ml-signal       # ML模型预测信号
  python "量化策略系统 v5.10.py" --ml-enhanced     # ML增强预测 v2.0 (四维优化模型)

架构特点 (借鉴Vibe-Trading):
  - Connector-first: 统一数据源抽象，支持多连接器配置 (Wind/通达信/AKShare/yfinance/tushare/新浪) ⭐
  - 策略注册表: 中心化策略管理与版本控制
  - 假设验证: 支持统计检验与随机对照试验
  - 研究目标: 支持目标生命周期管理
  - 实时反馈: 长时间任务的进度可视化
  - Excel驱动: 5个Excel表格联动，配置即策略
  - 算力赛道: 康波第六轮核心驱动力配置
  - 投资组合优化: 等权重/风险平价/风险配比/因子配比/自定义配置 (新增)
  - 康波周期监控: 商品价格+宏观指标+产业库存三维度 (新增)
  - 时序预测: Transformer模型骨架集成 (新增)
  - LSEG集成: 国际金融市场全维度数据 (股票/债券/FX/期权/宏观) ⭐
"""
# 21 个废弃模式占位 (原 cli.modes 导入, 现回退为占位 handler)
# run_etf_flow_monitor / run_social_security_analysis 已恢复为真实实现 (见 get_etf_flow_data 下方)
# run_social_security_analysis 已恢复为真实实现 (见 get_etf_flow_data 下方)
# ============================================================
# 通用辅助函数 — 消除各 run_* 模式中的重复样板
# ============================================================
def run_etf_flow_monitor(args: argparse.Namespace) -> dict:
    """ETF资金流向监控 (集成版) — Wind MCP/akshare 实时数据 + 国家队信号

    2026-09-09 从废弃桩恢复: 调用 utils.etf_fund_tracker (移植自 etf-tracker)。
    """
    logger.info("\n📊 ETF资金流向监控 (集成版: Wind MCP P1 → akshare P3 → 模拟 P6)")
    logger.info("=" * 70)
    if ETFFundFlowMonitor is None:
        logger.warning("⚠️ ETF资金流向监控模块不可用, 无法执行")
        return {"ok": False, "reason": "ETFFundFlowMonitor 未加载"}

    progress = ProgressIndicator("ETF资金流向分析", 5)
    progress.update(1, "初始化追踪器...")
    source = getattr(args, "source", "auto")
    days = int(getattr(args, "days", 5))
    top = int(getattr(args, "top", 15))
    tracker = ETFFundFlowMonitor(days=days, source=source, top_n=top)

    progress.update(2, "获取ETF行情与资金流 (Wind MCP 优先)...")
    tracker.analyze_fund_flow()
    progress.update(3, "检测国家队信号...")
    signals = tracker.detect_signals()
    progress.update(4, "生成投资建议...")
    tracker.get_investment_suggestion()
    progress.update(5, "生成报告并归档...")
    report = tracker.generate_report()
    write_report_file(report, getattr(args, "output", None))
    # 同步写入 reports/ (与独立脚本一致)
    report_path = os.path.join(
        BASE_DIR, "reports", f"report_{now_bj():%Y%m%d}.md"
    )
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(BASE_DIR, "reports", "latest.md"), "w", encoding="utf-8") as f:
        f.write(report)
    archive_path = tracker.archive(report)

    logger.info("\n" + report)
    if archive_path:
        logger.info(f"📁 已归档至: {archive_path}")
    progress.complete(f"检测到 {len(signals)} 条信号")
    return {"ok": True, "signals": len(signals), "archive": archive_path}


def run_social_security_analysis(args: argparse.Namespace) -> dict:
    """社保基金ETF风格追踪 (集成版) — 真实资金流数据 + 社保四风格映射

    数据流: utils.etf_fund_tracker (Wind MCP) → SocialSecurityETFTracker.analyze(flow_data)
    """
    logger.info("\n🏛️ 社保基金ETF风格追踪 (集成版)")
    logger.info("=" * 70)
    if ETFFundFlowMonitor is None or SocialSecurityETFTracker is None:
        logger.warning("⚠️ 社保ETF追踪所需模块未加载, 无法执行")
        return {"ok": False, "reason": "依赖模块缺失"}

    progress = ProgressIndicator("社保基金ETF追踪", 4)
    progress.update(1, "获取ETF资金流数据 (Wind MCP)...")
    flow_data = get_etf_flow_data()  # 复用集成版真实数据
    progress.update(2, "加载社保基金风格分类器...")
    ss_tracker = SocialSecurityETFTracker()
    progress.update(3, "综合分析(风格映射+资金信号)...")
    analysis = ss_tracker.analyze(flow_data)
    progress.update(4, "生成报告...")
    report = ss_tracker.generate_report(flow_data)
    write_report_file(report, getattr(args, "output", None))
    archive_path = archive_report(report, "社保基金ETF追踪")

    logger.info("\n" + report)
    progress.complete("完成")
    return {"ok": True, "signals": len(analysis.get("signals", [])), "archive": archive_path}


def run_ml_signal_mode(args: argparse.Namespace) -> None:
    """ML模型预测信号模式 - 基于训练好的模型生成涨跌信号

    P2-4: 函数体此前为空 (假成功)。未实现时显式抛错避免静默成功。
    """
    raise NotImplementedError(
        "[P2-4] run_ml_signal_mode 未实现: ML信号功能已迁移, 请使用 "
        "--ml-enhanced (ML增强预测) 或 v8.3_institutional/ 独立入口"
    )


def run_live_monitoring(args: argparse.Namespace) -> None:
    """实时监控模式 - 盘中实时行情监控 + 自动再平衡 + ML信号"""
    logger.info("\n🚀 启动实时监控模式")
    logger.info("=" * 70)

    progress = ProgressIndicator("初始化系统", 6)

    progress.update(1, "扫描ML预测信号...")
    ml_result = get_ml_signal_section(return_raw=True)
    if ml_result:
        ml_section, result = ml_result
        if "signals" in result:
            sig = result["signals"]
            buy_n = len(sig.get("buy", []))
            sell_n = len(sig.get("sell", []))
            hold_n = len(sig.get("hold", []))
            model_name = result.get("model_info", {}).get("best_model", "?")
            model_acc = result.get("model_info", {}).get("accuracy", 0)
            logger.info(
                f"\n  [ML信号] {model_name} (Acc={model_acc:.1%}) "
                f"买入:{buy_n} 卖出:{sell_n} 持有:{hold_n}"
            )
            for s in sig.get("buy", [])[:5]:
                name = get_stock_name(s["code"])
                logger.info(
                    f"    买入 {s['code']} {name:8s} 概率:{s['probability']:.1%}"
                )
    else:
        logger.warning("  ⚠️ ML信号不可用")

    progress.update(2, "加载交易系统...")
    AutoTradingSystem = auto_trading.get("AutoTradingSystem")

    if AutoTradingSystem:
        progress.update(3, "创建交易实例...")
        system = AutoTradingSystem()

        progress.update(4, "连接数据源...")

        progress.update(5, "启动监控循环...")
        system.run()
        progress.complete("监控结束")
    else:
        progress.complete("❌ 自动交易系统模块不可用")


def run_report_generation(args: argparse.Namespace) -> None:
    """报告生成模式 - 生成每日持仓报告 + ML信号 (v5.7 Phase 3: ConfigHub集成)"""
    logger.info("\n📝 生成每日报告")
    logger.info("=" * 70)

    progress = ProgressIndicator("生成报告", 7)

    progress.update(1, "加载报告模块...")
    generate_daily_report = daily_report.get("generate_daily_report")

    # v5.7 Phase 3: 使用 ConfigHub 获取统一配置
    portfolio_file = os.path.join(BASE_DIR, "config", "portfolio.yaml")
    if config_hub and not config_hub.check_and_reload():
        # 配置无变更，显示摘要
        summary = config_hub.get_summary()
        logger.info(
            f"  📋 配置摘要: {summary['asset_count']}个标的, "
            f"总资金 {summary['total_capital']:,.0f}, "
            f"数据源: {summary['primary_source']}"
        )

    if generate_daily_report:
        try:
            progress.update(2, "读取配置...")

            progress.update(3, "生成报告内容...")
            report_content = generate_daily_report(
                portfolio_file=portfolio_file, enable_ai_analysis=not args.no_ai
            )

            # ML信号追加到报告（若LLM已内置ML分析则跳过）
            if not getattr(args, "no_ml", False):
                progress.update(4, "检查ML预测信号...")
                if "本地ML量化模型分析" in report_content:
                    logger.info("  ✅ ML分析已由日报引擎自动内置，跳过追加")
                else:
                    ml_section = get_ml_signal_section()
                    if ml_section:
                        report_content += ml_section
                        logger.info("  ✅ ML信号已追加")
                    else:
                        logger.warning("  ⚠️ ML信号不可用")
            else:
                progress.update(4, "跳过ML信号...")

            progress.update(5, "保存报告...")
            archive_report(
                report_content, "综合日报", ext=".txt"
            )  # 归档路径由 archive_report 内部 logger 输出

            progress.complete("✅ 报告归档完成")

        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            progress.complete(f"\n❌ 报告生成失败: {e}")
            logger.error(f"报告生成失败: {e}")
    else:
        progress.complete("❌ 每日报告模块不可用")


def run_rebalance(args: argparse.Namespace) -> None:
    """再平衡模式 - 执行再平衡计划 (支持Excel和portfolio.yaml两种方式)"""
    logger.info("\n🔄 执行再平衡计划")
    logger.info("=" * 70)

    progress = ProgressIndicator("再平衡执行", 6)

    # 使用增强版Excel驱动引擎
    progress.update(1, "初始化再平衡引擎...")
    strategy_registry = StrategyRegistry()
    engine = ExcelDrivenRebalancingEngineV4(strategy_registry=strategy_registry)

    progress.update(2, "加载配置文件...")
    loaded = engine.load_all()

    # 如果Excel加载失败,尝试从portfolio.yaml加载
    if not loaded:
        logger.warning("\n⚠️ Excel文件不存在,尝试从portfolio.yaml加载...")
        try:
            import yaml

            yaml_path = os.path.join(BASE_DIR, "config", "portfolio.yaml")
            if os.path.exists(yaml_path):
                with open(yaml_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                assets = config.get("assets", [])
                if assets:
                    engine.complete_plan = [
                        {
                            "证券代码": a["code"],
                            "证券名称": a["name"],
                            "目标权重": a.get("target_weight", 0.1),
                            "风险权重": 0.25,
                            "当前仓位": 0,
                            "调整幅度": 0,
                            "当前股数": 0,
                            "最新价": 0,
                            "当前市值": 0,
                            "目标市值": 0,
                            "目标股数": 0,
                            "需调整股数": 0,
                            "交易方向": "待定",
                            "预计交易金额": 0,
                            "操作类型": "待定",
                            "执行批次": "待定",
                            "止损位": 0,
                            "止盈位": 0,
                        }
                        for a in assets
                    ]
                    engine.batch_plan = []
                    engine.is_loaded = True
                    loaded = True
                    logger.info(f"✅ 已从portfolio.yaml加载 {len(assets)} 只标的配置")
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"⚠️ portfolio.yaml加载失败: {e}")

    if engine.is_loaded:
        progress.update(3, "构建交易指令...")
        engine.build_trade_orders()

        progress.update(4, "生成报告...")
        report = engine.generate_report()
        logger.info("\n" + report)

        # 注册研究假设
        if strategy_registry:
            strategy_registry.register_hypothesis(
                "rebalance_2026",
                {
                    "title": "2026年组合再平衡",
                    "description": "基于当前持仓的再平衡计划",
                    "hypothesis": "核心-卫星策略配置能带来超额收益",
                    "status": "active",
                },
            )
            logger.info("已注册研究假设: rebalance_2026")

        if args.sync_sl:
            progress.update(5, "同步止损止盈规则...")
            engine.sync_to_stop_loss_monitor()
            logger.info("\n✅ 止损止盈规则已同步到 config/rebalance_stop_loss_v43.json")

        write_report_file(report, args.output)

        progress.complete("✅ 再平衡执行完成")
    else:
        progress.complete("❌ 无法加载再平衡数据")
        logger.info("\n💡 提示: 请检查以下文件是否存在:")
        logger.info("  1. config/portfolio.yaml (必需)")
        logger.info("  2. data_extraction_*.xlsx (可选,用于详细再平衡计划)")


def run_backtest(args: argparse.Namespace) -> None:
    """回测模式 - 历史数据回测验证"""
    logger.info("\n📊 运行回测")
    logger.info("=" * 70)

    progress = ProgressIndicator("回测执行", 4)

    progress.update(1, "加载回测模块...")
    try:
        from fast_backtest import run_fast_backtest

        progress.update(2, "执行快速回测...")
        run_fast_backtest()
        progress.update(3, "生成报告...")
        progress.complete("\n✅ 回测完成")
        return
    except ImportError:
        try:
            from backtest_engine import BacktestEngine
            from portfolio_config import PortfolioConfig

            progress.update(2, "加载配置...")
            config = load_portfolio_config()

            if config:
                portfolio = PortfolioConfig()
                settings = {
                    "capital": {"total": 1000000},
                    "rebalance": {"threshold": 0.06, "min_interval_days": 5},
                    "targets": {"annual_return": 0.08, "max_drawdown": 0.15},
                }
                engine = BacktestEngine(portfolio, settings)

                progress.update(3, "查找历史数据...")
                excel_files = [
                    f
                    for f in os.listdir(BASE_DIR)
                    if f.startswith("data_extraction") and f.endswith(".xlsx")
                ]
                if excel_files:
                    result = engine.run_backtest(os.path.join(BASE_DIR, excel_files[0]))
                    logger.info("\n✅ 回测完成")
                    logger.info(result)
                else:
                    logger.error("\n❌ 未找到历史数据文件")
            progress.complete()
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            progress.complete(f"❌ 回测模块不可用: {e}")


def run_quick_check(args: argparse.Namespace) -> None:
    """快速检查模式 - 检查系统状态"""
    logger.info("\n🔍 系统状态快速检查")
    logger.info("=" * 70)

    # 检查模块可用性
    def _package_available(pkg_name: str) -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec(pkg_name) is not None
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            return False

    modules = {
        "数据提供层": data_provider.get("get_quotes_batch") is not None,
        "自动交易系统": auto_trading.get("AutoTradingSystem") is not None,
        "再平衡引擎": rebalance_engine.get("RebalancingEngine") is not None,
        "每日报告": daily_report.get("generate_daily_report") is not None,
        "止损止盈监控": stop_loss.get("StopLossMonitor") is not None,
        "策略注册表": strategy_registry is not None,
        "连接器管理器": connector_manager is not None,
        "ETF资金流向监控": True,  # 内置模块，始终可用
        "投资组合优化": _package_available("pandas") or _package_available("numpy"),
        "康波周期监控": _package_available("yfinance") or _package_available("tushare"),
        "大宗商品基本面": _check_commodity_module(),  # 动态检查
        "时序预测模型": all(
            _package_available(pkg) for pkg in ["torch", "sklearn", "pandas", "numpy"]
        ),  # 新增
        # v5.9 新增模块
        "多模型路由器": _package_available("yaml"),  # 需要 yaml
        "Wind数据供应器": os.path.exists(
            os.path.join(BASE_DIR, "utils", "wind_data_provider.py")
        ),
    }

    logger.info("\n📦 模块状态:")
    for name, available in modules.items():
        status = "✅" if available else "❌"
        logger.info(f"  {status} {name}")

    # 检查策略注册表
    logger.info("\n📋 策略注册表:")
    try:
        strategies = strategy_registry.list()
        logger.info(f"  ✅ {len(strategies)} 个策略已注册")
        for strategy_id in strategies[:5]:
            strategy = strategy_registry.get(strategy_id)
            logger.info(f"    - {strategy['name']} (v{strategy['version']})")
    except (AttributeError, TypeError) as e:
        logger.warning(f"  ⚠️ 策略注册表 API 不完整 (list/get 方法缺失), 跳过: {e}")

    # 检查配置管理器
    logger.info("\n⚙️ 配置管理器:")
    try:
        configs = config_manager.get_all()
        logger.info(f"  ✅ 已加载 {len(configs)} 类配置")
        for config_name, config in configs.items():
            logger.info(f"    - {config_name}: {len(config)} 项配置")
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"  ❌ 配置管理器异常: {e}")

    # 检查ETF资金流向监控配置
    logger.info("\n📊 ETF资金流向监控:")
    if ETFFundFlowMonitor is not None:
        logger.info(f"  ✅ 监控标的: {len(ETFFundFlowMonitor.ETF_LIST)} 只ETF")
    else:
        logger.warning(
            "  ⚠️ ETFFundFlowMonitor 未加载 (engine/managers 缺失), 跳过ETF监控检查"
        )
    etf_config = config_manager.get("etf_monitor", "signal_high_threshold")
    if etf_config:
        try:
            logger.info(
                f"  ✅ 信号阈值: 高{etf_config/1e8:.0f}亿/中{config_manager.get('etf_monitor', 'signal_medium_threshold')/1e8:.0f}亿/低{config_manager.get('etf_monitor', 'signal_low_threshold')/1e8:.0f}亿"  # noqa: E501
            )
        except (TypeError, ValueError) as e:
            logger.warning(f"  ⚠️ ETF信号阈值配置格式异常, 跳过: {e}")

    # 检查数据源连接器状态
    logger.info("\n🔗 数据源连接器:")
    try:
        connector_status = connector_manager.get_status()
        logger.info(
            f"  当前活跃连接器: {connector_status.get('active_connector', 'None')}"
        )
        logger.error(
            f"  是否降级模式: {'✅ 是' if connector_status.get('fallback_mode') else '❌ 否'}"
        )
        logger.info(f"  注册连接器数: {connector_status.get('total_connectors', 0)}")
        logger.info(
            f"  可用连接器数: {connector_status.get('available_connectors', 0)}"
        )
    except (AttributeError, TypeError):
        active = connector_manager.get_active_connector()
        n = len(connector_manager.list_connectors())
        logger.warning(
            f"  ⚠️ 连接器状态 API 不完整 (get_status 缺失), 简化展示: 活跃={'有' if active else '无'}, 已注册 {n} 个"
        )

    # v5.9: 检查多模型路由器和 Wind 数据供应器
    logger.info("\n🤖 v5.10 AI 决策模块:")
    try:
        from utils.multi_model_router import ModelRouter

        router = ModelRouter()
        logger.info(
            f"  ✅ 多模型路由器: 已注册 {len(router.config.get('providers', {}))} 个模型提供商"
        )
        scenes = router.config.get("scenes", {})
        for scene_name, scene_cfg in scenes.items():
            primary = scene_cfg.get("primary", {})
            hedged = scene_cfg.get("parallel_hedge", {}).get("enabled", False)
            cv = scene_cfg.get("cross_validation", {}).get("enabled", False)
            extra = ""
            if hedged:
                extra = " (并行对冲)"
            elif cv:
                extra = " (交叉验证)"
            logger.info(
                f"    {scene_name}: {primary.get('provider')}/{primary.get('model')}{extra}"
            )
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"  ❌ 多模型路由器: {e}")

    try:
        from utils.wind_data_provider import WindDataProvider

        wp = WindDataProvider()
        status = wp.health_check()
        wind_ok = "可用" if status.get("wind_mcp_available") else "不可用(降级)"
        logger.warning(
            f"  {'✅' if status.get('wind_mcp_available') else '⚠️'} Wind MCP: {wind_ok}"
        )
        logger.info(f"    指数缓存: {status.get('index_cache_size', 0)} 项")
        logger.info(f"    基本面缓存: {status.get('fundamental_cache_size', 0)} 项")
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"  ❌ Wind 数据供应器: {e}")

    # 本地 LLM 模型选型 (llmfit 集成, P0)
    logger.info("\n🖥️ 本地 LLM 选型 (llmfit):")
    try:
        from utils.local_model_selector import quick_check as llmfit_quick_check

        llmfit_status = llmfit_quick_check()
        if llmfit_status["available"]:
            logger.info(f"  ✅ llmfit 可用 | 硬件: {llmfit_status['hardware']}")
            logger.info(f"  ✅ 推荐模型: {llmfit_status['selected']}")
            for i, rec in enumerate(llmfit_status["recommendations"][:3], 1):
                logger.info(f"    {i}. {rec}")
        else:
            logger.warning(f"  ⚠️ llmfit 不可用: {llmfit_status['reason']}")
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.warning(f"  ⚠️ 本地 LLM 选型检查跳过: {e}")

    # 检查配置文件
    logger.info("\n📋 配置文件:")
    config_files = [
        "config/portfolio.yaml",
        "config/settings.yaml",
        "config/positions.json",
        "config/rebalance.yaml",
        "config/risk.yaml",
    ]
    for config_file in config_files:
        path = os.path.join(BASE_DIR, config_file)
        exists = os.path.exists(path)
        status = "✅" if exists else "❌"
        logger.info(f"  {status} {config_file}")

    # 检查数据缓存
    logger.info("\n💾 数据缓存:")
    cache_dir = os.path.join(BASE_DIR, "data", "cache")
    if os.path.exists(cache_dir):
        cache_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
        logger.info(f"  ✅ 缓存目录存在，{len(cache_files)}个文件")
    else:
        logger.error("  ❌ 缓存目录不存在")

    # 检查报告目录
    logger.info("\n📄 报告目录:")
    reports_dir = os.path.join(BASE_DIR, "reports")
    if os.path.exists(reports_dir):
        report_days = len(os.listdir(reports_dir))
        logger.info(f"  ✅ 报告目录存在，{report_days}天报告")
    else:
        logger.error("  ❌ 报告目录不存在")

    # 检查日志目录
    logger.info("\n📝 日志目录:")
    if os.path.exists(LOG_DIR):
        log_files = [f for f in os.listdir(LOG_DIR) if f.startswith("system_")]
        logger.info(f"  ✅ 日志目录存在，{len(log_files)}个日志文件")
    else:
        logger.error("  ❌ 日志目录不存在")

    # 降级模式状态
    logger.info("\n🛡️ 优雅降级状态:")
    try:
        fallback_on = graceful_fallback.is_fallback_mode()
        logger.warning(f"  当前降级模式: {'⚠️ 已启用' if fallback_on else '✅ 正常'}")
    except (AttributeError, TypeError) as e:
        logger.warning(f"  ⚠️ 降级状态 API 不完整 (is_fallback_mode 缺失), 跳过: {e}")

    logger.info("\n" + "=" * 70)


def run_model_training(args: argparse.Namespace) -> None:
    """统一模型训练入口 (v5.7 Phase 2 增强)

    整合所有训练管线:
    - ML分类器: Optuna贝叶斯优化 + Triple Barrier标签 (★ NEW)
    - ML集成: model_train/signal_composer.py (三源信号合成)
    - 情感分析: model_train/finbert_sentiment.py (FinBERT)
    - DL时序: 16_金融市场预测模型/patchtst_trainer.py (PatchTST, 需GPU)

    P2-4: 此函数体此前为空 (假成功)。功能已迁移, 未实现时显式抛错避免静默成功。
    """
    raise NotImplementedError(
        "[P2-4] run_model_training 未实现: 模型训练功能已迁移, 请使用 "
        "--train-enhanced (ML增强训练) 或 v8.3_institutional/ 独立入口"
    )


def run_enhanced_training_mode(args: argparse.Namespace) -> dict | None:
    """ML增强训练 v2.0 — 四维优化管线"""
    if not ML_ENHANCED_TRAINER_AVAILABLE:
        logger.error("\n❌ 增强训练引擎未安装")
        return None

    horizon = getattr(args, "horizon", 1)
    filter_osc = getattr(args, "filter_oscillation", True)
    use_optuna = getattr(args, "optuna", False)
    n_trials = getattr(args, "trials", 50)
    n_features = getattr(args, "features", 30)
    northbound_path = getattr(args, "northbound", None) or None

    logger.info("\n" + "=" * 70)
    logger.info("  🧠 ML 增强训练引擎 v2.0 — 四维优化管线")
    logger.info("=" * 70)
    logger.info(
        f"  预测窗口: T+{horizon} | 过滤震荡: {filter_osc} | Optuna: {use_optuna}"
    )
    logger.info(f"  特征数: {n_features} | 样本加权: 时间衰减+波动率")
    logger.info("  增强特征: 行业RS+市场宽度+北向+PE/PB/ROE")
    logger.info("-" * 70)

    progress = ProgressIndicator("增强训练", 5)

    # ── 数据准备: 收敛标的池到当前持仓 + data_provider 降级链刷新 ──
    # (v5.10 原生 data/cache 平铺已不存在; data/cache/klines 为 791 标的陈旧
    #  全市场缓存, 直接喂入会全市场合并训练且数据停在历史日期.)
    try:
        from utils.enhanced_kline_prep import prepare_enhanced_kline_cache
    except Exception as e:  # noqa: BLE001  # fail-safe
        logger.error(f"❌ 增强训练数据准备模块加载失败: {e}")
        return None
    progress.update(1, "刷新持仓K线缓存(data_provider 降级链)...")
    prep = prepare_enhanced_kline_cache(
        os.path.join(BASE_DIR, "data", "cache", "enhanced_klines"),
        base_dir=BASE_DIR,
    )
    if prep is None:
        logger.error(
            "❌ 增强训练数据准备失败: 无可用持仓K线, "
            "请检查数据源 (Wind MCP > TDX > AKShare)"
        )
        return None
    logger.info(
        f"  训练标的池: {prep['n_available']} 只持仓 "
        f"(刷新 {prep['refreshed']} | 跳过 {prep['skipped']} | 失败 {prep['failed']})"
    )
    data_dir = prep["out_dir"]
    model_dir = os.path.join(BASE_DIR, "models")

    try:
        result = run_enhanced_training(
            data_dir=data_dir,
            model_dir=model_dir,
            prediction_horizon=horizon,
            filter_oscillation=filter_osc,
            use_optuna=use_optuna,
            n_trials=n_trials,
            n_features=n_features,
            northbound_path=northbound_path,
        )
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        import traceback

        logger.error("\n❌ train_enhanced 执行异常:")
        traceback.print_exc()
        return None

    if "error" in result:
        logger.error(f"\n❌ 训练失败: {result['error']}")
        return None

    progress.update(3, "训练...")
    progress.update(4, "保存...")
    progress.update(5, "完成")
    progress.complete("✅ 增强训练完成")

    logger.info(
        f"\n📊 最佳: {result['best_model']} | F1={result['best_f1']:.4f} "
        f"| AUC={result['best_auc']:.4f} | 样本={result['n_samples']}"
    )
    for name, m in result["results"].items():
        logger.info(f"  {name:<25} F1={m['f1']:.4f}  AUC={m['auc']:.4f}")
    logger.info("\n💡 python v5.10.py --ml-enhanced  # 使用新模型预测")
    logger.info("=" * 70)
    return result


# ============================================================
# ML 增强预测模式 — 四维优化模型预测
# ============================================================


def run_enhanced_prediction_mode(args: argparse.Namespace) -> dict | None:
    """ML增强预测 v2.0"""
    if not ML_ENHANCED_PREDICTOR_AVAILABLE:
        logger.error("\n❌ 增强预测器不可用")
        return None

    logger.info("\n" + "=" * 70)
    logger.info("  📈 ML 增强预测 v2.0")
    logger.info("=" * 70)

    model_dir = os.path.join(BASE_DIR, "models")
    data_dir = os.path.join(BASE_DIR, "data", "cache")
    threshold = getattr(args, "threshold", 0.55)

    progress = ProgressIndicator("增强预测", 4)
    progress.update(1, "加载模型...")
    predictor = EnhancedPredictor(model_dir=model_dir, weight_method="f1_weighted")

    if not predictor.auto_discover_and_load(prefer_enhanced=True):
        logger.warning("  ⚠️ 未找到增强模型，回退标准预测")
        return run_ml_signal_mode(args)

    info = predictor.get_model_info()
    logger.info(
        f"\n  T+{info.get('horizon',1)} | 过滤震荡={info.get('filter_oscillation',True)} "
        f"| 模型数={info.get('model_count',0)} | F1={info.get('f1',0):.4f}"
    )

    progress.update(2, "加载K线...")
    kline_dict = {}
    for f in glob.glob(os.path.join(data_dir, "kline_*.parquet")):
        code = os.path.basename(f).replace("kline_", "").replace("_daily.parquet", "")
        try:
            kline_dict[code] = pd.read_parquet(f)
        except Exception:
            continue  # noqa: BLE001  # fail-safe, 待后续精确化

    if not kline_dict:
        logger.warning("  ⚠️ 无K线数据")
        return None

    progress.update(3, "预测...")
    signals = predictor.generate_trading_signals(kline_dict, threshold=threshold)
    progress.complete("✅ 增强预测完成")

    logger.info(f"\n📊 信号分布 (T+{predictor.prediction_horizon}):")
    logger.info(
        f"  🟢 买入: {len(signals['buy'])} | 🔴 卖出: {len(signals['sell'])} | 🟡 震荡/持有: {len(signals['hold'])}"
    )

    for label, data in [("买入", signals["buy"]), ("卖出", signals["sell"])]:
        if data:
            emoji = "🟢" if label == "买入" else "🔴"
            logger.info(f"\n{emoji} {label}信号:")
            sort_rev = label == "买入"
            for s in sorted(data, key=lambda x: x["probability"], reverse=sort_rev):
                name = get_stock_name(s["code"])
                logger.info(
                    f"  {s['code']} {name:<8} 概率={s['probability']:.2%} "
                    f"置信={s['confidence']:.2%} 强度={s['strength']}"
                )

    if signals["hold"]:
        logger.info("\n🟡 震荡/持有 (建议观望):")
        for s in sorted(signals["hold"], key=lambda x: x["probability"], reverse=True)[
            :5
        ]:
            name = get_stock_name(s["code"])
            logger.info(f"  {s['code']} {name:<8} 概率={s['probability']:.2%}")

    logger.info(
        f"\n💡 四维优化: 三分类标签 | T+{predictor.prediction_horizon}窗口 | 增强特征(行业+北向+基本面) | 样本加权"
    )
    logger.info("=" * 70)
    return signals


# ============================================================
# v5.1 新增：康波周期 + 十五五规划 + 社保基金ETF 综合分析
# ============================================================


def run_hypothesis_test(args: argparse.Namespace) -> None:
    """假设验证模式 - 验证交易假设"""
    logger.info("\n🧪 假设验证模式")
    logger.info("=" * 70)

    if args.list:
        logger.info("\n📋 已注册的研究假设:")
        hypotheses = strategy_registry.list_hypotheses()
        if not hypotheses:
            logger.info("  暂无注册的假设")
        else:
            for i, hyp in enumerate(hypotheses, 1):
                logger.info(f"\n  {i}. {hyp.get('name', '未命名假设')}")
                logger.info(
                    f"     ID: {list(strategy_registry.hypotheses.keys())[i-1]}"
                )
                logger.info(f"     状态: {hyp.get('status', '未知')}")
                logger.info(f"     创建时间: {hyp.get('created_at', '未知')}")
                if "description" in hyp:
                    logger.info(f"     描述: {hyp['description']}")
        return

    if args.register:
        parts = args.register.split("|")
        if len(parts) >= 2:
            hyp_id = parts[0].strip()
            hyp_name = parts[1].strip()
            hyp_desc = parts[2].strip() if len(parts) > 2 else ""

            strategy_registry.register_hypothesis(
                hyp_id,
                {
                    "name": hyp_name,
                    "description": hyp_desc,
                    "methodology": "统计检验",
                    "evidence": [],
                },
            )
            logger.info(f"\n✅ 假设已注册: {hyp_name}")
        else:
            logger.error("\n❌ 注册格式错误，使用: --register id|名称|描述")
        return

    logger.info("\n💡 使用方法:")
    logger.info("  --list              列出所有研究假设")
    logger.info("  --register id|名称|描述    注册新假设")
    logger.info("  --validate <id>     验证假设")


# ============================================================
# 统一执行日志 (v5.7 Phase 1 新增)
# ============================================================


# ============================================================
# AI Hedge Fund — 19位大师级AI分析师联合决策模式
# ============================================================


def run_ai_hedge_mode(args: argparse.Namespace) -> None:
    """AI Hedge Fund — 19位大师级AI分析师联合决策模式

    P2-4: 函数体此前为空 (假成功)。未实现时显式抛错避免静默成功。
    """
    raise NotImplementedError(
        "[P2-4] run_ai_hedge_mode 未实现: AI Hedge Fund 功能已迁移, "
        "请使用 v8.3_institutional/ 或独立 ai_hedge 入口"
    )


def run_stress_test_mode(args: argparse.Namespace) -> None:
    """极端压力测试模式 v5.10 — 6历史情景+蒙特卡洛+硬止损检查"""
    import numpy as np
    import yaml

    logger.info("\n🛡️ 极端压力测试 v5.10")
    logger.info("=" * 70)
    logger.info(f"尾部保护阈值: {TAIL_HEDGE_THRESHOLD:.0%}")
    logger.info(f"硬止损阈值: {HARD_STOP_MAX_DRAWDOWN:.1%}")

    # 1. 加载组合配置
    portfolio_path = os.path.join(BASE_DIR, "config", "portfolio.yaml")
    if not os.path.exists(portfolio_path):
        logger.error("❌ 未找到 portfolio.yaml，使用默认配置")
        # 默认配置（14标的）
        default_positions = {
            "300750.SZ": {"shares": 1000, "sector": "高端制造"},
            "688041.SH": {"shares": 800, "sector": "高端制造"},
            "002371.SZ": {"shares": 600, "sector": "高端制造"},
            "300308.SZ": {"shares": 500, "sector": "高端制造"},
            "688981.SH": {"shares": 400, "sector": "高端制造"},
            "000425.SZ": {"shares": 700, "sector": "高端制造"},
            "601088.SH": {"shares": 1000, "sector": "顺周期"},
            "600219.SH": {"shares": 600, "sector": "顺周期"},
            "600019.SH": {"shares": 500, "sector": "顺周期"},
            "518880.SH": {"shares": 2000, "sector": "资源"},
            "000792.SZ": {"shares": 400, "sector": "资源"},
            "600276.SH": {"shares": 600, "sector": "防御"},
            "603259.SH": {"shares": 500, "sector": "防御"},
            "002422.SZ": {"shares": 400, "sector": "防御"},
        }
        positions = default_positions
    else:
        with open(portfolio_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        positions = config.get("positions", {})

    logger.info(f"✅ 加载持仓: {len(positions)} 个标的")

    # 2. 获取当前价格和权重
    prices = {}
    total_value = 0.0
    for code in positions.keys():
        # 尝试从缓存获取价格
        cache_file = os.path.join(
            BASE_DIR, "data", "cache", f"kline_{code}_daily.parquet"
        )
        if os.path.exists(cache_file):
            try:
                df = pd.read_parquet(cache_file)
                if len(df) > 0 and "close" in df.columns:
                    prices[code] = float(df["close"].iloc[-1])
                    total_value += positions[code]["shares"] * prices[code]
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                pass

    if total_value <= 0:
        logger.error("❌ 无法获取有效价格数据")
        return

    # UE-3: 缺行情标的不再用硬编码占位价 1 计算权重 (会严重失真, 如真实 100 元股按 1 元计)。
    # 缺价标的值跳过并告警, 避免污染风控压力测试结果。
    missing = [code for code in positions if prices.get(code, 0) <= 0]
    if missing:
        logger.warning(
            f"[STALE] {len(missing)} 个标的无有效价格, 已从压力测试权重中跳过: {missing}"
        )
    valid_codes = [code for code in positions if prices.get(code, 0) > 0]
    if not valid_codes:
        logger.error("❌ 无任何标的有有效价格, 无法计算压力测试权重")
        return
    weights = np.array(
        [positions[code]["shares"] * prices[code] / total_value for code in valid_codes]
    )
    names = list(valid_codes)
    sectors = {code: positions[code].get("sector", "未分类") for code in valid_codes}

    logger.info(f"✅ 组合总市值: ¥{total_value:.2f}")

    # 3. 加载历史收益率数据
    returns_data = []
    valid_codes = []
    for code in names:
        cache_file = os.path.join(
            BASE_DIR, "data", "cache", f"kline_{code}_daily.parquet"
        )
        if os.path.exists(cache_file):
            try:
                df = pd.read_parquet(cache_file)
                if "close" in df.columns and len(df) > 60:
                    rets = df["close"].pct_change().dropna().values
                    returns_data.append(
                        rets[-252:] if len(rets) >= 252 else rets
                    )  # 最近一年
                    valid_codes.append(code)
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                pass

    if len(valid_codes) == 0:
        logger.error("❌ 无法获取历史收益率数据")
        return

    # 对齐长度（取最短序列）
    min_len = min(len(r) for r in returns_data)
    returns_aligned = np.array([r[-min_len:] for r in returns_data])
    weights_aligned = np.array([weights[names.index(code)] for code in valid_codes])
    names_aligned = valid_codes
    sectors_aligned = {code: sectors[code] for code in valid_codes}

    logger.info(f"✅ 历史数据: {min_len} 交易日, {len(valid_codes)} 个标的")

    # 4. 生成压力测试报告
    report = generate_stress_report(
        returns=returns_aligned,
        weights=weights_aligned,
        names=names_aligned,
        sectors=sectors_aligned,
    )

    logger.info(report)

    # 5. 保存报告
    output_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = now_bj().strftime("%Y%m%d_%H%M%S")
    report_file = os.path.join(output_dir, f"stress_test_{timestamp}.md")

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info(f"\n✅ 报告已保存: {report_file}")
    logger.info("=" * 70)


def run_stop_loss_config_mode(args: argparse.Namespace) -> None:
    """止损配置模式 v5.10 — 查看/更新止损止盈规则"""
    import yaml

    config_path = os.path.join(BASE_DIR, "config", "stop_loss_rules_auto.yaml")
    logger.info("\n🛡️ 止损止盈配置 v5.10")
    logger.info("=" * 70)

    if not os.path.exists(config_path):
        logger.error("❌ 配置文件不存在，运行以下命令生成:")
        logger.info("  python scripts/generate_stop_loss_rules.py --regenerate")
        return

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # 显示全局设置
    gs = data.get("global_settings", {})
    logger.info(f"版本: {data.get('version', 'N/A')}")
    logger.info(f"更新时间: {data.get('updated', 'N/A')}")
    logger.info("\n全局设置:")
    logger.info(f"  ATR动态止损: {'启用' if gs.get('atr_enabled') else '禁用'}")
    logger.info(f"  ATR周期: {gs.get('atr_period', 14)}")
    logger.info(f"  预警阈值: {gs.get('warning_threshold_pct', 5.0):.1f}%")
    logger.info(f"  紧急阈值: {gs.get('critical_threshold_pct', 2.0):.1f}%")
    logger.info(f"  启用追踪止损: {gs.get('enable_trailing_stop_pct', 10.0):.0f}%")

    # 显示标的规则
    assets = data.get("assets", [])
    logger.info(f"\n标的配置 ({len(assets)} 只):")
    logger.info("-" * 70)
    logger.info(
        f"{'代码':<12} {'名称':<10} {'板块':<8} {'基准价':>8} {'ATR止损':>8} {'固定止损':>8} {'风险'}"
    )
    logger.info("-" * 70)

    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for a in assets:
        code = a.get("code", "")
        name = a.get("name", "")[:8]
        sector = a.get("sector", "")[:6]
        base = a.get("base_price", 0)
        atr_sl = a.get("atr_stop_loss_price", 0)
        fixed_sl = a.get("stop_loss_price", 0)
        risk = a.get("risk_level", "N/A")
        risk_summary[risk] = risk_summary.get(risk, 0) + 1
        logger.info(
            f"{code:<12} {name:<10} {sector:<8} {base:>8.2f} {atr_sl:>8.2f} {fixed_sl:>8.2f} {risk}"
        )

    logger.info("-" * 70)
    logger.info(
        f"\n风险分布: 高风险 {risk_summary.get('high', 0)} | 中风险 {risk_summary.get('medium', 0)} | 低风险 {risk_summary.get('low', 0)}"  # noqa: E501
    )
    logger.info(f"\n配置位置: {config_path}")
    logger.info("\n更新命令: python scripts/generate_stop_loss_rules.py --regenerate")
    logger.info("=" * 70)


def run_hedge_execute_mode(args: argparse.Namespace) -> None:
    """期权对冲订单执行模式 — 撮合执行 trade_plan 中 PENDING 期权订单 (P0 修复, 2026-08-06)

    补齐"订单→撮合→成交→持仓/Delta 更新"闭环. 用法:
        python "量化策略系统_统一入口_v8.6.py" --hedge-execute [--date YYYY-MM-DD] [--dry-run]
    """
    logger.info("\n🛡️ 期权对冲订单执行器 (Hedge Order Executor)")
    logger.info("=" * 70)

    from hedge_order_executor import execute_hedge_orders, print_result

    trade_date = getattr(args, "date", None) or now_bj().strftime("%Y-%m-%d")
    dry_run = bool(getattr(args, "dry_run", False))
    confirm_only = bool(getattr(args, "confirm_only", False))

    # UE-1: 统一实盘门控 — 真实 broker 就绪时非 dry_run 撮合需 --yes 确认
    if not _enforce_live_gate(
        dry_run, confirm_only, "期权对冲撮合", confirm=bool(getattr(args, "yes", False))
    ):
        logger.error("期权对冲撮合被实盘门控阻断, 中止")
        return

    logger.info(
        f"目标日期: {trade_date} | dry_run={dry_run} | confirm_only={confirm_only}"
    )
    result = execute_hedge_orders(
        trade_date=trade_date,
        dry_run=dry_run,
        confirm_only=confirm_only,
    )
    print_result(result)

    filled = result.get("filled_count", 0)
    beta_before = result.get("portfolio_beta", 0)
    beta_after = result.get("beta_after_hedge", 0)
    if not dry_run and not confirm_only:
        logger.info(
            f"✅ 期权对冲执行完成: {filled} 笔成交, "
            f"Beta {beta_before:.4f} → {beta_after:.4f}, "
            f"净成本 RMB {result.get('total_cost', 0):,.2f}"
        )


def run_rebalance_execute_mode(args: argparse.Namespace) -> dict:
    """再平衡撮合执行模式 — 撮合执行再平衡订单并落盘成交回报 (G2/G4 修复, 2026-08-08)

    复用 AutomatedExecutionSystem._generate_rebalance_orders() 已验证链路:
    生成 -> 路由 -> 撮合 -> 成交回报落盘 FillsStore -> TCA 归因 (G4 读 fills).
    用法:
        python "量化策略系统_统一入口_v8.6.py" --rebalance-execute [--date YYYY-MM-DD] [--dry-run]
    """
    logger.info("\n🔄 再平衡撮合执行器 (Rebalance Order Executor)")
    logger.info("=" * 70)

    from rebalance_order_executor import execute_rebalance_orders, print_result

    trade_date = getattr(args, "date", None) or now_bj().strftime("%Y-%m-%d")
    dry_run = bool(getattr(args, "dry_run", False))

    # UE-1: 统一实盘门控 — 真实 broker 就绪时非 dry_run 撮合需 --yes 确认
    if not _enforce_live_gate(
        dry_run, False, "再平衡撮合", confirm=bool(getattr(args, "yes", False))
    ):
        logger.error("再平衡撮合被实盘门控阻断, 中止")
        return {}

    logger.info(f"目标日期: {trade_date} | dry_run={dry_run}")
    result = execute_rebalance_orders(
        date=trade_date,
        dry_run=dry_run,
    )
    print_result(result)

    filled = result.get("filled", 0)
    if not dry_run and result.get("error") is None:
        logger.info(f"✅ 再平衡撮合执行完成: {filled} 笔成交已落盘 FillsStore")
    return result


def run_factor_research(args: argparse.Namespace) -> None:
    """因子研究模式 — Wind MCP 真实行情驱动 AutoFactorResearch 闭环 (2026-08-29)

    用 Wind MCP 拉取真实日线 OHLCV, 跑完整因子研究循环 (提案→实现→评审→可选ML组合),
    全程数据驱动、无前视偏差。要求: 环境变量 WIND_API_KEY; 标的数量建议 10-30 只才有
    统计意义的截面 IC (evaluator 截面 IC 需 >=5 只标的)。
    """
    logger.info("\n🔬 因子研究模式 (Wind MCP 真实数据)")
    symbols = [s.strip() for s in (args.factor_symbols or "").split(",") if s.strip()]
    if not symbols:
        logger.error(
            "❌ 未提供标的 — 使用 --factor-symbols 600036.SH,000001.SZ,588000.SH"
        )
        return
    try:
        from utils.alpha_factor.auto_research import AutoFactorResearch
    except Exception as e:
        logger.error(f"❌ 因子研究模块加载失败: {e}")
        return

    ar = AutoFactorResearch(use_llm=bool(args.factor_use_llm))
    report, price_data = ar.run_cycle_on_wind(symbols, days=int(args.factor_days))
    if not price_data:
        logger.error(
            "❌ Wind MCP 未返回有效数据 (检查 WIND_API_KEY / 网络 / 代码格式如 600036.SH)"
        )
        return

    logger.info("=" * 60)
    logger.info(f"因子研究 (Wind MCP 真实数据) — {len(price_data)} 只标的")
    logger.info("=" * 60)
    logger.info(
        f"提案={report.n_proposed} 实现={report.n_implemented} "
        f"接受={report.n_accepted} 拒绝={report.n_rejected}"
    )
    for name, r in report.reviews.items():
        logger.info(
            f"  {name:<20} IC={r.ic_mean:>7.4f} IR={r.ic_ir:>6.3f} "
            f"TO={r.turnover:>5.3f} {'通过' if r.passed else '拒绝'} "
            f"{('(' + r.reason + ')') if r.reason else ''}"
        )
    if args.factor_combine and ar.accepted:
        logger.info(f"ML 组合: {ar.combine_accepted()}")

    # 持久化 accepted 因子到 AlphaFactorLibrary (表达式因子, 未来 compute_all 自动纳入)
    if not args.no_factor_persist and ar.accepted:
        try:
            from utils.alpha_factor.library import save_research_factors

            specs = ar.get_accepted_specs()
            if specs:
                n = save_research_factors(specs)
                logger.info(
                    "[持久化] 已将 %d 个 accepted 因子写入 AlphaFactorLibrary 持久化 "
                    "(data/alpha_factor_research.json)，后续 compute_all 自动纳入",
                    n,
                )
            else:
                logger.info("[持久化] 本轮无 accepted 因子可持久化")
        except Exception as e:  # noqa: BLE001
            logger.warning("[持久化] 因子持久化跳过: %s", e)


def main() -> None:
    # ── 模式注册表：flag / dest / 帮助文本 / handler ──
    MODES = [
        (
            "--daily",
            "daily",
            "三阶段交易工作流 (盘前计划/盘中策略/盘后报告)",
            run_daily_workflow,
        ),
        ("--live", "live", "实时监控模式", run_live_monitoring),
        ("--report", "report", "报告生成模式", run_report_generation),
        ("--rebalance", "rebalance", "再平衡模式", run_rebalance),
        ("--backtest", "backtest", "回测模式", run_backtest),
        ("--risk", "risk", "风险监控模式", run_risk_monitor),
        ("--check", "check", "快速检查模式", run_quick_check),
        ("--hypothesis", "hypothesis", "假设验证模式", run_hypothesis_test),
        ("--etf-flow", "etf_flow", "ETF资金流向监控", run_etf_flow_monitor),
        (
            "--portfolio-opt",
            "portfolio_opt",
            "投资组合优化",
            run_portfolio_optimization,
        ),
        ("--kommo-monitor", "kommo_monitor", "康波周期监控", run_kommo_monitor),
        (
            "--commodity-fund",
            "commodity_fund",
            "大宗商品基本面",
            run_commodity_fundamentals,
        ),
        ("--train-model", "train_model", "时序预测训练", run_model_training),
        (
            "--train-enhanced",
            "train_enhanced",
            "ML增强训练 v2.0 - 四维优化(标签+窗口+特征+权重)",
            run_enhanced_training_mode,
        ),
        (
            "--kondratiev",
            "kondratiev",
            "康波周期+十五五交叠分析",
            run_kondratiev_analysis,
        ),
        (
            "--fifteen-five",
            "fifteen_five",
            "十五五规划适配分析",
            run_fifteen_five_analysis,
        ),
        (
            "--social-security",
            "social_security",
            "社保基金ETF风格追踪",
            run_social_security_analysis,
        ),
        (
            "--macro-analysis",
            "macro_analysis",
            "宏观综合分析（康波+十五五+社保ETF一键运行）",
            run_macro_analysis,
        ),
        (
            "--ai-decision",
            "ai_decision",
            "AI盘中决策 v5.10 - 场景路由+并行对冲+Wind MCP动态数据",
            run_ai_decision,
        ),
        (
            "--futures-options",
            "futures_options",
            "期货期权扫描",
            run_futures_options_scan,
        ),
        (
            "--unified-monitor",
            "unified_monitor",
            "统一监控模式 - 一键启动所有模块",
            run_unified_monitor,
        ),
        (
            "--ai-hedge",
            "ai_hedge",
            "AI Hedge Fund - 20位大师级AI分析师联合决策(含对冲分析师)",
            run_ai_hedge_mode,
        ),
        ("--ml-signal", "ml_signal", "ML模型预测信号", run_ml_signal_mode),
        (
            "--ml-enhanced",
            "ml_enhanced",
            "ML增强预测 v2.0 - 四维优化模型信号",
            run_enhanced_prediction_mode,
        ),
        (
            "--hedge",
            "hedge",
            "对冲分析 v5.10 — Taleb+Burry+Druck 多指数期货/期权风险对冲",
            run_hedge_mode,
        ),
        (
            "--hedge-rebalance",
            "hedge_rebalance",
            "对冲+再平衡联动分析 v5.10 — 组合自触发+多指数Beta加权",
            run_hedge_rebalance_joint,
        ),
        (
            "--hedge-detail",
            "hedge_detail",
            "期货对冲明细 v5.10 — 完整对冲规格+Beta表+触发机制+回测",
            run_hedge_detail_mode,
        ),
        (
            "--stress-test",
            "stress_test",
            "极端压力测试 v5.10 — 6历史情景+蒙特卡洛+硬止损",
            run_stress_test_mode,
        ),
        (
            "--stop-loss",
            "stop_loss",
            "止损配置 v5.10 — 查看/更新止损止盈规则(含ATR动态止损)",
            run_stop_loss_config_mode,
        ),
        (
            "--ml-significance",
            "ml_significance",
            "ML显著性验证 v5.10 — Bootstrap+置换检验+Rank IC",
            run_ml_significance_mode,
        ),
        (
            "--kronos",
            "kronos",
            "Kronos 金融K线预测 — 时序基础模型信号",
            run_kronos_predict_mode,
        ),
        (
            "--gemma",
            "gemma",
            "Gemma 4 分析增强 — 新闻情绪/异动解读/报告生成",
            run_gemma_analyze_mode,
        ),
        (
            "--dcf",
            "dcf",
            "DCF 估值模型 — WACC + 收入预测 + 敏感性分析 (Excel)",
            run_dcf_mode,
        ),
        (
            "--comps",
            "comps",
            "可比公司分析 — 运营指标 + 估值倍数 + 统计分位 (Excel)",
            run_comps_mode,
        ),
        (
            "--hedge-execute",
            "hedge_execute",
            "期权对冲订单执行器 — 撮合执行 PENDING 期权订单 (P0修复)",
            run_hedge_execute_mode,
        ),
        (
            "--rebalance-execute",
            "rebalance_execute",
            "再平衡撮合执行器 — 撮合再平衡订单并落盘成交回报 (G2/G4修复)",
            run_rebalance_execute_mode,
        ),
        (
            "--factor-research",
            "factor_research",
            "因子研究 — Wind MCP 真实行情驱动闭环 (提案/实现/评审/可选ML组合)",
            run_factor_research,
        ),
    ]

    # 由 MODES 动态生成 epilog 中的运行模式清单
    mode_lines = "\n".join(
        f"  {flag:<20s} {help_text}" for flag, _, help_text, _ in MODES
    )

    parser = argparse.ArgumentParser(
        description="量化策略系统 v5.10 — AI决策驱动 + 对冲再平衡联动v5.10",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
运行模式:
{mode_lines}

示例:
  python "量化策略系统 v5.10.py" --live              # 启动实时监控
  python "量化策略系统 v5.10.py" --report            # 生成报告
  python "量化策略系统 v5.10.py" --rebalance         # 执行再平衡
  python "量化策略系统 v5.10.py" --risk              # 风险监控
  python "量化策略系统 v5.10.py" --check             # 系统检查
  python "量化策略系统 v5.10.py" --etf-flow          # ETF资金流向监控
  python "量化策略系统 v5.10.py" --hypothesis --list # 列出假设
  python "量化策略系统 v5.10.py" --portfolio-opt     # 投资组合优化
  python "量化策略系统 v5.10.py" --kommo-monitor     # 康波周期监控
  python "量化策略系统 v5.10.py" --commodity-fund    # 大宗商品基本面
  python "量化策略系统 v5.10.py" --train-model       # 时序预测训练
  python "量化策略系统 v5.10.py" --kondratiev        # 康波周期+十五五交叠分析
  python "量化策略系统 v5.10.py" --fifteen-five      # 十五五规划适配分析
  python "量化策略系统 v5.10.py" --social-security   # 社保基金ETF风格追踪
  python "量化策略系统 v5.10.py" --macro-analysis    # 宏观综合分析
  python "量化策略系统 v5.10.py" --ai-decision       # AI盘中决策 (v5.10: 并行对冲+Wind MCP)
  python "量化策略系统 v5.10.py" --ai-decision --scene=rebalancing_analysis  # 再平衡深度分析
  python "量化策略系统 v5.10.py" --ai-decision --no-wind  # AI决策 (禁用Wind数据)
  python "量化策略系统 v5.10.py" --futures-options   # 期货期权扫描
  python "量化策略系统 v5.10.py" --unified-monitor    # 统一监控
  python "量化策略系统 v5.10.py" --ai-hedge          # AI Hedge Fund
  python "量化策略系统 v5.10.py" --ml-signal         # ML模型预测信号
  python "量化策略系统 v5.10.py" --hedge             # 对冲分析 (多指数期货/期权)
  python "量化策略系统 v5.10.py" --hedge --no-ai     # 对冲分析 (仅规则引擎)
  python "量化策略系统 v5.10.py" --hedge-rebalance                        # 对冲+再平衡联动分析 v5.10 (组合自触发)
  python "量化策略系统 v5.10.py" --hedge-rebalance --mode=tail_only       # 尾部保护模式 (默认)
  python "量化策略系统 v5.10.py" --hedge-rebalance --mode=dynamic         # 动态对冲模式
  python "量化策略系统 v5.10.py" --hedge-rebalance --show-reasoning       # 含详细推理过程
  python "量化策略系统 v5.10.py" --hedge-rebalance --auto-execute         # 自动化执行(需二次确认)
  python "量化策略系统 v5.10.py" --hedge-detail          # 终端打印完整明细
  python "量化策略系统 v5.10.py" --factor-research --factor-symbols 600036.SH,000001.SZ,588000.SH  # 因子研究(Wind真实数据)  # noqa: E501
  python "量化策略系统 v5.10.py" --factor-research --factor-symbols 600036.SH,000001.SZ --factor-use-llm --factor-combine  # 含GLM-5+ML组合  # noqa: E501
  python "量化策略系统 v5.10.py" --hedge-detail --json   # JSON 输出
  python "量化策略系统 v5.10.py" --hedge-detail -o hedge.json  # 保存到文件
  python "量化策略系统 v5.10.py" --kronos --kronos-code 000001                        # 单股预测
  python "量化策略系统 v5.10.py" --kronos --kronos-code 600519 --kronos-name 茅台     # 单股预测(带名称)
  python "量化策略系统 v5.10.py" --kronos --kronos-batch '[{{"code":"000001","name":"平安银行"}},{{"code":"600519","name":"贵州茅台"}}]'  # 批量预测  # noqa: E501
  python "量化策略系统 v5.10.py" --gemma --gemma-news '央行宣布降息25个基点'                       # 新闻情绪分析
  python "量化策略系统 v5.10.py" --gemma --gemma-stock 600519                                      # 股票基本面分析
  python "量化策略系统 v5.10.py" --gemma --gemma-prompt '分析当前A股市场走势'                       # 自定义分析

架构特点 (借鉴Vibe-Trading):
  • Connector-first: 统一数据源抽象，支持多连接器配置
  • 策略注册表: 中心化策略管理与版本控制
  • 假设验证: 支持统计检验与随机对照试验
  • 研究目标: 支持目标生命周期管理
  • 实时反馈: 长时间任务的进度可视化
  • ETF资金流向: 国家队资金监控，投资决策参考
  • 投资组合优化: 等权重/风险平价/风险配比/因子配比/自定义配置
  • 康波周期监控: 商品价格+宏观指标+产业库存三维度
  • 时序预测: Transformer模型骨架集成
        """,
    )

    # ── 注册运行模式（数据驱动，声明一次即可） ──
    mode_group = parser.add_mutually_exclusive_group(required=True)
    for flag, dest, help_text, _ in MODES:
        mode_group.add_argument(flag, dest=dest, action="store_true", help=help_text)

    # ── 子阶段 / 通用选项 / 模式专属选项 ──
    # --daily 子阶段
    parser.add_argument(
        "--phase",
        choices=["premarket", "intraday", "postmarket", "all"],
        default="all",
        help="三阶段工作流子阶段 (配合 --daily 使用)",
    )

    # 通用选项
    parser.add_argument("--no-ai", action="store_true", help="禁用AI分析模块")
    parser.add_argument("--output", "-o", default=None, help="输出报告文件名")
    parser.add_argument("--sync-sl", action="store_true", help="同步止损止盈规则")
    parser.add_argument(
        "--include-valuation",
        action="store_true",
        help="报告生成时追加 DCF + Comps 估值摘要",
    )

    # v5.9: AI 决策场景路由选项
    parser.add_argument(
        "--scene",
        type=str,
        default="intraday_decision",
        choices=[
            "intraday_decision",
            "rebalancing_analysis",
            "macro_analysis",
            "report_generation",
        ],
        help="AI决策场景 (默认: intraday_decision=盘中并行对冲)",
    )
    parser.add_argument(
        "--no-wind", action="store_true", help="禁用 Wind MCP 数据 (使用降级数据源)"
    )
    parser.add_argument(
        "--interval", type=int, default=300, help="AI决策检查间隔(秒, 默认 300)"
    )

    # AI Hedge Fund 选项
    parser.add_argument(
        "--ticker", "-t", nargs="+", default=None, help="AI Hedge Fund: 股票代码列表"
    )
    parser.add_argument(
        "--analysts",
        "-a",
        nargs="*",
        default=None,
        help="AI Hedge Fund: 选择分析师 (默认全部)",
    )
    parser.add_argument(
        "--show-reasoning", action="store_true", help="AI Hedge Fund: 显示分析详情"
    )
    parser.add_argument(
        "--model", type=str, default=None, help="AI Hedge Fund: LLM 模型名"
    )
    parser.add_argument(
        "--provider", type=str, default=None, help="AI Hedge Fund: LLM 提供商"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="AI Hedge Fund/回测: 开始日期 YYYY-MM-DD",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="AI Hedge Fund/回测: 结束日期 YYYY-MM-DD",
    )

    # v5.10: 对冲明细选项
    parser.add_argument(
        "--json", action="store_true", help="期货对冲明细: 输出 JSON 格式"
    )

    # v5.9: 对冲-再平衡联动选项
    parser.add_argument(
        "--auto-execute",
        action="store_true",
        help="对冲-再平衡联动: 自动化执行 (需二次确认)",
    )
    parser.add_argument(
        "--mode",
        dest="hedge_mode",
        type=str,
        default="tail_only",
        choices=["tail_only", "dynamic", "fixed", "none"],
        help="对冲-再平衡联动: 对冲模式 (默认 tail_only=仅尾部保护)",
    )

    # v8.6 P0 (2026-08-06): 期权对冲订单执行器选项
    parser.add_argument(
        "--date", type=str, default=None, help="期权对冲执行器: 目标交易日 YYYY-MM-DD"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="期权对冲执行器: 干跑模式 (不落盘不更新持仓)",
    )
    parser.add_argument(
        "--confirm-only",
        action="store_true",
        help="期权对冲执行器: 仅输出待确认订单, 不撮合",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="UE-1 实盘门控: 真实 broker 就绪时确认真实下单 (防裸实盘双签)",
    )

    # 假设验证选项
    parser.add_argument("--list", action="store_true", help="列出研究假设")
    parser.add_argument("--register", type=str, help="注册新假设: id|名称|描述")
    parser.add_argument("--validate", type=str, help="验证指定假设")

    # ML信号选项
    parser.add_argument(
        "--threshold", type=float, default=0.55, help="ML信号: 买入信号阈值 (默认 0.55)"
    )
    parser.add_argument("--no-ml", action="store_true", help="跳过ML模型信号扫描")

    # Kronos 预测选项
    parser.add_argument(
        "--kronos-code", type=str, default=None, help="Kronos: 单股代码 (如 000001)"
    )
    parser.add_argument(
        "--kronos-name", type=str, default=None, help="Kronos: 股票名称 (可选)"
    )
    parser.add_argument(
        "--kronos-pred-len", type=int, default=24, help="Kronos: 预测窗口 (默认 24)"
    )
    parser.add_argument(
        "--kronos-device",
        type=str,
        default="cpu",
        help="Kronos: 运行设备 (cpu 或 cuda:0)",
    )
    parser.add_argument(
        "--kronos-batch", type=str, default=None, help="Kronos: 批量预测 JSON 数组"
    )
    parser.add_argument(
        "--kronos-output",
        type=str,
        default=None,
        help="Kronos: 输出文件名 (保存到 reports/)",
    )
    # Gemma 分析选项
    parser.add_argument(
        "--gemma-model",
        type=str,
        default=None,
        help="Gemma: Ollama 模型名 (默认 qwen2.5:7b)",
    )
    parser.add_argument(
        "--gemma-news", type=str, default=None, help="Gemma: 新闻内容 (用于情绪分析)"
    )
    parser.add_argument(
        "--gemma-stock", type=str, default=None, help="Gemma: 股票代码 (用于基本面分析)"
    )
    parser.add_argument(
        "--gemma-prompt", type=str, default=None, help="Gemma: 自定义提示"
    )
    parser.add_argument(
        "--gemma-output",
        type=str,
        default=None,
        help="Gemma: 输出文件名 (保存到 reports/)",
    )
    # ── v5.7 Phase 2: 高级训练选项 ──
    parser.add_argument(
        "--optuna", action="store_true", help="训练: 启用 Optuna 贝叶斯超参数优化"
    )
    parser.add_argument(
        "--triple-barrier",
        action="store_true",
        help="训练: 启用 Triple Barrier 标签 (替代简单涨跌标签)",
    )
    parser.add_argument(
        "--stacking", action="store_true", help="训练/预测: 启用 Stacking 多模型集成"
    )
    parser.add_argument(
        "--optuna-trials",
        type=int,
        default=100,
        help="训练: Optuna 试验次数 (默认 100)",
    )
    parser.add_argument(
        "--horizon", type=int, default=1, help="训练: 预测窗口 T+N (1/5/10, 默认 1)"
    )
    parser.add_argument(
        "--trials", type=int, default=50, help="训练: Optuna 试验次数 (默认 50)"
    )
    parser.add_argument(
        "--skip-ml-filter", action="store_true", help="AI Hedge Fund: 跳过ML预筛选"
    )
    parser.add_argument(
        "--mlflow",
        action="store_true",
        help="训练: 启用 MLflow 实验追踪 (需 pip install mlflow)",
    )

    # 因子研究 (Wind MCP) 选项
    parser.add_argument(
        "--factor-symbols",
        type=str,
        default=None,
        help="因子研究: 逗号分隔 Wind 代码 (如 600036.SH,000001.SZ,588000.SH)",
    )
    parser.add_argument(
        "--factor-days", type=int, default=300, help="因子研究: Wind 回溯交易日数"
    )
    parser.add_argument(
        "--factor-use-llm",
        action="store_true",
        help="因子研究: 启用 GLM-5 生成因子 (默认仅规则模板)",
    )
    parser.add_argument(
        "--factor-combine",
        action="store_true",
        help="因子研究: 跑完后做 ML 组合阶段",
    )
    parser.add_argument(
        "--no-factor-persist",
        action="store_true",
        help="因子研究: 跑完不将 accepted 因子写入 AlphaFactorLibrary 持久化",
    )

    # ── ETF期权联动对冲组合策略 (v1.0) ──
    parser.add_argument(
        "--etf-combo",
        action="store_true",
        help="ETF现货与期权联动对冲组合策略 (备兑看涨/领口/CSP/垂直价差/日历价差)",
    )
    parser.add_argument(
        "--etf-combo-monitor",
        action="store_true",
        help="ETF期权联动: 全组合监控 (Greeks/保证金/行权风险)",
    )
    parser.add_argument(
        "--etf-combo-roll",
        action="store_true",
        help="ETF期权联动: 全组合滚仓 (DTE≤5触发)",
    )
    parser.add_argument(
        "--etf-combo-backtest",
        action="store_true",
        help="ETF期权联动: 组合策略回测",
    )

    args = parser.parse_args()

    # ── ETF期权联动对冲组合策略处理 ──
    if getattr(args, "etf_combo", False):
        try:
            from utils.etf_option_combo.combo_backtest import ComboBacktest
            from utils.etf_option_combo.combo_orchestrator import ComboOrchestrator
        except ImportError as e:
            logger.error(
                f"\n❌ ETF期权联动模块不可用 (utils/etf_option_combo 未安装或未纳入版本库): {e}"
            )
            sys.exit(2)  # 模块缺失: 非 0 退出, 避免 cron/CI 误判成功
        try:
            if getattr(args, "etf_combo_backtest", False):
                bt = ComboBacktest()
                result = bt.run_backtest("2026-01-01", "2026-09-01")
                m = result["metrics"]
                logger.info(
                    "ETF期权联动回测: 年化=%.4f 回撤=%.4f Sharpe=%.4f 对冲效率=%.4f 交易数=%d",
                    m["annual_return"], m["max_drawdown"], m["sharpe"],
                    result["hedge_efficiency"], result["trade_count"],
                )
                # IV Rank 自适应对比 (collar): static vs adaptive, 作 enabled=true 决策材料
                comparison = bt.run_comparison("2026-01-01", "2026-09-01")
                sm = comparison["static"]["metrics"]
                am = comparison["adaptive"]["metrics"]
                anc = comparison["avg_net_cost"]
                d = comparison["delta"]

                def _fmt_cost(v: float | None) -> str:
                    return "N/A" if v is None else f"{v:.2f}"

                logger.info(
                    "IV自适应对比[static  ] 回撤=%.4f Sharpe=%.4f 对冲效率=%.4f 均净成本=%s",
                    sm["max_drawdown"], sm["sharpe"],
                    comparison["static"]["hedge_efficiency"], _fmt_cost(anc["static"]),
                )
                logger.info(
                    "IV自适应对比[adaptive] 回撤=%.4f Sharpe=%.4f 对冲效率=%.4f 均净成本=%s",
                    am["max_drawdown"], am["sharpe"],
                    comparison["adaptive"]["hedge_efficiency"], _fmt_cost(anc["adaptive"]),
                )
                logger.info(
                    "IV自适应对比[delta  ] Δ回撤=%+.4f ΔSharpe=%+.4f Δ对冲效率=%+.4f Δ均净成本=%s (负=adaptive更省)",
                    d["max_drawdown"], d["sharpe"], d["hedge_efficiency"],
                    "N/A" if d["avg_net_cost"] is None else f"{d['avg_net_cost']:+.2f}",
                )
            elif getattr(args, "etf_combo_monitor", False):
                orch = ComboOrchestrator()
                orch.monitor()
                snapshot = orch.get_portfolio_snapshot()
                logger.info("ETF期权联动监控: 策略实例=%d 预算=%s", snapshot["strategy_instances"], snapshot["budgets"])
            elif getattr(args, "etf_combo_roll", False):
                orch = ComboOrchestrator()
                roll_results = orch.roll_all()
                logger.info("ETF期权联动滚仓: %d 策略需滚仓", len(roll_results))
            else:
                orch = ComboOrchestrator()
                results = orch.run_all(market_state={"regime": "calm"})
                total_orders = sum(
                    len(r.orders) for combo_list in results.values() for r in combo_list
                )
                logger.info("ETF期权联动运行: %d 标的, %d 订单", len(results), total_orders)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"\n❌ ETF期权联动执行失败: {e}")
            sys.exit(1)  # 非 0 退出码, 避免自动化脚本误判成功
        return

    # ── 数据驱动分发（v5.7 Phase 1 增强：统一执行时长追踪）──
    # P2-4: 已废弃模式不再记为假成功——stub 返回 {'deprecated': True} 时 success=False 且退出码非 0
    for _flag, dest, _, handler in MODES:
        if getattr(args, dest):
            start_time = time.time()
            try:
                result = handler(args)
                duration = time.time() - start_time
                # P2-4: 识别 deprecated stub 的假成功, 改为失败并给出生产入口提示
                if isinstance(result, dict) and result.get("deprecated"):
                    logger.error(
                        f"\n❌ {dest} 模式已废弃, 未实际执行。生产入口: "
                        f"{result.get('alt_entry') or 'py -3.8 v8.3_institutional/daily_workflow.py --phase all'}"
                    )
                    _log_execution_summary(dest, duration, False, result)
                    sys.exit(1)  # 非 0 退出码, 避免自动化脚本误判成功
                _log_execution_summary(dest, duration, True, result)
            except KeyboardInterrupt:
                duration = time.time() - start_time
                logger.info(f"\n⏹️  用户中断 ({dest})")
                _log_execution_summary(dest, duration, False, {"interrupted": True})
                sys.exit(130)
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                duration = time.time() - start_time
                logger.error(f"\n❌ {dest} 执行异常: {e}")
                _log_execution_summary(dest, duration, False, {"error": str(e)})
                sys.exit(1)
            break


if __name__ == "__main__":
    # 打印启动信息
    logger.info("=" * 70)
    logger.info("          量化策略系统 v5.10 - 对冲再平衡联动版")
    logger.info("                    HKUDS/Vibe-Trading Architecture")
    logger.info("=" * 70)
    logger.info(f"启动时间: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("-" * 70)

    main()
