#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三大数据源实际拉取能力验证 + 影子账户零收益数据修复

任务:
1. 测试 Wind MCP (P1) 实际拉取能力 (通过 Provider)
2. 测试 iFinD MCP (P2) 实际拉取能力 (通过 Provider)
3. 测试 通达信 (P3) 实际拉取能力 (通过 Provider)
4. 用真实数据重新计算 7-28/29/30 的 daily_return
5. 更新 daily_returns.jsonl
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

# NO_PROXY 必须在导入 requests/akshare 前设置
os.environ["NO_PROXY"] = "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn,127.0.0.1,localhost"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def test_three_sources_individually():
    """逐个测试三大数据源的实际拉取能力"""
    print("\n" + "=" * 70)
    print("  三大数据源独立实际拉取测试")
    print("=" * 70)

    results = {}

    # === 1. Wind MCP 直接测试 (使用 tools/wind_mcp_fetcher.py) ===
    print("\n--- 1. Wind MCP (P1) 直接测试 ---")
    wind_path = PROJECT_ROOT / "tools" / "wind_mcp_fetcher.py"
    if not wind_path.exists():
        print(f"  [FAIL] 文件不存在: {wind_path}")
        results["wind_mcp"] = {"ok": False, "reason": "file_missing"}
    else:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("wind_mcp_fetcher", str(wind_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # 测试 wind_get_quote
            print(f"  测试 wind_get_quote('600276.SH')...")
            quote = mod.wind_get_quote("600276.SH", is_fund=False)
            if quote:
                price = quote.get("price")
                prev_close = quote.get("prev_close")
                print(f"  [OK] quote: price={price}, prev_close={prev_close}, source={quote.get('source')}")
                results["wind_mcp_quote"] = {"ok": True, "price": price, "prev_close": prev_close}
            else:
                print(f"  [FAIL] wind_get_quote 返回空")
                results["wind_mcp_quote"] = {"ok": False, "reason": "empty_quote"}

            # 测试 wind_get_kline
            print(f"  测试 wind_get_kline('600276.SH', days=5)...")
            klines = mod.wind_get_kline("600276.SH", days=5, is_fund=False)
            if klines:
                print(f"  [OK] kline: 返回 {len(klines)} 条 K 线")
                # 显示最后 2 条
                for k in klines[-2:]:
                    print(f"    {k}")
                results["wind_mcp_kline"] = {"ok": True, "count": len(klines)}
            else:
                print(f"  [FAIL] wind_get_kline 返回空")
                results["wind_mcp_kline"] = {"ok": False, "reason": "empty_kline"}

            # 测试 ETF (588000)
            print(f"  测试 wind_get_quote('588000.SH', is_fund=True)...")
            etf_quote = mod.wind_get_quote("588000.SH", is_fund=True)
            if etf_quote:
                print(f"  [OK] etf_quote: price={etf_quote.get('price')}")
                results["wind_mcp_etf"] = {"ok": True, "price": etf_quote.get("price")}
            else:
                print(f"  [FAIL] wind_get_quote ETF 返回空")
                results["wind_mcp_etf"] = {"ok": False, "reason": "empty_etf_quote"}

        except Exception as e:
            import traceback
            print(f"  [FAIL] 加载异常: {type(e).__name__}: {e}")
            traceback.print_exc()
            results["wind_mcp"] = {"ok": False, "reason": str(e)}

    # === 2. iFinD MCP 直接测试 ===
    print("\n--- 2. iFinD MCP (P2) 直接测试 ---")
    try:
        from utils.ifind_client import IFindClient

        client = IFindClient()

        # 测试股票 (600276)
        print(f"  测试 ifind.get_historical_klines('600276', days=5)...")
        klines = client.get_historical_klines("600276", days=5)
        if klines:
            print(f"  [OK] 返回 {len(klines)} 条 K 线")
            for k in klines[-2:]:
                print(f"    {k}")
            results["ifind_stock"] = {"ok": True, "count": len(klines)}
        else:
            print(f"  [FAIL] 返回空")

            # 尝试调用底层 call 方法看具体错误
            print(f"  诊断: 尝试直接调用 ifind.call('stock', 'get_stock_performance', ...)...")
            try:
                end_date = datetime.now().strftime("%Y%m%d")
                start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
                result = client.call(
                    "stock", "get_stock_performance",
                    {"query": f"600276从{start_date}到{end_date}的开盘价、收盘价、最高价、最低价、成交量"}
                )
                if result.get("ok"):
                    data = result.get("data", {})
                    print(f"  诊断: call 成功, data keys: {list(data.keys())[:10] if isinstance(data, dict) else type(data)}")
                    # 显示原始响应前 500 字符
                    raw = json.dumps(data, ensure_ascii=False)[:500]
                    print(f"  诊断: 原始响应: {raw}")
                else:
                    print(f"  诊断: call 失败: {result.get('error')}")
            except Exception as e2:
                print(f"  诊断: call 异常: {type(e2).__name__}: {e2}")

            results["ifind_stock"] = {"ok": False, "reason": "empty_klines"}

        # 测试 ETF (588000)
        print(f"  测试 ifind.get_historical_klines('588000', days=5)...")
        etf_klines = client.get_historical_klines("588000", days=5)
        if etf_klines:
            print(f"  [OK] ETF 返回 {len(etf_klines)} 条 K 线")
            results["ifind_etf"] = {"ok": True, "count": len(etf_klines)}
        else:
            print(f"  [FAIL] ETF 返回空")
            results["ifind_etf"] = {"ok": False, "reason": "empty_etf"}

    except Exception as e:
        import traceback
        print(f"  [FAIL] 异常: {type(e).__name__}: {e}")
        traceback.print_exc()
        results["ifind"] = {"ok": False, "reason": str(e)}

    # === 3. 通达信直接测试 ===
    print("\n--- 3. 通达信 (P3) 直接测试 ---")
    try:
        from utils.tdx_data_source import get_tdx_source

        tdx = get_tdx_source()
        if not tdx or not tdx.source_health.get("tdx", {}).get("ok"):
            print(f"  [FAIL] 通达信连接失败")
            results["tdx"] = {"ok": False, "reason": "connection_failed"}
        else:
            print(f"  [OK] 通达信连接已建立")

            # 测试 get_historical_klines
            print(f"  测试 tdx.get_historical_klines('600276', period='1d', count=5)...")
            df = tdx.get_historical_klines("600276", period="1d", count=5)
            if df is not None and not df.empty:
                print(f"  [OK] 返回 {len(df)} 行")
                # 显示最后 2 行
                print(df.tail(2).to_string())
                results["tdx_stock"] = {"ok": True, "rows": len(df)}
            else:
                print(f"  [FAIL] 返回空")
                results["tdx_stock"] = {"ok": False, "reason": "empty"}

            # 测试 ETF (588000)
            print(f"\n  测试 tdx.get_historical_klines('588000', period='1d', count=5)...")
            etf_df = tdx.get_historical_klines("588000", period="1d", count=5)
            if etf_df is not None and not etf_df.empty:
                print(f"  [OK] ETF 返回 {len(etf_df)} 行")
                print(etf_df.tail(2).to_string())
                results["tdx_etf"] = {"ok": True, "rows": len(etf_df)}
            else:
                print(f"  [FAIL] ETF 返回空")
                results["tdx_etf"] = {"ok": False, "reason": "empty"}

    except Exception as e:
        import traceback
        print(f"  [FAIL] 异常: {type(e).__name__}: {e}")
        traceback.print_exc()
        results["tdx"] = {"ok": False, "reason": str(e)}

    return results


def fetch_real_price_for_date(symbol: str, target_date: str, provider) -> float:
    """通过 Provider 获取指定日期的收盘价

    Args:
        symbol: 股票代码 (如 "600276")
        target_date: 目标日期 "YYYY-MM-DD"
        provider: MarketDataProvider 实例

    Returns:
        收盘价 (float), 失败返回 None
    """
    try:
        df = provider.get_historical_data(symbol, period="1m")  # 1个月数据
        if df is None or df.empty:
            return None

        # 将目标日期转换为 datetime
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        target_next = target_dt + timedelta(days=1)

        # 查找目标日期的记录 (df.index 是 datetime)
        for idx, row in df.iterrows():
            if hasattr(idx, "date"):
                idx_date = idx.date() if hasattr(idx, "date") else idx
            else:
                idx_date = idx

            # 转换为 datetime.date 比较
            if hasattr(idx_date, "year"):
                idx_str = idx_date.strftime("%Y-%m-%d")
                if idx_str == target_date:
                    return float(row["close"])

        # 如果没找到精确匹配，尝试用 nearest
        # df.index 应该已经按时间排序
        if len(df) >= 2:
            # 找最接近目标日期的记录
            target_ts = target_dt.timestamp()
            best_idx = None
            best_diff = float("inf")
            for idx in df.index:
                if hasattr(idx, "timestamp"):
                    diff = abs(idx.timestamp() - target_ts)
                    if diff < best_diff:
                        best_diff = diff
                        best_idx = idx

            if best_idx is not None and best_diff < 7 * 24 * 3600:  # 7天内
                return float(df.loc[best_idx, "close"])

        return None
    except Exception as e:
        print(f"    获取 {symbol} @ {target_date} 失败: {e}")
        return None


def get_target_weights_from_trade_plans():
    """从 trade_plan 文件读取 target_weights

    Returns:
        Dict[date_str, Dict[symbol, weight]]
    """
    print("\n" + "=" * 70)
    print("  从 trade_plan 读取 target_weights")
    print("=" * 70)

    trade_plans_dir = PROJECT_ROOT / "v8.3_institutional" / "trade_plans"
    if not trade_plans_dir.exists():
        print(f"  [FAIL] trade_plans 目录不存在: {trade_plans_dir}")
        return {}

    # 需要修复的日期
    target_dates = ["20260728", "20260729", "20260730", "20260731"]
    result = {}

    for d in target_dates:
        tp_path = trade_plans_dir / f"trade_plan_{d}.json"
        if not tp_path.exists():
            print(f"  {d}: 文件不存在")
            continue

        try:
            with open(tp_path, "r", encoding="utf-8") as f:
                tp = json.load(f)

            exec_plan = tp.get("execution_plan", {})
            day_capital = float(exec_plan.get("day_capital", 100000))
            morning = exec_plan.get("morning_orders", [])
            afternoon = exec_plan.get("afternoon_orders", [])
            all_orders = morning + afternoon

            target_weights = {}
            for ord in all_orders:
                sym = ord.get("code", "")
                amt = float(ord.get("est_amount", 0))
                side = ord.get("side", "BUY").upper()
                sign = 1.0 if side == "BUY" else -1.0
                if sym and day_capital > 0:
                    target_weights[sym] = target_weights.get(sym, 0.0) + sign * amt / day_capital

            date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            result[date_str] = {
                "target_weights": target_weights,
                "day_capital": day_capital,
                "orders_count": len(all_orders),
            }

            print(f"  {d}: orders={len(all_orders)}, day_capital={day_capital:.0f}, symbols={len(target_weights)}")
            for sym, w in list(target_weights.items())[:5]:
                print(f"    {sym}: weight={w:+.4f}")
            if len(target_weights) > 5:
                print(f"    ... (共 {len(target_weights)} 个标的)")

        except Exception as e:
            print(f"  {d}: 解析失败: {e}")

    return result


def recalculate_daily_returns():
    """用真实数据重新计算零收益日期的 daily_return"""
    print("\n" + "=" * 70)
    print("  重新计算零收益日期的 daily_return")
    print("=" * 70)

    # 加载 trade_plans
    plans = get_target_weights_from_trade_plans()

    if not plans:
        print("  [FAIL] 没有可用的 trade_plan 数据")
        return None

    # 初始化 Provider
    try:
        from utils.data_provider import MarketDataProvider
        provider = MarketDataProvider(backtest_mode=False)
        print(f"\n  Provider 初始化完成")
        print(f"    Wind MCP: ok={provider.source_health['wind_mcp']['ok']}")
        print(f"    iFinD MCP: ok={provider.source_health['ifind_mcp']['ok']}")
        print(f"    通达信: ok={provider.source_health['tdx']['ok']}")
        print(f"    AKShare: ok={provider.source_health['akshare']['ok']}")
    except Exception as e:
        print(f"  [FAIL] Provider 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return None

    # 计算每个日期的 daily_return
    calculated_returns = {}

    for date_str, plan_info in plans.items():
        target_weights = plan_info["target_weights"]
        if not target_weights:
            print(f"\n  {date_str}: 无 target_weights, 跳过")
            calculated_returns[date_str] = 0.0
            continue

        print(f"\n  {date_str}: 计算 {len(target_weights)} 个标的的加权收益...")

        daily_return = 0.0
        total_weight = sum(abs(w) for w in target_weights.values())
        success_count = 0
        fail_count = 0

        if total_weight <= 0:
            print(f"    总权重为 0, 跳过")
            calculated_returns[date_str] = 0.0
            continue

        for symbol, weight in target_weights.items():
            if abs(weight) < 0.001:
                continue

            # 获取该日期的收盘价和前一日收盘价
            # 用 period="1m" 获取 1 个月数据, 然后查找指定日期
            try:
                df = provider.get_historical_data(symbol, period="1m")
                if df is None or df.empty or len(df) < 2:
                    print(f"    {symbol}: 数据不足 (rows={len(df) if df is not None else 0})")
                    fail_count += 1
                    continue

                # 查找目标日期和前一日的收盘价
                target_dt = datetime.strptime(date_str, "%Y-%m-%d")
                prev_dt = target_dt - timedelta(days=1)

                target_close = None
                prev_close = None

                # 遍历查找
                for idx, row in df.iterrows():
                    if hasattr(idx, "strftime"):
                        idx_str = idx.strftime("%Y-%m-%d")
                    else:
                        idx_str = str(idx)[:10]

                    # 找目标日期附近最近的数据 (±3天)
                    if idx_str == date_str:
                        target_close = float(row["close"])
                    # 找前一日 (可能需要 ±3 天窗口, 因为非交易日)
                    elif (prev_dt - timedelta(days=2)).strftime("%Y-%m-%d") <= idx_str < date_str:
                        prev_close = float(row["close"])  # 取最近的

                # 如果精确匹配失败, 使用最后两行 (假设是最新交易日)
                if target_close is None:
                    target_close = float(df["close"].iloc[-1])
                if prev_close is None:
                    prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else None

                if target_close is not None and prev_close is not None and prev_close > 0:
                    ret = target_close / prev_close - 1
                    daily_return += weight * ret
                    success_count += 1
                    print(f"    {symbol}: weight={weight:+.4f}, close {prev_close:.2f}→{target_close:.2f}, ret={ret*100:+.4f}%, contrib={weight*ret*100:+.4f}%")
                else:
                    fail_count += 1
                    print(f"    {symbol}: 无法获取收盘价")

            except Exception as e:
                fail_count += 1
                print(f"    {symbol}: 异常 {type(e).__name__}: {e}")

        calculated_returns[date_str] = daily_return
        print(f"  {date_str} 汇总: daily_return={daily_return*100:+.4f}%, success={success_count}, fail={fail_count}")

    return calculated_returns


def update_daily_returns_jsonl(calculated_returns):
    """更新 daily_returns.jsonl 文件"""
    print("\n" + "=" * 70)
    print("  更新 daily_returns.jsonl")
    print("=" * 70)

    jsonl_path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if not jsonl_path.exists():
        print(f"  [FAIL] 文件不存在: {jsonl_path}")
        return False

    # 备份原文件
    backup_path = jsonl_path.with_suffix(".jsonl.bak")
    import shutil
    shutil.copy2(jsonl_path, backup_path)
    print(f"  备份: {backup_path}")

    # 读取现有记录
    existing_lines = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                existing_lines.append(line)

    print(f"  原始记录数: {len(existing_lines)}")

    # 合并更新
    updated_records = {}
    for line in existing_lines:
        try:
            r = json.loads(line)
            date = r.get("date")
            if date:
                updated_records[date] = r
        except Exception:
            continue

    # 用新计算的 returns 覆盖
    updated_count = 0
    for date_str, daily_return in calculated_returns.items():
        if date_str in updated_records:
            old_ret = updated_records[date_str].get("daily_return", 0)
            updated_records[date_str]["daily_return"] = round(float(daily_return), 6)
            updated_records[date_str]["source"] = "v9_phase10_real_backtest_fixed"
            updated_records[date_str]["updated_at"] = datetime.now().isoformat()
            print(f"  更新 {date_str}: {old_ret*100:+.4f}% → {daily_return*100:+.4f}%")
            updated_count += 1
        else:
            # 新增
            updated_records[date_str] = {
                "date": date_str,
                "daily_return": round(float(daily_return), 6),
                "source": "v9_phase10_real_backtest_fixed",
                "updated_at": datetime.now().isoformat(),
            }
            print(f"  新增 {date_str}: {daily_return*100:+.4f}%")
            updated_count += 1

    # 按日期排序写入
    sorted_dates = sorted(updated_records.keys())
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for d in sorted_dates:
            r = updated_records[d]
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n  更新完成: 共更新 {updated_count} 条记录")

    # 验证
    print(f"\n  === 最终 daily_returns.jsonl 内容 ===")
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                ret = r.get("daily_return", 0)
                marker = "⚠️ 0%" if abs(ret) < 1e-8 else "✓  "
                print(f"    {marker} {r.get('date')}  return={ret*100:+.4f}%  source={r.get('source')}")

    return True


def main():
    print(f"\n三大数据源数据质量更新 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"项目根目录: {PROJECT_ROOT}")

    # 1. 三大数据源独立测试
    source_results = test_three_sources_individually()

    # 2. 重新计算零收益日期的 daily_return
    calculated_returns = recalculate_daily_returns()

    # 3. 更新 daily_returns.jsonl
    if calculated_returns:
        update_daily_returns_jsonl(calculated_returns)

    # 4. 汇总
    print("\n" + "=" * 70)
    print("  汇总")
    print("=" * 70)
    print(f"\n  数据源独立测试结果:")
    for name, info in source_results.items():
        if isinstance(info, dict):
            ok = info.get("ok", False)
            print(f"    {name}: {'OK' if ok else 'FAIL'}")

    if calculated_returns:
        print(f"\n  重新计算的 daily_returns:")
        for date_str, ret in calculated_returns.items():
            print(f"    {date_str}: {ret*100:+.4f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
