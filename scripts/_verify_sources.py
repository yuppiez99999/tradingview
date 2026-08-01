#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 Wind MCP (P1) 是否真的成为 Provider 的首选数据源"""
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ["NO_PROXY"] = "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn,127.0.0.1,localhost,mcp.wind.com.cn"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def clear_cache(provider):
    """清空 Provider 内存缓存和持久化缓存"""
    with provider.cache_lock:
        provider.data_cache.clear()
    # 清空持久化缓存目录
    cache_dir = provider.persistent_cache_dir
    if cache_dir.exists():
        cleared = 0
        for p in cache_dir.glob("*.parquet"):
            try:
                p.unlink()
                cleared += 1
            except Exception:
                pass
        for p in cache_dir.glob("*.json"):
            try:
                p.unlink()
                cleared += 1
            except Exception:
                pass
        print(f"  清空持久化缓存: {cleared} 个文件")


def test_provider_with_fresh_fetch():
    """测试 Provider 用全新数据拉取验证 Wind MCP 是否为 P1"""
    print("\n" + "=" * 70)
    print("  Provider 全新拉取测试 (清空缓存后)")
    print("=" * 70)

    from utils.data_provider import MarketDataProvider

    provider = MarketDataProvider(backtest_mode=False)
    print("\n  Provider 健康度:")
    for src in ["wind_mcp", "ifind_mcp", "tdx", "akshare", "sina_http"]:
        s = provider.source_health.get(src, {})
        print(f"    {src}: ok={s.get('ok')}, err={str(s.get('last_error', ''))[:50]}")

    # 清空缓存
    print("\n  清空缓存...")
    clear_cache(provider)

    # 测试 3 个标的
    test_symbols = ["600276", "588000", "601088"]

    for sym in test_symbols:
        print(f"\n  --- {sym} ---")
        try:
            # 重置 Wind/iFinD 健康度 (避免之前的失败状态影响)
            provider.source_health["wind_mcp"]["ok"] = True
            provider.source_health["wind_mcp"]["last_error"] = None
            provider.source_health["ifind_mcp"]["ok"] = True
            provider.source_health["ifind_mcp"]["last_error"] = None

            df = provider.get_historical_data(sym, period="5d")
            if df is None or df.empty:
                print("    [FAIL] 返回空")
                continue

            print(f"    [OK] 返回 {len(df)} 行")
            print(f"    最新日期: {df.index[-1]}")
            print(f"    最新 2 行收盘价: {df['close'].iloc[-2]:.4f} → {df['close'].iloc[-1]:.4f}")
            ret = df['close'].iloc[-1] / df['close'].iloc[-2] - 1
            print(f"    日收益率: {ret*100:+.4f}%")

            # 显示最终健康度
            print("    调用后健康度:")
            for src in ["wind_mcp", "ifind_mcp", "tdx"]:
                s = provider.source_health.get(src, {})
                ok = s.get("ok")
                err = str(s.get("last_error", ""))[:50]
                marker = "✓" if ok else "✗"
                print(f"      {marker} {src}: ok={ok}, err={err}")

        except Exception as e:
            import traceback
            print(f"    [FAIL] 异常: {type(e).__name__}: {e}")
            traceback.print_exc()


def main():
    print(f"\n三大数据源数据质量验证 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    test_provider_with_fresh_fetch()


if __name__ == "__main__":
    main()
