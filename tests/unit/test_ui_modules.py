"""UI 模块单元测试 — 终极量化交易系统 8.4 (T5.6).

任务: T5.6
责任层: L7 归因 + 前端

测试范围:
    - ui.auth: 密码哈希 / 鉴权流程 / 配置加载
    - ui.data_loader: JSON/YAML/JSONL 读取 / 路径解析 / 盘中时段判断
    - ui.layout: 格式化工具 / 状态色推断

设计原则:
    1. 纯 Python 函数测试, 不依赖 Streamlit 运行时上下文
    2. 测试覆盖率 >= 70%
    3. 不测试 st.* 渲染函数 (无法在测试环境运行)
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ui import FLAG_NAME, __version__
from ui import data_loader as dl
from ui import layout as layout_module
from ui.auth import (
    DEFAULT_SESSION_TTL,
    AuthConfig,
    authenticate,
    check_session_valid,
    hash_password,
    is_auth_required,
    is_production_env,
    is_ui_enabled,
    load_auth_config,
    verify_password,
)
from ui.data_loader import (
    DEFAULT_CACHE_TTL,
    INTRADAY_CACHE_TTL,
    file_exists,
    find_latest_file,
    format_date,
    get_cache_ttl,
    get_project_root,
    is_intraday_hours,
    list_files,
    read_json,
    read_jsonl,
    read_text,
    read_yaml,
    resolve_path,
    today_str,
)
from ui.layout import (
    COLOR_CRITICAL,
    COLOR_MUTED,
    COLOR_NORMAL,
    COLOR_WARNING,
    format_currency,
    format_number,
    format_percent,
    get_status_from_value,
)
from ui.pages import PAGE_REGISTRY

# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_json_file(tmp_path: Path) -> Path:
    """创建临时 JSON 文件."""
    p = tmp_path / "test.json"
    p.write_text(json.dumps({"key": "value", "num": 42}), encoding="utf-8")
    return p


@pytest.fixture
def tmp_yaml_file(tmp_path: Path) -> Path:
    """创建临时 YAML 文件."""
    p = tmp_path / "test.yaml"
    p.write_text("name: test\nvalue: 100\n", encoding="utf-8")
    return p


@pytest.fixture
def tmp_jsonl_file(tmp_path: Path) -> Path:
    """创建临时 JSONL 文件."""
    p = tmp_path / "test.jsonl"
    lines = [
        json.dumps({"id": 1, "msg": "first"}),
        "",
        json.dumps({"id": 2, "msg": "second"}),
        json.dumps({"id": 3, "msg": "third"}),
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


@pytest.fixture
def temp_project_root(tmp_path: Path, monkeypatch):
    """临时项目根目录 (用于隔离 data_loader 的 _PROJECT_ROOT)."""
    monkeypatch.setattr(dl, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(dl, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(dl, "CONFIG_DIR", tmp_path / "config")
    return tmp_path


# ============================================================
# ui 包初始化测试
# ============================================================

class TestUIPackage:
    """测试 ui 包级 API."""

    def test_flag_name(self):
        """FLAG_NAME 应为 USE_STREAMLIT_UI."""
        assert FLAG_NAME == "USE_STREAMLIT_UI"

    def test_version(self):
        """__version__ 应为 1.0.0."""
        assert __version__ == "1.0.0"

    def test_page_registry_has_14_pages(self):
        """PAGE_REGISTRY 应包含 14 个页面."""
        assert len(PAGE_REGISTRY) == 14

    def test_page_registry_keys(self):
        """PAGE_REGISTRY 应包含预期的页面键."""
        expected_keys = {
            "dashboard", "trade_plan", "positions", "risk",
            "attribution", "brinson", "barra", "tca",
            "shadow", "backtest", "macro", "sector",
            "logs", "config",
        }
        assert set(PAGE_REGISTRY.keys()) == expected_keys


# ============================================================
# ui.auth 模块测试
# ============================================================

class TestPasswordHashing:
    """密码哈希测试."""

    def test_hash_password_returns_sha256_hex(self):
        """hash_password 应返回 64 字符 SHA-256 十六进制."""
        h = hash_password("test123")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_password_deterministic(self):
        """相同密码应生成相同哈希."""
        assert hash_password("hello") == hash_password("hello")

    def test_hash_password_different_for_different_inputs(self):
        """不同密码应生成不同哈希."""
        assert hash_password("a") != hash_password("b")

    def test_hash_password_unicode(self):
        """支持 Unicode 密码."""
        h = hash_password("密码123")
        assert len(h) == 64

    def test_hash_password_raises_on_non_string(self):
        """非字符串输入应抛出 TypeError."""
        with pytest.raises(TypeError):
            hash_password(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            hash_password(None)  # type: ignore[arg-type]

    def test_verify_password_correct(self):
        """正确密码应校验通过."""
        h = hash_password("my_secret")
        assert verify_password("my_secret", h) is True

    def test_verify_password_wrong(self):
        """错误密码应校验失败."""
        h = hash_password("my_secret")
        assert verify_password("wrong", h) is False

    def test_verify_password_empty_inputs(self):
        """空输入应返回 False."""
        assert verify_password("", "somelonghash") is False
        assert verify_password("user", "") is False
        assert verify_password("", "") is False


class TestAuthConfig:
    """AuthConfig 数据类测试."""

    def test_default_values(self):
        """默认值应符合预期."""
        cfg = AuthConfig()
        assert cfg.enabled is None
        assert cfg.require_in_production is True
        assert cfg.credentials == []
        assert cfg.session_ttl_seconds == DEFAULT_SESSION_TTL

    def test_to_dict(self):
        """to_dict 应返回字典."""
        cfg = AuthConfig(enabled=True, credentials=[{"username": "a"}])
        d = cfg.to_dict()
        assert d["enabled"] is True
        assert d["n_credentials"] == 1
        assert d["session_ttl_seconds"] == DEFAULT_SESSION_TTL


class TestAuthFunctions:
    """鉴权核心函数测试."""

    def test_is_production_env_default_false(self, monkeypatch):
        """默认非生产环境."""
        monkeypatch.delenv("TRADING_ENV", raising=False)
        assert is_production_env() is False

    def test_is_production_env_true(self, monkeypatch):
        """TRADING_ENV=production 时为 True."""
        monkeypatch.setenv("TRADING_ENV", "production")
        assert is_production_env() is True

    def test_is_production_env_case_insensitive(self, monkeypatch):
        """大小写不敏感."""
        monkeypatch.setenv("TRADING_ENV", "PRODUCTION")
        assert is_production_env() is True

    def test_is_production_env_other_values(self, monkeypatch):
        """其他值应为 False."""
        for v in ["development", "test", "staging", ""]:
            monkeypatch.setenv("TRADING_ENV", v)
            assert is_production_env() is False

    def test_is_auth_required_explicit_enabled(self):
        """显式 enabled=True 应返回 True."""
        cfg = AuthConfig(enabled=True)
        assert is_auth_required(cfg) is True

    def test_is_auth_required_explicit_disabled(self):
        """显式 enabled=False 应返回 False."""
        cfg = AuthConfig(enabled=False)
        assert is_auth_required(cfg) is False

    def test_is_auth_required_production(self, monkeypatch):
        """生产环境默认强制鉴权."""
        monkeypatch.setenv("TRADING_ENV", "production")
        cfg = AuthConfig(enabled=None, require_in_production=True)
        assert is_auth_required(cfg) is True

    def test_is_auth_required_production_disabled(self, monkeypatch):
        """生产环境可配置为不强制."""
        monkeypatch.setenv("TRADING_ENV", "production")
        cfg = AuthConfig(enabled=None, require_in_production=False)
        assert is_auth_required(cfg) is False

    def test_is_auth_required_dev_default_false(self, monkeypatch):
        """开发环境默认不强制."""
        monkeypatch.delenv("TRADING_ENV", raising=False)
        cfg = AuthConfig(enabled=None)
        assert is_auth_required(cfg) is False

    def test_authenticate_success(self):
        """正确凭据应鉴权成功."""
        h = hash_password("pass123")
        cfg = AuthConfig(credentials=[{"username": "admin", "password_hash": h}])
        assert authenticate("admin", "pass123", cfg) is True

    def test_authenticate_wrong_password(self):
        """错误密码应鉴权失败."""
        h = hash_password("pass123")
        cfg = AuthConfig(credentials=[{"username": "admin", "password_hash": h}])
        assert authenticate("admin", "wrong", cfg) is False

    def test_authenticate_unknown_user(self):
        """未知用户应鉴权失败."""
        h = hash_password("pass123")
        cfg = AuthConfig(credentials=[{"username": "admin", "password_hash": h}])
        assert authenticate("unknown", "pass123", cfg) is False

    def test_authenticate_empty_inputs(self):
        """空输入应失败."""
        cfg = AuthConfig(credentials=[{"username": "admin", "password_hash": hash_password("p")}])
        assert authenticate("", "p", cfg) is False
        assert authenticate("admin", "", cfg) is False

    def test_authenticate_no_credentials(self):
        """无凭据时应失败."""
        cfg = AuthConfig(credentials=[])
        assert authenticate("admin", "pass", cfg) is False

    def test_check_session_valid_fresh(self):
        """刚登录的会话应有效."""
        now = time.time()
        assert check_session_valid(now, ttl=3600) is True

    def test_check_session_valid_expired(self):
        """过期会话应无效."""
        old = time.time() - 7200  # 2 小时前
        assert check_session_valid(old, ttl=3600) is False

    def test_check_session_valid_zero_login_time(self):
        """login_at=0 应无效."""
        assert check_session_valid(0, ttl=3600) is False

    def test_check_session_valid_negative_login_time(self):
        """负数 login_at 应无效."""
        assert check_session_valid(-1, ttl=3600) is False

    def test_check_session_valid_custom_now(self):
        """支持自定义 now 参数."""
        login_at = 1000.0
        assert check_session_valid(login_at, ttl=100, now=1050) is True
        assert check_session_valid(login_at, ttl=100, now=1200) is False


class TestAuthConfigLoading:
    """鉴权配置加载测试."""

    def test_load_auth_config_returns_authconfig(self):
        """load_auth_config 应返回 AuthConfig 实例."""
        cfg = load_auth_config()
        assert isinstance(cfg, AuthConfig)

    def test_load_auth_config_default_values(self):
        """无配置时应使用默认值."""
        cfg = load_auth_config()
        assert cfg.session_ttl_seconds == DEFAULT_SESSION_TTL
        assert cfg.require_in_production is True

    def test_load_auth_config_with_mock(self):
        """通过 mock 测试配置解析."""
        mock_raw = {
            "enabled": True,
            "require_in_production": False,
            "credentials": [{"username": "admin", "password_hash": "abc"}],
            "session_ttl_seconds": 1800,
        }
        with patch("ui.auth.get_config", return_value=mock_raw):
            cfg = load_auth_config()
        assert cfg.enabled is True
        assert cfg.require_in_production is False
        assert len(cfg.credentials) == 1
        assert cfg.session_ttl_seconds == 1800

    def test_load_auth_config_exception_fallback(self):
        """配置加载异常时应使用默认值."""
        with patch("ui.auth.get_config", side_effect=Exception("mock error")):
            cfg = load_auth_config()
        assert isinstance(cfg, AuthConfig)
        assert cfg.enabled is None  # 默认

    def test_load_auth_config_empty_dict(self):
        """空字典配置应使用默认值."""
        with patch("ui.auth.get_config", return_value={}):
            cfg = load_auth_config()
        assert cfg.enabled is None
        assert cfg.session_ttl_seconds == DEFAULT_SESSION_TTL

    def test_load_auth_config_none_return(self):
        """get_config 返回 None 时应使用默认值."""
        with patch("ui.auth.get_config", return_value=None):
            cfg = load_auth_config()
        assert isinstance(cfg, AuthConfig)


class TestUIEnabled:
    """UI 启用状态测试."""

    def test_is_ui_enabled_returns_bool(self):
        """is_ui_enabled 应返回布尔值."""
        result = is_ui_enabled()
        assert isinstance(result, bool)

    def test_is_ui_enabled_with_mock(self):
        """通过 mock 测试 Flag 启用."""
        with patch("ui.auth.is_enabled", return_value=True):
            assert is_ui_enabled() is True
        with patch("ui.auth.is_enabled", return_value=False):
            assert is_ui_enabled() is False


# ============================================================
# ui.data_loader 模块测试
# ============================================================

class TestPathResolution:
    """路径解析测试."""

    def test_get_project_root_returns_path(self):
        """get_project_root 应返回 Path."""
        p = get_project_root()
        assert isinstance(p, Path)
        assert p.exists()

    def test_resolve_path_absolute(self):
        """绝对路径应原样返回."""
        abs_path = Path("C:/some/absolute/path")
        result = resolve_path(abs_path)
        assert result == abs_path

    def test_resolve_path_relative(self):
        """相对路径应基于项目根目录."""
        result = resolve_path("reports/test.json")
        assert result.is_absolute()
        assert result.name == "test.json"

    def test_file_exists_true(self, tmp_json_file: Path):
        """存在的文件应返回 True."""
        assert file_exists(tmp_json_file) is True

    def test_file_exists_false(self):
        """不存在的文件应返回 False."""
        assert file_exists("/nonexistent/path/file.json") is False


class TestDateFormatting:
    """日期格式化测试."""

    def test_today_str_default_format(self):
        """today_str 默认格式应为 YYYY-MM-DD."""
        result = today_str()
        assert len(result) == 10
        assert result[4] == "-"
        assert result[7] == "-"

    def test_today_str_custom_format(self):
        """today_str 应支持自定义格式."""
        result = today_str("%Y%m%d")
        assert len(result) == 8

    def test_format_date_string_passthrough(self):
        """字符串日期应原样返回."""
        assert format_date("2026-07-27") == "2026-07-27"

    def test_format_date_datetime(self):
        """datetime 应按格式转换."""
        dt = datetime(2026, 7, 27, 10, 30)
        assert format_date(dt) == "2026-07-27"
        assert format_date(dt, "%Y%m%d") == "20260727"

    def test_format_date_date(self):
        """date 应按格式转换."""
        from datetime import date as date_cls
        d = date_cls(2026, 7, 27)
        assert format_date(d) == "2026-07-27"

    def test_format_date_invalid_type(self):
        """不支持的类型应抛出 TypeError."""
        with pytest.raises(TypeError):
            format_date(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            format_date(None)  # type: ignore[arg-type]


class TestFileReaders:
    """文件读取函数测试."""

    def test_read_json_success(self, tmp_json_file: Path):
        """read_json 应正确解析 JSON."""
        data = read_json(tmp_json_file)
        assert data == {"key": "value", "num": 42}

    def test_read_json_default_when_missing(self):
        """文件不存在时应返回 default."""
        assert read_json("/nonexistent/file.json", default=None) is None
        assert read_json("/nonexistent/file.json", default={}) == {}

    def test_read_json_default_when_invalid(self, tmp_path: Path):
        """JSON 解析失败时应返回 default."""
        p = tmp_path / "bad.json"
        p.write_text("{invalid json", encoding="utf-8")
        assert read_json(p, default=None) is None

    def test_read_yaml_success(self, tmp_yaml_file: Path):
        """read_yaml 应正确解析 YAML."""
        data = read_yaml(tmp_yaml_file)
        assert data == {"name": "test", "value": 100}

    def test_read_yaml_default_when_missing(self):
        """YAML 文件不存在时应返回 default."""
        assert read_yaml("/nonexistent/file.yaml", default=None) is None

    def test_read_jsonl_success(self, tmp_jsonl_file: Path):
        """read_jsonl 应正确解析 JSONL."""
        data = read_jsonl(tmp_jsonl_file)
        assert len(data) == 3  # 空行应被跳过
        assert data[0] == {"id": 1, "msg": "first"}
        assert data[2] == {"id": 3, "msg": "third"}

    def test_read_jsonl_default_when_missing(self):
        """JSONL 文件不存在时应返回 default."""
        result = read_jsonl("/nonexistent/file.jsonl", default=[])
        assert result == []

    def test_read_jsonl_default_list(self):
        """JSONL 默认 default 应为空列表."""
        result = read_jsonl("/nonexistent/file.jsonl")
        assert result == []

    def test_read_text_success(self, tmp_path: Path):
        """read_text 应正确读取文本."""
        p = tmp_path / "test.txt"
        p.write_text("hello world", encoding="utf-8")
        assert read_text(p) == "hello world"

    def test_read_text_default_when_missing(self):
        """文本文件不存在时应返回 default."""
        assert read_text("/nonexistent/file.txt", default="") == ""
        assert read_text("/nonexistent/file.txt", default="fallback") == "fallback"


class TestFileListing:
    """文件列举函数测试."""

    def test_list_files_returns_list(self, tmp_path: Path):
        """list_files 应返回列表."""
        # 创建一些测试文件
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "a.json").write_text("{}", encoding="utf-8")
        (tmp_path / "subdir" / "b.json").write_text("{}", encoding="utf-8")

        results = list_files("subdir/*.json", directory=tmp_path)
        assert len(results) == 2
        assert all(isinstance(p, Path) for p in results)

    def test_list_files_empty_when_no_match(self, tmp_path: Path):
        """无匹配时应返回空列表."""
        results = list_files("nonexistent/*.json", directory=tmp_path)
        assert results == []

    def test_find_latest_file_returns_path(self, tmp_path: Path):
        """find_latest_file 应返回最新文件."""
        (tmp_path / "logs").mkdir()
        f1 = tmp_path / "logs" / "a.json"
        f1.write_text("{}", encoding="utf-8")
        time.sleep(0.05)
        f2 = tmp_path / "logs" / "b.json"
        f2.write_text("{}", encoding="utf-8")

        latest = find_latest_file("logs/*.json", directory=tmp_path)
        assert latest is not None
        assert latest.name == "b.json"

    def test_find_latest_file_returns_none_when_empty(self, tmp_path: Path):
        """无文件时应返回 None."""
        assert find_latest_file("nonexistent/*.json", directory=tmp_path) is None


class TestIntradayHours:
    """盘中时段判断测试."""

    def test_is_intraday_hours_returns_bool(self):
        """is_intraday_hours 应返回布尔值."""
        assert isinstance(is_intraday_hours(), bool)

    def test_is_intraday_hours_weekday_morning(self):
        """工作日盘中时段应为 True."""
        # 周一 10:30
        dt = datetime(2026, 7, 27, 10, 30)  # 周一
        with patch("ui.data_loader.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert is_intraday_hours() is True

    def test_is_intraday_hours_weekday_afternoon(self):
        """工作日下午时段应为 True."""
        dt = datetime(2026, 7, 27, 14, 0)  # 周一 14:00
        with patch("ui.data_loader.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert is_intraday_hours() is True

    def test_is_intraday_hours_weekday_outside_hours(self):
        """工作日非盘中时段应为 False."""
        dt = datetime(2026, 7, 27, 16, 0)  # 周一 16:00
        with patch("ui.data_loader.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert is_intraday_hours() is False

    def test_is_intraday_hours_weekend(self):
        """周末应为 False."""
        dt = datetime(2026, 8, 1, 10, 30)  # 周六
        with patch("ui.data_loader.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            assert is_intraday_hours() is False


class TestCacheTTL:
    """缓存 TTL 测试."""

    def test_get_cache_ttl_default(self):
        """非盘中模式应返回默认 TTL."""
        assert get_cache_ttl(intraday_mode=False) == DEFAULT_CACHE_TTL

    def test_get_cache_ttl_intraday_during_hours(self):
        """盘中模式且在盘中时段应返回 1."""
        with patch("ui.data_loader.is_intraday_hours", return_value=True):
            assert get_cache_ttl(intraday_mode=True) == INTRADAY_CACHE_TTL

    def test_get_cache_ttl_intraday_outside_hours(self):
        """盘中模式但不在盘中时段应返回默认 TTL."""
        with patch("ui.data_loader.is_intraday_hours", return_value=False):
            assert get_cache_ttl(intraday_mode=True) == DEFAULT_CACHE_TTL


class TestBusinessLoaders:
    """业务专用加载函数测试."""

    def test_load_attribution_panel_returns_none_when_missing(self, temp_project_root):
        """归因面板不存在时应返回 None."""
        result = dl.load_attribution_panel()
        assert result is None

    def test_load_attribution_panel_with_date(self, temp_project_root):
        """支持指定日期."""
        result = dl.load_attribution_panel("2026-01-01")
        assert result is None

    def test_load_shadow_state_returns_none_when_missing(self, temp_project_root):
        """Shadow 状态不存在时应返回 None."""
        result = dl.load_shadow_state()
        assert result is None

    def test_load_tca_fills_returns_empty_when_missing(self, temp_project_root):
        """TCA 文件不存在时应返回空列表."""
        result = dl.load_tca_fills()
        assert result == []

    def test_load_risk_bus_events_returns_empty_when_missing(self, temp_project_root):
        """风险事件文件不存在时应返回空列表."""
        result = dl.load_risk_bus_events()
        assert result == []

    def test_load_feature_flags_config_returns_dict(self):
        """Feature Flags 配置应返回字典."""
        result = dl.load_feature_flags_config()
        assert isinstance(result, dict)

    def test_load_config_file_returns_dict(self):
        """配置文件加载应返回字典."""
        result = dl.load_config_file("nonexistent_config")
        assert isinstance(result, dict)
        assert result == {}

    def test_load_attribution_panel_reads_existing(self, temp_project_root):
        """存在归因面板时应正确读取."""
        # 创建测试文件
        attr_dir = temp_project_root / "reports" / "attribution"
        attr_dir.mkdir(parents=True)
        test_data = {"generation_time_ms": 50.0, "brinson": {"status": "ok"}}
        test_file = attr_dir / "daily_panel_2026-07-27.json"
        test_file.write_text(json.dumps(test_data), encoding="utf-8")

        result = dl.load_attribution_panel("2026-07-27")
        assert result == test_data


# ============================================================
# ui.layout 模块测试
# ============================================================

class TestFormatFunctions:
    """格式化函数测试."""

    def test_format_percent_positive(self):
        """正数百分比应带 + 号."""
        assert format_percent(0.046) == "+4.60%"

    def test_format_percent_negative(self):
        """负数百分比应带 - 号."""
        assert format_percent(-0.031) == "-3.10%"

    def test_format_percent_zero(self):
        """零应返回 +0.00%."""
        assert format_percent(0) == "+0.00%"

    def test_format_percent_custom_decimals(self):
        """支持自定义小数位."""
        assert format_percent(0.046, decimals=4) == "+4.6000%"

    def test_format_percent_invalid_input(self):
        """无效输入应返回 -."""
        assert format_percent(None) == "-"
        assert format_percent("abc") == "-"
        assert format_percent(float("nan")) == "-"
        assert format_percent(float("inf")) == "-"

    def test_format_number_basic(self):
        """基础数字格式化."""
        assert format_number(1234.5678) == "1,234.57"

    def test_format_number_custom_decimals(self):
        """自定义小数位 (Python 默认 banker's rounding)."""
        # 1234.5 → 1234 (banker's rounding, round-half-to-even)
        assert format_number(1234.5, decimals=0) == "1,234"
        # 1235.5 → 1236 (banker's rounding)
        assert format_number(1235.5, decimals=0) == "1,236"

    def test_format_number_nan_inf(self):
        """NaN/Inf 应返回 -."""
        assert format_number(float("nan")) == "-"
        assert format_number(float("inf")) == "-"

    def test_format_number_invalid_input(self):
        """无效输入应返回 -."""
        assert format_number(None) == "-"
        assert format_number("abc") == "-"

    def test_format_currency_basic(self):
        """基础货币格式化."""
        assert format_currency(5023000) == "¥5,023,000.00"

    def test_format_currency_custom_symbol(self):
        """自定义货币符号."""
        assert format_currency(100, symbol="$") == "$100.00"

    def test_format_currency_invalid_input(self):
        """无效输入应返回 -."""
        assert format_currency(None) == "-"


class TestStatusFromValue:
    """状态色推断测试."""

    def test_get_status_higher_is_worse_normal(self):
        """低值应为 NORMAL."""
        assert get_status_from_value(0.1) == "NORMAL"

    def test_get_status_higher_is_worse_warning(self):
        """中值应为 WARNING."""
        assert get_status_from_value(0.6) == "WARNING"

    def test_get_status_higher_is_worse_critical(self):
        """高值应为 CRITICAL."""
        assert get_status_from_value(0.9) == "CRITICAL"

    def test_get_status_lower_is_worse_normal(self):
        """低值应为 CRITICAL (反向)."""
        assert get_status_from_value(0.1, higher_is_worse=False) == "CRITICAL"

    def test_get_status_lower_is_worse_critical(self):
        """高值应为 NORMAL (反向)."""
        assert get_status_from_value(0.9, higher_is_worse=False) == "NORMAL"

    def test_get_status_invalid_input(self):
        """无效输入应返回 DISABLED."""
        assert get_status_from_value(None) == "DISABLED"
        assert get_status_from_value("abc") == "DISABLED"

    def test_get_status_custom_thresholds(self):
        """支持自定义阈值."""
        # warning=0.3, critical=0.5
        assert get_status_from_value(0.4, warning_threshold=0.3, critical_threshold=0.5) == "WARNING"
        assert get_status_from_value(0.6, warning_threshold=0.3, critical_threshold=0.5) == "CRITICAL"


class TestLayoutConstants:
    """布局常量测试."""

    def test_status_colors_complete(self):
        """STATUS_COLORS 应包含所有状态."""
        expected = {"NORMAL", "WARNING", "CRITICAL", "INFO", "DISABLED"}
        assert set(layout_module.STATUS_COLORS.keys()) == expected

    def test_color_normal_is_green(self):
        """NORMAL 色应为绿色."""
        assert COLOR_NORMAL == "#28a745"

    def test_color_warning_is_yellow(self):
        """WARNING 色应为黄色."""
        assert COLOR_WARNING == "#ffc107"

    def test_color_critical_is_red(self):
        """CRITICAL 色应为红色."""
        assert COLOR_CRITICAL == "#dc3545"

    def test_color_muted_is_gray(self):
        """MUTED 色应为灰色."""
        assert COLOR_MUTED == "#6c757d"


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
