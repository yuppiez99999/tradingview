"""全局初始化入口 — 三层保护 Layer 3.

模块整合 8.4 — ARCHITECTURE §1.4 / ADR-002
任务: T1.5

设计目标:
    1. 统一入口: 一次调用完成 ConfigManager + Logger + TradingEnv + KillSwitch 初始化
    2. 幂等: 多次调用不重复初始化, 返回同一 BootstrapResult
    3. 异常处理: 任何子步骤失败抛出 BootstrapError(step, reason, cause)
    4. 不破坏 V9 基线: USE_INTEGRATED_BOOTSTRAP flag 默认 False (ADR-003 铁律)

初始化步骤 (按依赖顺序):
    1. 加载 .env (dotenv 优先, 回退手动解析)
    2. 初始化根日志器 (复用 utils.logger._init_root_logging)
    3. 预热 ConfigManager 单例 (触发 4 级优先级搜索路径构建)
    4. 预热 TradingEnv 配置 (读 .env + 环境变量)
    5. 初始化 KillSwitch (不传 config_path, 走 ConfigManager 路径)
    6. 注册 broker_callback (可选, 由调用方传入)
    7. 检查 USE_INTEGRATED_BOOTSTRAP flag (仅日志, 不阻塞)

API:
    from utils.infra.bootstrap import initialize, BootstrapResult, BootstrapError

    result = initialize(broker_callback=my_callback, log_prefix="quant_strategy_system")
    ks = result.kill_switch
    cfg = result.config_manager

硬约束:
    - HC-1: 不破坏 V9 基线 (默认 False, flag 关闭时旧路径仍可用)
    - HC-5: ConfigManager 4 级优先级解析不可绕过
    - HC-2: KillSwitch.check_kill_switch() 同步路径延迟 <1ms
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


def _find_project_root() -> Path:
    """向上查找项目根目录 (通过已知 marker 文件/目录识别).

    比硬编码 parent.parent.parent 更健壮, 文件移动不会失效.
    """
    project_markers = [
        "config",
        "utils",
        "v8.3_institutional",
        "research",
        "tests",
        "requirements.txt",
        "ruff.toml",
        "pytest.ini",
    ]
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if any((current / m).exists() for m in project_markers):
            return current
        if current.parent == current:
            break
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


# 项目根目录
_PROJECT_ROOT = _find_project_root()

# 幂等控制
_initialized: bool = False
_last_result: BootstrapResult | None = None
_lock: RLock = RLock()


# ============================================================
# 异常与结果类型
# ============================================================
class BootstrapError(Exception):
    """全局初始化失败.

    Attributes:
        step: 失败的步骤名 (如 "load_env" / "init_logger" / "init_config_manager")
        reason: 失败原因描述
        cause: 原始异常 (可选)
    """

    def __init__(self, step: str, reason: str, cause: Exception | None = None) -> None:
        self.step = step
        self.reason = reason
        self.cause = cause
        super().__init__(
            f"Bootstrap 失败 [step={step}]: {reason}"
            + (f" (cause: {cause})" if cause else "")
        )


@dataclass
class BootstrapResult:
    """全局初始化结果 (持有所有单例引用)."""

    config_manager: Any  # ConfigManager 实例 (避免循环导入用 Any)
    env_config: Any  # TradingEnvConfig dataclass
    kill_switch: Any  # KillSwitch 实例
    feature_flags: Any  # FeatureFlags 单例
    initialized: bool = True

    def __bool__(self) -> bool:
        """`if result:` 直接判断是否初始化完成."""
        return self.initialized


# ============================================================
# 步骤 1: 加载 .env
# ============================================================
def _load_env_file() -> dict[str, str]:
    """加载 .env 文件到 os.environ (不覆盖已存在的环境变量).

    Returns:
        加载的键值对字典 (空字典表示无 .env 或加载失败)

    Raises:
        BootstrapError: .env 文件存在但解析失败
    """
    loaded: dict[str, str] = {}

    # 优先用 python-dotenv (如果安装)
    try:
        from dotenv import load_dotenv

        # 查找 .env: 当前目录 > 项目根
        for env_path in [Path.cwd() / ".env", _PROJECT_ROOT / ".env"]:
            if env_path.is_file():
                # 记录加载前的环境变量快照，用于计算 diff
                before_keys = set(os.environ.keys())
                load_dotenv(env_path, override=False)
                after_keys = set(os.environ.keys())
                # 返回实际新增的键值对
                new_keys = after_keys - before_keys
                for k in new_keys:
                    loaded[k] = os.environ[k]
                break
        return loaded
    except ImportError:
        pass  # dotenv 未安装, 走手动解析
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        SyntaxError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # OSError: .env/配置文件读取失败 (权限/编码/磁盘)
        # ValueError/TypeError/KeyError: 解析/格式/字段错误
        # AttributeError: 属性缺失
        # RuntimeError/SyntaxError: 运行时/语法错误
        raise BootstrapError(
            step="load_env",
            reason=f"python-dotenv 加载失败: {e}",
            cause=e,
        ) from e

    # 回退: 手动解析 .env (复制自 v8.3_institutional/main.py L18-39)
    try:
        env_path_fallback: Path | None = None
        for candidate in [Path.cwd() / ".env", _PROJECT_ROOT / ".env"]:
            if candidate.is_file():
                env_path_fallback = candidate
                break

        if env_path_fallback is None:
            return loaded  # 无 .env 文件, 静默返回

        # 多编码支持 (与 utils/trading_env.py 一致)
        content: str | None = None
        for encoding in ["utf-8", "gbk", "utf-8-sig", "latin-1"]:
            try:
                content = env_path_fallback.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            raise BootstrapError(
                step="load_env",
                reason=f".env 文件编码无法解析 (尝试 utf-8/gbk/utf-8-sig/latin-1 均失败): {env_path_fallback}",
            )

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 不覆盖已存在的环境变量
            if key and key not in os.environ:
                os.environ[key] = value
                loaded[key] = value

        return loaded
    except BootstrapError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        SyntaxError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # OSError: .env/配置文件读取失败 (权限/编码/磁盘)
        # ValueError/TypeError/KeyError: 解析/格式/字段错误
        # AttributeError: 属性缺失
        # RuntimeError/SyntaxError: 运行时/语法错误
        raise BootstrapError(
            step="load_env",
            reason=f".env 手动解析失败: {e}",
            cause=e,
        ) from e


# ============================================================
# 步骤 2: 初始化根日志器
# ============================================================
def _init_logger(log_prefix: str, log_dir: str, console_level: int) -> None:
    """初始化根日志器 (复用 utils.logger._init_root_logging).

    Raises:
        BootstrapError: 日志初始化失败
    """
    try:
        from utils.logger import _init_root_logging

        _init_root_logging(
            log_prefix=log_prefix, log_dir=log_dir, console_level=console_level
        )
    except ImportError as e:
        raise BootstrapError(
            step="init_logger",
            reason=f"无法导入 utils.logger._init_root_logging: {e}",
            cause=e,
        ) from e
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        SyntaxError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # OSError: .env/配置文件读取失败 (权限/编码/磁盘)
        # ValueError/TypeError/KeyError: 解析/格式/字段错误
        # AttributeError: 属性缺失
        # RuntimeError/SyntaxError: 运行时/语法错误
        raise BootstrapError(
            step="init_logger",
            reason=f"根日志器初始化失败: {e}",
            cause=e,
        ) from e


# ============================================================
# 步骤 3: 预热 ConfigManager
# ============================================================
def _init_config_manager() -> Any:
    """预热 ConfigManager 单例.

    Returns:
        ConfigManager 单例实例

    Raises:
        BootstrapError: ConfigManager 初始化失败
    """
    try:
        from utils.config_manager import ConfigManager

        return ConfigManager.get_instance()
    except ImportError as e:
        raise BootstrapError(
            step="init_config_manager",
            reason=f"无法导入 ConfigManager: {e}",
            cause=e,
        ) from e
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        SyntaxError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # OSError: .env/配置文件读取失败 (权限/编码/磁盘)
        # ValueError/TypeError/KeyError: 解析/格式/字段错误
        # AttributeError: 属性缺失
        # RuntimeError/SyntaxError: 运行时/语法错误
        raise BootstrapError(
            step="init_config_manager",
            reason=f"ConfigManager 单例初始化失败: {e}",
            cause=e,
        ) from e


# ============================================================
# 步骤 4: 预热 TradingEnv
# ============================================================
def _init_trading_env() -> Any:
    """预热 TradingEnv 配置 (读取 .env + 环境变量).

    Returns:
        TradingEnvConfig dataclass 实例

    Raises:
        BootstrapError: TradingEnv 配置读取失败
    """
    try:
        from utils.trading_env import get_trading_env_config, print_env_status

        config = get_trading_env_config()
        # 打印环境状态到日志 (调试用)
        try:
            print_env_status()
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            SyntaxError,
        ):  # P2 模块 fail-safe, 待后续精确化
            # OSError: 文件 IO 异常; ValueError/TypeError: 数据/类型异常
            # KeyError/AttributeError: 字段/属性缺失; RuntimeError/SyntaxError: 运行时/语法错误
            pass  # print_env_status 失败不影响初始化
        return config
    except ImportError as e:
        raise BootstrapError(
            step="init_trading_env",
            reason=f"无法导入 utils.trading_env: {e}",
            cause=e,
        ) from e
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        SyntaxError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # OSError: .env/配置文件读取失败 (权限/编码/磁盘)
        # ValueError/TypeError/KeyError: 解析/格式/字段错误
        # AttributeError: 属性缺失
        # RuntimeError/SyntaxError: 运行时/语法错误
        raise BootstrapError(
            step="init_trading_env",
            reason=f"TradingEnv 配置读取失败: {e}",
            cause=e,
        ) from e


# ============================================================
# 步骤 5: 初始化 KillSwitch
# ============================================================
def _init_kill_switch() -> Any:
    """初始化 KillSwitch (不传 config_path, 走 ConfigManager 路径).

    Returns:
        KillSwitch 实例

    Raises:
        BootstrapError: KillSwitch 初始化失败
    """
    try:
        from utils.kill_switch import KillSwitch

        # 关键: 不传 config_path, 让 KillSwitch._load_config 走路径 2 (ConfigManager)
        # 传 config_path 会触发路径 1 (直接读取), 绕过 ConfigManager (HC-5 违规)
        return KillSwitch()
    except ImportError as e:
        raise BootstrapError(
            step="init_kill_switch",
            reason=f"无法导入 utils.kill_switch: {e}",
            cause=e,
        ) from e
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        SyntaxError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # OSError: .env/配置文件读取失败 (权限/编码/磁盘)
        # ValueError/TypeError/KeyError: 解析/格式/字段错误
        # AttributeError: 属性缺失
        # RuntimeError/SyntaxError: 运行时/语法错误
        raise BootstrapError(
            step="init_kill_switch",
            reason=f"KillSwitch 初始化失败: {e}",
            cause=e,
        ) from e


# ============================================================
# 步骤 6: 注册 broker_callback (可选)
# ============================================================
def _register_broker_callback(ks: Any, callback: Callable | None) -> None:
    """注册 KillSwitch 的实盘执行回调.

    Args:
        ks: KillSwitch 实例
        callback: 实盘执行回调函数, None 表示不注册

    Raises:
        BootstrapError: 回调注册失败
    """
    if callback is None:
        return  # 不注册是合法的, KillSwitch 仍可用但 execute_kill_switch 会抛 RuntimeError

    try:
        ks.set_broker_callback(callback)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        SyntaxError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # OSError: .env/配置文件读取失败 (权限/编码/磁盘)
        # ValueError/TypeError/KeyError: 解析/格式/字段错误
        # AttributeError: 属性缺失
        # RuntimeError/SyntaxError: 运行时/语法错误
        raise BootstrapError(
            step="register_broker_callback",
            reason=f"broker_callback 注册失败: {e}",
            cause=e,
        ) from e


# ============================================================
# 步骤 7: 检查 Feature Flag (仅日志)
# ============================================================
def _check_feature_flags() -> Any:
    """检查 USE_INTEGRATED_BOOTSTRAP flag (仅日志, 不阻塞).

    Returns:
        FeatureFlags 单例实例

    Raises:
        BootstrapError: FeatureFlags 初始化失败 (非 flag 关闭, 是框架本身故障)
    """
    try:
        from utils.infra.feature_flags import FeatureFlags

        flags = FeatureFlags.get_instance()
        # 仅日志, 不阻塞: flag 关闭时 bootstrap 仍完成初始化
        # (调用方根据 flag 决定是否使用 result)
        bootstrap_enabled = flags.is_enabled("USE_INTEGRATED_BOOTSTRAP")
        logging.getLogger("bootstrap").info(
            f"Feature Flag USE_INTEGRATED_BOOTSTRAP = {bootstrap_enabled} "
            f"({'启用' if bootstrap_enabled else '关闭, 走旧路径'})"
        )
        return flags
    except ImportError as e:
        raise BootstrapError(
            step="check_feature_flags",
            reason=f"无法导入 FeatureFlags: {e}",
            cause=e,
        ) from e
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        SyntaxError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # OSError: .env/配置文件读取失败 (权限/编码/磁盘)
        # ValueError/TypeError/KeyError: 解析/格式/字段错误
        # AttributeError: 属性缺失
        # RuntimeError/SyntaxError: 运行时/语法错误
        raise BootstrapError(
            step="check_feature_flags",
            reason=f"FeatureFlags 初始化失败: {e}",
            cause=e,
        ) from e


# ============================================================
# 顶层入口: initialize()
# ============================================================
def initialize(
    broker_callback: Callable | None = None,
    log_prefix: str = "quant_strategy_system",
    log_dir: str = "logs",
    console_level: int = logging.INFO,
    force: bool = False,
) -> BootstrapResult:
    """全局初始化入口 (幂等).

    一次性完成 .env 加载 + 根日志器 + ConfigManager + TradingEnv + KillSwitch + FeatureFlags 初始化.

    Args:
        broker_callback: KillSwitch 的实盘执行回调 (None 表示不注册)
        log_prefix: 日志文件前缀 (默认 "quant_strategy_system")
        log_dir: 日志目录 (默认 "logs")
        console_level: 控制台日志级别 (默认 logging.INFO)
        force: 强制重新初始化 (测试用, 默认 False 走幂等路径)

    Returns:
        BootstrapResult 含所有单例引用

    Raises:
        BootstrapError: 任何子步骤失败

    Usage:
        >>> from utils.infra.bootstrap import initialize
        >>> result = initialize()
        >>> result.config_manager.get_kill_switch_config()
        >>> result.kill_switch.check_margin_status()

    幂等性:
        多次调用返回同一 BootstrapResult 对象 (force=True 除外).
        force=True 会清空缓存重新初始化 (仅供测试).
    """
    global _initialized, _last_result

    with _lock:
        if _initialized and _last_result is not None and not force:
            logging.getLogger("bootstrap").debug("Bootstrap 已初始化, 跳过 (幂等)")
            return _last_result

        logger = logging.getLogger("bootstrap")

        # 步骤 1: 初始化根日志器（必须最先执行，确保后续日志可被捕获）
        logger.info("Bootstrap 步骤 1/7: 初始化根日志器")
        _init_logger(
            log_prefix=log_prefix, log_dir=log_dir, console_level=console_level
        )

        # 步骤 2: 加载 .env
        logger.info("Bootstrap 步骤 2/7: 加载 .env")
        env_loaded = _load_env_file()
        if env_loaded:
            logger.info(".env 加载完成: %d 个变量", len(env_loaded))

        # 步骤 3: 预热 ConfigManager
        logger.info("Bootstrap 步骤 3/7: 预热 ConfigManager 单例")
        config_manager = _init_config_manager()

        # 步骤 4: 预热 TradingEnv
        logger.info("Bootstrap 步骤 4/7: 预热 TradingEnv 配置")
        env_config = _init_trading_env()

        # 步骤 5: 初始化 KillSwitch
        logger.info("Bootstrap 步骤 5/7: 初始化 KillSwitch")
        kill_switch = _init_kill_switch()

        # 步骤 6: 注册 broker_callback (可选)
        logger.info("Bootstrap 步骤 6/7: 注册 broker_callback")
        _register_broker_callback(kill_switch, broker_callback)

        # 步骤 7: 检查 Feature Flag
        logger.info("Bootstrap 步骤 7/7: 检查 Feature Flags")
        feature_flags = _check_feature_flags()

        # 构造结果
        result = BootstrapResult(
            config_manager=config_manager,
            env_config=env_config,
            kill_switch=kill_switch,
            feature_flags=feature_flags,
            initialized=True,
        )

        _initialized = True
        _last_result = result
        logger.info(
            f"Bootstrap 完成: env={env_config.env}, "
            f"fail_closed={env_config.fail_closed}, "
            f"allow_real_orders={env_config.allow_real_orders}"
        )
        return result


# ============================================================
# 重置函数 (测试用)
# ============================================================
def reset() -> None:
    """重置 bootstrap 状态 (仅测试用).

    清空 _initialized 与 _last_result, 并调用各单例的 reset_instance().

    注意:
        - 仅重置 ConfigManager 和 FeatureFlags.
        - KillSwitch 与 TradingEnv 当前不提供 reset_instance(),
          如测试序列需要完全隔离, 请在测试间手动清理相关状态.
    生产代码不应调用此函数.
    """
    global _initialized, _last_result

    with _lock:
        _initialized = False
        _last_result = None

        # 重置依赖单例 (尽力而为, 失败不阻塞)
        try:
            from utils.config_manager import ConfigManager

            ConfigManager.reset_instance()
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            SyntaxError,
        ):  # P2 模块 fail-safe, 待后续精确化
            # OSError: 文件 IO 异常; ValueError/TypeError: 数据/类型异常
            # KeyError/AttributeError: 字段/属性缺失; RuntimeError/SyntaxError: 运行时/语法错误
            pass

        try:
            from utils.infra.feature_flags import FeatureFlags

            FeatureFlags.reset_instance()
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            SyntaxError,
        ):  # P2 模块 fail-safe, 待后续精确化
            # OSError: 文件 IO 异常; ValueError/TypeError: 数据/类型异常
            # KeyError/AttributeError: 字段/属性缺失; RuntimeError/SyntaxError: 运行时/语法错误
            pass


def is_initialized() -> bool:
    """检查 bootstrap 是否已初始化."""
    with _lock:
        return _initialized


def get_result() -> BootstrapResult | None:
    """获取上一次初始化的结果 (未初始化返回 None)."""
    with _lock:
        return _last_result
