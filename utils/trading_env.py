"""
交易环境配置 (Trading Environment Configuration)
================================================

职责:
- 读取 TRADING_ENV 环境变量, 判断当前运行环境
- production 模式: 激活 fail-closed 设计 (异常时阻止交易, 而非放行)
- development 模式: 宽松模式, 允许降级运行 (仅用于研究/开发)
- shadow 模式: 影子账户模式, 记录但不执行真实订单

使用方式:
    from utils.trading_env import TradingEnv, get_trading_env

    env = get_trading_env()
    if env.is_production():
        # fail-closed: 任何风控异常都阻止交易
        ...
    elif env.is_shadow():
        # 影子账户: 记录订单但不执行
        ...
    else:
        # development: 宽松模式
        ...

环境变量:
    TRADING_ENV=production  # 生产环境, fail-closed
    TRADING_ENV=shadow      # 影子账户环境
    TRADING_ENV=development # 开发环境 (默认)

2026-07-25 顶级对冲基金审计: P0-11 fail-closed 设计的核心控制器
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class TradingEnv:
    """交易环境枚举"""

    PRODUCTION = "production"
    SHADOW = "shadow"
    DEVELOPMENT = "development"

    # 环境层级 (数字越大, 风控越严格)
    _LEVELS = {
        "development": 0,
        "shadow": 1,
        "production": 2,
    }

    @classmethod
    def is_valid(cls, env: str) -> bool:
        """检查环境值是否合法"""
        return env in (cls.PRODUCTION, cls.SHADOW, cls.DEVELOPMENT)

    @classmethod
    def level(cls, env: str) -> int:
        """获取环境风控级别 (数字越大越严格)"""
        return cls._LEVELS.get(env, 0)


@dataclass
class TradingEnvConfig:
    """交易环境配置"""

    env: str
    is_prod: bool
    is_shadow: bool
    is_dev: bool
    fail_closed: bool  # True = 异常时阻止交易; False = 异常时降级放行
    allow_real_orders: bool  # True = 允许真实下单; False = 仅记录
    shadow_capital_pct: float  # 影子账户资金比例 (0.0~1.0)
    description: str

    def __str__(self) -> str:
        return (
            f"TradingEnvConfig(env={self.env}, fail_closed={self.fail_closed}, "
            f"allow_real_orders={self.allow_real_orders}, "
            f"shadow_capital_pct={self.shadow_capital_pct:.0%})"
        )


def get_trading_env() -> str:
    """获取当前交易环境 (从环境变量读取)

    优先级:
        1. 环境变量 TRADING_ENV
        2. .env 文件中的 TRADING_ENV
        3. 默认值 "development"
    """
    # 1. 直接从环境变量读取
    env = os.environ.get("TRADING_ENV", "").strip().lower()
    if env and TradingEnv.is_valid(env):
        return env

    # 2. 尝试从 .env 文件读取
    env_from_file = _read_env_file()
    if env_from_file and TradingEnv.is_valid(env_from_file):
        return env_from_file

    # 3. 默认 development
    return TradingEnv.DEVELOPMENT


def _read_env_file() -> str | None:
    """从 .env 文件读取 TRADING_ENV (支持 UTF-8 和 GBK 编码)"""
    from pathlib import Path

    # 查找 .env 文件 (当前目录 + 项目根目录)
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]

    # 尝试多种编码 (Windows 中文系统常用 GBK)
    encodings = ["utf-8", "gbk", "utf-8-sig", "latin-1"]

    for env_file in candidates:
        if not env_file.exists():
            continue
        for enc in encodings:
            try:
                with open(env_file, encoding=enc) as f:
                    content = f.read()
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue
                    if "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("\"'")
                    if key == "TRADING_ENV":
                        logger.info("从 .env 读取 TRADING_ENV=%s (encoding=%s)", value, enc)
                        return value.lower()
                break  # 读取成功, 不再尝试其他编码
            except UnicodeDecodeError:
                continue  # 尝试下一个编码
            except Exception as e:  # P2 模块 fail-safe, 待后续精确化
                logger.warning("读取 .env 文件失败 %s (encoding=%s): %s", env_file, enc, e)
                break

    return None


def get_trading_env_config() -> TradingEnvConfig:
    """获取完整的交易环境配置

    根据当前 TRADING_ENV 返回对应的配置:
        - production: fail_closed=True, allow_real_orders=True, shadow_capital_pct=1.0
        - shadow:     fail_closed=True, allow_real_orders=False, shadow_capital_pct=0.1
        - development: fail_closed=False, allow_real_orders=False, shadow_capital_pct=0.0
    """
    env = get_trading_env()

    if env == TradingEnv.PRODUCTION:
        return TradingEnvConfig(
            env=env,
            is_prod=True,
            is_shadow=False,
            is_dev=False,
            fail_closed=True,  # P0-11: 生产环境强制 fail-closed
            allow_real_orders=True,
            shadow_capital_pct=1.0,
            description="生产环境: fail-closed 激活, 允许真实下单, 全量资金",
        )
    elif env == TradingEnv.SHADOW:
        return TradingEnvConfig(
            env=env,
            is_prod=False,
            is_shadow=True,
            is_dev=False,
            fail_closed=True,  # 影子账户也 fail-closed
            allow_real_orders=False,  # 不执行真实订单
            shadow_capital_pct=0.1,  # 10% 资金
            description="影子账户环境: fail-closed 激活, 不执行真实订单, 10% 资金跟踪",
        )
    else:
        return TradingEnvConfig(
            env=TradingEnv.DEVELOPMENT,
            is_prod=False,
            is_shadow=False,
            is_dev=True,
            fail_closed=False,  # 开发环境宽松
            allow_real_orders=False,
            shadow_capital_pct=0.0,
            description="开发环境: fail-open (降级放行), 不执行真实订单, 仅研究",
        )


def assert_production_fail_closed(error: Exception, context: str = "") -> None:
    """P0-11: 生产环境 fail-closed 断言

    在 production/shadow 环境下, 风控异常必须阻止交易 (fail-closed)
    在 development 环境下, 记录警告但允许继续 (fail-open)

    Args:
        error: 捕获的异常
        context: 异常上下文描述

    Raises:
        RuntimeError: 在 production/shadow 环境下, 阻止交易继续
    """
    config = get_trading_env_config()

    if config.fail_closed:
        # fail-closed: 阻止交易
        logger.critical(
            "[FAIL-CLOSED] %s 环境风控异常, 阻止交易! context=%s, error=%s",
            config.env.upper(),
            context,
            error,
            exc_info=True,
        )
        raise RuntimeError(
            f"FAIL-CLOSED: {config.env} 环境风控异常, 交易被阻止 (context={context}, error={error})"
        ) from error
    else:
        # fail-open: 降级放行 (仅开发环境)
        logger.warning(
            "[FAIL-OPEN] %s 环境风控异常, 降级放行 (仅开发环境): context=%s, error=%s",
            config.env.upper(),
            context,
            error,
            exc_info=True,
        )


def print_env_status() -> None:
    """打印当前环境状态 (用于日志/调试)"""
    config = get_trading_env_config()
    logger.info("=" * 60)
    logger.info("交易环境配置")
    logger.info("=" * 60)
    logger.info("  TRADING_ENV:          %s", config.env)
    logger.info("  fail_closed:          %s", config.fail_closed)
    logger.info("  allow_real_orders:    %s", config.allow_real_orders)
    logger.info("  shadow_capital_pct:   %.0f%%", config.shadow_capital_pct * 100)
    logger.info("  description:          %s", config.description)
    logger.info("=" * 60)


if __name__ == "__main__":
    # 直接运行时打印当前环境状态
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print_env_status()
