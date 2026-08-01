# -*- coding: utf-8 -*-
"""Streamlit UI 鉴权模块 — 终极量化交易系统 8.4 (T5.6).

任务: T5.6
责任层: L7 归因 + 前端

设计目标:
    1. HC-1: USE_STREAMLIT_UI Feature Flag 默认 False, 关闭时禁用 UI
    2. 生产环境强制鉴权 (TRADING_ENV=production 时必须登录)
    3. 开发/测试环境可选鉴权 (TRADING_ENV!=production 时跳过)
    4. 简单口令 + 会话状态 (session_state) 持久化
    5. HC-5: 配置走 ConfigManager 4 级优先级

鉴权流程:
    1. 检查 Feature Flag USE_STREAMLIT_UI 是否启用 (默认 False)
    2. 检查 TRADING_ENV 环境变量: production 强制鉴权, 其他可选
    3. 从 ConfigManager 读取 credentials 配置 (username/password_hash)
    4. 用户输入口令, 校验通过后写入 session_state["authed"]=True
    5. 后续页面调用 require_auth() 装饰器/函数验证

配置文件 (v8.3_institutional/config/ui_auth.yaml):
    enabled: true                # 是否启用鉴权 (覆盖环境推断)
    require_in_production: true  # 生产环境强制鉴权
    credentials:
      - username: admin
        password_hash: "<sha256>"  # SHA-256 哈希
    session_ttl_seconds: 3600    # 会话 TTL

安全注意:
    - 密码必须以 SHA-256 哈希存储, 禁止明文
    - 默认凭据仅用于开发, 生产环境必须修改
    - 会话状态在浏览器关闭后失效
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.config_manager import get_config
from utils.infra.feature_flags import is_enabled

logger = logging.getLogger("ui.auth")

# ============================================================
# 常量
# ============================================================

FLAG_NAME = "USE_STREAMLIT_UI"

# 会话状态键名
SESSION_AUTHED_KEY = "ui_authed"
SESSION_USERNAME_KEY = "ui_username"
SESSION_LOGIN_AT_KEY = "ui_login_at"

# 默认会话 TTL (1 小时)
DEFAULT_SESSION_TTL = 3600

# 配置文件名 (走 ConfigManager 4 级优先级, HC-5)
DEFAULT_CONFIG_NAME = "ui_auth"


# ============================================================
# 数据类
# ============================================================

@dataclass
class AuthConfig:
    """鉴权配置快照.

    Attributes:
        enabled: 是否启用鉴权 (None 时根据 TRADING_ENV 推断)
        require_in_production: 生产环境是否强制鉴权
        credentials: 凭据列表 [{username, password_hash}]
        session_ttl_seconds: 会话 TTL (秒)
    """
    enabled: Optional[bool] = None
    require_in_production: bool = True
    credentials: List[Dict[str, str]] = field(default_factory=list)
    session_ttl_seconds: int = DEFAULT_SESSION_TTL

    def to_dict(self) -> Dict[str, Any]:
        """转为字典 (用于审计日志)."""
        return {
            "enabled": self.enabled,
            "require_in_production": self.require_in_production,
            "n_credentials": len(self.credentials),
            "session_ttl_seconds": self.session_ttl_seconds,
        }


# ============================================================
# 核心函数
# ============================================================

def hash_password(password: str) -> str:
    """对密码进行 SHA-256 哈希.

    Args:
        password: 明文密码

    Returns:
        SHA-256 哈希 (64 字符十六进制字符串)
    """
    if not isinstance(password, str):
        raise TypeError(f"password 必须为 str, 收到 {type(password).__name__}")
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码是否匹配哈希.

    Args:
        password: 明文密码
        password_hash: SHA-256 哈希

    Returns:
        是否匹配
    """
    if not password or not password_hash:
        return False
    return hash_password(password) == password_hash


def load_auth_config(config_name: str = DEFAULT_CONFIG_NAME) -> AuthConfig:
    """从 ConfigManager 加载鉴权配置 (HC-5 4 级优先级).

    Args:
        config_name: 配置名 (默认 "ui_auth")

    Returns:
        AuthConfig 实例
    """
    cfg = AuthConfig()
    try:
        raw = get_config(config_name, default={}) or {}
        if raw:
            if "enabled" in raw:
                cfg.enabled = bool(raw["enabled"])
            cfg.require_in_production = bool(raw.get("require_in_production", True))
            cfg.credentials = list(raw.get("credentials", []))
            cfg.session_ttl_seconds = int(raw.get("session_ttl_seconds", DEFAULT_SESSION_TTL))
            logger.debug("鉴权配置加载: %s", cfg.to_dict())
    except Exception as e:
        logger.warning("鉴权配置加载失败, 使用默认值: %s", e)
    return cfg


def is_production_env() -> bool:
    """判断当前是否为生产环境.

    Returns:
        TRADING_ENV=production 时返回 True, 否则 False
    """
    return os.environ.get("TRADING_ENV", "").lower() == "production"


def is_auth_required(config: Optional[AuthConfig] = None) -> bool:
    """判断当前是否需要鉴权.

    优先级:
        1. AuthConfig.enabled 显式配置 (True/False)
        2. 生产环境强制鉴权 (TRADING_ENV=production)
        3. 默认 False (开发/测试环境不强制)

    Args:
        config: 鉴权配置 (None 时自动加载)

    Returns:
        是否需要鉴权
    """
    if config is None:
        config = load_auth_config()

    # 显式配置优先
    if config.enabled is not None:
        return config.enabled

    # 生产环境强制
    if is_production_env() and config.require_in_production:
        return True

    return False


def is_ui_enabled() -> bool:
    """检查 USE_STREAMLIT_UI Feature Flag 是否启用 (HC-1).

    Returns:
        Flag 启用时返回 True, 否则 False
    """
    return is_enabled(FLAG_NAME)


def authenticate(
    username: str,
    password: str,
    config: Optional[AuthConfig] = None,
) -> bool:
    """校验用户名密码.

    Args:
        username: 用户名
        password: 明文密码
        config: 鉴权配置 (None 时自动加载)

    Returns:
        校验通过返回 True, 否则 False
    """
    if not username or not password:
        return False

    if config is None:
        config = load_auth_config()

    for cred in config.credentials:
        if cred.get("username") == username:
            stored_hash = cred.get("password_hash", "")
            if verify_password(password, stored_hash):
                logger.info("鉴权成功: username=%s", username)
                return True

    logger.warning("鉴权失败: username=%s", username)
    return False


def check_session_valid(
    login_at: float,
    ttl: int = DEFAULT_SESSION_TTL,
    now: Optional[float] = None,
) -> bool:
    """检查会话是否仍在有效期内.

    Args:
        login_at: 登录时间戳 (time.time())
        ttl: 会话 TTL (秒)
        now: 当前时间戳 (None 时自动取)

    Returns:
        会话有效返回 True, 否则 False
    """
    if login_at <= 0:
        return False
    current = now if now is not None else time.time()
    return (current - login_at) < ttl


# ============================================================
# Streamlit 集成函数 (仅在使用 Streamlit 时调用)
# ============================================================

def get_session_state() -> Any:
    """获取 Streamlit session_state (若可用).

    Returns:
        st.session_state 对象, 或 None (未安装/未运行时)
    """
    try:
        import streamlit as st  # type: ignore[import-not-found]
        return st.session_state
    except (ImportError, RuntimeError):
        # ImportError: 未安装 streamlit
        # RuntimeError: 不在 streamlit 运行时上下文中
        return None


def is_authenticated() -> bool:
    """检查当前会话是否已鉴权.

    Returns:
        已鉴权返回 True, 否则 False
    """
    # 不需要鉴权的环境, 视为已鉴权
    if not is_auth_required():
        return True

    state = get_session_state()
    if state is None:
        return False

    authed = state.get(SESSION_AUTHED_KEY, False)
    login_at = state.get(SESSION_LOGIN_AT_KEY, 0.0)
    if not authed:
        return False

    # 检查会话是否过期
    config = load_auth_config()
    if not check_session_valid(login_at, config.session_ttl_seconds):
        # 会话过期, 清理状态
        state[SESSION_AUTHED_KEY] = False
        state[SESSION_USERNAME_KEY] = ""
        state[SESSION_LOGIN_AT_KEY] = 0.0
        return False

    return True


def require_auth() -> bool:
    """要求页面鉴权, 未鉴权时显示登录表单并停止执行.

    Usage:
        from ui.auth import require_auth
        if not require_auth():
            st.stop()  # 未鉴权, 停止页面渲染

    Returns:
        已鉴权返回 True, 未鉴权返回 False (调用方应 st.stop())
    """
    # Feature Flag 关闭时, 整个 UI 不可用
    if not is_ui_enabled():
        _render_flag_disabled()
        return False

    # 不需要鉴权, 直接通过
    if not is_auth_required():
        return True

    # 已鉴权, 通过
    if is_authenticated():
        return True

    # 未鉴权, 渲染登录表单
    _render_login_form()
    return False


def logout() -> None:
    """登出当前会话."""
    state = get_session_state()
    if state is None:
        return
    state[SESSION_AUTHED_KEY] = False
    state[SESSION_USERNAME_KEY] = ""
    state[SESSION_LOGIN_AT_KEY] = 0.0
    logger.info("用户登出")


def _render_login_form() -> None:
    """渲染登录表单 (Streamlit)."""
    try:
        import streamlit as st  # type: ignore[import-not-found]
    except ImportError:
        return

    st.subheader("🔒 系统登录")
    st.caption("生产环境需鉴权访问")

    with st.form("ui_login_form", clear_on_submit=False):
        username = st.text_input("用户名", key="ui_login_username")
        password = st.text_input("密码", type="password", key="ui_login_password")
        submitted = st.form_submit_button("登录")

        if submitted:
            if authenticate(username, password):
                state = st.session_state
                state[SESSION_AUTHED_KEY] = True
                state[SESSION_USERNAME_KEY] = username
                state[SESSION_LOGIN_AT_KEY] = time.time()
                st.success(f"登录成功, 欢迎 {username}")
                st.rerun()
            else:
                st.error("用户名或密码错误")


def _render_flag_disabled() -> None:
    """渲染 Feature Flag 关闭提示 (Streamlit)."""
    try:
        import streamlit as st  # type: ignore[import-not-found]
    except ImportError:
        return

    st.warning(
        f"⚠️ Streamlit UI 当前未启用 (Feature Flag `{FLAG_NAME}=False`).\n\n"
        f"如需启用, 请在 `v8.3_institutional/config/feature_flags.yaml` 中设置 "
        f"`{FLAG_NAME}: true` 并通过 `FeatureFlags.enable('{FLAG_NAME}', signer, co_signer)` 双签启用."
    )
    st.info(
        "**HC-1 硬约束**: 默认关闭以保护 V9 生产基线, 启用需双签审计."
    )


__all__ = [
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_SESSION_TTL",
    "FLAG_NAME",
    "SESSION_AUTHED_KEY",
    "SESSION_LOGIN_AT_KEY",
    "SESSION_USERNAME_KEY",
    "AuthConfig",
    "authenticate",
    "check_session_valid",
    "get_session_state",
    "hash_password",
    "is_auth_required",
    "is_authenticated",
    "is_production_env",
    "is_ui_enabled",
    "load_auth_config",
    "logout",
    "require_auth",
    "verify_password",
]
