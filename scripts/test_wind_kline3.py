# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import _exec_wind_kline, _is_wind_success, _format_wind_result

print("=== 测试 _exec_wind_kline 动力煤期货 ===")
r = _exec_wind_kline("ZC.CZC", days=5)
print(f"成功: {_is_wind_success(r)}")
print(f"错误: {r.get('error', 'N/A')}")
print(f"格式: {_format_wind_result(r, 'kline')}")

print("\n=== 测试 _exec_wind_kline 沪铜期货 ===")
r = _exec_wind_kline("CU.SHF", days=5)
print(f"成功: {_is_wind_success(r)}")
print(f"错误: {r.get('error', 'N/A')}")
print(f"格式: {_format_wind_result(r, 'kline')}")
