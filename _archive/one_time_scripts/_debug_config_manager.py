#!/usr/bin/env python
"""调试 ConfigManager 资金配置问题"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

print("=" * 70)
print("ConfigManager 调试")
print("=" * 70)

from utils.config_manager import (  # noqa: E402
    clear_config_cache,
    get_config,
    get_config_source,
    get_portfolio_config,
    list_available_configs,
)

clear_config_cache()

# 1. 直接读取 portfolio.yaml
print("\n[1] 直接读取 v8.3_institutional/config/portfolio.yaml:")
yaml_path = PROJECT_ROOT / "v8.3_institutional" / "config" / "portfolio.yaml"
print(f"  path: {yaml_path}")
print(f"  exists: {yaml_path.exists()}")

import yaml  # noqa: E402

with open(yaml_path, encoding="utf-8") as f:
    raw_cfg = yaml.safe_load(f)
print(f"  raw type: {type(raw_cfg).__name__}")
print(f"  raw keys (top-level): {list(raw_cfg.keys()) if isinstance(raw_cfg, dict) else 'NOT A DICT'}")
if isinstance(raw_cfg, dict):
    # 找 stock_etf_capital
    for k, v in raw_cfg.items():
        if "capital" in k.lower() or "etf" in k.lower() or "hedge" in k.lower():
            print(f"    {k}: {v} (type={type(v).__name__})")
    # 看是否在嵌套
    if "capital" in raw_cfg:
        print(f"  capital section: {raw_cfg['capital']}")
    if "account" in raw_cfg:
        print(f"  account section: {raw_cfg['account']}")
    if "portfolio" in raw_cfg:
        print(f"  portfolio section keys: {list(raw_cfg['portfolio'].keys()) if isinstance(raw_cfg['portfolio'], dict) else raw_cfg['portfolio']}")

# 2. 通过 ConfigManager 加载
print("\n[2] ConfigManager.get_portfolio_config():")
try:
    cfg = get_portfolio_config()
    print(f"  type: {type(cfg).__name__}")
    print(f"  keys: {list(cfg.keys()) if isinstance(cfg, dict) else 'NOT A DICT'}")
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if "capital" in k.lower() or "etf" in k.lower() or "hedge" in k.lower():
                print(f"    {k}: {v} (type={type(v).__name__})")
except Exception as e:
    print(f"  FAIL: {e}")
    import traceback
    traceback.print_exc()

# 3. 通过 get_config('portfolio')
print("\n[3] ConfigManager.get_config('portfolio'):")
try:
    cfg = get_config("portfolio")
    print(f"  type: {type(cfg).__name__}")
    print(f"  keys (top-level): {list(cfg.keys()) if isinstance(cfg, dict) else 'NOT A DICT'}")
    if isinstance(cfg, dict):
        # 递归找 stock_etf_capital
        def find_key(d, target, path=""):
            if isinstance(d, dict):
                for k, v in d.items():
                    if k == target:
                        print(f"    FOUND: {path}.{k} = {v} (type={type(v).__name__})")
                    find_key(v, target, f"{path}.{k}")
        find_key(cfg, "stock_etf_capital")
        find_key(cfg, "hedge_capital")
        # 顶层 capital 字段
        if "stock_etf_capital" in cfg:
            print(f"  top-level stock_etf_capital: {cfg['stock_etf_capital']}")
        if "hedge_capital" in cfg:
            print(f"  top-level hedge_capital: {cfg['hedge_capital']}")
except Exception as e:
    print(f"  FAIL: {e}")

# 4. get_config_source
print("\n[4] get_config_source('portfolio'):")
src = get_config_source("portfolio")
print(f"  source: {src}")

# 5. list_available_configs 详细
print("\n[5] list_available_configs() 详细:")
available = list_available_configs()
for item in available:
    print(f"  name={item.get('name'):20s} | source={item.get('source')} | exists={item.get('exists')}")

print("\n" + "=" * 70)
print("调试完成")
print("=" * 70)
