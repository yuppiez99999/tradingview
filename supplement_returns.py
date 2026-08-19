"""
历史收益率数据补充器
用于 v7.5 对冲引擎的真实 Beta / 相关性计算
"""

import json
import os
import sys
from pathlib import Path

import pandas as pd

# S3修复: 用 PROJECT_ROOT 替代硬编码绝对路径
PROJECT_ROOT = Path(__file__).resolve().parent
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

from utils.data_provider import MarketDataProvider

# 读取持仓
DATA_DIR = PROJECT_ROOT / "config"
positions_path = DATA_DIR / "positions.json"
with open(positions_path, encoding="utf-8") as f:
    positions_data = json.load(f)["positions"]

symbols = []
symbol_map = {}
for _key, item in positions_data.items():
    code = item.get("code")
    if code:
        symbols.append(code)
        symbol_map[code] = item.get("name", code)


# 获取历史数据
provider = MarketDataProvider()
period = "1y"  # 1年历史数据，约252个交易日

returns_data = {}
market_symbol = "510300"  # 用沪深300ETF作为市场基准


success_count = 0
fail_count = 0

for symbol in symbols:
    try:
        df = provider.get_historical_data(symbol, period)
        if df is not None and not df.empty and "close" in df.columns:
            # 计算日收益率
            df["return"] = df["close"].pct_change()
            returns_data[symbol] = df["return"].dropna()
            success_count += 1
        else:
            fail_count += 1
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        fail_count += 1


# 获取市场基准收益率
market_df = provider.get_historical_data(market_symbol, period)
market_returns = None
if market_df is not None and not market_df.empty and "close" in market_df.columns:
    market_returns = market_df["close"].pct_change().dropna()
else:
    pass


# 转换为DataFrame格式（用于对冲引擎）
if returns_data:
    returns_df = pd.DataFrame(returns_data)

    # 保存到文件
    output_dir = str(DATA_DIR)
    os.makedirs(output_dir, exist_ok=True)

    returns_path = os.path.join(output_dir, "returns_history.json")
    returns_df.to_json(returns_path, orient="split", date_format="iso")

    # 保存市场收益率
    if market_returns is not None:
        market_path = os.path.join(output_dir, "market_returns.json")
        market_returns.to_json(market_path, orient="split", date_format="iso")

    # 计算并显示一些统计信息

    mean_returns = returns_df.mean() * 252
    std_returns = returns_df.std() * (252**0.5)

    for col in returns_df.columns:
        ret = mean_returns.get(col, 0)
        vol = std_returns.get(col, 0)
        ret = 0.0 if pd.isna(ret) else float(ret)
        vol = 0.0 if pd.isna(vol) else float(vol)
        sharpe = ret / vol if vol > 1e-6 else 0.0

else:
    pass
