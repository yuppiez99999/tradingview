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

from cli.handlers.cli_parser import MODE_SPECS, build_parser
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
from cli.handlers.etf_combo_runner import run_etf_combo
from cli.handlers.helpers import (  # noqa: E402
    _check_commodity_module,
    _enforce_live_gate,
    archive_report,
    get_etf_flow_data,
    get_ml_signal_section,
    get_stock_name,
    write_report_file,
)
from cli.handlers.mode_dispatch import dispatch
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


# ── 模式表 (dest → handler)：声明在 cli_parser.MODE_SPECS，绑定落在这里（避免循环导入）──
MODE_HANDLERS = {
    "daily": run_daily_workflow,
    "live": run_live_monitoring,
    "report": run_report_generation,
    "rebalance": run_rebalance,
    "backtest": run_backtest,
    "risk": run_risk_monitor,
    "check": run_quick_check,
    "hypothesis": run_hypothesis_test,
    "etf_flow": run_etf_flow_monitor,
    "portfolio_opt": run_portfolio_optimization,
    "kommo_monitor": run_kommo_monitor,
    "commodity_fund": run_commodity_fundamentals,
    "train_model": run_model_training,
    "train_enhanced": run_enhanced_training_mode,
    "kondratiev": run_kondratiev_analysis,
    "fifteen_five": run_fifteen_five_analysis,
    "social_security": run_social_security_analysis,
    "macro_analysis": run_macro_analysis,
    "ai_decision": run_ai_decision,
    "futures_options": run_futures_options_scan,
    "unified_monitor": run_unified_monitor,
    "ai_hedge": run_ai_hedge_mode,
    "ml_signal": run_ml_signal_mode,
    "ml_enhanced": run_enhanced_prediction_mode,
    "hedge": run_hedge_mode,
    "hedge_rebalance": run_hedge_rebalance_joint,
    "hedge_detail": run_hedge_detail_mode,
    "stress_test": run_stress_test_mode,
    "stop_loss": run_stop_loss_config_mode,
    "ml_significance": run_ml_significance_mode,
    "kronos": run_kronos_predict_mode,
    "gemma": run_gemma_analyze_mode,
    "dcf": run_dcf_mode,
    "comps": run_comps_mode,
    "hedge_execute": run_hedge_execute_mode,
    "rebalance_execute": run_rebalance_execute_mode,
    "factor_research": run_factor_research,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ── ETF期权联动对冲组合策略处理 ──
    if getattr(args, "etf_combo", False):
        run_etf_combo(args)
        return

    # ── 数据驱动分发（v5.7 Phase 1 增强：统一执行时长追踪）──
    # P2-4: 已废弃模式不再记为假成功——stub 返回 {'deprecated': True} 时 success=False 且退出码非 0

    dispatch(args, MODE_SPECS, MODE_HANDLERS)


if __name__ == "__main__":
    # 打印启动信息
    logger.info("=" * 70)
    logger.info("          量化策略系统 v5.10 - 对冲再平衡联动版")
    logger.info("                    HKUDS/Vibe-Trading Architecture")
    logger.info("=" * 70)
    logger.info(f"启动时间: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("-" * 70)

    main()
