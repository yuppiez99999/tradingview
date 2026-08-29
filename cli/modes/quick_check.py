"""
快速检查模式 — v5.10 P0-9 重构
"""

import os
import sys
from typing import Any

from core.context import (
    BASE_DIR,
    ETFFundFlowMonitor,
    auto_trading,
    config_manager,
    connector_manager,
    daily_report,
    data_provider,
    rebalance_engine,
    stop_loss,
    strategy_registry,
)


def _check_commodity_module() -> bool:
    """检查大宗商品基本面模块是否可用"""
    try:
        sys.path.insert(0, os.path.join(BASE_DIR, "..", "03_投研与策略生成"))
        from 大宗商品基本面综合 import get_copper_fundamentals

        return callable(get_copper_fundamentals)
    except Exception:
        return False


def run_quick_check(args: Any) -> None:
    """快速检查模式 - 检查系统状态"""
    print("\n🔍 系统状态快速检查")
    print("=" * 70)

    # 检查模块可用性
    def _package_available(pkg_name: str) -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec(pkg_name) is not None
        except Exception:
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

    print("\n📦 模块状态:")
    for name, available in modules.items():
        status = "✅" if available else "❌"
        print(f"  {status} {name}")

    # 检查策略注册表
    print("\n📋 策略注册表:")
    strategies = strategy_registry.list()
    print(f"  ✅ {len(strategies)} 个策略已注册")
    for strategy_id in strategies[:5]:
        strategy = strategy_registry.get(strategy_id)
        print(f"    - {strategy['name']} (v{strategy['version']})")

    # 检查配置管理器
    print("\n⚙️ 配置管理器:")
    try:
        configs = config_manager.get_all()
        print(f"  ✅ 已加载 {len(configs)} 类配置")
        for config_name, config in configs.items():
            print(f"    - {config_name}: {len(config)} 项配置")
    except Exception as e:
        print(f"  ❌ 配置管理器异常: {e}")

    # 检查ETF资金流向监控配置
    print("\n📊 ETF资金流向监控:")
    print(f"  ✅ 监控标的: {len(ETFFundFlowMonitor.ETF_LIST)} 只ETF")
    etf_config = config_manager.get("etf_monitor", "signal_high_threshold")
    if etf_config:
        print(
            f"  ✅ 信号阈值: 高{etf_config/1e8:.0f}亿/中{config_manager.get('etf_monitor', 'signal_medium_threshold')/1e8:.0f}亿/低{config_manager.get('etf_monitor', 'signal_low_threshold')/1e8:.0f}亿"
        )

    # 检查数据源连接器状态
    print("\n🔗 数据源连接器:")
    connector_status = connector_manager.get_status()
    print(f"  当前活跃连接器: {connector_status.get('active_connector', 'None')}")
    print(
        f"  是否降级模式: {'✅ 是' if connector_status.get('fallback_mode') else '❌ 否'}"
    )
    print(f"  注册连接器数: {connector_status.get('total_connectors', 0)}")
    print(f"  可用连接器数: {connector_status.get('available_connectors', 0)}")

    # v5.9: 检查多模型路由器和 Wind 数据供应器
    print("\n🤖 v5.9 AI 决策模块:")
    try:
        from utils.multi_model_router import ModelRouter

        router = ModelRouter()
        print(
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
            print(
                f"    {scene_name}: {primary.get('provider')}/{primary.get('model')}{extra}"
            )
    except Exception as e:
        print(f"  ❌ 多模型路由器: {e}")

    try:
        from utils.wind_data_provider import WindDataProvider

        wp = WindDataProvider()
        status = wp.health_check()
        wind_ok = "可用" if status.get("wind_mcp_available") else "不可用(降级)"
        print(
            f"  {'✅' if status.get('wind_mcp_available') else '⚠️'} Wind MCP: {wind_ok}"
        )
        print(f"    指数缓存: {status.get('index_cache_size', 0)} 项")
        print(f"    基本面缓存: {status.get('fundamental_cache_size', 0)} 项")
    except Exception as e:
        print(f"  ❌ Wind 数据供应器: {e}")

    # 检查配置文件
    print("\n📋 配置文件:")
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
        print(f"  {status} {config_file}")

    # 检查数据缓存
    print("\n💾 数据缓存:")
    cache_dir = os.path.join(BASE_DIR, "data", "cache")
    if os.path.exists(cache_dir):
        cache_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
        print(f"  ✅ 缓存目录存在，{len(cache_files)}个文件")
    else:
        print("  ❌ 缓存目录不存在")
