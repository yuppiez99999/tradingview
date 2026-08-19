"""test_path_config_unit.py — 集中路径配置模块单元测试

覆盖要点:
    - get_project_root / get_data_root
    - get_data_cache_dir / get_output_dir / get_reports_dir / get_models_dir / get_logs_dir
    - get_config_dir / get_v8_src_dir / get_utils_dir / get_tests_dir / get_research_dir
    - setup_sys_path
    - get_historical_base_file
    - describe_paths
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from utils.path_config import (
    describe_paths,
    get_config_dir,
    get_data_cache_dir,
    get_data_root,
    get_historical_base_file,
    get_logs_dir,
    get_models_dir,
    get_output_dir,
    get_project_root,
    get_reports_dir,
    get_research_dir,
    get_scripts_dir,
    get_tests_dir,
    get_tools_dir,
    get_utils_dir,
    get_v8_root_dir,
    get_v8_src_dir,
    setup_sys_path,
)

# ============================================================
# 基本路径
# ============================================================


class TestBasicPaths:
    @pytest.mark.unit
    def test_project_root_exists(self):
        root = get_project_root()
        assert root.exists()
        assert (root / "utils").exists()

    @pytest.mark.unit
    def test_data_root_is_path(self):
        assert isinstance(get_data_root(), Path)

    @pytest.mark.unit
    def test_data_root_exists(self):
        assert get_data_root().exists()


# ============================================================
# 数据子目录
# ============================================================


class TestDataDirs:
    @pytest.mark.unit
    def test_data_cache_dir(self):
        d = get_data_cache_dir()
        assert d.name == "data_cache"
        assert d.parent == get_data_root()

    @pytest.mark.unit
    def test_output_dir(self):
        d = get_output_dir()
        assert d.name == "output"

    @pytest.mark.unit
    def test_reports_dir(self):
        d = get_reports_dir()
        assert d.name == "reports"

    @pytest.mark.unit
    def test_models_dir(self):
        d = get_models_dir()
        assert d.name == "models"

    @pytest.mark.unit
    def test_logs_dir(self):
        d = get_logs_dir()
        assert d.name == "logs"


# ============================================================
# 项目子目录
# ============================================================


class TestProjectDirs:
    @pytest.mark.unit
    def test_config_dir(self):
        assert get_config_dir().name == "config"
        assert get_config_dir().parent == get_project_root()

    @pytest.mark.unit
    def test_v8_src_dir(self):
        d = get_v8_src_dir()
        assert d.name == "src"
        assert d.parent.name == "v8.3_institutional"

    @pytest.mark.unit
    def test_v8_root_dir(self):
        assert get_v8_root_dir().name == "v8.3_institutional"

    @pytest.mark.unit
    def test_utils_dir(self):
        assert get_utils_dir().name == "utils"

    @pytest.mark.unit
    def test_tools_dir(self):
        assert get_tools_dir().name == "tools"

    @pytest.mark.unit
    def test_scripts_dir(self):
        assert get_scripts_dir().name == "scripts"

    @pytest.mark.unit
    def test_tests_dir(self):
        assert get_tests_dir().name == "tests"

    @pytest.mark.unit
    def test_research_dir(self):
        assert get_research_dir().name == "research"


# ============================================================
# setup_sys_path
# ============================================================


class TestSetupSysPath:
    @pytest.mark.unit
    def test_setup_injects_paths(self):
        original = list(sys.path)
        try:
            setup_sys_path()
            root = str(get_project_root())
            assert root in sys.path
            assert str(get_utils_dir()) in sys.path
        finally:
            sys.path[:] = original


# ============================================================
# get_historical_base_file
# ============================================================


class TestHistoricalBaseFile:
    @pytest.mark.unit
    def test_filename(self):
        p = get_historical_base_file("600519")
        assert p.name == "historical_600519_5y_base.parquet"
        assert p.parent == get_data_cache_dir()

    @pytest.mark.unit
    def test_different_symbol(self):
        p = get_historical_base_file("300750")
        assert "300750" in p.name


# ============================================================
# describe_paths
# ============================================================


class TestDescribePaths:
    @pytest.mark.unit
    def test_returns_dict(self):
        d = describe_paths()
        assert isinstance(d, dict)

    @pytest.mark.unit
    def test_has_keys(self):
        d = describe_paths()
        assert "project_root" in d
        assert "data_root" in d
        assert "data_cache" in d
        assert "output" in d
        assert "reports" in d
        assert "models" in d
        assert "logs" in d
        assert "using_d_drive" in d

    @pytest.mark.unit
    def test_project_root_is_string(self):
        d = describe_paths()
        assert isinstance(d["project_root"], str)
        assert isinstance(d["using_d_drive"], bool)
