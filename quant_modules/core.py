# -*- coding: utf-8 -*-
"""quant_modules.core — 量化系统核心基础设施

提供 8 个核心类/函数:
  - ConfigError / DataSourceError: 异常类
  - ConfigManager: 配置管理 (re-export utils.config_manager)
  - GracefulFallback: 降级管理器
  - ModuleLoader: 懒加载器 (优雅降级)
  - ProgressIndicator: 进度指示器
  - StrategyRegistry: 策略注册表
  - load_portfolio_config: 加载 portfolio.yaml

设计原则:
  1. 优雅降级 — 模块加载失败返回空 dict, 不阻断主流程
  2. 单一配置入口 — ConfigManager re-export utils.config_manager 的 4 级优先级实现
  3. 轻量 — ProgressIndicator/StrategyRegistry 等为独立实现, 不依赖重型框架
"""
from __future__ import annotations

import importlib
import logging
import os
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 异常类
# ============================================================

class ConfigError(Exception):
    """配置错误 — 配置文件缺失/格式错误/加载失败时抛出。"""


class DataSourceError(Exception):
    """数据源错误 — 数据源连接失败/数据获取异常时抛出。"""


# ============================================================
# ConfigManager — re-export utils.config_manager 的实现
# ============================================================
# utils.config_manager.py 已实现完整的 4 级优先级 ConfigManager:
#   优先级 1: QUANT_CONFIG_DIR 环境变量
#   优先级 2: v8.3_institutional/config/ (生产唯一事实源)
#   优先级 3: configs/ (v7.7 回退)
#   优先级 4: ms_strategy/config/ (策略特定)
try:
    from utils.config_manager import ConfigManager  # noqa: F401
    from utils.config_manager import get_config as _get_config
    from utils.config_manager import get_portfolio_config as _get_portfolio_config
except ImportError:
    # 兜底: utils.config_manager 不可用时提供占位实现
    logger.warning('utils.config_manager 加载失败, 使用占位 ConfigManager')

    class ConfigManager:  # type: ignore[no-redef]
        """占位 ConfigManager — utils.config_manager 不可用时的兜底。"""

        def get(self, name: str, default: Any = None) -> Any:
            try:
                path = os.path.join('configs', f'{name}.yaml')
                if os.path.isfile(path):
                    import yaml
                    with open(path, encoding='utf-8') as f:
                        return yaml.safe_load(f)
            except OSError:
                pass
            return default

    def _get_config(name: str) -> dict:
        return {}

    def _get_portfolio_config() -> dict:
        return {}


def load_portfolio_config() -> dict:
    """加载 portfolio.yaml 配置, 返回 dict (失败返回空 dict)。"""
    try:
        cfg = _get_portfolio_config()
        if cfg:
            return cfg
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning('load_portfolio_config 失败: %s', e)
    return {}


# ============================================================
# GracefulFallback — 降级管理器
# ============================================================

class GracefulFallback:
    """优雅降级管理器 — 注册异常类型对应的降级处理函数。

    用法:
        gf = GracefulFallback()
        gf.register_fallback(DataSourceError, lambda e: {})
        gf.register_fallback(ConfigError, lambda e: {})
        # 在 try/except 中调用 handle(exc) 获取降级值
    """

    def __init__(self) -> None:
        self._fallbacks: dict[type, Callable[[Exception], Any]] = {}

    def register_fallback(self, exc_type: type, handler: Callable[[Exception], Any]) -> None:
        """注册异常类型对应的降级处理函数。"""
        self._fallbacks[exc_type] = handler
        logger.debug('注册降级: %s -> %s', exc_type.__name__, handler.__name__ or 'lambda')

    def handle(self, exc: Exception) -> Any:
        """根据异常类型调用对应的降级处理函数, 无匹配则重新抛出。"""
        for exc_type, handler in self._fallbacks.items():
            if isinstance(exc, exc_type):
                logger.warning('降级处理: %s -> %s', exc_type.__name__, str(exc)[:100])
                return handler(exc)
        raise exc


# ============================================================
# ModuleLoader — 懒加载器 (优雅降级)
# ============================================================

class ModuleLoader:
    """模块懒加载器 — 按需加载模块并提取指定方法, 失败返回空 dict。

    用法:
        loader = ModuleLoader()
        data_provider = loader.load('wind_data_provider', {
            'get_quotes_batch': 'get_quotes_batch',
            'get_quote': 'get_quote',
        })
        # data_provider 是 dict-like, data_provider.get('get_quotes_batch') 返回函数或 None
    """

    def load(self, module_name: str, method_map: dict[str, str]) -> dict[str, Any]:
        """加载模块并提取方法, 返回 {alias: callable} 字典。

        Args:
            module_name: 模块名 (如 'wind_data_provider')
            method_map: {别名: 模块内函数名} 映射

        Returns:
            {别名: callable} 字典, 加载失败返回空 dict
        """
        result: dict[str, Any] = {}
        try:
            # 尝试从 utils 包加载
            module = importlib.import_module(f'utils.{module_name}')
            for alias, attr_name in method_map.items():
                attr = getattr(module, attr_name, None)
                if attr is not None:
                    result[alias] = attr
            logger.debug('ModuleLoader 加载 %s: %d/%d 方法', module_name, len(result), len(method_map))
        except ImportError as e:
            logger.debug('ModuleLoader 模块 %s 不可用: %s', module_name, e)
        except (ImportError, AttributeError) as e:
            logger.warning('ModuleLoader 加载 %s 异常: %s', module_name, e)
        return result


# ============================================================
# ProgressIndicator — 进度指示器
# ============================================================

class ProgressIndicator:
    """进度指示器 — 在控制台显示多步骤任务进度。

    用法:
        progress = ProgressIndicator("初始化系统", 6)
        progress.update(1, "扫描ML预测信号...")
        progress.update(2, "加载交易系统...")
        progress.complete("监控结束")
    """

    def __init__(self, title: str, total_steps: int) -> None:
        self.title = title
        self.total_steps = total_steps
        self.current_step = 0
        self._start_time = time.perf_counter()
        logger.info(f'\n▶ {title} (共 {total_steps} 步)')

    def update(self, step: int, message: str) -> None:
        """更新进度到指定步骤。"""
        self.current_step = step
        pct = step / self.total_steps * 100 if self.total_steps > 0 else 0
        logger.info(f'  [{step}/{self.total_steps}] ({pct:.0f}%) {message}')

    def complete(self, message: str = '完成') -> None:
        """标记任务完成。"""
        elapsed = time.perf_counter() - self._start_time
        logger.info(f'  ✅ {message} (耗时 {elapsed:.1f}s)')


# ============================================================
# StrategyRegistry — 策略注册表
# ============================================================

class StrategyRegistry:
    """策略注册表 — 管理交易策略和研究假设。

    用法:
        registry = StrategyRegistry()
        registry.register_hypothesis('rebalance_2026', {
            'title': '2026年组合再平衡',
            'description': '基于当前持仓的再平衡计划',
            'hypothesis': '核心-卫星策略配置能带来超额收益',
            'status': 'active'
        })
        hyps = registry.list_hypotheses()
    """

    def __init__(self) -> None:
        self._hypotheses: dict[str, dict[str, Any]] = {}
        self._strategies: dict[str, Any] = {}

    def register_hypothesis(self, hyp_id: str, data: dict[str, Any]) -> None:
        """注册或更新研究假设。"""
        self._hypotheses[hyp_id] = data
        logger.info('已注册研究假设: %s', hyp_id)

    def get_hypothesis(self, hyp_id: str) -> Optional[dict[str, Any]]:
        """获取假设数据, 不存在返回 None。"""
        return self._hypotheses.get(hyp_id)

    def list_hypotheses(self) -> list[str]:
        """列出所有假设 ID。"""
        return list(self._hypotheses.keys())

    def register_strategy(self, name: str, strategy: Any) -> None:
        """注册策略实例。"""
        self._strategies[name] = strategy
        logger.info('已注册策略: %s', name)

    def get_strategy(self, name: str) -> Optional[Any]:
        """获取策略实例, 不存在返回 None。"""
        return self._strategies.get(name)


__all__ = [
    'ConfigError',
    'ConfigManager',
    'DataSourceError',
    'GracefulFallback',
    'ModuleLoader',
    'ProgressIndicator',
    'StrategyRegistry',
    'load_portfolio_config',
]