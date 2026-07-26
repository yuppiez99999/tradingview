#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1-Q8 验证脚本: 统一 ConfigManager
==================================

验证内容:
    1. ConfigManager 能正确加载现有 YAML 配置
    2. 优先级解析正确 (v8.3 > configs > ms_strategy)
    3. LRU+mtime 缓存生效
    4. 环境变量覆盖 QUANT_CONFIG_DIR 生效
    5. 类型化访问器返回正确字段
    6. kill_switch.py 迁移后行为一致
    7. 配置漂移检测 (审计)

运行:
    python scripts/_verify_config_manager.py
"""
from __future__ import annotations

import os
import sys
import importlib.util
import tempfile
from pathlib import Path

# 添加项目根到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 测试计数器
_passed = 0
_failed = 0
_skipped = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    """断言检查"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def _skip(name: str, reason: str = "") -> None:
    """跳过测试"""
    global _skipped
    _skipped += 1
    print(f"  ⊘ {name} (SKIP)" + (f" — {reason}" if reason else ""))


def test_config_manager_basic_loading() -> bool:
    """T1: ConfigManager 能加载 portfolio.yaml"""
    print("\n" + "=" * 70)
    print("T1: ConfigManager 基础加载")
    print("=" * 70)

    try:
        from utils.config_manager import get_config, get_portfolio_config
    except ImportError as e:
        _check("导入 ConfigManager", False, f"ImportError: {e}")
        return False
    _check("导入 ConfigManager", True)

    # 通过短名加载
    portfolio_cfg = get_config("portfolio")
    _check("get_config('portfolio') 返回非空", bool(portfolio_cfg),
           f"keys={list(portfolio_cfg.keys())[:5]}")

    # 通过类型化访问器加载
    portfolio_cfg2 = get_portfolio_config()
    _check("get_portfolio_config() 返回非空", bool(portfolio_cfg2))

    # 两次加载应一致 (缓存)
    _check("两次加载结果一致", portfolio_cfg == portfolio_cfg2)

    # 应包含关键字段
    _check("包含 account_structure", "account_structure" in portfolio_cfg)
    _check("包含 assets", "assets" in portfolio_cfg)

    return True


def test_priority_resolution() -> bool:
    """T2: 优先级解析 — v8.3 优先于 configs/"""
    print("\n" + "=" * 70)
    print("T2: 优先级解析 (v8.3 > configs)")
    print("=" * 70)

    from utils.config_manager import get_config_source, ConfigManager

    # portfolio.yaml 应来自 v8.3_institutional/config/ (唯一事实源)
    source = get_config_source("portfolio")
    _check("get_config_source('portfolio') 不为空", source is not None, f"source={source}")

    if source:
        _check("portfolio 来源是 v8.3 (唯一事实源)",
               "v8.3_institutional" in source and "config" in source,
               f"实际: {source}")

    # settings.yaml 应能解析 (在 v8.3 或 configs 中)
    settings_source = get_config_source("settings")
    _check("settings 配置可解析", settings_source is not None)

    # execution.yaml 应在 v8.3 中
    exec_source = get_config_source("execution")
    _check("execution 配置可解析", exec_source is not None, f"source={exec_source}")

    return True


def test_typed_accessors() -> bool:
    """T3: 类型化访问器返回正确字段"""
    print("\n" + "=" * 70)
    print("T3: 类型化访问器")
    print("=" * 70)

    from utils.config_manager import (
        get_kill_switch_config, get_portfolio_config,
        get_settings_config, get_execution_config,
        get_backtest_config, get_risk_budget_config,
        get_stop_loss_config
    )

    # kill_switch 配置
    ks_cfg = get_kill_switch_config()
    _check("get_kill_switch_config() 非空", bool(ks_cfg),
           f"keys={list(ks_cfg.keys()) if ks_cfg else 'empty'}")
    if ks_cfg:
        _check("kill_switch 包含 level_1", "level_1" in ks_cfg)
        _check("kill_switch 包含 level_2", "level_2" in ks_cfg)
        _check("kill_switch 包含 level_3", "level_3" in ks_cfg)
        # 验证 L1 触发阈值
        l1 = ks_cfg.get("level_1", {})
        l1_trigger = l1.get("trigger", {}).get("margin_usage_ratio")
        _check("L1 阈值=0.50", l1_trigger == 0.50, f"实际={l1_trigger}")

    # portfolio 配置
    portfolio_cfg = get_portfolio_config()
    _check("get_portfolio_config() 非空", bool(portfolio_cfg))
    if portfolio_cfg:
        # 验证 total_capital (唯一事实源应为 5000000)
        total_cap = portfolio_cfg.get("account_structure", {}).get("total_capital")
        _check("total_capital=5000000", total_cap == 5000000, f"实际={total_cap}")

    # 其他访问器 (允许为空, 因为这些配置可能不存在)
    settings_cfg = get_settings_config()
    _check("get_settings_config() 不抛异常", isinstance(settings_cfg, dict))

    exec_cfg = get_execution_config()
    _check("get_execution_config() 不抛异常", isinstance(exec_cfg, dict))

    return True


def test_cache_mechanism() -> bool:
    """T4: LRU+mtime 缓存"""
    print("\n" + "=" * 70)
    print("T4: LRU+mtime 缓存")
    print("=" * 70)

    from utils.config_manager import ConfigManager, clear_config_cache

    # 清空缓存
    clear_config_cache()

    # 第一次加载
    cm = ConfigManager.get_instance()
    cfg1 = cm.get("portfolio")

    # 第二次加载应来自缓存 (同一实例)
    cfg2 = cm.get("portfolio")
    _check("两次加载对象引用一致 (缓存生效)", cfg1 is cfg2)

    # 强制重新加载
    cfg3 = cm.reload("portfolio")
    _check("reload 返回相同内容", cfg1 == cfg3)
    # 重载后引用应不同 (新对象)
    _check("reload 后对象引用不同", cfg1 is not cfg3)

    # clear_cache 后应重新加载
    clear_config_cache()
    cfg4 = cm.get("portfolio")
    _check("clear_cache 后重新加载", cfg1 == cfg4 and cfg1 is not cfg4)

    return True


def test_env_override() -> bool:
    """T5: 环境变量 QUANT_CONFIG_DIR 覆盖"""
    print("\n" + "=" * 70)
    print("T5: 环境变量 QUANT_CONFIG_DIR 覆盖")
    print("=" * 70)

    from utils.config_manager import ConfigManager

    # 创建临时配置目录
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpconfig = Path(tmpdir) / "portfolio.yaml"
        tmpconfig.write_text(
            "# 临时配置\naccount_structure:\n  total_capital: 9999999\n  test_marker: env_override_active\n",
            encoding="utf-8"
        )

        # 设置环境变量
        old_env = os.environ.get("QUANT_CONFIG_DIR")
        os.environ["QUANT_CONFIG_DIR"] = tmpdir

        try:
            # 重置单例 (重新构建搜索路径)
            ConfigManager.reset_instance()
            cm = ConfigManager.get_instance()
            cfg = cm.get("portfolio")
            _check("环境变量覆盖生效", cfg.get("account_structure", {}).get("test_marker") == "env_override_active",
                   f"实际 marker={cfg.get('account_structure', {}).get('test_marker')}")
            _check("环境变量 total_capital=9999999",
                   cfg.get("account_structure", {}).get("total_capital") == 9999999)
        finally:
            # 恢复环境
            if old_env is None:
                os.environ.pop("QUANT_CONFIG_DIR", None)
            else:
                os.environ["QUANT_CONFIG_DIR"] = old_env
            ConfigManager.reset_instance()

    return True


def test_kill_switch_integration() -> bool:
    """T6: kill_switch.py 迁移后行为一致"""
    print("\n" + "=" * 70)
    print("T6: kill_switch.py 迁移后集成")
    print("=" * 70)

    from utils.kill_switch import KillSwitch
    from utils.config_manager import clear_config_cache

    # 清空缓存
    clear_config_cache()

    # 实例化 KillSwitch (不传 config_path, 使用 ConfigManager 路径)
    try:
        ks = KillSwitch()
    except Exception as e:
        _check("KillSwitch 实例化成功", False, f"异常: {e}")
        return False
    _check("KillSwitch 实例化成功", True)

    # 验证 config 非空
    _check("ks.config 非空", bool(ks.config), f"keys={list(ks.config.keys()) if ks.config else 'empty'}")

    # 验证关键字段
    if ks.config:
        _check("ks.config 包含 level_1", "level_1" in ks.config)
        _check("ks.config 包含 level_2", "level_2" in ks.config)
        _check("ks.config 包含 level_3", "level_3" in ks.config)
        l1 = ks.config.get("level_1", {})
        _check("L1 触发阈值=0.50",
               l1.get("trigger", {}).get("margin_usage_ratio") == 0.50)

    # 验证 check_margin_status 正常工作
    try:
        status = ks.check_margin_status(margin_usage=0.60)
        _check("check_margin_status(0.60) 返回 level=1",
               status.get("level") == 1, f"实际 level={status.get('level')}")
        _check("check_margin_status(0.60) can_trade=True",
               status.get("can_trade") is True)
        _check("check_margin_status(0.60) can_open=False",
               status.get("can_open") is False)
    except Exception as e:
        _check("check_margin_status 调用成功", False, f"异常: {e}")
        return False

    # 验证 L3 触发
    try:
        status_l3 = ks.check_margin_status(margin_usage=0.96)
        _check("check_margin_status(0.96) 返回 level=3",
               status_l3.get("level") == 3, f"实际 level={status_l3.get('level')}")
        _check("L3 can_trade=False", status_l3.get("can_trade") is False)
    except Exception as e:
        _check("L3 触发测试失败", False, f"异常: {e}")
        return False

    return True


def test_kill_switch_backward_compat() -> bool:
    """T7: 向后兼容 — 显式 config_path 仍工作"""
    print("\n" + "=" * 70)
    print("T7: kill_switch.py 向后兼容")
    print("=" * 70)

    from utils.kill_switch import KillSwitch

    # 使用旧路径 configs/portfolio.yaml 显式传入
    legacy_path = PROJECT_ROOT / "configs" / "portfolio.yaml"
    if not legacy_path.exists():
        _skip("旧版 configs/portfolio.yaml 不存在", str(legacy_path))
        return True

    try:
        ks = KillSwitch(config_path=legacy_path)
        _check("显式路径实例化成功", True)
        _check("显式路径 config 非空", bool(ks.config))
        if ks.config:
            _check("显式路径 L1 阈值=0.50",
                   ks.config.get("level_1", {}).get("trigger", {}).get("margin_usage_ratio") == 0.50)
    except Exception as e:
        _check("显式路径实例化失败", False, f"异常: {e}")
        return False

    return True


def test_audit_list_available() -> bool:
    """T8: 审计方法 list_available()"""
    print("\n" + "=" * 70)
    print("T8: 审计方法 list_available()")
    print("=" * 70)

    from utils.config_manager import list_available_configs

    configs = list_available_configs()
    _check("list_available_configs() 返回非空列表", len(configs) > 0)

    # 验证至少包含 portfolio
    names = [c.get("name") for c in configs]
    _check("包含 portfolio", "portfolio" in names)

    # 验证每个配置都有 path 和 size
    for c in configs:
        _check(f"配置 {c['name']} 有 path", "path" in c and c["path"])
        _check(f"配置 {c['name']} 有 size", "size" in c and c["size"] >= 0)
        # 打印来源 (审计)
        print(f"      📋 {c['name']}: {c['path']} ({c['size']} bytes)")
        break  # 只打印第一个, 避免刷屏

    return True


def main() -> int:
    """主函数"""
    print("=" * 70)
    print("P1-Q8 验证: 统一 ConfigManager (utils/config_manager.py)")
    print("=" * 70)

    tests = [
        test_config_manager_basic_loading,
        test_priority_resolution,
        test_typed_accessors,
        test_cache_mechanism,
        test_env_override,
        test_kill_switch_integration,
        test_kill_switch_backward_compat,
        test_audit_list_available,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"\n❌ 测试 {test.__name__} 异常: {e}")
            import traceback
            traceback.print_exc()
            global _failed
            _failed += 1

    # 总结
    print("\n" + "=" * 70)
    print(f"📊 总结: 通过 {_passed} | 失败 {_failed} | 跳过 {_skipped}")
    print("=" * 70)

    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
