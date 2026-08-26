"""Wind 数据供应器适配层 (v8.6.14 兼容桥)

原 utils/wind_data_provider.py 不存在 (AGENTS.md 文档偏差),
v8.6.14 统一指向 utils/data_provider.py 的 MarketDataProvider
(四源优先链: Wind MCP -> 通达信 -> AKShare -> 新浪 HTTP)。

本模块提供向后兼容的 get_wind_provider() / WindDataProvider 接口,
内部委托给 MarketDataProvider, 供 glm5_decision_engine / cli 等调用方使用。

参考: quant_modules/connectors.py v8.6.14 修复注记。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_provider_instance: Optional["WindDataProvider"] = None


class WindDataProvider:
    """Wind 数据供应器 -- MarketDataProvider 的兼容包装。

    提供 _wind_available 属性和 build_market_data() 方法,
    供 GLM5DecisionEngine 等 v5.8 架构调用方使用。
    """

    def __init__(self) -> None:
        from utils.data_provider import MarketDataProvider

        self._provider = MarketDataProvider()
        self._wind_available = self._provider.source_health.get(
            "wind_mcp", {}
        ).get("ok", False)

    def build_market_data(
        self,
        positions: dict[str, Any],
        include_fundamentals: bool = False,
    ) -> dict[str, Any]:
        """从持仓代码构建市场数据。

        Args:
            positions: {code: holding_dict} 持仓映射, holding_dict 含 '名称' 等
            include_fundamentals: 是否包含基本面 (当前忽略, 仅行情)

        Returns:
            {'指数行情': {name: {'收盘': price, '涨跌幅': pct}}, ...}
        """
        result: dict[str, Any] = {"指数行情": {}}
        for code, info in positions.items():
            try:
                quote = self._provider.get_market_data(code)
                price = quote.get("index_price") or quote.get("price") or 0.0
                prev_close = quote.get("prev_close", 0.0)
                if price > 0:
                    name = info.get("名称", code) if isinstance(info, dict) else code
                    change_pct = (
                        (price - prev_close) / prev_close * 100
                        if prev_close > 0
                        else 0.0
                    )
                    result["指数行情"][name] = {
                        "收盘": price,
                        "涨跌幅": round(change_pct, 2),
                    }
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                RuntimeError,
            ) as e:
                logger.debug("WindDataProvider: 获取 %s 行情失败: %s", code, e)
        return result


def get_wind_provider() -> WindDataProvider:
    """获取 Wind 数据供应器单例 (延迟初始化)。"""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = WindDataProvider()
    return _provider_instance