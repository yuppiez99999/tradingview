import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import _format_wind_result

# 模拟铜数据
copper_kline = {
    "data": [
        {
            "columns": [
                {"name": "Wind代码", "type": "string"},
                {"name": "证券简称", "type": "string"},
                {"name": "交易品种", "type": "string"},
                {"name": "最新基差", "type": "number"},
                {"name": "最新收盘价", "type": "number", "unit": "万元"},
                {"name": "交易币种", "type": "string"},
                {"name": "交易时间", "type": "string"},
                {"name": "报价单位", "type": "string"}
            ],
            "rows": [
                ["CU.SHF", "SHFE铜", "阴极铜", 480, 10.423, "CNY", "20260717 01:00:00", "人民币元/吨"]
            ]
        }
    ],
    "source": "Wind MCP"
}

print("=== 测试 _format_wind_result 铜期货 ===")
result = _format_wind_result(copper_kline, 'kline')
print(f"结果: {result}")

# 测试动力煤期货
coal_kline = {
    "data": [
        {
            "columns": [
                {"name": "Wind代码", "type": "string"},
                {"name": "证券简称", "type": "string"},
                {"name": "收盘价", "type": "number", "unit": "元"},
                {"name": "交易币种", "type": "string"},
                {"name": "交易时间", "type": "string"},
                {"name": "报价单位", "type": "string"}
            ],
            "rows": [
                ["ZC.CZC", "CZCE动力煤", 0, "CNY", "20260716 20:50:00", "人民币元/吨"]
            ]
        }
    ],
    "source": "Wind MCP"
}

print("\n=== 测试 _format_wind_result 动力煤期货 ===")
result = _format_wind_result(coal_kline, 'kline')
print(f"结果: {result}")
