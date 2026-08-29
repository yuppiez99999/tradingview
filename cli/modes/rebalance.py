"""
再平衡模式 — v5.10 P0-9 重构
"""

import os

from core.context import (
    BASE_DIR,
    ExcelDrivenRebalancingEngineV4,
    ProgressIndicator,
    StrategyRegistry,
    logger,
)
from utils.cli_helpers import write_report_file


def run_rebalance(args):
    """再平衡模式 - 执行再平衡计划 (支持Excel和portfolio.yaml两种方式)"""
    print("\n🔄 执行再平衡计划")
    print("=" * 70)

    progress = ProgressIndicator("再平衡执行", 6)

    # 使用增强版Excel驱动引擎
    progress.update(1, "初始化再平衡引擎...")
    strategy_registry = StrategyRegistry()
    engine = ExcelDrivenRebalancingEngineV4(strategy_registry=strategy_registry)

    progress.update(2, "加载配置文件...")
    loaded = engine.load_all()

    # 如果Excel加载失败,尝试从portfolio.yaml加载
    if not loaded:
        print("\n⚠️ Excel文件不存在,尝试从portfolio.yaml加载...")
        try:
            import yaml

            yaml_path = os.path.join(BASE_DIR, "config", "portfolio.yaml")
            if os.path.exists(yaml_path):
                with open(yaml_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                # v5.10+: normalize positions dict → assets list
                if "assets" not in config and "positions" in config:
                    config["assets"] = [
                        {
                            "code": c,
                            "name": v.get("name", c),
                            "category": v.get("sector", ""),
                            "target_weight": v.get("target_weight", 0.0),
                        }
                        for c, v in config["positions"].items()
                        if isinstance(v, dict)
                    ]
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
                    print(f"✅ 已从portfolio.yaml加载 {len(assets)} 只标的配置")
        except Exception as e:
            print(f"⚠️ portfolio.yaml加载失败: {e}")

    if engine.is_loaded:
        progress.update(3, "构建交易指令...")
        engine.build_trade_orders()

        progress.update(4, "生成报告...")
        report = engine.generate_report()
        print("\n" + report)

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
            print("\n✅ 止损止盈规则已同步到 config/rebalance_stop_loss_v43.json")

        write_report_file(report, args.output)

        progress.complete("✅ 再平衡执行完成")
    else:
        progress.complete("❌ 无法加载再平衡数据")
        print("\n💡 提示: 请检查以下文件是否存在:")
        print("  1. config/portfolio.yaml (必需)")
        print("  2. data_extraction_*.xlsx (可选,用于详细再平衡计划)")
