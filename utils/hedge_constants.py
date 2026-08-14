"""对冲相关共享常量。

Q-1 修复: 将防御资产配置从 hedge_execution_orders.py 和 hedge_quantity_calculator.py
的重复硬编码提取为单一事实源。未来动态化时改为从 positions.json 读取。
"""

# 防御资产配置 (代码, 名称, 持仓金额)
DEFENSE_ASSETS = {
    "sh600900": ("长江电力", 58_800),
    "sz518880": ("黄金ETF华安", 99_450),
    "sh601088": ("中国神华", 38_500),
}