"""
utils/strategy_lib — 候补高价值策略子算法沉淀库 (2026-08-10 高价值资产集成专项)

本目录收纳从资产地图 (cairn/high-value-code-assets.md) 抽取的、可独立复用的策略子算法。
设计原则:
- 纯函数 / 不可变接口 (输入不产生副作用, 返回新对象)
- 不 import 主链路 (institutional_pipeline_runner / daily_trade_executor 等), 避免耦合
- 单文件 200-400 行, 独立可测

当前模块:
- pairs_trading: 配对交易 (原 ms_strategy/src/alpha/pairs_trading.py, 全仓库零消费, 迁移至此沉淀)
"""

from .pairs_trading import (  # noqa: F401
    PairSignal,
    PairsTrading,
)

__all__ = ["PairSignal", "PairsTrading"]
