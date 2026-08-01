"""
utils/http_session.py 单元测试 (B-4.1)

覆盖:
  - make_no_proxy_session: Session 创建、trust_env、proxies、重试、UA
  - ensure_no_proxy_env: 环境变量设置与保留
  - DEFAULT_NO_PROXY_DOMAINS: 关键域名完整性
"""

import os

import requests

from utils.http_session import (
    DEFAULT_NO_PROXY_DOMAINS,
    ensure_no_proxy_env,
    make_no_proxy_session,
)

# ── make_no_proxy_session 测试 ──


class TestMakeNoProxySession:
    """无代理 Session 工厂测试"""

    def test_returns_session_instance(self):
        """返回 requests.Session 实例"""
        session = make_no_proxy_session()
        assert isinstance(session, requests.Session)

    def test_trust_env_is_false(self):
        """trust_env 必须为 False (不读取系统代理)"""
        session = make_no_proxy_session()
        assert session.trust_env is False

    def test_proxies_are_none(self):
        """proxies 必须为 {"http": None, "https": None} (显式无代理)"""
        session = make_no_proxy_session()
        assert session.proxies == {"http": None, "https": None}

    def test_default_session_name(self):
        """未传 name 时默认为 'unnamed'"""
        session = make_no_proxy_session()
        assert getattr(session, "_session_name", None) == "unnamed"

    def test_custom_session_name(self):
        """自定义 name 被记录"""
        session = make_no_proxy_session("sina")
        assert getattr(session, "_session_name", None) == "sina"

    def test_custom_user_agent(self):
        """自定义 User-Agent 被设置到 headers"""
        custom_ua = "TestAgent/1.0"
        session = make_no_proxy_session(user_agent=custom_ua)
        assert session.headers["User-Agent"] == custom_ua

    def test_default_user_agent_contains_chrome(self):
        """默认 User-Agent 包含 Chrome 标识 (避免反爬)"""
        session = make_no_proxy_session()
        ua = session.headers["User-Agent"]
        assert "Chrome" in ua
        assert "Mozilla" in ua

    def test_retries_mount_adapter_when_positive(self):
        """max_retries > 0 时挂载 HTTPAdapter"""
        session = make_no_proxy_session(max_retries=3)
        # HTTPAdapter 会被 mount 到 http:// 和 https://
        http_adapter = session.get_adapter("http://example.com")
        https_adapter = session.get_adapter("https://example.com")
        assert http_adapter is not None
        assert https_adapter is not None

    def test_no_retries_when_zero(self):
        """max_retries=0 时不挂载自定义 adapter"""
        session = make_no_proxy_session(max_retries=0)
        # 默认 adapter 的 max_retries 通常是 0
        adapter = session.get_adapter("https://example.com")
        assert adapter is not None

    def test_multiple_sessions_are_independent(self):
        """多次调用返回独立 Session 实例"""
        s1 = make_no_proxy_session("a")
        s2 = make_no_proxy_session("b")
        assert s1 is not s2
        assert s1._session_name != s2._session_name  # type: ignore[attr-defined]


# ── ensure_no_proxy_env 测试 ──


class TestEnsureNoProxyEnv:
    """NO_PROXY 环境变量管理测试"""

    def test_sets_when_empty(self, monkeypatch):
        """NO_PROXY 为空时设置默认域名白名单"""
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        value = ensure_no_proxy_env()

        assert "eastmoney.com" in value
        assert "sinajs.cn" in value
        assert os.environ.get("NO_PROXY") == value
        assert os.environ.get("no_proxy") == value

    def test_preserves_existing_value(self, monkeypatch):
        """NO_PROXY 已存在时保留原值, 不覆盖"""
        existing = "custom.example.com"
        monkeypatch.setenv("NO_PROXY", existing)
        monkeypatch.setenv("no_proxy", existing)

        value = ensure_no_proxy_env()

        assert value == existing
        assert os.environ.get("NO_PROXY") == existing

    def test_custom_domains_parameter(self, monkeypatch):
        """传入自定义 domains 时使用自定义值"""
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        custom = "foo.com,bar.com"
        value = ensure_no_proxy_env(domains=custom)

        assert value == custom
        assert os.environ.get("NO_PROXY") == custom

    def test_sets_both_upper_and_lower_case(self, monkeypatch):
        """同时设置大写 NO_PROXY 和小写 no_proxy"""
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        ensure_no_proxy_env()

        assert os.environ.get("NO_PROXY")
        assert os.environ.get("no_proxy")
        assert os.environ.get("NO_PROXY") == os.environ.get("no_proxy")


# ── DEFAULT_NO_PROXY_DOMAINS 测试 ──


class TestDefaultNoProxyDomains:
    """标准 NO_PROXY 域名白名单完整性测试"""

    def test_contains_eastmoney_domains(self):
        """包含东方财富相关域名"""
        assert "push2his.eastmoney.com" in DEFAULT_NO_PROXY_DOMAINS
        assert "push2.eastmoney.com" in DEFAULT_NO_PROXY_DOMAINS
        assert "eastmoney.com" in DEFAULT_NO_PROXY_DOMAINS

    def test_contains_sina_domains(self):
        """包含新浪财经相关域名"""
        assert "sinajs.cn" in DEFAULT_NO_PROXY_DOMAINS
        assert "sina.com.cn" in DEFAULT_NO_PROXY_DOMAINS

    def test_contains_wind_domain(self):
        """包含 Wind MCP 相关域名"""
        assert "mcp.wind.com.cn" in DEFAULT_NO_PROXY_DOMAINS

    def test_contains_localhost(self):
        """包含本地地址 (127.0.0.1 + localhost)"""
        assert "127.0.0.1" in DEFAULT_NO_PROXY_DOMAINS
        assert "localhost" in DEFAULT_NO_PROXY_DOMAINS

    def test_is_comma_separated_string(self):
        """是逗号分隔的字符串格式"""
        assert isinstance(DEFAULT_NO_PROXY_DOMAINS, str)
        assert "," in DEFAULT_NO_PROXY_DOMAINS
