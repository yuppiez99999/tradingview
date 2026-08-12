"""ECC GAP-7: 训练管道可复现性测试.

测试覆盖:
    1. TrainingConfig 不可变性 (frozen=True)
    2. with_dataset / with_code_sha / with_environment / with_config_hash 链式构造
    3. compute_dataset_uri (同数据→同 hash, 不同数据→不同 hash)
    4. compute_code_sha (同文件→同 hash, 空列表→空字符串)
    5. artifact_name (不同 code_sha → 不同名称)
    6. write_manifest (文件创建, 17+ 必填字段齐全)
    7. verify_reproducibility (同 config → reproducible=True)
    8. construct_default_config (lgbm_factor_mining.py 默认配置)
    9. 同 config+seed+dataset 重跑 → importance top-10 一致率 100%

注意: 本测试不依赖 lightgbm, 只测试可复现性基础设施.
    lightgbm 训练的可复现性由 lightgbm 自身的 seed 机制保证.
"""
import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.lgbm_reproducibility import construct_default_config  # noqa: E402
from utils.lgbm_reproducibility import (  # noqa: E402
    MANIFEST_REQUIRED_FIELDS,
    TrainingConfig,
    artifact_name,
    compute_code_sha,
    compute_dataset_uri,
    verify_reproducibility,
    write_manifest,
)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def sample_panel() -> pd.DataFrame:
    """构造样本 panel (含 code/date/y/MOM_5D/VOL_20D 等因子列)."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    codes = ["000001", "000002", "000003"]
    rows = []
    for code in codes:
        for date in dates:
            rows.append({
                "code": code,
                "date": date.strftime("%Y-%m-%d"),
                "y": float(np.random.randn() * 0.02),
                "MOM_5D": float(np.random.randn() * 0.05),
                "MOM_20D": float(np.random.randn() * 0.1),
                "VOL_5D": float(abs(np.random.randn()) * 0.15),
                "VOL_20D": float(abs(np.random.randn()) * 0.2),
                "RSI_14D": float(np.random.uniform(20, 80)),
                "MACD": float(np.random.randn() * 0.01),
            })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_config(sample_panel: pd.DataFrame) -> TrainingConfig:
    """构造完整填充的 TrainingConfig (已 with_dataset/code_sha/env/config_hash)."""
    config = TrainingConfig(
        model_name="test_lgbm",
        seed=42,
        lgb_params={
            "objective": "regression",
            "metric": "mse",
            "boosting_type": "gbdt",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
            "seed": 42,
        },
        num_boost_round=200,
        early_stopping_rounds=20,
        n_splits=5,
        feature_list=("MOM_5D", "MOM_20D", "VOL_5D", "VOL_20D", "RSI_14D", "MACD"),
        label_def="forward_return_5d",
        label_horizon=5,
    )
    # 链式填充
    source_files = [Path(__file__).resolve().parent.parent.parent / "research" / "lgbm_factor_mining.py"]
    config = config.with_dataset(sample_panel)
    config = config.with_code_sha(source_files)
    config = config.with_environment(env="test")
    config = config.with_config_hash()
    return config


@pytest.fixture
def sample_importance() -> dict:
    """构造样本特征重要性 dict (模拟 lightgbm 训练输出)."""
    return {
        "MOM_5D": 150.5,
        "VOL_20D": 120.3,
        "RSI_14D": 95.8,
        "MOM_20D": 80.2,
        "VOL_5D": 65.1,
        "MACD": 40.7,
    }


# ============================================================
# 测试组 1: TrainingConfig 不可变性
# ============================================================
class TestTrainingConfigImmutability:
    """ECC coding-standards: 不可变优先 (frozen=True)."""

    def test_frozen_dataclass_blocks_attribute_assignment(self, sample_config: TrainingConfig):
        """frozen=True 时直接赋值应抛 FrozenInstanceError."""
        with pytest.raises(FrozenInstanceError):
            sample_config.seed = 99  # type: ignore[misc]

    def test_frozen_dataclass_blocks_lgb_params_mutation(self, sample_config: TrainingConfig):
        """frozen=True 时修改 lgb_params dict 也应抛 (实际 dict 内部仍可变, 但赋值新 dict 不行)."""
        with pytest.raises(FrozenInstanceError):
            sample_config.lgb_params = {"new": "value"}  # type: ignore[misc]

    def test_with_methods_return_new_instance(self, sample_config: TrainingConfig, sample_panel: pd.DataFrame):
        """with_* 方法应返回新实例, 不修改原实例."""
        original_hash = sample_config.config_hash
        new_config = sample_config.with_dataset(sample_panel)
        # 原实例不变
        assert sample_config.config_hash == original_hash
        # 新实例的 dataset_uri 与原相同 (同 panel)
        assert new_config.dataset_uri == sample_config.dataset_uri


# ============================================================
# 测试组 2: with_* 链式构造
# ============================================================
class TestTrainingConfigChainMethods:
    """ECC GAP-7: with_dataset / with_code_sha / with_environment / with_config_hash."""

    def test_with_dataset_fills_dataset_uri(self, sample_panel: pd.DataFrame):
        """with_dataset 应填充 dataset_uri (非空 64 字符 hex)."""
        config = TrainingConfig(model_name="test")
        new_config = config.with_dataset(sample_panel)
        assert new_config.dataset_uri != ""
        assert len(new_config.dataset_uri) == 64  # sha256 hex
        assert new_config.dataset_sha256 == new_config.dataset_uri

    def test_with_dataset_fills_shape_and_row_count(self, sample_panel: pd.DataFrame):
        """with_dataset 应填充 dataset_shape / dataset_row_count / dataset_date_range."""
        config = TrainingConfig(model_name="test")
        new_config = config.with_dataset(sample_panel)
        assert new_config.dataset_shape == tuple(sample_panel.shape)
        assert new_config.dataset_row_count == len(sample_panel)
        assert new_config.dataset_date_range[0] != ""
        assert new_config.dataset_date_range[1] != ""

    def test_with_code_sha_fills_code_sha(self, tmp_path: Path):
        """with_code_sha 应填充 code_sha (非空 64 字符 hex)."""
        # 创建临时文件
        test_file = tmp_path / "test_source.py"
        test_file.write_text("# test code\n", encoding="utf-8")
        config = TrainingConfig(model_name="test")
        new_config = config.with_code_sha([test_file])
        assert new_config.code_sha != ""
        assert len(new_config.code_sha) == 64

    def test_with_environment_fills_env_info(self):
        """with_environment 应填充 training_env / python_version / lib_versions / created_at."""
        config = TrainingConfig(model_name="test")
        new_config = config.with_environment(env="sim")
        assert new_config.training_env == "sim"
        assert new_config.python_version != ""
        assert "pandas" in new_config.lib_versions
        assert new_config.created_at != ""

    def test_with_config_hash_is_deterministic(self, sample_config: TrainingConfig):
        """with_config_hash 应是确定性的 (同输入 → 同输出)."""
        # 重新构造相同 config (created_at 不同会导致 hash 不同, 需固定 created_at)
        from dataclasses import replace
        fixed_config = replace(sample_config, created_at="2026-07-29T00:00:00")
        hash1 = fixed_config.with_config_hash().config_hash

        # 重新调用 with_config_hash (基于相同字段)
        fixed_config_2 = replace(sample_config, created_at="2026-07-29T00:00:00")
        hash2 = fixed_config_2.with_config_hash().config_hash

        assert hash1 == hash2

    def test_with_config_hash_excludes_self(self, sample_config: TrainingConfig):
        """config_hash 字段不应参与自身 hash 计算 (避免递归)."""
        # 同 config (除 config_hash 外其他字段相同), hash 应一致
        from dataclasses import replace
        c1 = replace(sample_config, config_hash="")
        c2 = replace(sample_config, config_hash="dummy_value")
        # with_config_hash 排除 config_hash 字段
        h1 = c1.with_config_hash().config_hash
        h2 = c2.with_config_hash().config_hash
        assert h1 == h2


# ============================================================
# 测试组 3: compute_dataset_uri
# ============================================================
class TestComputeDatasetUri:
    """ECC GAP-7: 数据 hash 计算."""

    def test_same_panel_yields_same_uri(self, sample_panel: pd.DataFrame):
        """同 panel (内容一致) → 同 dataset_uri."""
        panel_copy = sample_panel.copy()
        uri1 = compute_dataset_uri(sample_panel)
        uri2 = compute_dataset_uri(panel_copy)
        assert uri1 == uri2

    def test_different_panel_yields_different_uri(self, sample_panel: pd.DataFrame):
        """不同 panel → 不同 dataset_uri."""
        modified = sample_panel.copy()
        modified.loc[0, "y"] += 0.001  # 微小修改
        uri1 = compute_dataset_uri(sample_panel)
        uri2 = compute_dataset_uri(modified)
        assert uri1 != uri2

    def test_row_order_does_not_affect_uri(self, sample_panel: pd.DataFrame):
        """行顺序不影响 dataset_uri (按 code/date 排序后 hash)."""
        shuffled = sample_panel.sample(frac=1, random_state=42).reset_index(drop=True)
        uri1 = compute_dataset_uri(sample_panel)
        uri2 = compute_dataset_uri(shuffled)
        assert uri1 == uri2

    def test_empty_panel_yields_consistent_uri(self):
        """空 panel 也能计算 hash (不抛异常)."""
        empty = pd.DataFrame(columns=["code", "date", "y"])
        uri = compute_dataset_uri(empty)
        assert len(uri) == 64


# ============================================================
# 测试组 4: compute_code_sha
# ============================================================
class TestComputeCodeSha:
    """ECC GAP-7: 代码 hash 计算."""

    def test_same_file_yields_same_sha(self, tmp_path: Path):
        """同文件内容 → 同 code_sha."""
        f1 = tmp_path / "a.py"
        f1.write_text("print('hello')\n", encoding="utf-8")
        sha1 = compute_code_sha([f1])
        sha2 = compute_code_sha([f1])
        assert sha1 == sha2

    def test_different_content_yields_different_sha(self, tmp_path: Path):
        """不同内容 → 不同 code_sha."""
        f1 = tmp_path / "a.py"
        f1.write_text("print('hello')\n", encoding="utf-8")
        f2 = tmp_path / "b.py"
        f2.write_text("print('world')\n", encoding="utf-8")
        sha1 = compute_code_sha([f1])
        sha2 = compute_code_sha([f2])
        assert sha1 != sha2

    def test_empty_list_returns_empty_string(self):
        """空文件列表 → 空字符串 (非 hash)."""
        sha = compute_code_sha([])
        assert sha == ""

    def test_multiple_files_order_independent(self, tmp_path: Path):
        """多文件 hash 顺序无关 (内部 sorted)."""
        f1 = tmp_path / "a.py"
        f1.write_text("a\n", encoding="utf-8")
        f2 = tmp_path / "b.py"
        f2.write_text("b\n", encoding="utf-8")
        sha1 = compute_code_sha([f1, f2])
        sha2 = compute_code_sha([f2, f1])  # 反序
        assert sha1 == sha2


# ============================================================
# 测试组 5: artifact_name
# ============================================================
class TestArtifactName:
    """ECC GAP-7: artifact 命名."""

    def test_artifact_name_format(self, sample_config: TrainingConfig):
        """artifact_name 格式: {model_name}_v{short_sha}_d{yyyymmdd}.

        model_name 可能含下划线 (如 test_lgbm), 用正则匹配更稳健.
        """
        import re
        name = artifact_name(sample_config)
        # 格式: <model_name>_v<8hex>_d<yyyymmdd>
        pattern = r'^.+_v[0-9a-f]{8}_d\d{8}$'
        assert re.match(pattern, name), f"artifact_name 格式错误: {name}"
        # 验证 model_name 前缀
        assert name.startswith(sample_config.model_name)

    def test_different_code_sha_yields_different_name(self, sample_config: TrainingConfig):
        """不同 code_sha → 不同 config_hash → 不同 artifact_name.

        注意: artifact_name 优先用 config_hash (而非 code_sha), 因为 code_sha 仅标识代码版本,
        config_hash 含 code_sha + seed + dataset, 区分度更高. 此测试通过修改 code_sha 间接
        修改 config_hash, 验证最终 artifact_name 不同.
        """
        from dataclasses import replace
        other = replace(sample_config, code_sha="a" * 64).with_config_hash()
        assert artifact_name(sample_config) != artifact_name(other)

    def test_empty_code_sha_falls_back_to_config_hash(self, sample_config: TrainingConfig):
        """code_sha 为空时, 用 config_hash 兜底."""
        from dataclasses import replace
        no_code = replace(sample_config, code_sha="")
        name = artifact_name(no_code)
        short_hash = sample_config.config_hash[:8]
        assert f"v{short_hash}" in name


# ============================================================
# 测试组 6: write_manifest
# ============================================================
class TestWriteManifest:
    """ECC GAP-7: manifest.json 落盘."""

    def test_manifest_file_created(self, tmp_path: Path, sample_config: TrainingConfig, sample_importance: dict):
        """write_manifest 应创建 manifest.json 文件."""
        artifact_dir = tmp_path / "test_artifact"
        manifest_path = write_manifest(artifact_dir, sample_config, metrics=sample_importance)
        assert manifest_path.exists()
        assert manifest_path.name == "manifest.json"

    def test_manifest_contains_all_required_fields(self, tmp_path: Path, sample_config: TrainingConfig, sample_importance: dict):
        """manifest.json 必须包含所有 MANIFEST_REQUIRED_FIELDS 字段."""
        artifact_dir = tmp_path / "test_artifact"
        manifest_path = write_manifest(artifact_dir, sample_config, metrics=sample_importance)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for field_name in MANIFEST_REQUIRED_FIELDS:
            assert field_name in data, f"缺失必填字段: {field_name}"

    def test_manifest_metrics_recorded(self, tmp_path: Path, sample_config: TrainingConfig, sample_importance: dict):
        """manifest.json 的 metrics 字段应记录特征重要性."""
        artifact_dir = tmp_path / "test_artifact"
        manifest_path = write_manifest(artifact_dir, sample_config, metrics=sample_importance)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "metrics" in data
        assert data["metrics"] == sample_importance

    def test_manifest_contract_validation_optional(self, tmp_path: Path, sample_config: TrainingConfig):
        """contract_validation 字段可选 (None 时不写入)."""
        artifact_dir = tmp_path / "test_artifact"
        manifest_path = write_manifest(artifact_dir, sample_config, metrics={}, contract_validation=None)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "contract_validation" not in data

    def test_manifest_with_contract_validation(self, tmp_path: Path, sample_config: TrainingConfig):
        """contract_validation 不为 None 时应写入."""
        artifact_dir = tmp_path / "test_artifact"
        cv = {"passed": True, "violations": []}
        manifest_path = write_manifest(artifact_dir, sample_config, metrics={}, contract_validation=cv)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["contract_validation"] == cv

    def test_manifest_creates_parent_dirs(self, tmp_path: Path, sample_config: TrainingConfig):
        """artifact_dir 不存在时, write_manifest 应自动创建父目录."""
        artifact_dir = tmp_path / "deep" / "nested" / "path"
        manifest_path = write_manifest(artifact_dir, sample_config, metrics={})
        assert manifest_path.exists()

    def test_manifest_dataset_uri_matches_compute(self, tmp_path: Path, sample_config: TrainingConfig, sample_panel: pd.DataFrame):
        """manifest 的 dataset_uri 应与 compute_dataset_uri(panel) 一致."""
        artifact_dir = tmp_path / "test_artifact"
        manifest_path = write_manifest(artifact_dir, sample_config, metrics={})
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_uri = compute_dataset_uri(sample_panel)
        assert data["dataset_uri"] == expected_uri


# ============================================================
# 测试组 7: verify_reproducibility
# ============================================================
class TestVerifyReproducibility:
    """ECC GAP-7: 可复现性验证工具."""

    def test_same_config_same_importance_yields_reproducible(
        self, sample_config: TrainingConfig, sample_importance: dict
    ):
        """同 config + 同 importance → reproducible=True."""
        result = verify_reproducibility(
            sample_config, sample_config, sample_importance, sample_importance, top_k=5
        )
        assert result["config_match"] is True
        assert result["dataset_match"] is True
        assert result["code_match"] is True
        assert result["top_k_overlap"] == 1.0
        assert result["top_k_consistency"] == 1.0
        assert result["reproducible"] is True

    def test_different_config_yields_not_reproducible(
        self, sample_config: TrainingConfig, sample_importance: dict
    ):
        """不同 config → reproducible=False.

        注意: replace 不会自动重算 config_hash, 需手动调用 with_config_hash().
        """
        from dataclasses import replace
        # seed=99 → config_hash 不同
        other_config = replace(sample_config, seed=99).with_config_hash()
        result = verify_reproducibility(
            sample_config, other_config, sample_importance, sample_importance, top_k=5
        )
        assert result["config_match"] is False
        assert result["reproducible"] is False

    def test_different_importance_order_yields_partial_consistency(
        self, sample_config: TrainingConfig, sample_importance: dict
    ):
        """同 config + 不同 importance 排序 → top_k_consistency<1.0, reproducible=False.

        构造场景: 原 importance MOM_5D(150) > VOL_20D(120) > RSI_14D(95)
                  新 importance RSI_14D(150) > VOL_20D(120) > MOM_5D(95)
        → top-3 集合相同 (overlap=1.0), 但顺序不同 (consistency<1.0)
        → reproducible=False (因为顺序不一致)
        """
        # 构造不同 importance (交换 MOM_5D 和 RSI_14D 的值)
        different_importance = dict(sample_importance)
        different_importance["MOM_5D"] = 95.0   # 原值 150.5, 降低
        different_importance["RSI_14D"] = 150.0  # 原值 95.8, 提升

        result = verify_reproducibility(
            sample_config, sample_config, sample_importance, different_importance, top_k=3
        )
        # config 完全匹配
        assert result["config_match"] is True
        # top-3 集合相同 (overlap=1.0)
        assert result["top_k_overlap"] == 1.0
        # 但顺序不同 (consistency < 1.0)
        assert result["top_k_consistency"] < 1.0
        # reproducible=False (顺序不一致)
        assert result["reproducible"] is False


# ============================================================
# 测试组 8: construct_default_config (lgbm_factor_mining.py)
# ============================================================
class TestConstructDefaultConfig:
    """ECC GAP-7: lgbm_factor_mining.py 默认配置构造."""

    def test_default_config_fully_populated(self, sample_panel: pd.DataFrame):
        """construct_default_config 应返回完整填充的 config."""
        config = construct_default_config(sample_panel)
        assert config.model_name == "lgbm_factor_mining"
        assert config.seed == 42  # HC-1: 保留原 seed
        assert config.dataset_uri != ""
        assert config.code_sha != ""
        assert config.training_env != ""
        assert config.config_hash != ""
        assert config.num_boost_round == 200
        assert config.early_stopping_rounds == 20
        assert config.n_splits == 5

    def test_default_config_lgb_params_preserves_v9_baseline(self, sample_panel: pd.DataFrame):
        """HC-1: 默认 lgb_params 必须保留原 V9 基线参数 (num_leaves=31, lr=0.05, seed=42)."""
        config = construct_default_config(sample_panel)
        assert config.lgb_params["num_leaves"] == 31
        assert config.lgb_params["learning_rate"] == 0.05
        assert config.lgb_params["feature_fraction"] == 0.8
        assert config.lgb_params["bagging_fraction"] == 0.8
        assert config.lgb_params["bagging_freq"] == 5
        assert config.lgb_params["seed"] == 42

    def test_default_config_feature_list_from_panel(self, sample_panel: pd.DataFrame):
        """feature_list 应从 panel 列提取 (排除 code/date/y)."""
        config = construct_default_config(sample_panel)
        expected = tuple(c for c in sample_panel.columns if c not in ("code", "date", "y"))
        assert config.feature_list == expected


# ============================================================
# 测试组 9: 同 config+seed+dataset 重跑 → importance top-10 一致率 100%
# ============================================================
class TestReproducibilityScenario:
    """ECC GAP-7 验收核心: 同 config+seed+dataset 重跑一致性."""

    def test_same_config_same_seed_same_dataset_yields_same_importance(
        self, sample_config: TrainingConfig, sample_importance: dict
    ):
        """同 config+seed+dataset 重跑, importance top-10 一致率 100%.

        模拟场景: 同一研究员在不同时间, 用同一份 panel + 同一份代码 + 同一个 seed 跑两次训练,
        得到的特征重要性 top-10 应完全一致 (顺序 + 因子名).

        注意: 本测试不跑真实 lightgbm, 而是验证复现性基础设施的正确性.
        lightgbm 自身的 seed 机制由其保证.
        """
        # 模拟两次训练输出 (lightgbm 同 seed+同 data 应输出相同 importance)
        importance_run_1 = dict(sample_importance)
        importance_run_2 = dict(sample_importance)  # 完全相同

        result = verify_reproducibility(
            sample_config, sample_config, importance_run_1, importance_run_2, top_k=5
        )

        # 验收: reproducible == True
        assert result["reproducible"] is True, (
            f"同 config+seed+dataset 应可复现, 但 reproducible=False: {result}"
        )
        assert result["top_k_overlap"] == 1.0
        assert result["top_k_consistency"] == 1.0

    def test_different_seed_yields_different_artifact_name(
        self, sample_panel: pd.DataFrame, sample_config: TrainingConfig
    ):
        """seed=42 vs seed=43 → artifact_name 不同 (区分两次实验)."""
        from dataclasses import replace
        config_42 = sample_config
        config_43 = replace(sample_config, seed=43)
        # config_hash 也应不同 (seed 是字段之一)
        config_42_hashed = config_42.with_config_hash()
        config_43_hashed = config_43.with_config_hash()
        # artifact_name 应不同
        assert artifact_name(config_42_hashed) != artifact_name(config_43_hashed)

    def test_manifest_recorded_for_reproducible_run(
        self, tmp_path: Path, sample_config: TrainingConfig, sample_importance: dict
    ):
        """可复现训练应能落盘 manifest, 包含足够信息复现."""
        artifact_dir = tmp_path / artifact_name(sample_config)
        manifest_path = write_manifest(artifact_dir, sample_config, metrics=sample_importance)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))

        # 验收: manifest 含复现所需的全部信息
        assert data["seed"] == 42
        assert data["dataset_uri"] != ""
        assert data["code_sha"] != ""
        assert data["config_hash"] != ""
        assert data["lgb_params"]["seed"] == 42
        assert len(data["feature_list"]) > 0
        assert data["label_def"] == "forward_return_5d"
        assert data["label_horizon"] == 5
