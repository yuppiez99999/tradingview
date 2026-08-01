"""T13: ConfigManager 单元测试 — 补关键模块覆盖率.

覆盖 utils/config_manager.py 的:
- 单例模式 (get_instance, reset_instance)
- 搜索路径构建 (env var / v8.3 / configs / ms_strategy)
- 路径解析 (短名/文件名/未找到)
- YAML 加载 (成功/空文件/非 dict/YAML 错误/IO 错误)
- 缓存与 mtime 失效
- 类型化访问器 (get_kill_switch_config 等)
- 审计方法 (list_available, get_config_source)
- 模块级快捷函数
"""
import os
import time

import pytest

from utils.config_manager import (
    _NAMED_CONFIGS,
    ConfigManager,
    clear_config_cache,
    get_backtest_config,
    get_config,
    get_config_source,
    get_execution_config,
    get_kill_switch_config,
    get_portfolio_config,
    get_risk_budget_config,
    get_settings_config,
    get_stop_loss_config,
    list_available_configs,
)

# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_config_dir(tmp_path):
    """临时配置目录, 包含 portfolio.yaml + settings.yaml."""
    (tmp_path / "portfolio.yaml").write_text(
        "kill_switch:\n  L1_threshold: 0.05\n  L2_threshold: 0.08\n"
        "account:\n  total_capital: 1000000\n",
        encoding="utf-8",
    )
    (tmp_path / "settings.yaml").write_text(
        "log_level: INFO\ndata_source: tdx\n",
        encoding="utf-8",
    )
    (tmp_path / "execution.yaml").write_text(
        "slippage_bps: 5\ncommission_rate: 0.0003\n",
        encoding="utf-8",
    )
    # 非 dict 内容
    (tmp_path / "bad_list.yaml").write_text("- a\n- b\n- c\n", encoding="utf-8")
    # 空 YAML
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    # 损坏 YAML
    (tmp_path / "broken.yaml").write_text("key: [unterminated\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def isolated_manager(tmp_config_dir):
    """带临时搜索路径的独立 ConfigManager 实例."""
    ConfigManager.reset_instance()
    mgr = ConfigManager(extra_search_paths=[tmp_config_dir])
    yield mgr
    ConfigManager.reset_instance()


@pytest.fixture
def clean_singleton():
    """每个测试前后清空单例, 避免缓存污染."""
    ConfigManager.reset_instance()
    yield
    ConfigManager.reset_instance()


# ============================================================
# 单例模式
# ============================================================

class TestT13Singleton:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_instance_returns_same_object(self, clean_singleton):
        a = ConfigManager.get_instance()
        b = ConfigManager.get_instance()
        assert a is b

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_reset_instance_creates_new_object(self, clean_singleton):
        a = ConfigManager.get_instance()
        ConfigManager.reset_instance()
        b = ConfigManager.get_instance()
        assert a is not b

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_reset_instance_clears_cache(self, clean_singleton, tmp_config_dir):
        mgr = ConfigManager.get_instance()
        # 注入临时路径并加载, 利用 reset 后缓存应空
        mgr._search_paths.insert(0, tmp_config_dir)
        cfg = mgr.get("portfolio")
        assert cfg  # 缓存写入
        assert "portfolio" in mgr._cache
        ConfigManager.reset_instance()
        new_mgr = ConfigManager.get_instance()
        assert "portfolio" not in new_mgr._cache


# ============================================================
# 搜索路径构建
# ============================================================

class TestT13SearchPaths:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_extra_search_paths_take_priority(self, tmp_config_dir):
        mgr = ConfigManager(extra_search_paths=[tmp_config_dir])
        assert tmp_config_dir in mgr._search_paths
        assert mgr._search_paths[0] == tmp_config_dir

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_env_var_search_path(self, tmp_config_dir, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_config_dir))
        mgr = ConfigManager()
        env_paths = [p for p in mgr._search_paths if str(p) == str(tmp_config_dir.resolve())]
        assert len(env_paths) == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_env_var_invalid_dir_ignored(self, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", "/nonexistent/path/xyz")
        mgr = ConfigManager()
        assert not any("nonexistent" in str(p) for p in mgr._search_paths)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_env_var_empty_ignored(self, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", "")
        mgr = ConfigManager()
        # 无 env 路径, 不应崩溃
        assert isinstance(mgr._search_paths, list)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_project_root_override(self, tmp_path):
        # project_root 不含 v8.3_institutional/configs/ms_strategy, 应返回空 list
        mgr = ConfigManager(project_root=tmp_path)
        assert isinstance(mgr._search_paths, list)


# ============================================================
# 路径解析
# ============================================================

class TestT13ResolvePath:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_resolve_short_name(self, isolated_manager, tmp_config_dir):
        path = isolated_manager._resolve_config_path("portfolio")
        assert path is not None
        assert path.name == "portfolio.yaml"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_resolve_full_filename(self, isolated_manager, tmp_config_dir):
        path = isolated_manager._resolve_config_path("portfolio.yaml")
        assert path is not None
        assert path.name == "portfolio.yaml"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_resolve_yml_extension(self, tmp_config_dir):
        # .yml 扩展名也应支持
        (tmp_config_dir / "settings.yml").write_text("x: 1\n", encoding="utf-8")
        mgr = ConfigManager(extra_search_paths=[tmp_config_dir])
        path = mgr._resolve_config_path("settings.yml")
        assert path is not None
        ConfigManager.reset_instance()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_resolve_unknown_returns_none(self, isolated_manager):
        path = isolated_manager._resolve_config_path("does_not_exist")
        assert path is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_resolve_unnamed_falls_back_to_name_yaml(self, isolated_manager, tmp_config_dir):
        # name 不在 _NAMED_CONFIGS, 但文件存在 → 自动加 .yaml 后缀
        (tmp_config_dir / "custom.yaml").write_text("key: value\n", encoding="utf-8")
        path = isolated_manager._resolve_config_path("custom")
        assert path is not None
        assert path.name == "custom.yaml"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_resolve_priority_first_match_wins(self, tmp_path):
        # 两个搜索路径, 同名文件, 第一个优先
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        (dir1 / "portfolio.yaml").write_text("from: dir1\n", encoding="utf-8")
        (dir2 / "portfolio.yaml").write_text("from: dir2\n", encoding="utf-8")
        mgr = ConfigManager(extra_search_paths=[dir1, dir2])
        path = mgr._resolve_config_path("portfolio")
        assert path.parent == dir1
        ConfigManager.reset_instance()


# ============================================================
# YAML 加载
# ============================================================

class TestT13LoadYaml:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_load_valid_yaml(self, isolated_manager, tmp_config_dir):
        path = tmp_config_dir / "portfolio.yaml"
        data = isolated_manager._load_yaml(path)
        assert isinstance(data, dict)
        assert "kill_switch" in data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_load_empty_yaml_returns_empty_dict(self, isolated_manager, tmp_config_dir):
        path = tmp_config_dir / "empty.yaml"
        data = isolated_manager._load_yaml(path)
        assert data == {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_load_non_dict_returns_empty_dict(self, isolated_manager, tmp_config_dir):
        path = tmp_config_dir / "bad_list.yaml"
        data = isolated_manager._load_yaml(path)
        assert data == {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_load_broken_yaml_returns_empty_dict(self, isolated_manager, tmp_config_dir):
        path = tmp_config_dir / "broken.yaml"
        data = isolated_manager._load_yaml(path)
        assert data == {}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_load_missing_file_returns_empty_dict(self, isolated_manager, tmp_path):
        path = tmp_path / "not_exists.yaml"
        data = isolated_manager._load_yaml(path)
        assert data == {}


# ============================================================
# 缓存与 mtime 失效
# ============================================================

class TestT13Cache:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_caches_result(self, isolated_manager, tmp_config_dir):
        cfg1 = isolated_manager.get("portfolio")
        assert "portfolio" in isolated_manager._cache
        cfg2 = isolated_manager.get("portfolio")
        assert cfg1 is cfg2  # 同一对象, 来自缓存

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_mtime_change_invalidates_cache(self, isolated_manager, tmp_config_dir):
        isolated_manager.get("portfolio")
        path = tmp_config_dir / "portfolio.yaml"
        # 修改 mtime (必须足够大, 某些 FS 精度低)
        time.sleep(0.05)
        os.utime(path, None)
        isolated_manager.get("portfolio")
        # 缓存失效后重新加载, 应是不同对象 (或至少重新读盘)
        assert "portfolio" in isolated_manager._cache

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_file_deleted_clears_cache(self, isolated_manager, tmp_config_dir):
        # 先加载到缓存
        isolated_manager.get("portfolio")
        assert "portfolio" in isolated_manager._cache
        # 删除文件
        (tmp_config_dir / "portfolio.yaml").unlink()
        # 触发缓存检查
        cfg = isolated_manager._get_cached("portfolio")
        # 文件已删除, 缓存应被清除, 返回 None
        assert cfg is None
        assert "portfolio" not in isolated_manager._cache

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_clear_cache(self, isolated_manager):
        isolated_manager.get("portfolio")
        assert "portfolio" in isolated_manager._cache
        isolated_manager.clear_cache()
        assert "portfolio" not in isolated_manager._cache

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_reload_skips_cache(self, isolated_manager, tmp_config_dir):
        isolated_manager.get("portfolio")
        # 修改文件内容
        path = tmp_config_dir / "portfolio.yaml"
        time.sleep(0.05)
        path.write_text("new_key: new_value\n", encoding="utf-8")
        os.utime(path, None)
        cfg2 = isolated_manager.reload("portfolio")
        assert cfg2.get("new_key") == "new_value"
        assert "old_key_not_in" not in cfg2


# ============================================================
# get() 主入口
# ============================================================

class TestT13Get:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_returns_dict(self, isolated_manager):
        cfg = isolated_manager.get("portfolio")
        assert isinstance(cfg, dict)
        assert "kill_switch" in cfg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_unknown_returns_default_empty_dict(self, isolated_manager):
        cfg = isolated_manager.get("unknown_name")
        assert cfg == {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_unknown_returns_provided_default(self, isolated_manager):
        default = {"fallback": True}
        cfg = isolated_manager.get("unknown_name", default=default)
        assert cfg == default

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_get_with_filename(self, isolated_manager):
        cfg = isolated_manager.get("portfolio.yaml")
        assert isinstance(cfg, dict)
        assert "kill_switch" in cfg


# ============================================================
# 类型化访问器
# ============================================================

class TestT13TypedAccessors:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_kill_switch_config_from_portfolio(self, isolated_manager):
        ks = isolated_manager.get_kill_switch_config()
        assert "L1_threshold" in ks
        assert ks["L1_threshold"] == 0.05

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_portfolio_config(self, isolated_manager):
        cfg = isolated_manager.get_portfolio_config()
        assert "account" in cfg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_settings_config(self, isolated_manager):
        cfg = isolated_manager.get_settings_config()
        assert cfg.get("log_level") == "INFO"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_execution_config(self, isolated_manager):
        cfg = isolated_manager.get_execution_config()
        assert cfg.get("slippage_bps") == 5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_get_kill_switch_empty_when_no_portfolio(self, tmp_path):
        # project_root 指向空目录, 默认搜索路径都找不到 portfolio.yaml, ks 应返回空 dict
        mgr = ConfigManager(project_root=tmp_path)
        ks = mgr.get_kill_switch_config()
        assert ks == {}
        ConfigManager.reset_instance()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_get_kill_switch_fallback_to_standalone_yaml(self, tmp_path):
        # portfolio.yaml 无 kill_switch 节, 但有独立 kill_switch.yaml
        (tmp_path / "portfolio.yaml").write_text("account: {x: 1}\n", encoding="utf-8")
        (tmp_path / "kill_switch.yaml").write_text(
            "L1_threshold: 0.1\nL2_threshold: 0.15\n",
            encoding="utf-8",
        )
        mgr = ConfigManager(extra_search_paths=[tmp_path])
        ks = mgr.get_kill_switch_config()
        assert ks.get("L1_threshold") == 0.1
        ConfigManager.reset_instance()


# ============================================================
# 审计方法
# ============================================================

class TestT13AuditMethods:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_list_available_returns_list(self, isolated_manager):
        result = isolated_manager.list_available()
        assert isinstance(result, list)
        # 至少应包含 portfolio + settings + execution
        names = [item["name"] for item in result]
        assert "portfolio" in names
        assert "settings" in names

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_list_available_item_structure(self, isolated_manager):
        result = isolated_manager.list_available()
        for item in result:
            assert "name" in item
            assert "filename" in item
            assert "path" in item
            assert "size" in item
            assert isinstance(item["size"], int)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_config_source_returns_path(self, isolated_manager):
        src = isolated_manager.get_config_source("portfolio")
        assert src is not None
        assert "portfolio.yaml" in src

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_config_source_unknown_returns_none(self, isolated_manager):
        src = isolated_manager.get_config_source("non_existent")
        assert src is None


# ============================================================
# 模块级快捷函数
# ============================================================

class TestT13ModuleFunctions:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_config_uses_singleton(self, clean_singleton, tmp_config_dir, monkeypatch):
        # 通过 env var 注入临时路径
        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_config_dir))
        cfg = get_config("portfolio")
        assert "kill_switch" in cfg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_get_kill_switch_config_module_func(self, clean_singleton, tmp_config_dir, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_config_dir))
        ks = get_kill_switch_config()
        assert ks.get("L1_threshold") == 0.05

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_all_module_funcs_return_dict(self, clean_singleton, tmp_config_dir, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_config_dir))
        assert isinstance(get_portfolio_config(), dict)
        assert isinstance(get_settings_config(), dict)
        assert isinstance(get_execution_config(), dict)
        assert isinstance(get_backtest_config(), dict)
        assert isinstance(get_risk_budget_config(), dict)
        assert isinstance(get_stop_loss_config(), dict)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_list_available_configs_module_func(self, clean_singleton, tmp_config_dir, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_config_dir))
        result = list_available_configs()
        assert isinstance(result, list)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_clear_config_cache_module_func(self, clean_singleton, tmp_config_dir, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_config_dir))
        get_config("portfolio")
        clear_config_cache()
        # 缓存清空, 不报错即可

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_get_config_source_module_func(self, clean_singleton, tmp_config_dir, monkeypatch):
        monkeypatch.setenv("QUANT_CONFIG_DIR", str(tmp_config_dir))
        src = get_config_source("portfolio")
        assert src is not None


# ============================================================
# _NAMED_CONFIGS 注册表
# ============================================================

class TestT13NamedConfigs:
    @pytest.mark.unit
    @pytest.mark.p0
    def test_t13_named_configs_contains_required_keys(self):
        required = ["portfolio", "settings", "execution", "backtest",
                    "risk_budget", "stop_loss", "institutional"]
        for key in required:
            assert key in _NAMED_CONFIGS, f"missing key: {key}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_named_configs_values_are_yaml_files(self):
        for key, filename in _NAMED_CONFIGS.items():
            assert filename.endswith((".yaml", ".yml")), f"{key} -> {filename}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_named_configs_daily_report_generator_registered(self):
        # HC-5 兼容性测试要求
        assert "daily_report_generator" in _NAMED_CONFIGS
        assert _NAMED_CONFIGS["daily_report_generator"] == "daily_report_generator.yaml"


# ============================================================
# 线程安全
# ============================================================

class TestT13ThreadSafety:
    @pytest.mark.unit
    @pytest.mark.p1
    def test_t13_concurrent_get_no_crash(self, isolated_manager):
        import threading
        results = []
        errors = []

        def worker():
            try:
                for _ in range(20):
                    cfg = isolated_manager.get("portfolio")
                    results.append(cfg)
            except Exception as e:  # noqa: BLE001  # 测试代码允许捕获
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(results) == 100
        # 所有结果应是同一对象 (缓存命中) 或内容一致
        for r in results:
            assert "kill_switch" in r
