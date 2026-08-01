"""
研究/生产环境物理隔离模块
根据审计建议 #P2-8 创建

核心原则：
1. 研究环境（research/）用于探索性分析，不允许直接调用生产数据
2. 生产环境（v8.3_institutional/src/）使用C++/Go重写核心路径
3. 数据管道必须经过接入层→清洗层→特征层→服务层独立部署
4. 所有模型上线前必须通过影子账户验证
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class EnvironmentType(Enum):
    """环境类型枚举"""

    RESEARCH = "research"
    PRODUCTION = "production"
    SHADOW = "shadow"  # 影子账户环境
    TESTING = "testing"


class EnvironmentIsolation:
    """环境隔离管理器"""

    _current_env: Optional[EnvironmentType] = None
    _config: Dict[str, Any] = {}

    @classmethod
    def detect_environment(cls) -> EnvironmentType:
        """自动检测当前运行环境"""
        # 检查环境变量
        env_var = os.environ.get("QUANT_SYSTEM_ENV", "").lower()

        if env_var == "production":
            cls._current_env = EnvironmentType.PRODUCTION
        elif env_var == "shadow":
            cls._current_env = EnvironmentType.SHADOW
        elif env_var == "testing":
            cls._current_env = EnvironmentType.TESTING
        else:
            # 根据路径检测
            cwd = Path.cwd().resolve()
            if "research" in str(cwd).lower() or "dev" in str(cwd).lower():
                cls._current_env = EnvironmentType.RESEARCH
            elif "v8.3_institutional" in str(cwd).lower():
                cls._current_env = EnvironmentType.PRODUCTION
            else:
                # 默认视为研究环境（安全优先）
                cls._current_env = EnvironmentType.RESEARCH

        return cls._current_env

    @classmethod
    def set_environment(cls, env: EnvironmentType):
        """手动设置环境类型（用于测试）"""
        cls._current_env = env
        os.environ["QUANT_SYSTEM_ENV"] = env.value

    @classmethod
    def get_current_environment(cls) -> EnvironmentType:
        """获取当前环境"""
        if cls._current_env is None:
            return cls.detect_environment()
        return cls._current_env

    @classmethod
    def is_production(cls) -> bool:
        """是否在生产环境"""
        return cls.get_current_environment() in [EnvironmentType.PRODUCTION, EnvironmentType.SHADOW]

    @classmethod
    def is_research(cls) -> bool:
        """是否在研究环境"""
        return cls.get_current_environment() == EnvironmentType.RESEARCH

    @classmethod
    def require_production(cls, operation: str):
        """
        要求当前必须是生产环境才能执行操作

        Args:
            operation: 操作名称（用于错误提示）

        Raises:
            RuntimeError: 如果当前不是生产环境
        """
        if not cls.is_production():
            raise RuntimeError(
                f"⚠️ [SECURITY] 操作 '{operation}' 仅允许在生产环境执行！\n"
                f"当前环境: {cls.get_current_environment().value}\n"
                f"请设置环境变量 QUANT_SYSTEM_ENV=production 或进入生产目录运行"
            )

    @classmethod
    def require_research(cls, operation: str):
        """
        要求当前必须是研究环境才能执行操作

        Args:
            operation: 操作名称（用于错误提示）

        Raises:
            RuntimeError: 如果当前是生产环境
        """
        if cls.is_production():
            raise RuntimeError(
                f"⚠️ [SECURITY] 操作 '{operation}' 仅限在研究环境执行！\n"
                f"当前环境: {cls.get_current_environment().value}\n"
                f"研究代码严禁直接上线到生产环境"
            )

    @classmethod
    def validate_data_access(cls, data_source: str, operation: str = "read"):
        """
        验证数据访问权限

        Args:
            data_source: 数据源路径
            operation: 操作类型（read/write）

        Raises:
            PermissionError: 如果数据源不在当前环境允许范围内
        """
        current_env = cls.get_current_environment()

        # 定义各环境允许的数据源
        allowed_sources = {
            EnvironmentType.RESEARCH: ["research/data", "tmp", "temp"],
            EnvironmentType.PRODUCTION: ["src/data", "production/data", "cache"],
            EnvironmentType.SHADOW: ["src/data", "shadow/data", "cache"],
            EnvironmentType.TESTING: ["tests/data", "tmp", "test_fixtures"],
        }

        allowed = allowed_sources.get(current_env, [])
        is_allowed = any(source in data_source for source in allowed)

        if not is_allowed:
            raise PermissionError(
                f"⚠️ [DATA SECURITY] 环境 '{current_env.value}' 无权访问数据源: {data_source}\n"
                f"允许的数据源路径: {', '.join(allowed)}"
            )

    @classmethod
    def log_environment_context(cls, context: str = ""):
        """记录环境上下文到日志"""
        timestamp = datetime.now().isoformat()
        env = cls.get_current_environment()
        message = f"[{timestamp}] ENV={env.value} | {context}"

        # 输出到stderr以确保可见性
        print(message, file=sys.stderr, flush=True)

        return message

    @classmethod
    def get_environment_summary(cls) -> Dict[str, Any]:
        """获取环境摘要信息"""
        return {
            "environment": cls.get_current_environment().value,
            "is_production": cls.is_production(),
            "is_research": cls.is_research(),
            "working_directory": str(Path.cwd().resolve()),
            "timestamp": datetime.now().isoformat(),
            "config": cls._config,
        }


# 全局初始化
EnvironmentIsolation.detect_environment()


def production_only(operation: str):
    """装饰器：限制函数仅在生产环境执行"""

    def decorator(func):
        def wrapper(*args, **kwargs):
            EnvironmentIsolation.require_production(operation)
            EnvironmentIsolation.log_environment_context(f"Executing: {operation}")
            return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


def research_only(operation: str):
    """装饰器：限制函数仅在研究环境执行"""

    def decorator(func):
        def wrapper(*args, **kwargs):
            EnvironmentIsolation.require_research(operation)
            EnvironmentIsolation.log_environment_context(f"Executing: {operation}")
            return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


# 使用示例：
# @production_only("提交订单")
# def submit_order(order):
#     # 仅在生产环境执行
#     pass

# @research_only("回测策略")
# def backtest_strategy(strategy):
#     仅在研究环境执行
#     pass
