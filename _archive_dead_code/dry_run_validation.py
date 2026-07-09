#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dry-run 验证：配置加载 + broker 连接 + 风控触发 + 订单生成"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))


def check_config():
    import config as cfg_module
    cfg = cfg_module.Config()._config
    api_cfg = getattr(cfg, "api_config", None) or {}
    broker_cfg = api_cfg.get("broker", {})
    print("[CONFIG] broker.enable =", broker_cfg.get("enable"))
    print("[CONFIG] broker.base_url =", broker_cfg.get("base_url"))
    print("[CONFIG] broker.account =", broker_cfg.get("account"))
    print("[CONFIG] broker.dry_run =", broker_cfg.get("dry_run"))
    print("[ENV] EASYTRADER_ENABLE =", os.getenv("EASYTRADER_ENABLE", "(unset)"))
    print("[ENV] EASYTRADER_CLIENT =", os.getenv("EASYTRADER_CLIENT", "(unset)"))
    return broker_cfg


def dry_run_mock():
    from auto_hedge_executor import AutoHedgeExecutor, MockBroker
    from black_swan_auto_responder_light import ScenarioSimulator
    broker = MockBroker()
    if not broker.connect():
        print("[DRY-RUN] MockBroker 连接失败")
        return False

    executor = AutoHedgeExecutor(total_capital=5_000_000, broker=broker)

    scenarios = ["normal", "bear_market", "black_swan"]
    results = {}
    for name in scenarios:
        sim = getattr(ScenarioSimulator, name)()
        report = executor.run_once(scenario=name, **sim)
        results[name] = {
            "orders": len(report.get("orders", [])),
            "level": report.get("trigger_level"),
            "drawdown": report.get("drawdown"),
        }
        print(f"[DRY-RUN] {name} 订单数={results[name]['orders']} 触发级别={results[name]['level']} drawdown={results[name]['drawdown']}")

    # 自定义场景 1：闪崩
    flash_crash = ScenarioSimulator.black_swan()
    flash_crash.update({
        "portfolio_value": 1_200_000,
        "current_price": 1800,
        "vix_level": 85.0,
        "daily_drop": -0.18,
        "weekly_drop": -0.28,
        "limit_down_count": 5000,
        "volatility": 0.70,
        "liquidity": 0.15,
        "var_95": 0.18,
        "es_95": 0.30,
    })
    report = executor.run_once(scenario="flash_crash", **flash_crash)
    results["flash_crash"] = {
        "orders": len(report.get("orders", [])),
        "level": report.get("trigger_level"),
        "drawdown": report.get("drawdown"),
    }
    print(f"[DRY-RUN] flash_crash 订单数={results['flash_crash']['orders']} 触发级别={results['flash_crash']['level']} drawdown={results['flash_crash']['drawdown']}")

    # 自定义场景 2：深度熊市
    deep_bear = ScenarioSimulator.bear_market()
    deep_bear.update({
        "portfolio_value": 2_200_000,
        "current_price": 2200,
        "vix_level": 55.0,
        "daily_drop": -0.09,
        "weekly_drop": -0.18,
        "limit_down_count": 1200,
        "volatility": 0.50,
        "liquidity": 0.30,
        "var_95": 0.10,
        "es_95": 0.16,
    })
    report = executor.run_once(scenario="deep_bear_market", **deep_bear)
    results["deep_bear_market"] = {
        "orders": len(report.get("orders", [])),
        "level": report.get("trigger_level"),
        "drawdown": report.get("drawdown"),
    }
    print(f"[DRY-RUN] deep_bear_market 订单数={results['deep_bear_market']['orders']} 触发级别={results['deep_bear_market']['level']} drawdown={results['deep_bear_market']['drawdown']}")

    # 自定义场景 3：高波动但未崩
    high_vol = ScenarioSimulator.normal()
    high_vol.update({
        "portfolio_value": 4_200_000,
        "current_price": 2750,
        "vix_level": 42.0,
        "daily_drop": -0.04,
        "weekly_drop": -0.07,
        "limit_down_count": 80,
        "volatility": 0.38,
        "liquidity": 0.65,
        "var_95": 0.07,
        "es_95": 0.11,
    })
    report = executor.run_once(scenario="high_volatility", **high_vol)
    results["high_volatility"] = {
        "orders": len(report.get("orders", [])),
        "level": report.get("trigger_level"),
        "drawdown": report.get("drawdown"),
    }
    print(f"[DRY-RUN] high_volatility 订单数={results['high_volatility']['orders']} 触发级别={results['high_volatility']['level']} drawdown={results['high_volatility']['drawdown']}")

    # 自定义场景 4：流动性危机
    liquidity_crisis = ScenarioSimulator.bear_market()
    liquidity_crisis.update({
        "portfolio_value": 2_400_000,
        "current_price": 2300,
        "vix_level": 48.0,
        "daily_drop": -0.05,
        "weekly_drop": -0.10,
        "limit_down_count": 600,
        "volatility": 0.45,
        "liquidity": 0.20,
        "var_95": 0.09,
        "es_95": 0.14,
    })
    report = executor.run_once(scenario="liquidity_crisis", **liquidity_crisis)
    results["liquidity_crisis"] = {
        "orders": len(report.get("orders", [])),
        "level": report.get("trigger_level"),
        "drawdown": report.get("drawdown"),
    }
    print(f"[DRY-RUN] liquidity_crisis 订单数={results['liquidity_crisis']['orders']} 触发级别={results['liquidity_crisis']['level']} drawdown={results['liquidity_crisis']['drawdown']}")

    # 自定义场景 5：双周连续下跌
    biweekly_decline = ScenarioSimulator.bear_market()
    biweekly_decline.update({
        "portfolio_value": 2_500_000,
        "current_price": 2350,
        "vix_level": 38.0,
        "daily_drop": -0.04,
        "weekly_drop": -0.16,
        "limit_down_count": 350,
        "volatility": 0.40,
        "liquidity": 0.45,
        "var_95": 0.08,
        "es_95": 0.13,
    })
    report = executor.run_once(scenario="biweekly_decline", **biweekly_decline)
    results["biweekly_decline"] = {
        "orders": len(report.get("orders", [])),
        "level": report.get("trigger_level"),
        "drawdown": report.get("drawdown"),
    }
    print(f"[DRY-RUN] biweekly_decline 订单数={results['biweekly_decline']['orders']} 触发级别={results['biweekly_decline']['level']} drawdown={results['biweekly_decline']['drawdown']}")

    return results


def dry_run_real_broker(broker_type: str = "rest", broker_cfg: Dict[str, Any] = None):
    from real_broker import create_broker
    cfg = broker_cfg or {}
    if broker_type == "rest":
        config = {
            "enable": cfg.get("enable", True),
            "base_url": cfg.get("base_url", ""),
            "token": cfg.get("token", ""),
            "account": cfg.get("account", ""),
            "dry_run": cfg.get("dry_run", False),
        }
    else:
        config = None
    broker = create_broker(broker_type, config=config)
    connected = broker.connect()
    print(f"[DRY-RUN] {broker_type} 连接结果 =", connected)
    if not connected:
        return False

    account = broker.get_account_info()
    print("[DRY-RUN] 账户信息 =", json.dumps(account, ensure_ascii=False))
    positions = broker.get_positions()
    print("[DRY-RUN] 持仓数 =", len(positions))
    return True


def main():
    print("=== 1. 检查 broker 配置 ===")
    broker_cfg = check_config()

    print("\n=== 2. Mock dry-run ===")
    mock_results = dry_run_mock()
    if not mock_results:
        print("[ERROR] Mock dry-run 失败")
        return 1

    print("\n=== 3. 真实 broker dry-run（仅连接/鉴权/查询，不真实下单） ===")
    use_easytrader = os.getenv("EASYTRADER_ENABLE", "false").lower() == "true"
    if broker_cfg.get("dry_run") or (broker_cfg.get("enable") and broker_cfg.get("base_url")):
        dry_run_real_broker("rest", broker_cfg=broker_cfg)
    elif use_easytrader:
        dry_run_real_broker("easytrader", broker_cfg=broker_cfg)
    else:
        print("[SKIP] 未启用真实 broker")
        print("[INFO] RestBroker: 请将 broker.enable=true 并填写 base_url/token/account，或设置 broker.dry_run=true 进行模拟验证")
        print("[INFO] EasyTraderBroker: 请设置 EASYTRADER_ENABLE=true，并登录同花顺/国金等客户端")

    print("\n=== 验证完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
