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
    """Token 鉴权: 未配置→503, 不匹配→401, 匹配→放行。"""

    def test_no_token_configured_rejects_503(self, monkeypatch):
        monkeypatch.delenv("QMT_RPC_TOKEN", raising=False)
        with pytest.raises(Exception) as exc:
            _verify_token(x_token="anything")
        assert exc.value.status_code == 503

    def test_empty_token_configured_rejects_503(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "")
        with pytest.raises(Exception) as exc:
            _verify_token(x_token="anything")
        assert exc.value.status_code == 503

    def test_mismatched_token_rejects_401(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        with pytest.raises(Exception) as exc:
            _verify_token(x_token="wrong")
        assert exc.value.status_code == 401

    def test_none_x_token_rejects_401(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        with pytest.raises(Exception) as exc:
            _verify_token(x_token=None)
        assert exc.value.status_code == 401

    def test_matched_token_passes(self, monkeypatch):
        monkeypatch.setenv("QMT_RPC_TOKEN", "secret123")
        _verify_token(x_token="secret123")


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
