"""安全反序列化收口 — pickle/joblib 加载的唯一入口 (CWE-502 / bandit B301).

背景
----
2026-09-12 Issue #30 安全专项把「首方代码 bandit MEDIUM+ 归零」做成事后审计,
过程中扫出同一类**结构性**问题: `pickle.load(s)` 散落在 5 个模块里, 每个都在
自己旁边复制一份 SHA256 侧车校验, 而**「侧车不存在」这一分支全都静默放行**
(只 warning, 继续反序列化)。也就是说: 覆盖面看着有, 真实攻击路径 (投毒者直接
替换 .pkl, 不提供侧车) 全程无事发生 —— 典型的「审计口径已达标、实质防护缺位」。

本模块把反序列化收口为一处, 并给出**显式策略**:

- `verify_pickle_integrity(path)` — 返回 `(verified, digest)`:
    - 有 `expected_sha256` 或 `<path>.sha256` 侧车 → 严格比对, 不一致抛
      `PickleIntegrityError`;
    - 无侧车 → 计算 digest 并 `warning` 记录审计线索, `verified=False`。
- `load_pickle(path, expected_sha256=None, *, require_integrity=None)` —
    唯一反序列化入口。`require_integrity` 缺省走环境开关:

        QUANT_REQUIRE_PICKLE_INTEGRITY=1   (推荐在生产机 / CI 打开)

    开启后「无侧车」视为**校验失败**并抛 `PickleIntegrityError` (fail-closed);
    未开启时保持既有行为 (warning 放行), 因此**默认行为与接入前逐字节一致**,
    不会把现有单机流程直接打断。

调用方约定: 除本模块外, 项目内**禁止**直接调用 `pickle.load(s)`。新增入口请
复用 `load_pickle`, 而不是再复制一份校验。
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
from pathlib import Path
from typing import Any

logger = logging.getLogger("safe_pickle")

# 生产机/CI 建议置 1: 无 SHA256 侧车即拒绝反序列化 (fail-closed)
_ENV_REQUIRE_INTEGRITY = "QUANT_REQUIRE_PICKLE_INTEGRITY"

_TRUTHY = frozenset({"1", "true", "TRUE", "yes", "on", "ON"})


class PickleIntegrityError(ValueError):
    """pickle 完整性校验失败 (哈希不一致 or 缺侧车且已开启强制校验)."""


def require_integrity_default() -> bool:
    """当前环境下「无侧车」是否视为失败 (由 `QUANT_REQUIRE_PICKLE_INTEGRITY` 决定)."""
    return os.environ.get(_ENV_REQUIRE_INTEGRITY, "").strip() in _TRUTHY


def sha256_file(path: str | Path) -> str:
    """流式计算文件 SHA256 (分块读取, 兼容大文件)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sidecar_path(path: str | Path) -> Path:
    """`<path>.sha256` 侧车文件路径."""
    return Path(str(path) + ".sha256")


def verify_pickle_integrity(
    path: str | Path,
    expected_sha256: str | None = None,
) -> tuple[bool, str]:
    """校验 pickle 文件完整性.

    Returns:
        `(verified, digest)`: verified=True 表示已与**可信哈希**比对通过;
        False 表示无侧车/未提供期望值 (digest 为实测值, 供调用方审计或拒绝).

    Raises:
        PickleIntegrityError: 提供了期望哈希但实测不一致 (篡改/损坏).
    """
    p = Path(path)
    expected = (expected_sha256 or "").strip() or None
    if expected is None:
        sc = sidecar_path(p)
        if sc.exists():
            expected = (sc.read_text(encoding="utf-8").strip().split() or [""])[0] or None

    digest = sha256_file(p)
    if expected is not None:
        if digest != expected.lower():
            raise PickleIntegrityError(
                f"模型完整性校验失败: {p} (expected={expected[:12]}..., "
                f"actual={digest[:12]}...) — 文件可能被篡改, 拒绝加载"
            )
        return True, digest

    logger.warning(
        "模型无 SHA256 侧车, 记录哈希作审计线索: %s sha256=%s", p, digest
    )
    return False, digest


def loads_verified(raw: bytes, *, integrity_checked: bool) -> Any:
    """反序列化**已读入内存**的 pickle 字节, 并把「是否已通过校验」显式化.

    适用于调用方自身已完成读盘 + 校验 (例如需要同时复用原始字节做哈希) 的场景。
    与 `load_pickle` 共享同一套环境策略, 避免每个调用点各写一遍「无侧车怎么办」。

    Args:
        raw: pickle 字节.
        integrity_checked: 调用方是否已完成与可信哈希的比对.

    Raises:
        PickleIntegrityError: 未完成校验且已开启 `QUANT_REQUIRE_PICKLE_INTEGRITY`.

    Notes:
        `# nosec B301` 是实质豁免: 未校验时由策略决定 fail-closed, 不存在
        「未校验 + 放行 + 无审计」的路径。
    """
    if not integrity_checked and require_integrity_default():
        digest = hashlib.sha256(raw).hexdigest()
        raise PickleIntegrityError(
            f"模型未通过完整性校验且已开启强制校验 ({_ENV_REQUIRE_INTEGRITY}=1), "
            f"拒绝反序列化 sha256={digest[:12]}"
        )
    if not integrity_checked:
        logger.warning(
            "模型未通过完整性校验, 记录哈希作审计线索: sha256=%s",
            hashlib.sha256(raw).hexdigest(),
        )
    return pickle.loads(raw)  # noqa: S301  # nosec B301 — 见本函数 docstring


def load_pickle(
    path: str | Path,
    expected_sha256: str | None = None,
    *,
    require_integrity: bool | None = None,
) -> Any:
    """反序列化 pickle 文件 (全项目唯一入口), 先做完整性校验.

    Args:
        path: pickle 文件路径.
        expected_sha256: 期望哈希; None 则尝试 `<path>.sha256` 侧车.
        require_integrity: 无侧车时是否拒绝; None 则读环境开关.

    Raises:
        FileNotFoundError / ValueError: 路径不存在 / 不是文件.
        PickleIntegrityError: 哈希不一致, 或 (强制校验时) 缺侧车.

    Notes:
        `# nosec B301` 在本模块是**实质豁免**而非消音 —— 反序列化前必经
        `verify_pickle_integrity`; 缺侧车时由 `QUANT_REQUIRE_PICKLE_INTEGRITY`
        决定 fail-closed, 不存在「未校验却放行且无审计」的路径。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"模型文件不存在: {p}")
    if not p.is_file():
        raise ValueError(f"模型路径不是文件: {p}")

    verified, digest = verify_pickle_integrity(p, expected_sha256)
    if not verified:
        required = require_integrity_default() if require_integrity is None else require_integrity
        if required:
            raise PickleIntegrityError(
                f"缺少 SHA256 侧车且已开启强制校验 ({_ENV_REQUIRE_INTEGRITY}=1), "
                f"拒绝反序列化: {p} sha256={digest}"
            )

    with open(p, "rb") as f:
        return pickle.load(f)  # noqa: S301  # nosec B301 — 见本函数 docstring: 收口 + 显式策略
