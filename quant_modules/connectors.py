"""quant_modules.connectors — 数据源连接器注册

register_all_connectors 尝试注册所有可用数据源连接器到 DataConnectorManager:
  - Wind MCP (优先级 200)   → utils.data_provider.MarketDataProvider (统一数据层, P1 Wind MCP 优先链)
  - 通达信 (优先级 300)     → utils.tdx_data_source.get_tdx_source
  - AKShare (优先级 400)    → utils.akshare_data_source.get_akshare_source
  - 本地缓存 (优先级 500, 兜底) → 由 MarketDataProvider 内部 P5 本地缓存兜底 (无独立 provider)

注:
  - iFinD 已于 2026-08-03 全局剔除, 不再注册。
  - v8.6.14 (2026-08-25) 修复悬空导入: 原 utils.wind_data_provider /
    utils.sina_data_provider / utils.local_cache_provider 均不存在 (AGENTS.md 文档
    偏差), 现全部指向实际生效的 utils/data_provider.py / tdx_data_source.py /
    akshare_data_source.py。sina 实时/历史兜底已并入 MarketDataProvider (P4)。

实际生效的数据层为 utils/data_provider.py 的 MarketDataProvider (四源优先链:
Wind MCP→通达信→AKShare→新浪 HTTP)。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quant_modules.data_layer import DataConnectorManager

logger = logging.getLogger(__name__)


def register_all_connectors(manager: 'DataConnectorManager') -> int:
    """注册所有可用数据源连接器到 manager, 返回成功注册数量。

    Args:
        manager: DataConnectorManager 实例

    Returns:
        成功注册的连接器数量 (0 表示无可用数据源, 系统走离线模式)
    """
    n_registered = 0

    # 1. Wind MCP (优先级 200) — 统一数据层, 内部 P1 优先链 + P5 本地缓存兜底
    try:
        from utils.data_provider import MarketDataProvider
        provider = MarketDataProvider(backtest_mode=False)
        manager.register('Wind MCP', provider, priority=200)
        n_registered += 1
        logger.info('Wind MCP 连接器注册成功 (优先级 200, MarketDataProvider)')
    except ImportError:
        logger.debug('Wind MCP 连接器不可用 (模块未安装)')
    except (AttributeError, OSError, RuntimeError) as e:
        logger.debug('Wind MCP 连接器注册失败: %s', e)

    # 2. 通达信 (优先级 300)
    try:
        from utils.tdx_data_source import get_tdx_source
        provider = get_tdx_source()
        if provider is not None:
            manager.register('通达信', provider, priority=300)
            n_registered += 1
            logger.info('通达信连接器注册成功 (优先级 300)')
    except ImportError:
        logger.debug('通达信连接器不可用 (pytdx 未安装)')
    except (AttributeError, OSError, RuntimeError) as e:
        logger.debug('通达信连接器注册失败: %s', e)

    # 3. AKShare (优先级 400)
    try:
        from utils.akshare_data_source import get_akshare_source
        provider = get_akshare_source()
        manager.register('AKShare', provider, priority=400)
        n_registered += 1
        logger.info('AKShare 连接器注册成功 (优先级 400)')
    except ImportError:
        logger.debug('AKShare 连接器不可用 (akshare 未安装)')
    except (AttributeError, OSError, RuntimeError) as e:
        logger.debug('AKShare 连接器注册失败: %s', e)

    return n_registered


__all__ = ['register_all_connectors']
