#!/usr/bin/env python3
"""
三大数据源数据质量诊断与更新脚本

数据源优先级: Wind MCP (P1) > iFinD MCP (P2) > 通达信 (P3) > AKShare (P4) > 新浪 (P5)

诊断内容:
1. 文件存在性检查 (wind_mcp_fetcher.py / ifind_client.py / tdx_data_source.py)
2. 环境变量检查 (IFIND_TOKEN / WIND_API_KEY / NO_PROXY)
3. 数据源初始化健康度
4. 实际数据拉取测试 (3个标的 × 5天历史数据)
5. 收益率计算验证
"""

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 设置 NO_PROXY 必须在导入 requests/akshare 前
os.environ["NO_PROXY"] = "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn,127.0.0.1,localhost"

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def print_header(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_row(label: str, value, ok: bool = None) -> None:
    if ok is True:
        marker = "[OK]   "
    elif ok is False:
        marker = "[FAIL] "
    else:
        marker = "       "
    print(f"  {marker}{label}: {value}")


def check_files() -> dict:
    """1. 文件存在性检查"""
    print_header("1. 文件存在性检查")
    files = {
        "wind_mcp_fetcher.py": PROJECT_ROOT / "wind_mcp_fetcher.py",
        "utils/ifind_client.py": PROJECT_ROOT / "utils" / "ifind_client.py",
        "utils/tdx_data_source.py": PROJECT_ROOT / "utils" / "tdx_data_source.py",
        "utils/akshare_data_source.py": PROJECT_ROOT / "utils" / "akshare_data_source.py",
        "utils/data_provider.py": PROJECT_ROOT / "utils" / "data_provider.py",
    }
    result = {}
    for name, p in files.items():
        exists = p.exists()
        size = p.stat().st_size if exists else 0
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") if exists else "N/A"
        print_row(f"{name:<35}", f"exists={exists}, size={size}, mtime={mtime}", ok=exists)
        result[name] = {"exists": exists, "path": str(p), "size": size, "mtime": mtime}
    return result


def check_env() -> dict:
    """2. 环境变量检查"""
    print_header("2. 环境变量检查")
    vars_to_check = ["IFIND_TOKEN", "WIND_API_KEY", "NO_PROXY", "HTTP_PROXY", "HTTPS_PROXY"]
    result = {}
    for v in vars_to_check:
        val = os.environ.get(v, "")
        if val:
            if "TOKEN" in v or "KEY" in v:
                masked = val[:4] + "***" + val[-4:] if len(val) > 8 else "***"
                print_row(f"{v:<15}", masked, ok=True)
            else:
                print_row(f"{v:<15}", val[:80], ok=True)
        else:
            print_row(f"{v:<15}", "(empty)", ok=False)
        result[v] = bool(val)
    return result


def check_provider_health() -> dict:
    """3. MarketDataProvider 数据源健康度"""
    print_header("3. MarketDataProvider 数据源健康度")
    try:
        from utils.data_provider import MarketDataProvider

        provider = MarketDataProvider(backtest_mode=False)
        health = provider.source_health

        for src in ["wind_mcp", "ifind_mcp", "tdx", "akshare", "sina_http"]:
            s = health.get(src, {})
            ok = s.get("ok", False)
            err = str(s.get("last_error", "") or "")[:100]
            print_row(f"{src:<12}", f"ok={ok}, err={err}", ok=ok)

        return {"provider": provider, "health": health}
    except Exception as e:
        print_row("provider_init", f"异常: {type(e).__name__}: {e}", ok=False)
        import traceback
        traceback.print_exc()
        return {}


def check_actual_fetch(provider) -> dict:
    """4. 实际数据拉取测试"""
    print_header("4. 实际数据拉取测试 (3 标的 × 5d 历史)")

    test_symbols = ["600276", "588000", "601088"]  # 恒瑞医药 / 华夏科创50ETF / 中国神华
    results = {}

    for sym in test_symbols:
        print(f"\n  --- {sym} ---")
        try:
            df = provider.get_historical_data(sym, period="5d")
            if df is None or df.empty:
                print_row(f"  {sym} 获取", "返回 None 或空 DataFrame", ok=False)
                results[sym] = {"ok": False, "reason": "empty"}
                continue

            if len(df) < 2:
                print_row(f"  {sym} 行数", f"仅 {len(df)} 行, 不足 2 行", ok=False)
                results[sym] = {"ok": False, "reason": "insufficient_rows"}
                continue

            # 检查 close 字段
            if "close" not in df.columns:
                print_row(f"  {sym} close 字段", f"列: {list(df.columns)}", ok=False)
                results[sym] = {"ok": False, "reason": "no_close_column"}
                continue

            c2 = float(df["close"].iloc[-1])
            c1 = float(df["close"].iloc[-2])
            ret = c2 / c1 - 1 if c1 > 0 else 0

            print_row(f"  {sym} 行数", len(df), ok=True)
            print_row(f"  {sym} 最新日期", str(df.index[-1]), ok=True)
            print_row(f"  {sym} 前收盘", f"{c1:.4f}", ok=True)
            print_row(f"  {sym} 最新收盘", f"{c2:.4f}", ok=True)
            print_row(f"  {sym} 日收益率", f"{ret*100:.4f}%", ok=abs(ret) < 0.21)  # A股涨跌停 ±20%

            results[sym] = {
                "ok": True,
                "rows": len(df),
                "last_date": str(df.index[-1]),
                "prev_close": c1,
                "last_close": c2,
                "return": ret,
            }

            # 尝试识别数据源
            source = "unknown"
            if hasattr(df, "attrs"):
                source = df.attrs.get("source", "unknown")
            print_row(f"  {sym} 数据源", source)

        except Exception as e:
            print_row(f"  {sym} 异常", f"{type(e).__name__}: {e}", ok=False)
            results[sym] = {"ok": False, "reason": str(e)}

    return results


def check_source_individually() -> dict:
    """5. 逐个数据源独立测试 (绕过 provider, 直接调用)"""
    print_header("5. 逐个数据源独立测试 (绕过 Provider 直接调用)")

    results = {}

    # 5.1 Wind MCP 直接测试
    print("\n  --- 5.1 Wind MCP 直接测试 ---")
    wind_path = PROJECT_ROOT / "wind_mcp_fetcher.py"
    if not wind_path.exists():
        print_row("wind_mcp_fetcher.py", "文件不存在", ok=False)
        results["wind_mcp"] = {"ok": False, "reason": "file_missing"}
    else:
        try:
            spec = importlib.util.spec_from_file_location("wind_mcp_fetcher", str(wind_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # 测试 wind_get_quote
            if hasattr(mod, "wind_get_quote"):
                try:
                    quote = mod.wind_get_quote("600276.SH", is_fund=False)
                    if quote:
                        print_row("wind_get_quote(600276.SH)", f"price={quote.get('price')}", ok=True)
                        results["wind_mcp"] = {"ok": True, "quote": quote}
                    else:
                        print_row("wind_get_quote(600276.SH)", "返回空", ok=False)
                        results["wind_mcp"] = {"ok": False, "reason": "empty_quote"}
                except Exception as e:
                    print_row("wind_get_quote 异常", f"{type(e).__name__}: {e}", ok=False)
                    results["wind_mcp"] = {"ok": False, "reason": str(e)}
            else:
                print_row("wind_get_quote 函数", "不存在", ok=False)
                results["wind_mcp"] = {"ok": False, "reason": "no_quote_function"}

            # 测试 wind_get_kline
            if hasattr(mod, "wind_get_kline"):
                try:
                    klines = mod.wind_get_kline("600276.SH", days=5, is_fund=False)
                    if klines:
                        print_row("wind_get_kline(600276.SH, 5d)", f"返回 {len(klines)} 条 K 线", ok=True)
                        if results.get("wind_mcp", {}).get("ok"):
                            results["wind_mcp"]["klines"] = len(klines)
                    else:
                        print_row("wind_get_kline(600276.SH, 5d)", "返回空", ok=False)
                except Exception as e:
                    print_row("wind_get_kline 异常", f"{type(e).__name__}: {e}", ok=False)
        except Exception as e:
            print_row("wind_mcp_fetcher 加载", f"异常: {type(e).__name__}: {e}", ok=False)
            results["wind_mcp"] = {"ok": False, "reason": str(e)}

    # 5.2 iFinD MCP 直接测试
    print("\n  --- 5.2 iFinD MCP 直接测试 ---")
    ifind_token = os.environ.get("IFIND_TOKEN", "")
    if not ifind_token:
        print_row("IFIND_TOKEN", "未设置", ok=False)
        results["ifind_mcp"] = {"ok": False, "reason": "no_token"}
    else:
        try:
            from utils.ifind_client import IFindClient

            client = IFindClient()
            klines = client.get_historical_klines("600276", days=5)
            if klines:
                print_row("ifind.get_historical_klines(600276, 5d)", f"返回 {len(klines)} 条", ok=True)
                results["ifind_mcp"] = {"ok": True, "klines": len(klines)}
            else:
                print_row("ifind.get_historical_klines(600276, 5d)", "返回空", ok=False)
                results["ifind_mcp"] = {"ok": False, "reason": "empty"}
        except Exception as e:
            print_row("ifind_client 异常", f"{type(e).__name__}: {e}", ok=False)
            results["ifind_mcp"] = {"ok": False, "reason": str(e)}

    # 5.3 通达信直接测试
    print("\n  --- 5.3 通达信直接测试 ---")
    try:
        from utils.tdx_data_source import get_tdx_source

        tdx = get_tdx_source()
        if tdx and tdx.source_health.get("tdx", {}).get("ok"):
            print_row("通达信连接", "已建立", ok=True)

            # 测试获取历史数据
            df = tdx.get_historical_data("600276", days=5) if hasattr(tdx, "get_historical_data") else None
            if df is not None and not df.empty:
                print_row("tdx.get_historical_data(600276, 5d)", f"返回 {len(df)} 行", ok=True)
                results["tdx"] = {"ok": True, "rows": len(df)}
            else:
                # 尝试其他方法
                if hasattr(tdx, "get_kline"):
                    klines = tdx.get_kline("600276", count=5)
                    if klines:
                        print_row("tdx.get_kline(600276, 5)", f"返回 {len(klines)} 条", ok=True)
                        results["tdx"] = {"ok": True, "klines": len(klines)}
                    else:
                        print_row("tdx.get_kline(600276, 5)", "返回空", ok=False)
                        results["tdx"] = {"ok": False, "reason": "empty_kline"}
                else:
                    print_row("tdx 数据获取", "无可用方法", ok=False)
                    results["tdx"] = {"ok": False, "reason": "no_method"}
        else:
            print_row("通达信连接", "初始化失败", ok=False)
            results["tdx"] = {"ok": False, "reason": "init_failed"}
    except Exception as e:
        print_row("通达信 异常", f"{type(e).__name__}: {e}", ok=False)
        results["tdx"] = {"ok": False, "reason": str(e)}

    # 5.4 AKShare 直接测试
    print("\n  --- 5.4 AKShare 直接测试 ---")
    try:
        import akshare as ak

        df = ak.stock_zh_a_hist(symbol="600276", period="daily", adjust="qfq")
        if df is not None and not df.empty:
            print_row("akshare.stock_zh_a_hist(600276)", f"返回 {len(df)} 行", ok=True)
            results["akshare"] = {"ok": True, "rows": len(df)}
        else:
            print_row("akshare.stock_zh_a_hist(600276)", "返回空", ok=False)
            results["akshare"] = {"ok": False, "reason": "empty"}
    except Exception as e:
        print_row("akshare 异常", f"{type(e).__name__}: {e}", ok=False)
        results["akshare"] = {"ok": False, "reason": str(e)}

    return results


def check_shadow_state_and_returns() -> dict:
    """6. 影子账户 daily_returns.jsonl 数据质量检查"""
    print_header("6. 影子账户 daily_returns.jsonl 数据质量检查")

    jsonl_path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if not jsonl_path.exists():
        print_row("daily_returns.jsonl", "文件不存在", ok=False)
        return {"ok": False, "reason": "not_exists"}

    print_row("daily_returns.jsonl 路径", str(jsonl_path), ok=True)
    print_row("文件大小", f"{jsonl_path.stat().st_size} bytes", ok=True)
    print_row("最后修改", datetime.fromtimestamp(jsonl_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"), ok=True)

    # 逐行解析
    records = []
    zero_count = 0
    nonzero_count = 0
    parse_errors = 0

    with open(jsonl_path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ret = float(r.get("daily_return", 0))
                records.append({"date": r.get("date"), "return": ret, "source": r.get("source")})
                if abs(ret) < 1e-8:
                    zero_count += 1
                else:
                    nonzero_count += 1
            except Exception as e:
                parse_errors += 1
                print_row(f"第 {i} 行解析", f"失败: {e}", ok=False)

    print_row("总记录数", len(records), ok=len(records) > 0)
    print_row("零收益数", zero_count, ok=zero_count == 0)
    print_row("非零收益数", nonzero_count, ok=nonzero_count > 0)
    print_row("解析错误数", parse_errors, ok=parse_errors == 0)

    # 显示最近 10 条记录
    print("\n  --- 最近 10 条记录 ---")
    for r in records[-10:]:
        marker = "⚠️ 0%" if abs(r["return"]) < 1e-8 else "✓  "
        print(f"    {marker} {r['date']}  return={r['return']*100:+.4f}%  source={r['source']}")

    return {
        "ok": zero_count == 0 and nonzero_count > 0,
        "total": len(records),
        "zero_count": zero_count,
        "nonzero_count": nonzero_count,
        "records": records,
    }


def main() -> int:
    print(f"\n三大数据源数据质量诊断 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"项目根目录: {PROJECT_ROOT}")

    # 步骤 1: 检查文件
    files_info = check_files()

    # 步骤 2: 检查环境变量
    env_info = check_env()

    # 步骤 3: Provider 健康度
    provider_info = check_provider_health()

    # 步骤 4: 实际数据拉取
    if provider_info.get("provider"):
        check_actual_fetch(provider_info["provider"])

    # 步骤 5: 逐数据源独立测试
    source_results = check_source_individually()

    # 步骤 6: 影子账户数据质量
    shadow_info = check_shadow_state_and_returns()

    # === 汇总 ===
    print_header("汇总")
    print("\n  文件状态:")
    for name, info in files_info.items():
        print_row(name, "存在" if info["exists"] else "缺失", ok=info["exists"])

    print("\n  环境变量:")
    for name, exists in env_info.items():
        print_row(name, "已设置" if exists else "未设置", ok=exists)

    print("\n  数据源健康度:")
    if provider_info.get("health"):
        for src, info in provider_info["health"].items():
            print_row(src, info.get("ok", False), ok=info.get("ok", False))

    print("\n  独立测试结果:")
    for src, info in source_results.items():
        print_row(src, info.get("ok", False), ok=info.get("ok", False))

    print("\n  影子账户数据质量:")
    print_row("零收益占比",
              f"{shadow_info.get('zero_count', 0)}/{shadow_info.get('total', 0)}",
              ok=shadow_info.get("zero_count", 0) == 0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
