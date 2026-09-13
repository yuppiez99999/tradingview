"""test_config_manager_unit.py — 统一配置管理器单元测试

覆盖要点:
    - _NAMED_CONFIGS 注册表
    - _build_search_paths (环境变量/默认路径)
    - ConfigManager 构造 (project_root / extra_search_paths)
    - 单例 (get_instance / reset_instance)
    - _resolve_config_path (短名/全名/未找到)
    - _load_yaml (正常/空文件/非 dict/损坏)
    - _get_cached (命中/mtime 失效/文件删除)
    - get (加载/缓存/default/未找到)
    - 类型化访问器 (get_kill_switch_config 等)
    - list_available / get_config_source / clear_cache / reload
    - 模块级快捷函数
"""

from __future__ import annotations

import os
import time

import pytest
import yaml

from utils.config_manager import (
    ConfigManager,
    clear_config_cache,
    get_backtest_config,
    get_config,
    get_config_source,
    get_kill_switch_config,
    get_portfolio_config,
    get_risk_budget_config,
    get_risk_params_config,
    get_settings_config,
    get_stop_loss_config,
    list_available_configs,
)

# ============================================================
# fixture: 测试用配置目录
# ============================================================


@pytest.fixture(autouse=True)
def _isolate_degradation_log(tmp_path, monkeypatch):
    """隔离降级审计日志 (2026-09-02 巡检 P3-1b)

    ConfigManager.get 未命中时内部调用 record_degradation 写真实
    reports/degradation_log.jsonl — get("nonexistent") 等测试键曾污染
    生产 health score 的 config_missing_keys 明细。重定向 LOG_FILE 到
    tmp_path 并清进程内去重标记, 测试副作用全隔离。
    """
    from utils import degradation_audit

    monkeypatch.setattr(
        degradation_audit, "LOG_FILE", tmp_path / "degradation_log.jsonl"
    )
    degradation_audit.reset_dedupe()
    yield
    degradation_audit.reset_dedupe()


@pytest.fixture
def config_dir(tmp_path):
    """创建临时配置目录。

    2026-09-13 映射修正后的契约:
      - "portfolio" → portfolio.yaml (主业务: fallback_prices/positions/hedge)
      - "account_structure" → account_structure.yaml (v7.7: kill_switch/assets)
      - kill_switch 独立文件 kill_switch.yaml (get_kill_switch_config 的回退源)
    """
    d = tmp_path / "config"
    d.mkdir()
    (d / "account_structure.yaml").write_text(
        yaml.dump({"kill_switch": {"L1": 0.10, "L2": 0.15}, "assets": ["stock_a"]}),
        encoding="utf-8",
    )
    (d / "portfolio.yaml").write_text(
        yaml.dump(
            {
                "fallback_prices": {
                    "last_updated": "2026-09-13",
                    "prices": {"600519": 1800.0},
                },
                "positions": {"600519.SH": {"shares": 100}},
                "hedge": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    (d / "kill_switch.yaml").write_text(
        yaml.dump({"L1": 0.10, "L2": 0.15}),
        encoding="utf-8",
    )
    (d / "settings.yaml").write_text(
        yaml.dump({"log_level": "DEBUG", "data_source": "tushare"}),
        encoding="utf-8",
    )
    (d / "backtest.yaml").write_text(
        yaml.dump({"start": "2020-01-01", "initial_capital": 1_000_000}),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def manager(config_dir, tmp_path):
    """用 extra_search_paths 注入测试配置目录的 ConfigManager.
    project_root 设为 tmp_path 以隔离真实项目配置目录."""
    ConfigManager.reset_instance()
    m = ConfigManager(project_root=tmp_path, extra_search_paths=[config_dir])
    yield m
    ConfigManager.reset_instance()


# ============================================================
# _NAMED_CONFIGS
# ============================================================


class TestNamedConfigs:
    @pytest.mark.unit
    def test_has_portfolio(self):
        from utils.config_manager import _NAMED_CONFIGS

        # 2026-09-13 映射修正: "portfolio" → portfolio.yaml (主业务配置)
        assert _NAMED_CONFIGS["portfolio"] == "portfolio.yaml"

    @pytest.mark.unit
    def test_has_account_structure(self):
        from utils.config_manager import _NAMED_CONFIGS

        # v7.7 账户结构独立命名, 消除同名歧义
        assert _NAMED_CONFIGS["account_structure"] == "account_structure.yaml"

    @pytest.mark.unit
    def test_has_settings(self):
        from utils.config_manager import _NAMED_CONFIGS

        assert _NAMED_CONFIGS["settings"] == "settings.yaml"

    @pytest.mark.unit
    def test_has_backtest(self):
        from utils.config_manager import _NAMED_CONFIGS

        assert _NAMED_CONFIGS["backtest"] == "backtest.yaml"

    @pytest.mark.unit
    def test_has_risk_params(self):
        from utils.config_manager import _NAMED_CONFIGS

        assert _NAMED_CONFIGS["risk_params"] == "risk_params.yaml"


# ============================================================
# _build_search_paths
# ============================================================


class TestBuildSearchPaths:
    @pytest.mark.unit
    def test_env_var_included(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_path))
        from utils.config_manager import _build_search_paths

        paths = _build_search_paths()
        assert tmp_path.resolve() in paths

    @pytest.mark.unit
    def test_no_env_var(self, monkeypatch):
        monkeypatch.delenv("QUANT_CONFIG_DIR", raising=False)
        from utils.config_manager import _build_search_paths

        paths = _build_search_paths()
        # 至少应包含项目默认路径 (v8.3_institutional/config 或 configs)
        assert isinstance(paths, list)


# ============================================================
# ConfigManager 构造
# ============================================================


class TestInit:
    @pytest.mark.unit
    def test_default_init(self):
        ConfigManager.reset_instance()
        m = ConfigManager()
        assert m._cache == {}
        assert isinstance(m._search_paths, list)
        ConfigManager.reset_instance()

    @pytest.mark.unit
    def test_extra_search_paths_first(self, config_dir):
        ConfigManager.reset_instance()
        m = ConfigManager(extra_search_paths=[config_dir])
        # extra_search_paths 应排在最前
        assert m._search_paths[0] == config_dir
        ConfigManager.reset_instance()

    @pytest.mark.unit
    def test_custom_project_root(self, tmp_path):
        ConfigManager.reset_instance()
        m = ConfigManager(project_root=tmp_path)
        assert m._project_root == tmp_path
        ConfigManager.reset_instance()


# ============================================================
# 单例
# ============================================================


class TestSingleton:
    @pytest.mark.unit
    def test_get_instance_same(self):
        ConfigManager.reset_instance()
        a = ConfigManager.get_instance()
        b = ConfigManager.get_instance()
        assert a is b
        ConfigManager.reset_instance()

    @pytest.mark.unit
    def test_reset_instance(self):
        a = ConfigManager.get_instance()
        ConfigManager.reset_instance()
        b = ConfigManager.get_instance()
        assert a is not b
        ConfigManager.reset_instance()


# ============================================================
# _resolve_config_path
# ============================================================


class TestResolveConfigPath:
    @pytest.mark.unit
    def test_short_name(self, manager, config_dir):
        path = manager._resolve_config_path("portfolio")
        assert path is not None
        # 2026-09-13 映射修正: portfolio → portfolio.yaml
        assert path.name == "portfolio.yaml"

    @pytest.mark.unit
    def test_full_filename(self, manager, config_dir):
        path = manager._resolve_config_path("account_structure.yaml")
        assert path is not None
        assert path.name == "account_structure.yaml"

    @pytest.mark.unit
    def test_not_found(self, manager):
        path = manager._resolve_config_path("nonexistent_config")
        assert path is None

    @pytest.mark.unit
    def test_yml_extension(self, manager, config_dir):
        """完整 .yml 文件名直接使用"""
        (config_dir / "short.yml").write_text("key: val", encoding="utf-8")
        path = manager._resolve_config_path("short.yml")
        assert path is not None
        assert path.name == "short.yml"


# ============================================================
# _load_yaml
# ============================================================


class TestLoadYaml:
    @pytest.mark.unit
    def test_valid_yaml(self, manager, config_dir):
        data = manager._load_yaml(config_dir / "account_structure.yaml")
        assert "kill_switch" in data
        assert data["kill_switch"]["L1"] == 0.10

    @pytest.mark.unit
    def test_empty_file(self, manager, config_dir):
        (config_dir / "empty.yaml").write_text("", encoding="utf-8")
        data = manager._load_yaml(config_dir / "empty.yaml")
        assert data == {}

    @pytest.mark.unit
    def test_non_dict_yaml(self, manager, config_dir):
        """YAML 列表 → 返回空 dict"""
        (config_dir / "list.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
        data = manager._load_yaml(config_dir / "list.yaml")
        assert data == {}

    @pytest.mark.unit
    def test_corrupted_yaml(self, manager, config_dir):
        (config_dir / "bad.yaml").write_text(
            "invalid: [unclosed\n  - another\n", encoding="utf-8"
        )
        data = manager._load_yaml(config_dir / "bad.yaml")
        assert data == {}


# ============================================================
# _get_cached (mtime 失效)
# ============================================================


class TestGetCached:
    @pytest.mark.unit
    def test_cache_miss(self, manager):
        assert manager._get_cached("portfolio") is None

    @pytest.mark.unit
    def test_cache_hit(self, manager, config_dir):
        manager.get("portfolio")
        cached = manager._get_cached("portfolio")
        assert cached is not None
        assert "fallback_prices" in cached

    @pytest.mark.unit
    def test_mtime_invalidation(self, manager, config_dir):
        manager.get("portfolio")
        # 修改文件 mtime
        p = config_dir / "portfolio.yaml"
        time.sleep(0.05)
        os.utime(str(p), None)
        assert manager._get_cached("portfolio") is None

    @pytest.mark.unit
    def test_file_deleted(self, manager, config_dir):
        manager.get("portfolio")
        (config_dir / "portfolio.yaml").unlink()
        assert manager._get_cached("portfolio") is None


# ============================================================
# get
# ============================================================


class TestGet:
    @pytest.mark.unit
    def test_load(self, manager):
        cfg = manager.get("portfolio")
        assert "fallback_prices" in cfg

    @pytest.mark.unit
    def test_cache_returns_same(self, manager):
        a = manager.get("portfolio")
        b = manager.get("portfolio")
        # 缓存应返回同一对象
        assert a is b

    @pytest.mark.unit
    def test_not_found_returns_empty(self, manager):
        cfg = manager.get("nonexistent")
        assert cfg == {}

    @pytest.mark.unit
    def test_not_found_with_default(self, manager):
        fallback = {"fallback": True}
        cfg = manager.get("nonexistent", default=fallback)
        assert cfg == fallback

    @pytest.mark.unit
    def test_full_filename_load(self, manager):
        cfg = manager.get("account_structure.yaml")
        assert "kill_switch" in cfg


# ============================================================
# 类型化访问器
# ============================================================


class TestTypedAccessors:
    @pytest.mark.unit
    def test_get_kill_switch_config(self, manager):
        # 2026-09-13 契约: kill_switch 现来自独立 kill_switch.yaml (回退源)
        ks = manager.get_kill_switch_config()
        assert ks["L1"] == 0.10
        assert ks["L2"] == 0.15

    @pytest.mark.unit
    def test_get_portfolio_config(self, manager):
        cfg = manager.get_portfolio_config()
        assert "positions" in cfg

    @pytest.mark.unit
    def test_get_account_structure_config(self, manager):
        """2026-09-13: account_structure 独立访问器 (assets 事实源)"""
        cfg = manager.get_account_structure_config()
        assert "assets" in cfg

    @pytest.mark.unit
    def test_get_settings_config(self, manager):
        cfg = manager.get_settings_config()
        assert cfg["log_level"] == "DEBUG"

    @pytest.mark.unit
    def test_get_backtest_config(self, manager):
        cfg = manager.get_backtest_config()
        assert cfg["initial_capital"] == 1_000_000

    @pytest.mark.unit
    def test_get_risk_budget_config_empty(self, manager):
        """未创建 risk_budget.yaml → 返回空 dict"""
        cfg = manager.get_risk_budget_config()
        assert cfg == {}

    @pytest.mark.unit
    def test_get_risk_params_config_empty(self, manager):
        cfg = manager.get_risk_params_config()
        assert cfg == {}

    @pytest.mark.unit
    def test_get_stop_loss_config_empty(self, manager):
        cfg = manager.get_stop_loss_config()
        assert cfg == {}


# ============================================================
# kill_switch 回退逻辑
# ============================================================


class TestKillSwitchFallback:
    @pytest.mark.unit
    def test_fallback_to_standalone(self, tmp_path):
        """portfolio.yaml 无 kill_switch 节 (2026-09-13 新 schema 本就无) → 回退到 kill_switch.yaml"""
        ConfigManager.reset_instance()
        d = tmp_path / "myconf"
        d.mkdir()
        (d / "account_structure.yaml").write_text(yaml.dump({"assets": []}), encoding="utf-8")
        (d / "kill_switch.yaml").write_text(yaml.dump({"L1": 0.05}), encoding="utf-8")
        m = ConfigManager(project_root=tmp_path, extra_search_paths=[d])
        ks = m.get_kill_switch_config()
        assert ks["L1"] == 0.05
        ConfigManager.reset_instance()

    @pytest.mark.unit
    def test_no_kill_switch_anywhere(self, tmp_path):
        """portfolio.yaml 和 kill_switch.yaml 都无 → 返回空 dict"""
        ConfigManager.reset_instance()
        d = tmp_path / "myconf"
        d.mkdir()
        (d / "account_structure.yaml").write_text(yaml.dump({"assets": []}), encoding="utf-8")
        m = ConfigManager(project_root=tmp_path, extra_search_paths=[d])
        ks = m.get_kill_switch_config()
        assert ks == {}
        ConfigManager.reset_instance()


# ============================================================
# list_available / get_config_source
# ============================================================


class TestAudit:
    @pytest.mark.unit
    def test_list_available(self, manager):
        result = manager.list_available()
        assert isinstance(result, list)
        names = [item["name"] for item in result]
        assert "portfolio" in names
        assert "settings" in names

    @pytest.mark.unit
    def test_list_available_item_fields(self, manager):
        result = manager.list_available()
        for item in result:
            assert "name" in item
            assert "filename" in item
            assert "path" in item
            assert "size" in item

    @pytest.mark.unit
    def test_get_config_source_found(self, manager):
        src = manager.get_config_source("portfolio")
        assert src is not None
        assert "portfolio.yaml" in src

    @pytest.mark.unit
    def test_get_config_source_not_found(self, manager):
        src = manager.get_config_source("nonexistent")
        assert src is None


# ============================================================
# clear_cache / reload
# ============================================================


class TestCacheControl:
    @pytest.mark.unit
    def test_clear_cache(self, manager):
        manager.get("portfolio")
        assert len(manager._cache) > 0
        manager.clear_cache()
        assert len(manager._cache) == 0

    @pytest.mark.unit
    def test_reload(self, manager, config_dir):
        manager.get("portfolio")
        # 修改文件内容
        (config_dir / "portfolio.yaml").write_text(
            yaml.dump({"positions": {"600519.SH": {"shares": 200}}}), encoding="utf-8"
        )
        time.sleep(0.05)
        cfg = manager.reload("portfolio")
        assert cfg["positions"]["600519.SH"]["shares"] == 200


# ============================================================
# 模块级快捷函数
# ============================================================


class TestModuleFunctions:
    @pytest.fixture(autouse=True)
    def _setup_singleton(self, config_dir, tmp_path):
        """将单例替换为使用测试配置目录的实例 (隔离真实项目配置)"""
        ConfigManager.reset_instance()
        m = ConfigManager(project_root=tmp_path, extra_search_paths=[config_dir])
        ConfigManager._instance = m
        yield
        ConfigManager.reset_instance()

    @pytest.mark.unit
    def test_get_config(self):
        cfg = get_config("portfolio")
        assert "fallback_prices" in cfg

    @pytest.mark.unit
    def test_get_kill_switch_config(self):
        ks = get_kill_switch_config()
        assert ks["L1"] == 0.10

    @pytest.mark.unit
    def test_get_portfolio_config(self):
        cfg = get_portfolio_config()
        assert "positions" in cfg

    @pytest.mark.unit
    def test_get_settings_config(self):
        cfg = get_settings_config()
        assert cfg["log_level"] == "DEBUG"

    @pytest.mark.unit
    def test_get_backtest_config(self):
        cfg = get_backtest_config()
        assert cfg["initial_capital"] == 1_000_000

    @pytest.mark.unit
    def test_get_risk_budget_config(self):
        assert get_risk_budget_config() == {}

    @pytest.mark.unit
    def test_get_risk_params_config(self):
        assert get_risk_params_config() == {}

    @pytest.mark.unit
    def test_get_stop_loss_config(self):
        assert get_stop_loss_config() == {}

    @pytest.mark.unit
    def test_list_available_configs(self):
        result = list_available_configs()
        names = [item["name"] for item in result]
        assert "portfolio" in names

    @pytest.mark.unit
    def test_get_config_source(self):
        src = get_config_source("portfolio")
        assert src is not None

    @pytest.mark.unit
    def test_clear_config_cache(self):
        get_config("portfolio")
        clear_config_cache()
        # 清空后仍可重新加载
        cfg = get_config("portfolio")
        assert "fallback_prices" in cfg
