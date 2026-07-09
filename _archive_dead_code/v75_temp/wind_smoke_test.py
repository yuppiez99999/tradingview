# -*- coding: utf-8 -*-
"""
Wind MCP 连通性测试 — 用环境变量传入 API Key
避免写入任何配置文件, 仅在进程内使用
"""
import os
import subprocess
import json
from pathlib import Path

WIND_SKILL_DIR = r"C:\Users\Administrator\.agents\skills\wind-mcp-skill"
WIND_API_KEY = "ak_Tk4Y_UE-MfUof8DLLbKpHZZY-kh1q5KD"

# 设置环境变量 (仅当前进程及子进程可见)
os.environ["WIND_API_KEY"] = WIND_API_KEY


def call_wind(server_type, tool_name, params):
    """调用 Wind CLI"""
    cli_path = os.path.join(WIND_SKILL_DIR, "scripts", "cli.mjs")
    cmd = [
        "node", cli_path, "call", server_type, tool_name,
        json.dumps(params, ensure_ascii=False),
    ]
    print(f"\n>>> wind {server_type}.{tool_name} {json.dumps(params, ensure_ascii=False)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=WIND_SKILL_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=os.environ,
        )
        print(f"  [exit={result.returncode}] stdout (前 800 字): {result.stdout[:800]}")
        if result.stderr:
            print(f"  STDERR (前 300 字): {result.stderr[:300]}")
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except Exception:
                return {"ok": True, "raw": result.stdout[:1000]}
        else:
            try:
                return json.loads(result.stdout)
            except Exception:
                return {"ok": False, "error": result.stdout[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    print("=" * 60)
    print("Wind MCP 连通性测试 (环境变量传入 Key)")
    print(f"Key masked: {WIND_API_KEY[:3]}***{WIND_API_KEY[-4:]}")
    print("=" * 60)

    # 测试1: 基金 K 线 (510300.SH 沪深300ETF, 最近1年)
    r1 = call_wind("fund_data", "get_fund_kline", {
        "windcode": "510300.SH",
        "begin_date": "20250701",
        "end_date": "20260705",
        "period": "10",  # 日K
    })
    print(f"\n[测试1] fund_data.get_fund_kline 510300.SH")
    print(f"  ok: {r1.get('ok', 'N/A')}")
    if r1.get("ok"):
        # 简要显示
        content = r1.get("data", {}).get("result", {}).get("content", [])
        if isinstance(content, list) and content:
            text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
            print(f"  content[0] (前300字): {text[:300]}")
        else:
            print(f"  data: {json.dumps(r1.get('data', {}), ensure_ascii=False)[:300]}")
    else:
        print(f"  error: {r1.get('error', 'N/A')}")

    # 测试2: 股票行情快照 (600519.SH 贵州茅台)
    r2 = call_wind("stock_data", "get_stock_price_indicators", {
        "windcode": "600519.SH",
        "indexes": "中文简称,最新成交价,涨跌幅,总市值2",
    })
    print(f"\n[测试2] stock_data.get_stock_price_indicators 600519.SH")
    print(f"  ok: {r2.get('ok', 'N/A')}")
    if r2.get("ok"):
        content = r2.get("data", {}).get("result", {}).get("content", [])
        if isinstance(content, list) and content:
            text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
            print(f"  content[0] (前300字): {text[:300]}")
        else:
            print(f"  data: {json.dumps(r2.get('data', {}), ensure_ascii=False)[:300]}")
    else:
        print(f"  error: {r2.get('error', 'N/A')}")


if __name__ == "__main__":
    main()
