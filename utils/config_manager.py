# -*- coding: utf-8 -*-
"""
统一配置管理器 (ConfigManager)
================================

P1-Q8 (2026-07-26): 解决 5 个分散 YAML 配置目录的漂移问题.

历史问题:
    - configs/ (v7.7 旧版, 12KB)
    - v8.3_institutional/config/ (v8.4 唯一事实源, P0-1 修复)
    - ms_strategy/config/ (策略模块独立配置)
    - 各模块通过 `Path(__file__).resolve().parent.parent / "configs" / "portfolio.yaml"` 硬编码路径
    - kill_switch.py 仍在用旧版 configs/portfolio.yaml, 与生产唯一事实源漂移

解决方案:
    单一入口 + 优先级解析 + LRU+mtime 缓存:
        优先级 1: 环境变量 QUANT_CONFIG_DIR (运维快速覆盖, 测试场景)
        优先级 2: v8.3_institutional/config/ (生产唯一事实源)
        优先级 3: configs/ (历史回退, v7.7)
        优先级 4: ms_strategy/config/ (策略模块独立配置)

API:
    from utils.config_manager import get_config, get_kill_switch_config
    ks_cfg = get_kill_switch_config()  # 类型化访问器, 推荐
    any_cfg = get_config("portfolio")  # 通用加载, name 无扩展名

设计原则:
    1. Single Source of Truth: 全项目通过此模块读取 yaml 配置
    2. Fail-safe: 加载失败返回空 dict + 警告日志, 不抛异常阻断业务
    3. Hot Reload: mtime 变化自动失效缓存, 支持运行时配置更新
    4. Backward Compatible: 现有 yaml.safe_load 代码不强制迁移, 渐进式替换
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger("config_manager")

# 项目根目录 (utils/config_manager.py 的上两级)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 配置搜索路径优先级 (高 -> 低)
# 优先级 1: 环境变量 QUANT_CONFIG_DIR (运维/测试快速覆盖)
# 优先级 2: v8.3_institutional/config/ (生产唯一事实源, P0-1 修复后)
# 优先级 3: configs/ (历史 v7.7 回退, 兼容旧代码)
# 优先级 4: ms_strategy/config/ (策略模块独立配置)
_CONFIG_SEARCH_PATHS: List[Path] = []

# 已注册的命名配置 (短名 -> 文件名映射)
# 业务代码用 get_kill_switch_config() 等类型化访问器, 也可用 get_config("portfolio")
_NAMED_CONFIGS: Dict[str, str] = {
    "portfolio": "portfolio.yaml",
    "settings": "settings.yaml",
    "institutional": "institutional_config.yaml",
    "comprehensive": "comprehensive_config.yaml",
    "comprehensive_v7": "comprehensive_config_v7.yaml",
    "backtest": "backtest.yaml",
    "execution": "execution.yaml",
    "risk_budget": "risk_budget.yaml",
    "stop_loss": "stop_loss_vol_adjusted.yaml",
    "model_router": "model_router.yaml",
    "liquidation_scheduler": "liquidation_scheduler.yaml",
}


def _build_search_paths() -> List[Path]:
    """构建配置搜索路径列表 (按优先级)"""
    paths: List[Path] = []

    # 优先级 1: 环境变量覆盖
    env_dir = os.environ.get("QUANT_CONFIG_DIR", "").strip()
    if env_dir:
        env_path = Path(env_dir).expanduser().resolve()
        if env_path.is_dir():
            paths.append(env_path)
            logger.debug(f"[ConfigManager] 环境变量配置目录: {env_path}")

    # 优先级 2: v8.3_institutional/config/ (生产唯一事实源)
    v83_config = _PROJECT_ROOT / "v8.3_institutional" / "config"
    if v83_config.is_dir():
        paths.append(v83_config)

    # 优先级 3: configs/ (历史回退)
    legacy_config = _PROJECT_ROOT / "configs"
    if legacy_config.is_dir():
        paths.append(legacy_config)

    # 优先级 4: ms_strategy/config/ (策略模块独立配置)
    ms_config = _PROJECT_ROOT / "ms_strategy" / "config"
    if ms_config.is_dir():
        paths.append(ms_config)

    return paths


# 模块加载时初始化 (后续可通过 refresh_search_paths() 重置)
_CONFIG_SEARCH_PATHS = _build_search_paths()


class ConfigManager:
    """统一配置管理器 (单例模式)

    线程安全: 使用 RLock 保护缓存字典
    缓存策略: LRU + mtime 失效 (文件修改时自动重新加载)
    错误处理: fail-safe, 加载失败返回空 dict, 不抛异常阻断业务

    Usage:
        >>> from utils.config_manager import get_config, get_kill_switch_config
        >>> ks_cfg = get_kill_switch_config()
        >>> portfolio_cfg = get_config("portfolio")
    """

    _instance: Optional["ConfigManager"] = None
    _instance_lock = RLock()

    def __init__(self, project_root: Optional[Path] = None,
                 extra_search_paths: Optional[List[Path]] = None) -> None:
        """
        Args:
            project_root: 项目根目录, 默认为 utils/config_manager.py 上两级
            extra_search_paths: 额外的搜索路径 (优先级最高, 用于测试注入)
        """
        self._project_root = project_root or _PROJECT_ROOT
        self._cache: Dict[str, Tuple[Dict, float, Path]] = {}  # name -> (config, mtime, source_path)
        self._lock = RLock()

        # 构建搜索路径 (额外路径优先于默认路径)
        self._search_paths: List[Path] = []
        if extra_search_paths:
            self._search_paths.extend(extra_search_paths)
        self._search_paths.extend(self._build_default_search_paths())

    def _build_default_search_paths(self) -> List[Path]:
        """构建默认搜索路径 (实例级, 允许 project_root 覆盖)"""
        paths: List[Path] = []

        # 优先级 1: 环境变量覆盖
        env_dir = os.environ.get("QUANT_CONFIG_DIR", "").strip()
        if env_dir:
            env_path = Path(env_dir).expanduser().resolve()
            if env_path.is_dir():
                paths.append(env_path)

        # 优先级 2: v8.3_institutional/config/
        v83_config = self._project_root / "v8.3_institutional" / "config"
        if v83_config.is_dir():
            paths.append(v83_config)

        # 优先级 3: configs/
        legacy_config = self._project_root / "configs"
        if legacy_config.is_dir():
            paths.append(legacy_config)

        # 优先级 4: ms_strategy/config/
        ms_config = self._project_root / "ms_strategy" / "config"
        if ms_config.is_dir():
            paths.append(ms_config)

        return paths

    @classmethod
    def get_instance(cls) -> "ConfigManager":
        """获取全局单例 (双重检查锁定)"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例 (测试时使用, 清空所有缓存)"""
        with cls._instance_lock:
            cls._instance = None

    # ============================================================
    # 核心方法: 通用配置加载
    # ============================================================
    def _resolve_config_path(self, name: str) -> Optional[Path]:
        """按优先级解析配置文件路径

        Args:
            name: 配置短名 (如 "portfolio") 或完整文件名 (如 "portfolio.yaml")

        Returns:
            首个匹配的配置文件路径, 未找到返回 None
        """
        # 标准化文件名
        if not name.endswith((".yaml", ".yml")):
            filename = _NAMED_CONFIGS.get(name, f"{name}.yaml")
        else:
            filename = name

        for search_path in self._search_paths:
            candidate = search_path / filename
            if candidate.is_file():
                return candidate

        return None

    def _load_yaml(self, path: Path) -> Dict:
        """加载 YAML 文件 (fail-safe)"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data is None:
                return {}
            if not isinstance(data, dict):
                logger.warning(
                    f"[ConfigManager] 配置文件非 dict 类型: {path}, 实际类型={type(data).__name__}"
                )
                return {}
            return data
        except yaml.YAMLError as e:
            logger.error(f"[ConfigManager] YAML 解析失败: {path}, error={e}")
            return {}
        except Exception as e:
            logger.error(f"[ConfigManager] 加载配置失败: {path}, error={e}", exc_info=True)
            return {}

    def _get_cached(self, name: str) -> Optional[Dict]:
        """获取缓存中的配置 (检查 mtime 失效)

        Returns:
            缓存的配置 dict, 若缓存不存在或已失效返回 None
        """
        if name not in self._cache:
            return None

        config, cached_mtime, source_path = self._cache[name]
        try:
            current_mtime = source_path.stat().st_mtime
            if current_mtime == cached_mtime:
                return config
            # mtime 变化, 缓存失效
            logger.debug(f"[ConfigManager] 缓存失效 (mtime 变化): {name} -> {source_path}")
            del self._cache[name]
            return None
        except OSError:
            # 文件已被删除, 清除缓存
            logger.warning(f"[ConfigManager] 配置文件已删除: {source_path}")
            del self._cache[name]
            return None

    def get(self, name: str, default: Optional[Dict] = None) -> Dict:
        """通用配置加载 (带缓存)

        Args:
            name: 配置短名 (如 "portfolio") 或完整文件名 (如 "portfolio.yaml")
            default: 加载失败时返回的默认值, 默认为空 dict

        Returns:
            配置字典, 加载失败返回 default 或空 dict

        Usage:
            >>> cfg = manager.get("portfolio")
            >>> cfg = manager.get("unknown_config", default={"fallback": True})
        """
        with self._lock:
            # 1. 检查缓存
            cached = self._get_cached(name)
            if cached is not None:
                return cached

            # 2. 解析路径
            path = self._resolve_config_path(name)
            if path is None:
                logger.warning(
                    f"[ConfigManager] 配置未找到: name={name}, "
                    f"搜索路径={[str(p) for p in self._search_paths]}"
                )
                return default if default is not None else {}

            # 3. 加载并缓存
            config = self._load_yaml(path)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            self._cache[name] = (config, mtime, path)
            logger.debug(f"[ConfigManager] 加载配置: name={name}, source={path}")
            return config

    # ============================================================
    # 类型化访问器 (推荐使用, 自文档化)
    # ============================================================
    def get_kill_switch_config(self) -> Dict:
        """获取 kill_switch 配置 (从 portfolio.yaml 的 kill_switch 节读取)

        优先级: portfolio.yaml 的 kill_switch 节 > 独立 kill_switch.yaml

        Returns:
            kill_switch 配置字典, 包含 L1/L2/L3 阈值、total_margin 等
        """
        # 优先从 portfolio.yaml 的 kill_switch 节读取
        portfolio_cfg = self.get("portfolio")
        ks_cfg = portfolio_cfg.get("kill_switch", {})
        if ks_cfg:
            return ks_cfg

        # 回退: 独立 kill_switch.yaml (若存在)
        return self.get("kill_switch", default={})

    def get_portfolio_config(self) -> Dict:
        """获取投资组合配置 (account_structure, assets, hedge_capital 等)"""
        return self.get("portfolio")

    def get_settings_config(self) -> Dict:
        """获取全局设置 (日志、数据源、运行时参数)"""
        return self.get("settings")

    def get_institutional_config(self) -> Dict:
        """获取机构交易策略配置"""
        return self.get("institutional")

    def get_comprehensive_config(self) -> Dict:
        """获取综合配置 (多模块聚合)"""
        return self.get("comprehensive")

    def get_execution_config(self) -> Dict:
        """获取交易执行配置 (订单路由、滑点、佣金)"""
        return self.get("execution")

    def get_backtest_config(self) -> Dict:
        """获取回测配置 (起止日期、初始资金、基准)"""
        return self.get("backtest")

    def get_risk_budget_config(self) -> Dict:
        """获取风险预算配置 (vol target, drawdown threshold)"""
        return self.get("risk_budget")

    def get_stop_loss_config(self) -> Dict:
        """获取动态止损配置 (波动率调整止损)"""
        return self.get("stop_loss")

    # ============================================================
    # 审计与运维方法
    # ============================================================
    def list_available(self) -> List[Dict[str, Any]]:
        """列出所有可用配置 (含来源路径, 用于审计)

        Returns:
            [{"name": "portfolio", "path": "...", "size": 9931}, ...]
        """
        result: List[Dict[str, Any]] = []
        seen: set = set()

        for short_name, filename in _NAMED_CONFIGS.items():
            path = self._resolve_config_path(short_name)
            if path and path not in seen:
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                result.append({
                    "name": short_name,
                    "filename": filename,
                    "path": str(path),
                    "size": size,
                })
                seen.add(path)

        return result

    def get_config_source(self, name: str) -> Optional[str]:
        """获取配置实际加载的源路径 (用于审计配置漂移)

        Args:
            name: 配置短名

        Returns:
            配置文件路径字符串, 未找到返回 None
        """
        path = self._resolve_config_path(name)
        return str(path) if path else None

    def clear_cache(self) -> None:
        """清空缓存 (测试时使用, 或强制重新加载)"""
        with self._lock:
            self._cache.clear()
            logger.debug("[ConfigManager] 缓存已清空")

    def reload(self, name: str) -> Dict:
        """强制重新加载指定配置 (跳过缓存)

        Args:
            name: 配置短名

        Returns:
            重新加载的配置字典
        """
        with self._lock:
            if name in self._cache:
                del self._cache[name]
            return self.get(name)


# ============================================================
# 模块级快捷函数 (推荐业务代码使用的入口)
# ============================================================
def get_config(name: str, default: Optional[Dict] = None) -> Dict:
    """加载 YAML 配置 (统一入口)

    优先级: QUANT_CONFIG_DIR 环境变量 > v8.3_institutional/config/ > configs/ > ms_strategy/config/

    Args:
        name: 配置短名 (如 "portfolio") 或完整文件名 (如 "portfolio.yaml")
        default: 加载失败时的默认值

    Returns:
        配置字典

    Usage:
        >>> from utils.config_manager import get_config
        >>> cfg = get_config("portfolio")
        >>> ks_cfg = get_config("portfolio").get("kill_switch", {})
    """
    return ConfigManager.get_instance().get(name, default)


def get_kill_switch_config() -> Dict:
    """获取 kill_switch 配置 (类型化访问器, 推荐)

    替代 kill_switch.py 中 `yaml.safe_load(configs/portfolio.yaml)["kill_switch"]` 模式
    自动从 v8.3_institutional/config/portfolio.yaml (唯一事实源) 读取
    """
    return ConfigManager.get_instance().get_kill_switch_config()


def get_portfolio_config() -> Dict:
    """获取投资组合配置 (类型化访问器, 推荐)"""
    return ConfigManager.get_instance().get_portfolio_config()


def get_settings_config() -> Dict:
    """获取全局设置 (类型化访问器, 推荐)"""
    return ConfigManager.get_instance().get_settings_config()


def get_execution_config() -> Dict:
    """获取交易执行配置 (类型化访问器, 推荐)"""
    return ConfigManager.get_instance().get_execution_config()


def get_backtest_config() -> Dict:
    """获取回测配置 (类型化访问器, 推荐)"""
    return ConfigManager.get_instance().get_backtest_config()


def get_risk_budget_config() -> Dict:
    """获取风险预算配置 (类型化访问器, 推荐)"""
    return ConfigManager.get_instance().get_risk_budget_config()


def get_stop_loss_config() -> Dict:
    """获取动态止损配置 (类型化访问器, 推荐)"""
    return ConfigManager.get_instance().get_stop_loss_config()


def list_available_configs() -> List[Dict[str, Any]]:
    """列出所有可用配置 (审计用)"""
    return ConfigManager.get_instance().list_available()


def clear_config_cache() -> None:
    """清空配置缓存 (测试时使用)"""
    ConfigManager.get_instance().clear_cache()


def get_config_source(name: str) -> Optional[str]:
    """获取配置实际加载的源路径 (审计配置漂移)"""
    return ConfigManager.get_instance().get_config_source(name)
