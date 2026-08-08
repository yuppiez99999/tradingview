"""
LightGBM 训练管道可复现性模块 — GAP-7 交付物.

ECC mle-workflow MLE-04/09 修复:
    - MLE-04: Training is reproducible from code, config, data version, and seed
    - MLE-09: Model artifact carries version, config, dataset reference, and preprocessing

设计原则:
    1. TrainingConfig frozen=True (coding-standards 不可变优先)
    2. manifest.json 落盘失败不阻断训练 (warn_only)
    3. 不修改 lgbm_factor_mining.py 因子计算逻辑 (HC-1 V9 基线保护)
    4. hash 算法用 sha256 (确定性, 跨平台一致)

硬约束:
    - HC-1: 不破坏 V9 基线 (本模块独立, 不修改因子计算)
    - HC-5: ConfigManager 4 级优先级不受影响 (本模块独立)

API:
    from research.lgbm_reproducibility import (
        TrainingConfig, compute_dataset_uri, compute_config_hash,
        compute_code_sha, artifact_name, write_manifest, verify_reproducibility
    )

    config = TrainingConfig(
        model_name="v9_lgb",
        seed=42,
        lgb_params={"num_leaves": 31, "learning_rate": 0.05, ...},
        feature_list=("MOM_5D", "VOL_20D", ...),
        label_def="forward_return_5d",
    )
    config = config.with_dataset(panel)            # 计算 dataset_uri + dataset_sha256
    config = config.with_code_sha([Path("research/lgbm_factor_mining.py")])
    config = config.with_environment(env="sim")    # 填充 python_version / lib_versions
    config = config.with_config_hash()             # 计算 config_hash (含 dataset_uri + code_sha)
    name = artifact_name(config)
    manifest_path = write_manifest(OUTPUT_DIR, config, metrics=feature_importance_dict)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

logger = logging.getLogger("lgbm_reproducibility")


# ============================================================
# 异常定义
# ============================================================
class ReproducibilityError(Exception):
    """训练可复现性错误."""


class ManifestWriteError(ReproducibilityError):
    """manifest.json 落盘失败."""


# ============================================================
# TrainingConfig (不可变, frozen=True)
# ============================================================
@dataclass(frozen=True)
class TrainingConfig:
    """训练配置 - 不可变, 用于复现训练.

    所有 hash 字段(dataset_uri / dataset_sha256 / config_hash / code_sha)
    通过 with_* 方法链式构造, 构造后不可修改, 符合 coding-standards 不可变优先原则.
    """

    # === 模型标识 ===
    model_name: str

    # === 随机种子 ===
    seed: int = 42

    # === LGB 超参 ===
    lgb_params: dict[str, Any] = field(default_factory=dict)
    num_boost_round: int = 200
    early_stopping_rounds: int = 20

    # === TimeSeriesSplit ===
    n_splits: int = 5

    # === 特征列表 (顺序敏感) ===
    feature_list: tuple[str, ...] = field(default_factory=tuple)

    # === 标签定义 ===
    label_def: str = "forward_return_5d"
    label_horizon: int = 5

    # === 时间分割 ===
    train_split: tuple[str, str] = ("2023-01-01", "2024-12-31")
    val_split: tuple[str, str] = ("2025-01-01", "2025-12-31")
    test_split: tuple[str, str] = ("2026-01-01", "2026-06-30")

    # === 数据 hash (通过 with_dataset 填充) ===
    dataset_uri: str = ""
    dataset_sha256: str = ""
    dataset_shape: tuple[int, int] = (0, 0)
    dataset_row_count: int = 0
    dataset_date_range: tuple[str, str] = ("", "")

    # === 代码 hash (通过 with_code_sha 填充) ===
    code_sha: str = ""

    # === 配置 hash (通过 with_config_hash 填充, 含 dataset_uri + code_sha) ===
    config_hash: str = ""

    # === 环境信息 (通过 with_environment 填充) ===
    training_env: str = ""
    python_version: str = ""
    lib_versions: dict[str, str] = field(default_factory=dict)
    created_at: str = ""

    # ============================================================
    # 链式构造方法 (返回新实例, 不可变)
    # ============================================================
    def with_dataset(self, panel: pd.DataFrame) -> TrainingConfig:
        """计算并填充 dataset_uri + dataset_sha256 + 形状信息.

        Args:
            panel: 因子面板, 含 code/date/y/factor_columns

        Returns:
            新的 TrainingConfig 实例 (原实例不变)
        """
        dataset_uri = compute_dataset_uri(panel)
        date_col = panel.get("date")
        if date_col is not None and len(date_col) > 0:
            dates_sorted = sorted(date_col.astype(str))
            date_range: tuple[str, str] = (dates_sorted[0], dates_sorted[-1])
        else:
            date_range = ("", "")

        return replace(
            self,
            dataset_uri=dataset_uri,
            dataset_sha256=dataset_uri,
            dataset_shape=tuple(int(x) for x in panel.shape),  # type: ignore[arg-type]
            dataset_row_count=len(panel),
            dataset_date_range=date_range,
        )

    def with_code_sha(self, file_paths: Sequence[Path]) -> TrainingConfig:
        """计算并填充 code_sha (源代码文件的 sha256).

        Args:
            file_paths: 源代码文件路径列表 (如 lgbm_factor_mining.py + 因子计算所在文件)

        Returns:
            新的 TrainingConfig 实例
        """
        code_sha = compute_code_sha(list(file_paths))
        return replace(self, code_sha=code_sha)

    def with_environment(self, env: str = "") -> TrainingConfig:
        """填充环境信息 (python_version / lib_versions / training_env / created_at).

        Args:
            env: 训练环境标识 (ci / sim / live / local), 空字符串自动检测

        Returns:
            新的 TrainingConfig 实例
        """
        lib_versions: dict[str, str] = {}
        for lib_name in ("lightgbm", "pandas", "numpy", "sklearn", "scipy"):
            try:
                mod = __import__(lib_name)
                lib_versions[lib_name] = getattr(mod, "__version__", "unknown")
            except ImportError:
                lib_versions[lib_name] = "not_installed"

        return replace(
            self,
            training_env=env or _detect_training_env(),
            python_version=sys.version.split()[0],
            lib_versions=lib_versions,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

    def with_config_hash(self) -> TrainingConfig:
        """计算并填充 config_hash (基于其他所有字段, 不含 config_hash 自身).

        Returns:
            新的 TrainingConfig 实例
        """
        d = asdict(self)
        d.pop("config_hash", None)  # 排除自身避免递归
        # 序列化为稳定 JSON (sort_keys 确保字段顺序无关, ensure_ascii 跨平台)
        canonical = json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
        config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return replace(self, config_hash=config_hash)


def _detect_training_env() -> str:
    """检测当前训练环境 (ci / sim / live / local)."""
    env = os.environ.get("TRADING_ENV", "") or os.environ.get("QUANT_ENV", "")
    if env:
        return env
    if os.environ.get("CI"):
        return "ci"
    return "local"


# ============================================================
# hash 计算函数
# ============================================================
def compute_dataset_uri(panel: pd.DataFrame) -> str:
    """计算 panel 的 sha256 hash 作为 dataset_uri.

    实现:
        1. 将 panel 按 (code, date) 排序确保顺序稳定
        2. 用 CSV 字节流计算 sha256 (跨平台一致, 不含索引)
        3. 包含所有列名 (schema 一致性)

    Args:
        panel: 因子面板, 含 code/date/y/factor_columns

    Returns:
        64 字符 sha256 hex 字符串
    """
    sort_cols = [c for c in ("code", "date") if c in panel.columns]
    if sort_cols:
        panel_sorted = panel.sort_values(sort_cols).reset_index(drop=True)
    else:
        panel_sorted = panel.reset_index(drop=True)

    csv_bytes = panel_sorted.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(csv_bytes).hexdigest()


def compute_code_sha(file_paths: list[Path]) -> str:
    """计算源代码文件的 sha256 hash.

    实现:
        1. 对每个文件读取字节
        2. 按 path 排序后拼接 (顺序稳定)
        3. 计算整体 sha256 (含 path 名作为 salt, 防止不同位置同名文件 hash 撞车)

    Args:
        file_paths: 源代码文件路径列表

    Returns:
        64 字符 sha256 hex 字符串, 空列表返回 ""
    """
    if not file_paths:
        return ""

    hasher = hashlib.sha256()
    for path in sorted(file_paths):
        try:
            content = Path(path).read_bytes()
            hasher.update(str(path).encode("utf-8"))
            hasher.update(b"\x00")  # separator
            hasher.update(content)
            hasher.update(b"\x00")
        except OSError as e:
            logger.warning(f"读取文件失败 {path}: {e}")
            hasher.update(str(path).encode("utf-8"))
            hasher.update(b"\x00")
    return hasher.hexdigest()


def artifact_name(config: TrainingConfig) -> str:
    """生成 artifact 名称.

    格式: {model_name}_v{short_sha}_d{yyyymmdd}
        short_sha = config_hash[:8] (优先, 区分不同 seed/config 的训练)
        若 config_hash 为空, 退化为 code_sha[:8] (同代码同 artifact, 用于早期阶段)

    设计决策: 优先用 config_hash 而非 code_sha, 因为:
        - 不同 seed 应产出不同 artifact (避免覆盖)
        - config_hash 含 seed/dataset/code_sha, 唯一性更强
        - code_sha 仅用于"代码版本"识别, 不用于 artifact 命名

    Args:
        config: 训练配置 (需已填充 config_hash 或 code_sha)

    Returns:
        artifact 名称字符串 (如 v9_lgb_v1a2b3c4d_d20260729)
    """
    short_sha = (config.config_hash or config.code_sha)[:8]
    date_str = datetime.now().strftime("%Y%m%d")
    return f"{config.model_name}_v{short_sha}_d{date_str}"


# ============================================================
# manifest.json 落盘
# ============================================================
MANIFEST_REQUIRED_FIELDS: tuple[str, ...] = (
    "manifest_version",
    "artifact_name",
    "model_name",
    "created_at",
    "training_env",
    "seed",
    "dataset_uri",
    "dataset_sha256",
    "dataset_shape",
    "dataset_row_count",
    "dataset_date_range",
    "code_sha",
    "config_hash",
    "lgb_params",
    "feature_list",
    "label_def",
    "label_horizon",
    "num_boost_round",
    "early_stopping_rounds",
    "n_splits",
    "train_split",
    "val_split",
    "test_split",
    "python_version",
    "lib_versions",
    "metrics",
)


def write_manifest(
    artifact_dir: Path,
    config: TrainingConfig,
    metrics: dict[str, Any] | None = None,
    contract_validation: dict[str, Any] | None = None,
) -> Path:
    """落盘 manifest.json (含 17+ 字段 Iteration Compact 子集).

    Args:
        artifact_dir: artifact 目录 (不存在则创建)
        config: 训练配置 (需已调用 with_dataset / with_code_sha / with_config_hash / with_environment)
        metrics: 训练指标 (feature_importance / fold_scores / mse / r2 等)
        contract_validation: 数据契约校验结果 (GAP-8 写入, None 则不写入此字段)

    Returns:
        manifest.json 文件路径

    Raises:
        ManifestWriteError: 落盘失败 (但调用方应捕获并 warn_only, 不阻断训练)
    """
    try:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, Any] = {
            "manifest_version": "1.0",
            "artifact_name": artifact_name(config),
            "model_name": config.model_name,
            "created_at": config.created_at or datetime.now().isoformat(timespec="seconds"),
            "training_env": config.training_env,
            "seed": config.seed,
            "dataset_uri": config.dataset_uri,
            "dataset_sha256": config.dataset_sha256,
            "dataset_shape": list(config.dataset_shape),
            "dataset_row_count": config.dataset_row_count,
            "dataset_date_range": list(config.dataset_date_range),
            "code_sha": config.code_sha,
            "config_hash": config.config_hash,
            "lgb_params": dict(config.lgb_params),
            "feature_list": list(config.feature_list),
            "label_def": config.label_def,
            "label_horizon": config.label_horizon,
            "num_boost_round": config.num_boost_round,
            "early_stopping_rounds": config.early_stopping_rounds,
            "n_splits": config.n_splits,
            "train_split": list(config.train_split),
            "val_split": list(config.val_split),
            "test_split": list(config.test_split),
            "python_version": config.python_version,
            "lib_versions": dict(config.lib_versions),
            "metrics": dict(metrics) if metrics else {},
        }
        if contract_validation is not None:
            manifest["contract_validation"] = dict(contract_validation)

        manifest_path = artifact_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info(f"manifest.json 已落盘: {manifest_path}")
        return manifest_path
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.warning(f"manifest.json 落盘失败 (训练继续): {e}")
        raise ManifestWriteError(str(e)) from e


# ============================================================
# 复现性验证工具
# ============================================================
def verify_reproducibility(
    config_a: TrainingConfig,
    config_b: TrainingConfig,
    importance_a: dict[str, float],
    importance_b: dict[str, float],
    top_k: int = 10,
) -> dict[str, Any]:
    """验证两次训练的可复现性.

    用于 tests/unit/test_lgbm_reproducibility.py 自动化验证.

    Args:
        config_a: 第一次训练配置
        config_b: 第二次训练配置
        importance_a: 第一次特征重要性 {factor_name: importance}
        importance_b: 第二次特征重要性
        top_k: 比较前 K 个因子 (默认 10)

    Returns:
        验证结果 dict:
            config_match: bool - 配置 hash 是否一致
            dataset_match: bool - dataset_uri 是否一致
            code_match: bool - code_sha 是否一致
            top_k_overlap: float - top-K 集合重叠率 (0-1)
            top_k_consistency: float - top-K 顺序一致率 (0-1)
            reproducible: bool - 是否可复现 (config+dataset+code 一致 + top-K 一致率 100%)
    """
    top_a = [k for k, _ in sorted(importance_a.items(), key=lambda x: -x[1])[:top_k]]
    top_b = [k for k, _ in sorted(importance_b.items(), key=lambda x: -x[1])[:top_k]]
    overlap = len(set(top_a) & set(top_b)) / max(top_k, 1)
    same_order = sum(1 for a, b in zip(top_a, top_b) if a == b) / max(top_k, 1)

    config_match = config_a.config_hash == config_b.config_hash
    dataset_match = config_a.dataset_uri == config_b.dataset_uri
    code_match = config_a.code_sha == config_b.code_sha

    return {
        "config_match": config_match,
        "dataset_match": dataset_match,
        "code_match": code_match,
        "top_k_overlap": overlap,
        "top_k_consistency": same_order,
        "reproducible": bool(
            config_match
            and dataset_match
            and code_match
            and overlap == 1.0
            and same_order == 1.0
        ),
    }
