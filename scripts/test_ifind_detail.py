import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import (
    _exec_ifind,
    _is_ifind_success,
)

print("=== 测试 iFinD MCP 动力煤 ===")
result = _exec_ifind("edb", "get_edb_data", {"query": "动力煤期货活跃合约收盘价"})
print(f"成功: {_is_ifind_success(result)}")
print(f"错误: {result.get('error', 'N/A')}")
print(f"来源: {result.get('source', 'N/A')}")
if result.get('data'):
    data = result['data']
    if isinstance(data, dict):
        print(f"答案: {str(data.get('answer', ''))[:300]}")
        print(f"结果: {str(data.get('result', ''))[:300]}")

print("\n=== 测试 iFinD MCP 港口库存 ===")
for port in ["秦皇岛", "曹妃甸", "黄骅港"]:
    result = _exec_ifind("edb", "get_edb_data", {"query": f"{port}港动力煤库存"})
    print(f"\n{port}:")
    print(f"  成功: {_is_ifind_success(result)}")
    print(f"  错误: {result.get('error', 'N/A')}")
    if result.get('data'):
        data = result['data']
        if isinstance(data, dict):
            print(f"  答案: {str(data.get('answer', ''))[:200]}")

print("\n=== 测试 iFinD MCP 碳市场 ===")
for name, query in [
    ("CEA收盘价", "全国碳排放权交易市场CEA最新收盘价"),
    ("CEA成交量", "全国碳排放权交易市场CEA最新成交量"),
    ("CCER挂牌价", "CCER挂牌价及成交情况"),
]:
    result = _exec_ifind("edb", "get_edb_data", {"query": query})
    print(f"\n{name}:")
    print(f"  成功: {_is_ifind_success(result)}")
    print(f"  错误: {result.get('error', 'N/A')}")
    if result.get('data'):
        data = result['data']
        if isinstance(data, dict):
            print(f"  答案: {str(data.get('answer', ''))[:200]}")
