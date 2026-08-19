"""
LightGBM 训练管道可复现性模块 — 兼容转发层
=============================================

⚠️ 2026-08-09 G5 物理隔离: 本模块已迁移至 utils/lgbm_reproducibility.py
（原仅依赖 pandas/标准库 + 本地常量, 无 research 内部依赖, 属纯生产级模块）。

本文件保留为兼容转发壳, 仅供历史/归档代码通过
`from research.lgbm_reproducibility import ...` 引用时使用, 避免破坏可追溯性。

生产代码与测试应直接 `from utils.lgbm_reproducibility import ...`。
"""
from utils.lgbm_reproducibility import *  # noqa: F401,F403 — 向后兼容转发
from utils.lgbm_reproducibility import (
    MANIFEST_REQUIRED_FIELDS,
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

__all__ = [
    "ReproducibilityError",
    "ManifestWriteError",
    "TrainingConfig",
    "compute_dataset_uri",
    "compute_code_sha",
    "artifact_name",
    "MANIFEST_REQUIRED_FIELDS",
    "write_manifest",
    "verify_reproducibility",
    "construct_default_config",
]
