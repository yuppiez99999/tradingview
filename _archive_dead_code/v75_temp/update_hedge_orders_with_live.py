# -*- coding: utf-8 -*-
"""
轻量刷新：获取 IF 期货实时价格 + 510300 ETF 实时价格
数据源：新浪 HTTP / 腾讯 HTTP 回退
"""
import json
import os
import re
from datetime import datetime

import requests

BASE_DIR = r"e:\各种PY程序\28-终极量化交易系统7.1"
JSON_PATH = os.path.join(BASE_DIR, "v7.5_institutional", "reports", "hedge_execution_orders_20260707.json")

session = requests.Session()
session.trust_env = False
session.proxies = {"http": None, "https": None}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://stock.finance.sina.com.cn/",
}


def _first_positive(parts, indices):
    for idx in indices:
        try:
            v = float(parts[idx])
            if v > 0:
                return v
        except (ValueError, TypeError, IndexError):
            continue
    return None


def get_if_realtime() -> dict:
    sina_candidates = ["IF0", "IF2506", "IF"]
    for sym in sina_candidates:
        try:
            url = f"https://hq.sinajs.cn/list=nf_{sym}"
            resp = session.get(url, headers=headers, timeout=10, verify=False)
            text = resp.text
            print(f"[DEBUG] 新浪期货 {sym} 状态: {resp.status_code}, 长度: {len(text)}")
            m = re.search(r'var hq_str_nf_' + re.escape(sym) + r'="(.+)"', text)
            if not m:
                continue
            parts = m.group(1).split(",")
            if len(parts) < 15:
                continue
            latest = _first_positive(parts, [8, 7, 3, 2])
            if latest is None:
                print(f"[DEBUG] 新浪 {sym} 无有效最新价")
                continue
            return {
                "symbol": sym,
                "source": "sina_http",
                "price": latest,
                "open": _first_positive(parts, [2, 3]),
                "high": _first_positive(parts, [3, 1]),
                "low": _first_positive(parts, [4, 2]),
                "volume": _first_positive(parts, [5, 14]),
                "open_interest": _first_positive(parts, [6, 13]),
                "settlement": _first_positive(parts, [7, 10]),
                "change_ratio": _first_positive(parts, [9]),
            }
        except Exception as e:
            print(f"[DEBUG] 新浪 {sym} 请求失败: {e}")
            continue

    try:
        url = "https://qt.gtimg.cn/q=IF"
        resp = session.get(url, headers=headers, timeout=10, verify=False)
        text = resp.text
        print(f"[DEBUG] 腾讯期货状态: {resp.status_code}, 长度: {len(text)}")
        m = re.search(r'v_(.+)="(.+)"', text)
        if m:
            parts = m.group(2).split("~")
            if len(parts) >= 5:
                latest = _first_positive(parts, [3, 5])
                if latest:
                    return {
                        "symbol": "IF",
                        "source": "tencent_http",
                        "price": latest,
                        "open": _first_positive(parts, [5]),
                        "high": _first_positive(parts, [33]),
                        "low": _first_positive(parts, [34]),
                        "volume": _first_positive(parts, [36]),
                    }
    except Exception as e:
        print(f"[DEBUG] 腾讯IF请求失败: {e}")

    return {}


def get_510300_realtime() -> dict:
    candidates = [
        ("sina_etf", "https://hq.sinajs.cn/list=sz510300"),
        ("sina_etf_sh", "https://hq.sinajs.cn/list=sh510300"),
        ("sina_fund", "https://hq.sinajs.cn/list=fu_510300"),
        ("tencent_etf", "https://qt.gtimg.cn/q=sz510300"),
        ("tencent_etf_sh", "https://qt.gtimg.cn/q=sh510300"),
        ("tencent_fund", "https://qt.gtimg.cn/q=fu_510300"),
    ]
    for name, url in candidates:
        try:
            resp = session.get(url, headers=headers, timeout=10, verify=False)
            text = resp.text
            print(f"[DEBUG] {name} 状态: {resp.status_code}, 长度: {len(text)}")
            print(f"[DEBUG] {name} body: {text[:200]!r}")

            if "hq_str_" in text:
                m = re.search(r'var hq_str_[^=]+="(.+)"', text)
                if m:
                    payload = m.group(1)
                    parts = payload.split(",")
                    if len(parts) >= 10 and parts[3] not in ("", "0.000"):
                        try:
                            return {
                                "symbol": "510300",
                                "source": name,
                                "price": float(parts[3]),
                                "open": float(parts[1]),
                                "high": float(parts[4]),
                                "low": float(parts[5]),
                                "volume": float(parts[8]),
                            }
                        except (ValueError, TypeError) as e:
                            print(f"[DEBUG] 解析 {name} 失败: {e}")

            if text.startswith("v_"):
                m = re.search(r'v_[^=]+="(.+)"', text)
                if m:
                    parts = m.group(1).split("~")
                    if len(parts) >= 5:
                        try:
                            price = float(parts[3])
                            if price > 0:
                                return {
                                    "symbol": "510300",
                                    "source": name,
                                    "price": price,
                                    "open": float(parts[5]) if len(parts) > 5 else None,
                                    "high": float(parts[33]) if len(parts) > 33 else None,
                                    "low": float(parts[34]) if len(parts) > 34 else None,
                                }
                        except (ValueError, TypeError) as e:
                            print(f"[DEBUG] 解析 {name} 失败: {e}")
        except Exception as e:
            print(f"[DEBUG] {name} 请求失败: {e}")
    return {}


def main():
    print("=" * 70)
    print("轻量刷新：IF 期货 + 510300 实时价格")
    print("=" * 70)

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated = False

    print("\n[1/2] 获取 IF 期货实时价格...")
    if_info = get_if_realtime()
    if if_info and if_info.get("price"):
        new_price = if_info["price"]
        old_price = data["hedge_plan"][0].get("futures_price", 0.0)
        print(f"  IF 最新价: {new_price} (来源: {if_info.get('source')})")
        print(f"  旧估计价: {old_price}")
        if abs(new_price - old_price) > 1e-6:
            data["hedge_plan"][0]["futures_price"] = new_price
            data["hedge_plan"][0]["futures_price_source"] = if_info.get("source")
            data["hedge_plan"][0]["futures_price_updated_at"] = datetime.now().isoformat()
            multiplier = data["hedge_plan"][0].get("multiplier", 300)
            contracts = data["hedge_plan"][0].get("contracts", 1)
            margin_rate = data["hedge_plan"][0].get("margin_rate", 0.12)
            notional = contracts * multiplier * new_price
            margin = contracts * multiplier * new_price * margin_rate
            data["hedge_plan"][0]["notional"] = round(notional, 2)
            data["hedge_plan"][0]["margin"] = round(margin, 2)
            commission_rate = 0.000023
            slippage_rate = 0.0001
            cost = contracts * multiplier * new_price * (commission_rate + slippage_rate)
            data["hedge_plan"][0]["estimated_cost"] = round(cost, 2)
            data["hedge_plan"][0]["cost_ratio"] = round(cost / 3_000_000, 6)
            print(f"  [更新] 名义价值: {notional:,.0f} | 保证金: {margin:,.0f} | 成本: {cost:,.0f}")
            updated = True
        else:
            print("  [跳过] 价格未变化")
    else:
        print("  [失败] 无法获取 IF 实时价格")

    print("\n[2/2] 获取 510300 实时价格...")
    etf_info = get_510300_realtime()
    if etf_info and etf_info.get("price"):
        new_price = etf_info["price"]
        print(f"  510300 最新价: {new_price} (来源: {etf_info.get('source')})")
        data["meta"]["underlying_510300"] = {
            "price": new_price,
            "source": etf_info.get("source"),
            "updated_at": datetime.now().isoformat(),
        }
        for layer in data.get("hedge_plan", []):
            if layer.get("type") == "OPTIONS" and layer.get("underlying") == "510300":
                for leg in layer.get("legs", []):
                    if "strike_pct" in leg and "strike_price" in leg:
                        leg["strike_price"] = round(new_price * leg["strike_pct"], 4)
                data["meta"]["underlying_510300"]["reference_strike_95"] = round(new_price * 0.95, 4)
                data["meta"]["underlying_510300"]["reference_strike_85"] = round(new_price * 0.85, 4)
                data["meta"]["underlying_510300"]["reference_strike_110"] = round(new_price * 1.10, 4)
                print(f"  [更新] 行权价已按 {new_price} 重算")
                updated = True
    else:
        print("  [失败] 无法获取 510300 实时价格")

    data["meta"]["options_iv_status"] = {
        "status": "UNAVAILABLE",
        "reason": "510300 期权 IV/权利金无公开稳定 HTTP 源，当前仅更新标的价格",
        "updated_at": datetime.now().isoformat(),
    }

    if updated:
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n[完成] 已保存: {JSON_PATH}")
    else:
        print("\n[跳过] 无更新内容")

    print("\n" + "=" * 70)
    print("执行单摘要")
    print("=" * 70)
    for layer in data.get("hedge_plan", []):
        print(f"L{layer.get('layer', '-')} | {layer.get('type', '')} | {layer.get('name', '')}")
        if "futures_price" in layer:
            print(f"     IF 价格: {layer.get('futures_price')} | 名义价值: {layer.get('notional', 0):,.0f}")
        if "legs" in layer:
            for leg in layer["legs"]:
                print(f"     {leg.get('leg')}: {leg.get('action')} {leg.get('option_type')} {leg.get('strike_pct', 0):.0%} @ {leg.get('estimated_premium', 0)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
