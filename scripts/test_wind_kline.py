import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import _exec_wind_kline, _is_wind_success

print("=== 测试 _exec_wind_kline 沪铜期货 ===")
r = _exec_wind_kline("CU.SHF", days=5)
print(f"成功: {_is_wind_success(r)}")
print(f"错误: {r.get('error', 'N/A')}")
print(f"来源: {r.get('source', 'N/A')}")
if r.get('data'):
    data = r['data']
    print(f"\n数据类型: {type(data)}")
    if isinstance(data, list) and data:
        print(f"数据项数: {len(data)}")
        for i, item in enumerate(data):
            print(f"\n--- 第{i}项 ---")
            print(f"列名: {[c.get('name', '') for c in item.get('columns', [])]}")
            print(f"行数: {len(item.get('rows', []))}")
            if item.get('rows'):
                print(f"第一行: {item['rows'][0]}")
                if len(item.get('rows', [])) > 1:
                    print(f"第二行: {item['rows'][1]}")
