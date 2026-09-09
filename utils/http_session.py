"""
无代理 HTTP Session 工厂 (B-4.1)
==============================

集中管理绕过系统代理的 requests.Session 创建, 避免国内金融 API
(eastmoney/sina/wind) 被系统代理 (127.0.0.1:7897) 拦截。

设计原则:
  - trust_env=False: 不读取系统代理环境变量
  - proxies=None: 显式禁用代理
  - 可选重试: HTTPAdapter + Retry
  - 可选默认 User-Agent

使用方式:
  from utils.http_session import make_no_proxy_session, ensure_no_proxy_env

  # 1. 在导入 akshare/requests 前调用 (模块级, 设置 NO_PROXY 环境变量)
  ensure_no_proxy_env()

  # 2. 创建无代理 Session
  session = make_no_proxy_session("sina")
  session.get("https://sinajs.cn/...", timeout=10)

标准 NO_PROXY 域名 (AGENTS.md 硬约束):
  push2his.eastmoney.com, push2.eastmoney.com, eastmoney.com,
  sinajs.cn, sina.com.cn, 127.0.0.1, localhost, mcp.wind.com.cn
"""
from __future__ import annotations

import logging
import os

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger("http_session")

# ── 标准 NO_PROXY 域名白名单 (国内金融 API + 本地) ──
# 来源: AGENTS.md 第 6 章 NO_PROXY 配置硬约束
DEFAULT_NO_PROXY_DOMAINS = (
    "push2his.eastmoney.com,"
    "push2.eastmoney.com,"
    "eastmoney.com,"
    "sinajs.cn,"
    "sina.com.cn,"
    "127.0.0.1,"
    "localhost,"
    "m.wind.com.cn,"
    "mcp.wind.com.cn"
)

# 默认 User-Agent (避免被反爬拦截)
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def ensure_no_proxy_env(domains: str | None = None) -> str:
    """确保 NO_PROXY 环境变量已设置 (在导入 akshare/requests 前调用)

    若已存在 NO_PROXY 且非空, 保留原值; 否则设置默认域名白名单。
    同时设置大写和小写两个变量 (部分库读小写 no_proxy)。

    Args:
        domains: 自定义域名列表 (逗号分隔), None 则使用 DEFAULT_NO_PROXY_DOMAINS

    Returns:
        当前 NO_PROXY 值
    """
    value = domains or DEFAULT_NO_PROXY_DOMAINS
    existing = os.environ.get("NO_PROXY", "")
    if not existing:
        os.environ["NO_PROXY"] = value
        os.environ["no_proxy"] = value  # 部分库读小写
        logger.debug("[http_session] NO_PROXY 已设置: %s", value)
    else:
        logger.debug("[http_session] NO_PROXY 已存在, 保留: %s", existing)
    return os.environ.get("NO_PROXY", value)


def make_no_proxy_session(
    name: str | None = None,
    max_retries: int = 3,
    user_agent: str | None = None,
    pool_connections: int = 10,
    pool_maxsize: int = 10,
) -> requests.Session:
    """创建无代理 requests.Session 工厂

    绕过系统代理, 避免国内金融 API (eastmoney/sina/wind) 被代理拦截。
    可选配置 HTTP 重试机制和连接池。

    Args:
        name: Session 名称 (用于日志标识, 如 "sina"/"ifind")
        max_retries: 最大重试次数 (0 表示不重试)
        user_agent: 自定义 User-Agent, None 使用默认
        pool_connections: 连接池大小 (默认 10)
        pool_maxsize: 最大连接数 (默认 10)

    Returns:
        配置好的 requests.Session 实例 (trust_env=False, proxies=None)

    注意:
        requests 不支持 session 级 timeout, 调用方需在 get/post 中显式传 timeout。
    """
    session = requests.Session()
    session.trust_env = False  # 不读取系统代理环境变量
    session.proxies = {}  # 显式禁用代理

    # 默认 headers
    session.headers.update(
        {
            "User-Agent": user_agent or _DEFAULT_UA,
        }
    )

    # 重试机制 + 连接池
    if max_retries > 0:
        adapter = HTTPAdapter(
            max_retries=max_retries,
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

    # 标识信息 (供调试/日志使用)
    session._session_name = name or "unnamed"  # type: ignore[attr-defined]

    logger.debug(
        "[http_session] 创建无代理 Session: name=%s, retries=%s, pool=%s",
        name or "unnamed",
        max_retries,
        pool_connections,
    )
    return session


__all__ = [
    "DEFAULT_NO_PROXY_DOMAINS",
    "ensure_no_proxy_env",
    "make_no_proxy_session",
]
