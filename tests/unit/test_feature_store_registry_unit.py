# -*- coding: utf-8 -*-
"""feature_store registry 单元测试 — 因子注册表/状态流转/JSON 原子写入全分支覆盖

被测模块: utils/feature_store/registry.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.feature_store.config import FeatureStoreConfig  # noqa: E402
from utils.feature_store.registry import FactorMeta, Registry  # noqa: E402


def _config(tmp_path) -> FeatureStoreConfig:
    return FeatureStoreConfig(offline_parquet_dir=os.path.join(str(tmp_path), "pq"))


def _meta(name="alpha", version="1.0", **kw) -> FactorMeta:
    return FactorMeta(name=name, version=version, **kw)


class TestFactorMetaToFromDict:
    def test_round_trip(self):
        m = _meta(category="momentum", dependencies=("a", "b"), calc_frequency="weekly",
                  storage_tier="online", description="d")
        d = m.to_dict()
        assert d["dependencies"] == ["a", "b"]
        m2 = FactorMeta.from_dict(d)
        assert m2 == m
        assert isinstance(m2.dependencies, tuple)

    def test_from_dict_dependencies_list(self):
        m = FactorMeta.from_dict({"name": "x", "version": "1", "dependencies": ["p", "q"]})
        assert m.dependencies == ("p", "q")

    def test_from_dict_dependencies_tuple(self):
        m = FactorMeta.from_dict({"name": "x", "version": "1", "dependencies": ("p",)})
        assert m.dependencies == ("p",)

    def test_from_dict_dependencies_invalid_becomes_empty(self):
        m = FactorMeta.from_dict({"name": "x", "version": "1", "dependencies": "not a list"})
        assert m.dependencies == ()

    def test_from_dict_dependencies_missing(self):
        m = FactorMeta.from_dict({"name": "x", "version": "1"})
        assert m.dependencies == ()

    def test_from_dict_defaults(self):
        m = FactorMeta.from_dict({})
        assert m.name == ""
        assert m.version == ""
        assert m.calc_frequency == "daily"
        assert m.storage_tier == "both"
        assert m.status == "active"

    def test_frozen_cannot_mutate(self):
        m = _meta()
        with pytest.raises((AttributeError, TypeError)):
            m.status = "inactive"


class TestRegistryRegister:
    def test_register_new(self, tmp_path):
        reg = Registry(_config(tmp_path))
        ok, msg = reg.register(_meta())
        assert ok and msg == "registered"
        got = reg.get("alpha", "1.0")
        assert got is not None
        assert got.created_at != ""
        assert got.updated_at != ""

    def test_register_duplicate_rejected(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        ok, msg = reg.register(_meta())
        assert not ok and msg == "duplicate registration"

    def test_register_invalid_storage_tier(self, tmp_path):
        reg = Registry(_config(tmp_path))
        ok, msg = reg.register(_meta(storage_tier="bogus"))
        assert not ok and "invalid storage_tier" in msg

    def test_register_invalid_calc_frequency(self, tmp_path):
        reg = Registry(_config(tmp_path))
        ok, msg = reg.register(_meta(calc_frequency="hourly"))
        assert not ok and "invalid calc_frequency" in msg

    def test_register_invalid_status_falls_back_active(self, tmp_path):
        reg = Registry(_config(tmp_path))
        ok, _ = reg.register(_meta(status="weird"))
        assert ok
        assert reg.get("alpha", "1.0").status == "active"

    def test_register_preserves_created_at(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta(created_at="2020-01-01T00:00:00Z"))
        assert reg.get("alpha", "1.0").created_at == "2020-01-01T00:00:00Z"

    def test_register_save_failure_rolls_back(self, tmp_path):
        reg = Registry(_config(tmp_path))
        with patch.object(reg, "_save", return_value=False):
            ok, msg = reg.register(_meta())
        assert not ok and msg == "registry backend unavailable"
        assert reg.get("alpha", "1.0") is None


class TestRegistryGet:
    def test_get_with_version(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta("f", "1.0"))
        reg.register(_meta("f", "2.0"))
        assert reg.get("f", "1.0").version == "1.0"
        assert reg.get("f", "2.0").version == "2.0"

    def test_get_latest_when_version_none(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta("f", "1.0"))
        reg.register(_meta("f", "2.0"))
        assert reg.get("f").version == "2.0"

    def test_get_nonexistent_returns_none(self, tmp_path):
        reg = Registry(_config(tmp_path))
        assert reg.get("nope") is None
        assert reg.get("nope", "1.0") is None


class TestRegistryListAndRegistered:
    def test_list_all(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta("a", "1"))
        reg.register(_meta("b", "1"))
        assert len(reg.list_factors()) == 2

    def test_list_filter_status(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta("a", "1"))
        reg.register(_meta("b", "1", status="inactive"))
        assert len(reg.list_factors(status="active")) == 1
        assert len(reg.list_factors(status="inactive")) == 1

    def test_list_filter_category(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta("a", "1", category="momentum"))
        reg.register(_meta("b", "1", category="value"))
        assert len(reg.list_factors(category="momentum")) == 1

    def test_list_filter_both(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta("a", "1", category="momentum"))
        reg.register(_meta("b", "1", category="value", status="inactive"))
        assert len(reg.list_factors(status="active", category="momentum")) == 1
        assert len(reg.list_factors(status="active", category="value")) == 0

    def test_is_registered_active(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        assert reg.is_registered("alpha", "1.0") is True

    def test_is_registered_inactive_false(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        reg.deactivate("alpha", "1.0")
        assert reg.is_registered("alpha", "1.0") is False

    def test_is_registered_missing_false(self, tmp_path):
        reg = Registry(_config(tmp_path))
        assert reg.is_registered("nope") is False


class TestStateTransitions:
    def test_active_to_inactive_to_active(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        ok, msg = reg.deactivate("alpha", "1.0")
        assert ok and msg == "inactive"
        assert reg.get("alpha", "1.0").status == "inactive"
        ok, msg = reg.activate("alpha", "1.0")
        assert ok and msg == "active"
        assert reg.get("alpha", "1.0").status == "active"

    def test_active_to_deprecated(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        ok, msg = reg.deprecate("alpha", "1.0")
        assert ok and msg == "deprecated"
        assert reg.get("alpha", "1.0").status == "deprecated"

    def test_deprecated_to_active_rejected(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        reg.deprecate("alpha", "1.0")
        ok, msg = reg.activate("alpha", "1.0")
        assert not ok and "cannot" in msg
        assert reg.get("alpha", "1.0").status == "deprecated"

    def test_deactivate_from_inactive_rejected(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        reg.deactivate("alpha", "1.0")
        ok, msg = reg.deactivate("alpha", "1.0")
        assert not ok and "already" in msg

    def test_deactivate_from_deprecated_rejected(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        reg.deprecate("alpha", "1.0")
        ok, msg = reg.deactivate("alpha", "1.0")
        assert not ok and "cannot" in msg

    def test_deprecate_from_inactive_allowed(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        reg.deactivate("alpha", "1.0")
        ok, msg = reg.deprecate("alpha", "1.0")
        assert ok and msg == "deprecated"

    def test_deprecate_already_deprecated(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        reg.deprecate("alpha", "1.0")
        ok, msg = reg.deprecate("alpha", "1.0")
        assert not ok and "already" in msg

    def test_activate_already_active(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        ok, msg = reg.activate("alpha", "1.0")
        assert not ok and "already" in msg

    def test_status_change_not_found(self, tmp_path):
        reg = Registry(_config(tmp_path))
        assert reg.deactivate("nope", "1") == (False, "factor not found")

    def test_status_change_save_failure_rolls_back(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta())
        with patch.object(reg, "_save", return_value=False):
            ok, msg = reg.deactivate("alpha", "1.0")
        assert not ok and msg == "registry backend unavailable"
        assert reg.get("alpha", "1.0").status == "active"

    def test_deactivate_latest_version(self, tmp_path):
        reg = Registry(_config(tmp_path))
        reg.register(_meta("f", "1.0"))
        reg.register(_meta("f", "2.0"))
        ok, _ = reg.deactivate("f")
        assert ok
        assert reg.get("f").status == "inactive"


class TestJsonPersistence:
    def test_atomic_write_file_exists(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        reg.register(_meta())
        assert os.path.exists(reg._json_path)

    def test_reload_preserves_data(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        reg.register(_meta(category="momentum"))
        reg2 = Registry(cfg)
        m = reg2.get("alpha", "1.0")
        assert m is not None
        assert m.category == "momentum"

    def test_load_missing_file_empty_cache(self, tmp_path):
        reg = Registry(_config(tmp_path))
        assert reg.list_factors() == []

    def test_load_corrupted_json_empty_cache(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        with open(reg._json_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        reg2 = Registry(cfg)
        assert reg2.list_factors() == []
        ok, _ = reg2.register(_meta())
        assert ok

    def test_load_non_dict_json_empty_cache(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        with open(reg._json_path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        reg2 = Registry(cfg)
        assert reg2.list_factors() == []

    def test_load_non_dict_meta_value_skipped(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        with open(reg._json_path, "w", encoding="utf-8") as f:
            json.dump({"alpha\x001.0": "not a dict"}, f)
        reg2 = Registry(cfg)
        assert reg2.list_factors() == []

    def test_load_key_without_separator_uses_meta_fields(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        with open(reg._json_path, "w", encoding="utf-8") as f:
            json.dump({"plainkey": {"name": "beta", "version": "9"}}, f)
        reg2 = Registry(cfg)
        m = reg2.get("beta", "9")
        assert m is not None
        assert m.name == "beta"

    def test_save_creates_parent_dir(self, tmp_path):
        nested = os.path.join(str(tmp_path), "deep", "nest")
        cfg = FeatureStoreConfig(offline_parquet_dir=os.path.join(nested, "pq"))
        reg = Registry(cfg)
        ok, _ = reg.register(_meta())
        assert ok
        assert os.path.exists(reg._json_path)

    def test_save_replace_failure_returns_false_and_cleans_tmp(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        with patch("utils.feature_store.registry.os.replace", side_effect=OSError("boom")):
            ok, msg = reg.register(_meta())
        assert not ok and msg == "registry backend unavailable"
        assert not os.path.exists(reg._json_path + ".tmp")

    def test_save_replace_failure_unlink_exception_swallowed(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        with patch("utils.feature_store.registry.os.replace", side_effect=OSError("boom")), \
             patch("utils.feature_store.registry.os.unlink", side_effect=OSError("boom2")):
            ok, _ = reg.register(_meta())
        assert not ok

    def test_save_makedirs_failure_no_tmp_to_clean(self, tmp_path):
        cfg = _config(tmp_path)
        reg = Registry(cfg)
        with patch("utils.feature_store.registry.os.makedirs", side_effect=OSError("boom")):
            ok, msg = reg.register(_meta())
        assert not ok and msg == "registry backend unavailable"
        assert not os.path.exists(reg._json_path + ".tmp")