# -*- coding: utf-8 -*-
"""quant_modules.connectors — 数据源连接器注册

register_all_connectors 尝试注册所有可用数据源连接器到 DataConnectorManager:
  - Wind MCP (优先级 200)
  - 通达信 (优先级 300)
  - 新浪 (优先级 400)
  - 本地缓存 (优先级 500, 兜底)

注: iFinD 已于 2026-08-03 全局剔除, 不再注册。

环境无 MCP 时, 仅本地缓存可用, 返回注册数 0 或 1。
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

    # 1. Wind MCP (优先级 200)
    try:
        from utils.wind_data_provider import WindDataProvider
        provider = WindDataProvider()
        manager.register('Wind MCP', provider, priority=200)
        n_registered += 1
        logger.info('Wind MCP 连接器注册成功 (优先级 200)')
    except ImportError:
        logger.debug('Wind MCP 连接器不可用 (模块未安装)')
    except Exception as e:  # noqa: BLE001  # fail-safe
        logger.debug('Wind MCP 连接器注册失败: %s', e)

    # 2. 通达信 (优先级 300)
    try:
        from pytdx.hq import TdxHq_API  # type: ignore
        # pytdx 是通达信数据源, 注册为连接器
        manager.register('通达信', TdxHq_API(), priority=300)
        n_registered += 1
        logger.info('通达信连接器注册成功 (优先级 300)')
    except ImportError:
        logger.debug('通达信连接器不可用 (pytdx 未安装)')
    except Exception as e:  # noqa: BLE001  # fail-safe
        logger.debug('通达信连接器注册失败: %s', e)

    # 3. 新浪 (优先级 400)
    try:
        from utils.sina_data_provider import SinaDataProvider  # type: ignore
        manager.register('新浪', SinaDataProvider(), priority=400)
        n_registered += 1
        logger.info('新浪连接器注册成功 (优先级 400)')
    except ImportError:
        logger.debug('新浪连接器不可用 (模块未安装)')
    except Exception as e:  # noqa: BLE001  # fail-safe
        logger.debug('新浪连接器注册失败: %s', e)

    # 4. 本地缓存 (优先级 500, 兜底)
    try:
        from utils.local_cache_provider import LocalCacheProvider  # type: ignore
        manager.register('本地缓存', LocalCacheProvider(), priority=500)
        n_registered += 1
        logger.info('本地缓存连接器注册成功 (优先级 500)')
    except ImportError:
        logger.debug('本地缓存连接器不可用 (模块未安装)')
    except Exception as e:  # noqa: BLE001  # fail-safe
        logger.debug('本地缓存连接器注册失败: %s', e)

    return n_registered


__all__ = ['register_all_connectors']
