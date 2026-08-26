"""成交回报驱动 PnL 桥接层 (G4 缺口补齐)

`reporting.pnl_calculator.calculate_pnl` 当前用 `market_prices[code]['close']`
做 mark-to-market 估算, 不消费真实成交价。本模块提供 fail-open 桥接:

  - augment_market_prices(market_prices, date): 若当日有 fills 落盘, 用真实
    成交均价覆盖对应标的的 `close`, 使 PnL 反映实际成交而非行情快照估值。
  - realized_pnl(date): 额外汇总当日已实现 PnL, 供报告附加展示。

设计原则 (来自工业级架构铁律 + 08-07 防复发):
  - fail-open: 文件缺失 / 为空 / 解析失败 → 原样返回行情价, 绝不阻断 EOD。
  - 不修改 pnl_calculator 内部契约, 只在调用前增强传入的 market_prices。
  - 仅覆盖当日有成交的标的, 无成交标的仍走行情估算, 避免数据断链。
"""

import logging
from typing import Collection, Optional

logger = logging.getLogger(__name__)

try:
    from utils.execution.fills_store import FillsStore
except ImportError:  # 兼容不同工作目录调用
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from utils.execution.fills_store import FillsStore


def augment_market_prices(
    market_prices: dict[str, dict],
    date: Optional[str] = None,
    strategies: Optional[Collection[str]] = None,
) -> dict[str, dict]:
    """用当日真实成交均价覆盖 market_prices 中的 close。

    Args:
        market_prices: fetch_market_prices 返回的价格字典 (key 可能为带/不带后缀 code)
        date: 交易日, None 取今天
        strategies: P3.0 门禁新增 — 只用指定策略的 fills 覆盖 (None=全部, 兼容)

    Returns:
        增强后的价格字典 (原字典被复制, 不原地修改 — 遵循不可变性原则)。
        若无 fills 或无覆盖, 返回原字典的浅拷贝 (结构不变)。
    """
    try:
        store = FillsStore()
        latest = store.latest_avg_price_by_symbol(date, strategies=strategies)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning("[FillsPnLBridge] 读取 fills 失败, 回退行情估算: %s", e)
        return dict(market_prices)

    if not latest:
        return dict(market_prices)

    # 复制外层, 仅对命中标的新建内层 dict (不可变性)
    augmented = dict(market_prices)
    covered = 0
    for code, price in latest.items():
        if code in augmented:
            inner = dict(augmented[code])
            inner["close"] = price
            inner["close_source"] = "fill"  # 标记来源, 便于报告区分
            augmented[code] = inner
            covered += 1
        else:
            # 带后缀 code 兼容: 去掉后缀再试
            code_num = code.split(".")[0]
            if code_num in augmented:
                inner = dict(augmented[code_num])
                inner["close"] = price
                inner["close_source"] = "fill"
                augmented[code_num] = inner
                covered += 1

    if covered:
        logger.info("[FillsPnLBridge] 用 %d 笔真实成交价覆盖行情估算 close", covered)
    return augmented


def realized_pnl(
    date: Optional[str] = None,
    strategies: Optional[Collection[str]] = None,
) -> dict[str, float]:
    """返回当日已实现 PnL 汇总 (fail-open)。"""
    try:
        return FillsStore().realized_pnl(date, strategies=strategies)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning("[FillsPnLBridge] 计算已实现 PnL 失败: %s", e)
        return {}


if __name__ == "__main__":
    mp = {"600519": {"close": 1700.0, "prev_close": 1680.0}}
    print("before:", mp)
    print("after :", augment_market_prices(mp))
