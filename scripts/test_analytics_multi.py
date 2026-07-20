# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import _format_analytics_multi

# 铜数据
copper_data = [
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
]

# 动力煤数据
coal_data = [
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
]

# 碳市场数据
carbon_data = [
    {
        "columns": [
            {"name": "日期", "type": "date"},
            {"name": "中国:广东:碳排放权配额收盘价(HBEA)", "type": "number", "unit": "元/吨"},
            {"name": "中国:北京:碳排放权成交量(BEA)", "type": "number", "unit": "吨"},
            {"name": "中国:湖北:碳排放权成交量(CQEA)", "type": "number", "unit": "吨"},
            {"name": "中国:天津:碳排放权成交量(TJEA)", "type": "number", "unit": "吨"},
            {"name": "中国:广东:碳排放权成交量 HBEA)", "type": "number", "unit": "吨"},
            {"name": "中国:广东:碳排放权成交量(HBEA)", "type": "number", "unit": "吨"},
            {"name": "中国:北京:碳排放权成交量(BEA)", "type": "number", "unit": "吨"},
            {"name": "中国:天津:碳排放权成交量(TJEA)", "type": "number", "unit": "吨"},
            {"name": "中国:湖北:碳排放权成交量(CQEA)", "type": "number", "unit": "吨"},
            {"name": "中国:福建:碳排放权成交量(FJEA)", "type": "number", "unit": "吨"},
            {"name": "中国:福建:碳排放权成交量(FJEA)", "type": "number", "unit": "吨"}
        ],
        "rows": [
            ["2026-07-15", 36.98, 21343128, 12275120.5, 46035442, 101394403, 31, None, None, None, None, None],
            ["2026-07-14", 36, 21343128, 12275120.5, 46035442, 101394372, 466, None, None, None, None, None]
        ]
    }
]

print("=== 测试 _format_analytics_multi 铜期货 (kline) ===")
result = _format_analytics_multi(copper_data, 'kline')
print(f"结果: {result}")

print("\n=== 测试 _format_analytics_multi 动力煤期货 (kline) ===")
result = _format_analytics_multi(coal_data, 'kline')
print(f"结果: {result}")

print("\n=== 测试 _format_analytics_multi 碳市场 (carbon_market) ===")
result = _format_analytics_multi(carbon_data, 'carbon_market')
print(f"结果: {result}")
