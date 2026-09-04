"""QMT RPC 网关守卫单元测试 — _assert_safe_bind / _mask_account / _verify_token / _verify_ip

覆盖 HIGH-1 加固 (2026-08-24):
- fail-closed 启动守卫: 非回环且无白名单 → SystemExit
- 账户脱敏: 尾 4 位
- Token 恒定时间比较 + 未配置 fail-closed
- IP 白名单 fail-open (空白名单放行, 兼容默认 127.0.0.1)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.execution.qmt_rpc_server import (
    _assert_safe_bind,
    _client_ip,
    _mask_account,
    _verify_ip,
    _verify_token,
)

# ============================================================
# _assert_safe_bind 启动守卫
# ============================================================


class TestAssertSafeBind:
    """fail-closed: 非回环且无白名单 → 拒绝启动。"""

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_always_allowed(self, host, monkeypatch):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        monkeypatch.delenv("QMT_RPC_ALLOW_PUBLIC", raising=False)
        _assert_safe_bind(host)

    def test_non_loopback_no_whitelist_rejects(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        monkeypatch.delenv("QMT_RPC_ALLOW_PUBLIC", raising=False)
        with pytest.raises(SystemExit, match="安全策略拒绝启动"):
            _assert_safe_bind("0.0.0.0")

    def test_non_loopback_with_whitelist_allows(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", "10.0.0.1,10.0.0.2")
        monkeypatch.delenv("QMT_RPC_ALLOW_PUBLIC", raising=False)
        _assert_safe_bind("0.0.0.0")

    @pytest.mark.parametrize("val", ["1", "true", "yes", "TRUE", "Yes"])
    def test_non_loopback_allow_public_override(self, monkeypatch, val):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        monkeypatch.setenv("QMT_RPC_ALLOW_PUBLIC", val)
        _assert_safe_bind("192.168.1.100")

    @pytest.mark.parametrize("val", ["0", "false", "no", "", "random"])
    def test_allow_public_invalid_still_rejects(self, monkeypatch, val):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        monkeypatch.setenv("QMT_RPC_ALLOW_PUBLIC", val)
        with pytest.raises(SystemExit):
            _assert_safe_bind("0.0.0.0")


# ============================================================
# _mask_account 账户脱敏
# ============================================================


class TestMaskAccount:
    """CWE-200: 只回传尾 4 位。"""

    def test_empty_returns_masked(self):
        assert _mask_account("") == "****"

    def test_none_returns_masked(self):
        assert _mask_account(None) == "****"

    @pytest.mark.parametrize("acct", ["123", "AB", "1234"])
    def test_short_returns_masked(self, acct):
        assert _mask_account(acct) == "****"

    def test_long_returns_tail4(self):
        assert _mask_account("123456789") == "****6789"

    def test_exactly_5_chars(self):
        assert _mask_account("12345") == "****2345"


# ============================================================
# _verify_token 恒定时间比较 + fail-closed
# ============================================================


class TestVerifyToken:
    """Token 鉴权: 未配置→503, 不匹配→401, 匹配→放行 + 暴力破解封禁。"""

    @pytest.fixture(autouse=True)
    def _reset_guard(self):
        # 每个用例清空失败计数/封禁, 避免跨用例污染 (封禁按来源 IP)
        from utils.execution import qmt_rpc_server as _m

        _m._auth_guard._fails.clear()
        _m._auth_guard._lockout_until.clear()
        yield

    def _req(self, host="10.0.0.1") -> MagicMock:
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = host
        return req

    def test_no_token_configured_rejects_503(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_TOKEN", raising=False)
        with pytest.raises(Exception) as exc:
            _verify_token(self._req(), x_token="anything")
        assert exc.value.status_code == 503

    def test_empty_token_configured_rejects_503(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "")
        with pytest.raises(Exception) as exc:
            _verify_token(self._req(), x_token="anything")
        assert exc.value.status_code == 503

    def test_mismatched_token_rejects_401(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        with pytest.raises(Exception) as exc:
            _verify_token(self._req(), x_token="wrong")
        assert exc.value.status_code == 401

    def test_none_x_token_rejects_401(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        with pytest.raises(Exception) as exc:
            _verify_token(self._req(), x_token=None)
        assert exc.value.status_code == 401

    def test_matched_token_passes(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        _verify_token(self._req(), x_token="secret123")

    def test_exceed_max_fail_triggers_429(self, monkeypatch):
        """超过失败阈值后, 该来源 IP 被临时封禁, 即使 token 正确也返回 429 (暴力破解防护)。"""
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        # 连续 5 次错误 → 触发封禁
        for _ in range(5):
            with pytest.raises(Exception) as exc:
                _verify_token(self._req("10.9.9.9"), x_token="wrong")
            assert exc.value.status_code == 401
        # 封禁后: 正确 token 也应被 429 拒绝
        with pytest.raises(Exception) as exc:
            _verify_token(self._req("10.9.9.9"), x_token="secret123")
        assert exc.value.status_code == 429

    def test_different_ip_not_locked(self, monkeypatch):
        """封禁只针对攻击来源 IP, 不影响其他来源 IP 正常鉴权。"""
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        # 来源 IP .77 触发封禁 (超过阈值后被 429 拒绝)
        for i in range(6):
            with pytest.raises(Exception) as exc:
                _verify_token(self._req("10.0.0.77"), x_token="wrong")
            assert exc.value.status_code == (429 if i >= 5 else 401)
        # 其他来源 IP .88 完全不受影响, 正确 token 正常放行
        _verify_token(self._req("10.0.0.88"), x_token="secret123")

    def test_success_clears_fail_count(self, monkeypatch):
        """鉴权成功后清零该 IP 失败计数, 不累计封禁。"""
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        for _ in range(4):
            with pytest.raises(Exception) as exc:
                _verify_token(self._req("10.0.0.66"), x_token="wrong")
            assert exc.value.status_code == 401
        # 一次成功 → 失败计数清零
        _verify_token(self._req("10.0.0.66"), x_token="secret123")
        # 再来 4 次失败也不触发封禁
        for _ in range(4):
            with pytest.raises(Exception) as exc:
                _verify_token(self._req("10.0.0.66"), x_token="wrong")
            assert exc.value.status_code == 401


# ============================================================
# _verify_ip IP 白名单 (fail-open)
# ============================================================


class TestVerifyIp:
    """IP 校验: 空白名单放行 (兼容默认), 白名单内放行, 白名单外 403。"""

    def _mock_request(self, host: str) -> MagicMock:
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = host
        return req

    def test_empty_whitelist_allows_fail_open(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_ALLOWED_IPS", raising=False)
        _verify_ip(self._mock_request("10.0.0.99"))

    def test_ip_in_whitelist_allows(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", "10.0.0.1,10.0.0.2")
        _verify_ip(self._mock_request("10.0.0.1"))

    def test_ip_not_in_whitelist_rejects_403(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", "10.0.0.1,10.0.0.2")
        with pytest.raises(Exception) as exc:
            _verify_ip(self._mock_request("10.0.0.99"))
        assert exc.value.status_code == 403

    def test_whitelist_with_spaces_allows(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", " 10.0.0.1 , 10.0.0.2 ")
        _verify_ip(self._mock_request("10.0.0.2"))


# ============================================================
# _client_ip 真实客户端 IP 解析 (可信反代头, 默认关闭)
# ============================================================


class TestClientIp:
    """默认仅信直连对端 IP; QMT_RPC_TRUST_PROXY_HEADERS=1 时解析代理头。

    修复: 反代之后所有请求共享对端 IP, 导致暴力破解封禁与 IP 白名单失效。
    """

    def _req(self, host="10.0.0.1", headers=None) -> MagicMock:
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = host
        store = dict(headers or {})
        req.headers.get = lambda key, default="": store.get(key, default)
        return req

    def test_default_ignores_proxy_headers(self, monkeypatch):
        """未开启开关: 即使带 X-Forwarded-For 也只用直连对端地址 (可信头默认关闭)。"""
        monkeypatch.delenv("QMT_RPC_TRUST_PROXY_HEADERS", raising=False)
        req = self._req(headers={"x-forwarded-for": "1.2.3.4, 10.0.0.2"})
        assert _client_ip(req) == "10.0.0.1"

    def test_env_falsy_values_still_ignore_headers(self, monkeypatch):
        """显式 '0'/'false' 等不属于信任值, 仍忽略代理头 (fail-closed 语义)。"""
        monkeypatch.setenv("QMT_RPC_TRUST_PROXY_HEADERS", "0")
        req = self._req(headers={"x-forwarded-for": "1.2.3.4"})
        assert _client_ip(req) == "10.0.0.1"

    def test_xff_leftmost_valid_taken(self, monkeypatch):
        """"XFF: client, proxy1, proxy2" → 取最左侧合法 IP (客户端真实 IP)。"""
        monkeypatch.setenv("QMT_RPC_TRUST_PROXY_HEADERS", "1")
        req = self._req(headers={"x-forwarded-for": "1.2.3.4, 10.0.0.2"})
        assert _client_ip(req) == "1.2.3.4"

    def test_xff_invalid_segments_skipped(self, monkeypatch):
        """X-Forwarded-For 中非法段跳过, 取首个合法段 (IPv6 也支持)。"""
        monkeypatch.setenv("QMT_RPC_TRUST_PROXY_HEADERS", "true")
        req = self._req(headers={"x-forwarded-for": "not-an-ip, ::1, 10.0.0.5"})
        assert _client_ip(req) == "::1"

    def test_xff_missing_falls_back_to_x_real_ip(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TRUST_PROXY_HEADERS", "yes")
        req = self._req(headers={"x-real-ip": "192.168.1.9"})
        assert _client_ip(req) == "192.168.1.9"

    def test_all_headers_invalid_falls_back_to_direct(self, monkeypatch):
        """代理头全部非法时回退直连对端地址, 不引入伪造 IP。"""
        monkeypatch.setenv("QMT_RPC_TRUST_PROXY_HEADERS", "1")
        req = self._req(
            host="10.0.0.1",
            headers={"x-forwarded-for": "garbage", "x-real-ip": "also-bad"},
        )
        assert _client_ip(req) == "10.0.0.1"

    def test_no_headers_falls_back_to_direct(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TRUST_PROXY_HEADERS", "1")
        req = self._req(host="10.0.0.7")
        assert _client_ip(req) == "10.0.0.7"


# ============================================================
# _verify_ip + 可信反代头联动 (P0-3 白名单失效修复)
# ============================================================


class TestVerifyIpWithTrustedProxyHeaders:
    """开启可信反代头后, IP 白名单按真实客户端 IP 校验, 不再全员共享反代对端 IP。"""

    def test_whitelist_checked_against_real_client_ip(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", "10.0.0.1")
        monkeypatch.setenv("QMT_RPC_TRUST_PROXY_HEADERS", "1")
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "127.0.0.1"  # 反代对端
        store = {"x-forwarded-for": "10.0.0.1"}
        req.headers.get = lambda key, default="": store.get(key, default)
        _verify_ip(req)  # 真实客户端在白名单 → 不抛 403

    def test_whitelist_rejects_non_matching_real_client(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_ALLOWED_IPS", "10.0.0.1")
        monkeypatch.setenv("QMT_RPC_TRUST_PROXY_HEADERS", "1")
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        store = {"x-forwarded-for": "10.0.0.99"}
        req.headers.get = lambda key, default="": store.get(key, default)
        with pytest.raises(Exception) as exc:
            _verify_ip(req)
        assert exc.value.status_code == 403
