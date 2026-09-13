"""QmtBrokerAPI 导入降级 fail-safe 回归 (2026-09-13).

背景 (同源缺陷, 与 `scripts/verify_qmt_paper_chain.py` 口径不一致):
    `ms_strategy/src/execution/qmt_broker.py` 的 `XTQUANT_AVAILABLE` 早已是**能力级**
    (三子模块导入), 但 `except` **只捕 `ImportError`**; 而 `.pyd` 加载失败的真实形态
    (ABI 不匹配 / 依赖 DLL 缺失) 还会抛 `OSError` / `AttributeError` / `ValueError`。
    对照 `scripts/verify_qmt_paper_chain.py` 的 `_xtquant_available()` 已捕
    `(ImportError, OSError, AttributeError, ValueError)` ⇒ 两处口径不一致。

后果: 一旦在 Python≤3.13 环境装上 xtquant 而二进制加载失败, 异常会**冒泡使模块导入崩溃**,
连带炸掉所有 `import qmt_broker` 的链路 (含 fail-open 降级设计本身), 而不是降级为
`XTQUANT_AVAILABLE=False`。

编写口径 (遵循项目铁律):
    - **修复类须附「修复前会失败」的回归**: 用例 1/3 (`.pyd` 形态抛 OSError/ValueError)
      在放宽 `except` 面前**必红** (子进程导入崩溃、退出码 ≠ 0), 修复后转绿。
    - **观测路径 fail-open 降级, 但必须留痕不静默**: 同时断言 warning 已发出。
    - 放置于 `tests/unit/` (禁 `tests/e2e/`, 祖先目录关键字会令用例天生被 skip)。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TARGET = "ms_strategy.src.execution.qmt_broker"

# 子进程探针: 在**脏 xtquant** 存在的前提下导入目标模块
_PROBE_SRC = f"""
import importlib
m = importlib.import_module("{_TARGET}")
assert m.XTQUANT_AVAILABLE is False, (
    "xtquant 加载失败却判为可用 -> 会造出假能力"
)
print("PROBE_OK XTQUANT_AVAILABLE = False")
"""


def _write_fake_xtquant(tmp_path: Path, body: str) -> None:
    pkg = tmp_path / "xtquant"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(body, encoding="utf-8")


def _run_probe(tmp_path: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # 假 xtquant 必须**抢先**于 site-packages
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(_PROJECT_ROOT)])
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", _PROBE_SRC],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_PROJECT_ROOT),
        timeout=timeout,
        env=env,
        check=False,
    )


# ============================================================
# 核心回归: .pyd 加载失败必须降级, 不得让导入崩溃
# ============================================================
@pytest.mark.parametrize(
    ("name", "exc_type", "message"),
    [
        ("oserror", "OSError", "DLL load failed while importing datacenter"),
        ("valueerror", "ValueError", "module compiled against wrong ABI"),
        ("attributeerror", "AttributeError", "module 'xtquant' has no attribute"),
    ],
)
def test_pyd_load_failure_degrades_instead_of_crashing(
    tmp_path: Path, name: str, exc_type: str, message: str
):
    """`make_kwargs` 形态: 包内抛非 ImportError → 必须降级为 False 且模块可导入.

    修复前 (`except ImportError`) 本用例**必红**: 异常冒泡 -> 子进程导入崩溃 -> RC≠0。
    """
    _write_fake_xtquant(
        tmp_path,
        f"raise {exc_type}({message!r})\n",
    )

    proc = _run_probe(tmp_path)

    assert proc.returncode == 0, (
        f"[{name}] xtquant 抛 {exc_type} 时模块导入崩溃 -> "
        "本模块会把整条 import 链路炸掉, 而非降级 XTQUANT_AVAILABLE=False\n"
        f"stdout={proc.stdout[-1000:]}\nstderr={proc.stderr[-1000:]}"
    )
    assert "PROBE_OK XTQUANT_AVAILABLE = False" in proc.stdout, proc.stdout[-1000:]


def test_pyd_load_failure_is_logged_not_silent(tmp_path: Path):
    """降级必须留痕 (不许静默): warning 要带异常类型与原因, 便于运维定位."""
    _write_fake_xtquant(
        tmp_path, "raise OSError('DLL load failed while importing datacenter')\n"
    )

    proc = _run_probe(tmp_path)

    assert proc.returncode == 0, proc.stderr[-1000:]
    combined = proc.stdout + proc.stderr
    assert "xtquant 不可用" in combined, (
        "降级未留任何日志 -> 静默失败 (违反'观测路径 fail-open 但须留日志')"
    )
    assert "OSError" in combined, "日志未带异常类型, 无法区分'未安装'与'装上但加载失败'"


# ============================================================
# 不回归: 命名空间级安装 (Py3.14 真实形态) 仍须判不可用
# ============================================================
def test_namespace_only_install_still_unavailable(tmp_path: Path):
    """`import xtquant` 成功但子模块全废 ⇒ 必须判不可用 (AC-002 假绿防复发)."""
    _write_fake_xtquant(tmp_path, "")

    proc = _run_probe(tmp_path)

    assert proc.returncode == 0, proc.stderr[-1000:]
    assert "PROBE_OK XTQUANT_AVAILABLE = False" in proc.stdout


def test_healthy_xtquant_is_still_detected_as_available(tmp_path: Path):
    """反向对照: 能力齐备时必须判**可用** —— 防止放宽 except 把判据变成恒 False.

    这是"防恒 FAIL 废门禁"的镜像断言: 放宽捕获面不得让真可用环境被误判。
    """
    pkg = tmp_path / "xtquant"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for sub in ("xtconstant", "xtdata", "xttrader"):
        (pkg / f"{sub}.py").write_text("", encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(_PROJECT_ROOT)])
    env["PYTHONIOENCODING"] = "utf-8"
    probe = (
        "import importlib\n"
        f'm = importlib.import_module("{_TARGET}")\n'
        "assert m.XTQUANT_AVAILABLE is True, "
        "'能力齐备却判不可用 -> 判据过严, 会把真环境误杀'\n"
        "print('PROBE_OK XTQUANT_AVAILABLE = True')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_PROJECT_ROOT),
        timeout=180,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, (
        "能力齐备的 xtquant 被判不可用 -> 判据恒 False (废门禁)\n"
        f"stdout={proc.stdout[-1000:]}\nstderr={proc.stderr[-1000:]}"
    )


# ============================================================
# 口径一致性: 两处 xtquant 能力判据的捕获面必须同宽
# ============================================================
def _tuple_names_from_ast(node: ast.AST) -> set[str]:
    """把 `(A, B, C)` 形式的元组字面量解析成名字集合."""
    if not isinstance(node, ast.Tuple):
        raise AssertionError(f"期望元组字面量, 实际 {type(node).__name__}")
    names: set[str] = set()
    for elt in node.elts:
        if isinstance(elt, ast.Name):
            names.add(elt.id)
        elif isinstance(elt, ast.Attribute):  # 兼容 err.ImportError 形态
            names.add(elt.attr)
        else:
            raise AssertionError(f"元组元素形态未知: {ast.dump(elt)[:80]}")
    return names


def _broker_capture_surface() -> set[str]:
    """`qmt_broker.py` 实际使用的捕获面 (取自 `_XTQUANT_IMPORT_ERRORS` 赋值)."""
    tree = ast.parse(
        (
            _PROJECT_ROOT / "ms_strategy" / "src" / "execution" / "qmt_broker.py"
        ).read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_XTQUANT_IMPORT_ERRORS"
            for t in node.targets
        ):
            return _tuple_names_from_ast(node.value)
    raise AssertionError("qmt_broker.py 未定义 _XTQUANT_IMPORT_ERRORS")


def _paper_chain_capture_surface() -> set[str]:
    """`verify_qmt_paper_chain.py` 中 `_xtquant_available()` 实际捕获的异常集合."""
    tree = ast.parse(
        (_PROJECT_ROOT / "scripts" / "verify_qmt_paper_chain.py").read_text(
            encoding="utf-8"
        )
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_xtquant_available":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Try):
                    for handler in sub.handlers:
                        raw = handler.type
                        node_type = (
                            raw
                            if isinstance(raw, ast.Tuple)
                            else ast.Tuple(elts=[raw], ctx=ast.Load())
                        )
                        return _tuple_names_from_ast(node_type)
    raise AssertionError("verify_qmt_paper_chain.py 未找到 _xtquant_available() 的捕获面")


def test_capture_surface_aligned_with_paper_chain_check():
    """两处「xtquant 是否可用」判据的**实际捕获面**必须完全一致.

    必须走 AST 取**代码**里的元组: 早先用"全文 grep 异常名"会被注释里的文字
    误判为已对齐 (实测漏拦) ⇒ 断言不可采信。捕获面窄的那一处会先崩。
    """
    broker_surface = _broker_capture_surface()
    paper_surface = _paper_chain_capture_surface()

    assert broker_surface == paper_surface, (
        "两处 xtquant 能力判据捕获面不一致 -> 窄的一处会在 .pyd 加载失败时崩溃而非降级\n"
        f"qmt_broker.py          = {sorted(broker_surface)}\n"
        f"verify_qmt_paper_chain = {sorted(paper_surface)}"
    )
    # 捕获面必须**真的**覆盖非 ImportError 形态 (防退化成只捕 ImportError)
    assert {"OSError", "AttributeError", "ValueError"} <= broker_surface, (
        f"捕获面缺 .pyd 失败形态 -> 会崩溃而非降级: {sorted(broker_surface)}"
    )
