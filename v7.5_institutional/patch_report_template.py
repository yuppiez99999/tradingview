# -*- coding: utf-8 -*-
"""
修改 weekly_trade_executor.py，在报告中添加收盘跟踪指标
"""
from pathlib import Path

file_path = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\weekly_trade_executor.py")

text = file_path.read_text(encoding="utf-8")

old_text = '''        report += f"\\n---\\n*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
        return report'''

new_text = '''
        report += """
## 八、市场常规跟踪（每日/每周）

| 指标 | 数值/信号 | 备注 |
|------|----------|------|
| 持仓标的价格与成交量 | 待收盘后填入 | 重点观察是否放量突破/跌破 |
| 沪深300指数点位与波动率 | 待收盘后填入 | 关注日内高低点与振幅 |
| 股指期货基差（近月/远月） | 待收盘后填入 | 正基差=升水，负基差=贴水 |
| 50ETF/300ETF期权隐含波动率 | 待收盘后填入 | IV与RV对比判断波动率溢价 |
| 北向资金流向 | 待收盘后填入 | 连续流入/流出判断外资情绪 |
| 两融余额变化 | 待收盘后填入 | 杠杆资金情绪指标 |
| 行业轮动信号 | 待收盘后填入 | 关注顺周期/科技/消费切换 |

## 九、事件跟踪（不定期）

| 事件类型 | 最新动态 | 影响评估 |
|----------|----------|----------|
| 央行货币政策信号（LPR/MLF/降准） | 待更新 | 关注利率走廊与流动性 |
| 产业政策（半导体/新能源/医药） | 待更新 | 十五五重点方向 |
| 海外宏观（美联储/FOMC/非农） | 待更新 | 影响外资流向与汇率 |
| 地缘政治（台海/中美/能源） | 待更新 | 风险溢价与避险情绪 |
| 财报季（7-8月中报、10月三季报） | 待更新 | 个股业绩雷与超预期 |

"""
        report += f"\\n---\\n*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
        return report'''

if old_text in text:
    text = text.replace(old_text, new_text, 1)
    file_path.write_text(text, encoding="utf-8")
    print("已添加收盘跟踪指标到报告模板")
else:
    print("未找到替换目标，请检查文件内容")
    print("查找内容:", repr(old_text[:100]))
