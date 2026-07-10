"""
v7.5 同花顺客户端配置

包含窗口标题、控件映射、路径等静态配置。
不同版本同花顺客户端可能需要调整。
"""

# 同花顺客户端可执行文件路径（请按实际安装路径修改）
HEXIN_STOCK_EXE = r"D:\新建文件夹 (3)\同花顺\同花顺\xiadan.exe"
HEXIN_FUTURES_EXE = r"D:\新建文件夹 (3)\同花顺\同花顺\xiadan.exe"

# 窗口标题关键字（用于 pywinauto 查找窗口）
HEXIN_STOCK_WINDOW_TITLES = [
    "同花顺(",
    "同花顺软件",
    "同花顺交易",
    "网上股票交易系统",
]
HEXIN_FUTURES_WINDOW_TITLES = [
    "同花顺期货通",
    "同花顺期货交易",
    "同花顺期货 v",
]

# 模拟交易相关窗口标题
HEXIN_SIM_STOCK_TITLES = [
    "模拟炒股",
    "模拟交易",
    "模拟股票",
]
HEXIN_SIM_FUTURES_TITLES = [
    "模拟期货",
    "模拟交易",
    "同花顺期货模拟",
]

# 控件映射（根据实际客户端版本调整）
# 股票下单面板
STOCK_ORDER_CONTROLS = {
    "code_edit": {"title": "代码编辑"},       # 证券代码输入框
    "price_edit": {"title": "价格"},          # 委托价格输入框
    "qty_edit": {"title": "数量"},            # 委托数量输入框
    "buy_button": {"title": "买入"},          # 买入按钮
    "sell_button": {"title": "卖出"},         # 卖出按钮
    "submit_button": {"title": "下单"},       # 确认下单按钮
}

# 期货下单面板
FUTURES_ORDER_CONTROLS = {
    "code_edit": {"title": "合约编辑"},       # 合约代码输入框
    "price_edit": {"title": "价格"},          # 委托价格输入框
    "qty_edit": {"title": "数量"},            # 委托手数输入框
    "buy_open_button": {"title": "买开"},     # 买开仓按钮
    "sell_open_button": {"title": "卖开"},    # 卖开仓按钮
    "buy_close_button": {"title": "买平"},    # 买平仓按钮
    "sell_close_button": {"title": "卖平"},   # 卖平仓按钮
    "submit_button": {"title": "下单"},       # 确认下单按钮
}

# 查询面板（按实际客户端 5.0 资金页校准）
QUERY_CONTROLS = {
    "account_tab": {"title": "资金余额"},              # 资金余额
    "position_tab": {"title": "持仓"},                 # 持仓标签页
    "order_tab": {"title": "委托"},                    # 委托标签页
    "refresh_button": {"title": "刷新"},               # 刷新按钮
    "available_amount_static": {"title": "可用金额"},  # 可用金额
    "total_asset_static": {"title": "总 资 产"},       # 总资产
    "market_value_static": {"title": "股票市值"},      # 股票市值
}

# 下单等待超时（秒）
ORDER_SUBMIT_TIMEOUT = 10
QUERY_REFRESH_TIMEOUT = 5

# 重试配置
MAX_RETRY = 3
RETRY_INTERVAL = 1.0  # 秒

# 安全开关
ENABLE_CLIENT_AUTOMATION = True  # 是否启用客户端自动化
SIMULATION_MODE_FALLBACK = True  # 客户端异常时是否回退到模拟盘
