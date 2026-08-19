"""lgbm_reproducibility 单元测试 — LightGBM 训练可复现性模块."""

from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd
import pytest

from utils.lgbm_reproducibility import (
    ManifestWriteError,
    ReproducibilityError,
    TrainingConfig,
    artifact_name,
    compute_code_sha,
    compute_dataset_uri,
    construct_default_config,
    verify_reproducibility,
    write_manifest,
)


class TestTrainingConfig:
    def test_default_creation(self):
        cfg = TrainingConfig(model_name="test_model")
        assert cfg.model_name == "test_model"
        assert cfg.seed == 42
        assert cfg.num_boost_round == 200
        assert cfg.early_stopping_rounds == 20
        assert cfg.n_splits == 5
        assert cfg.label_def == "forward_return_5d"
        assert cfg.label_horizon == 5
        assert cfg.dataset_uri == ""
        assert cfg.code_sha == ""
        assert cfg.config_hash == ""

    def test_frozen(self):
        cfg = TrainingConfig(model_name="test")
        with pytest.raises((AttributeError, TypeError)):
            cfg.model_name = "other"

    def test_custom_params(self):
        cfg = TrainingConfig(
            model_name="m",
            seed=99,
            num_boost_round=500,
            lgb_params={"num_leaves": 31},
            feature_list=("f1", "f2"),
        )
        assert cfg.seed == 99
        assert cfg.num_boost_round == 500
        assert cfg.lgb_params == {"num_leaves": 31}
        assert cfg.feature_list == ("f1", "f2")


class TestWithDataset:
    def test_basic(self):
        cfg = TrainingConfig(model_name="m")
        panel = pd.DataFrame(
            {"code": ["A", "B", "A"], "date": ["2026-01-01", "2026-01-01", "2026-01-02"], "f1": [1.0, 2.0, 3.0]}
        )
        cfg2 = cfg.with_dataset(panel)
        assert cfg2.dataset_uri != ""
        assert cfg2.dataset_sha256 == cfg2.dataset_uri
        assert cfg2.dataset_shape == (3, 3)
        assert cfg2.dataset_row_count == 3
        assert cfg2.dataset_date_range == ("2026-01-01", "2026-01-02")

    def test_no_date_col(self):
        cfg = TrainingConfig(model_name="m")
        panel = pd.DataFrame({"f1": [1.0, 2.0]})
        cfg2 = cfg.with_dataset(panel)
        assert cfg2.dataset_date_range == ("", "")
        assert cfg2.dataset_row_count == 2

    def test_immutable(self):
        cfg = TrainingConfig(model_name="m")
        panel = pd.DataFrame({"f1": [1.0]})
        cfg2 = cfg.with_dataset(panel)
        assert cfg.dataset_uri == ""
        assert cfg2.dataset_uri != ""


class TestWithCodeSha:
    def test_basic(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("print(1)")
        cfg = TrainingConfig(model_name="m")
        cfg2 = cfg.with_code_sha([f])
        assert cfg2.code_sha != ""
        assert len(cfg2.code_sha) == 64

    def test_empty_list(self):
        cfg = TrainingConfig(model_name="m")
        cfg2 = cfg.with_code_sha([])
        assert cfg2.code_sha == ""


class TestWithEnvironment:
    def test_basic(self):
        cfg = TrainingConfig(model_name="m")
        cfg2 = cfg.with_environment(env="ci")
        assert cfg2.training_env == "ci"
        assert cfg2.python_version != ""
        assert "pandas" in cfg2.lib_versions
        assert cfg2.created_at != ""

    def test_auto_detect(self):
        cfg = TrainingConfig(model_name="m")
        with patch.dict("os.environ", {"TRADING_ENV": "live"}, clear=False):
            cfg2 = cfg.with_environment()
            assert cfg2.training_env == "live"


class TestWithConfigHash:
    def test_basic(self):
        cfg = TrainingConfig(model_name="m", seed=42)
        cfg2 = cfg.with_config_hash()
        assert cfg2.config_hash != ""
        assert len(cfg2.config_hash) == 64

    def test_deterministic(self):
        cfg1 = TrainingConfig(model_name="m", seed=42).with_config_hash()
        cfg2 = TrainingConfig(model_name="m", seed=42).with_config_hash()
        assert cfg1.config_hash == cfg2.config_hash

    def test_different_seed_different_hash(self):
        cfg1 = TrainingConfig(model_name="m", seed=42).with_config_hash()
        cfg2 = TrainingConfig(model_name="m", seed=99).with_config_hash()
        assert cfg1.config_hash != cfg2.config_hash


class TestComputeDatasetUri:
    def test_deterministic(self):
        df = pd.DataFrame({"code": ["A", "B"], "date": ["2026-01-01", "2026-01-02"], "v": [1.0, 2.0]})
        assert compute_dataset_uri(df) == compute_dataset_uri(df)

    def test_order_independent(self):
        df1 = pd.DataFrame({"code": ["A", "B"], "date": ["2026-01-01", "2026-01-02"], "v": [1.0, 2.0]})
        df2 = pd.DataFrame({"code": ["B", "A"], "date": ["2026-01-02", "2026-01-01"], "v": [2.0, 1.0]})
        assert compute_dataset_uri(df1) == compute_dataset_uri(df2)

    def test_different_data_different_hash(self):
        df1 = pd.DataFrame({"v": [1.0, 2.0]})
        df2 = pd.DataFrame({"v": [1.0, 3.0]})
        assert compute_dataset_uri(df1) != compute_dataset_uri(df2)


class TestComputeCodeSha:
    def test_empty(self):
        assert compute_code_sha([]) == ""

    def test_basic(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        sha = compute_code_sha([f])
        assert len(sha) == 64

    def test_multiple_files(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("x = 1")
        f2.write_text("y = 2")
        sha = compute_code_sha([f1, f2])
        assert len(sha) == 64

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "nonexist.py"
        sha = compute_code_sha([f])
        assert len(sha) == 64


class TestArtifactName:
    def test_with_config_hash(self):
        cfg = TrainingConfig(model_name="my_model", config_hash="abcdef1234567890")
        name = artifact_name(cfg)
        assert name.startswith("my_model_vabcdef12_d")

    def test_fallback_to_code_sha(self):
        cfg = TrainingConfig(model_name="my_model", code_sha="99999999abcdef")
        name = artifact_name(cfg)
        assert name.startswith("my_model_v99999999_d")

    def test_empty_sha(self):
        cfg = TrainingConfig(model_name="m")
        name = artifact_name(cfg)
        assert name.startswith("m_v_d")


class TestWriteManifest:
    def test_basic(self, tmp_path):
        cfg = TrainingConfig(
            model_name="m",
            seed=42,
            config_hash="abc123",
            code_sha="def456",
            dataset_uri="uri789",
            created_at="2026-08-18T12:00:00",
            training_env="ci",
            python_version="3.8.9",
        )
        path = write_manifest(tmp_path / "artifact", cfg, metrics={"auc": 0.8})
        assert path.exists()
        assert path.name == "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["model_name"] == "m"
        assert data["seed"] == 42
        assert data["config_hash"] == "abc123"
        assert data["metrics"] == {"auc": 0.8}
        assert data["manifest_version"] == "1.0"

    def test_with_contract_validation(self, tmp_path):
        cfg = TrainingConfig(model_name="m")
        path = write_manifest(
            tmp_path / "art",
            cfg,
            contract_validation={"passed": True},
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["contract_validation"] == {"passed": True}

    def test_no_metrics(self, tmp_path):
        cfg = TrainingConfig(model_name="m")
        path = write_manifest(tmp_path / "art", cfg)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["metrics"] == {}


class TestVerifyReproducibility:
    def test_identical(self):
        cfg = TrainingConfig(model_name="m", seed=42, config_hash="h1", dataset_uri="d1", code_sha="c1")
        imp = {"f1": 0.5, "f2": 0.3, "f3": 0.2}
        result = verify_reproducibility(cfg, cfg, imp, imp, top_k=3)
        assert result["config_match"] is True
        assert result["dataset_match"] is True
        assert result["code_match"] is True
        assert result["top_k_overlap"] == 1.0
        assert result["top_k_consistency"] == 1.0
        assert result["reproducible"] is True

    def test_different_config(self):
        cfg_a = TrainingConfig(model_name="m", seed=42, config_hash="h1")
        cfg_b = TrainingConfig(model_name="m", seed=99, config_hash="h2")
        imp = {"f1": 0.5}
        result = verify_reproducibility(cfg_a, cfg_b, imp, imp, top_k=1)
        assert result["config_match"] is False
        assert result["reproducible"] is False

    def test_different_importance_order(self):
        cfg = TrainingConfig(model_name="m", config_hash="h", dataset_uri="d", code_sha="c")
        imp_a = {"f1": 0.5, "f2": 0.3}
        imp_b = {"f2": 0.5, "f1": 0.3}
        result = verify_reproducibility(cfg, cfg, imp_a, imp_b, top_k=2)
        assert result["top_k_overlap"] == 1.0
        assert result["top_k_consistency"] == 0.0
        assert result["reproducible"] is False


class TestConstructDefaultConfig:
    def test_basic(self):
        panel = pd.DataFrame(
            {"code": ["A", "B"], "date": ["2026-01-01", "2026-01-02"], "f1": [1.0, 2.0], "y": [0.1, 0.2]}
        )
        cfg = construct_default_config(panel)
        assert cfg.model_name == "lgbm_factor_mining"
        assert cfg.seed == 42
        assert cfg.num_boost_round == 200
        assert "f1" in cfg.feature_list
        assert "y" not in cfg.feature_list
        assert "code" not in cfg.feature_list
        assert "date" not in cfg.feature_list
        assert cfg.dataset_uri != ""
        assert cfg.code_sha != ""
        assert cfg.config_hash != ""
        assert cfg.training_env != ""
        assert cfg.lgb_params["num_leaves"] == 31
        assert cfg.lgb_params["learning_rate"] == 0.05


class TestExceptions:
    def test_hierarchy(self):
        assert issubclass(ManifestWriteError, ReproducibilityError)
        assert issubclass(ReproducibilityError, Exception)
