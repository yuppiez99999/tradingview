"""G6 mypy 基线门禁轻量单测 (2026-09-04).

不实跑全量 mypy (慢), 通过 monkeypatch 替换 gate 内部 _count_current_errors 与
BASELINE_FILE 来验证 delta 判定逻辑 + fail-closed 行为:

- current > baseline  -> RC=1 (阻断新增类型债)
- current == baseline -> RC=0 (持平放行)
- current <  baseline -> RC=0 (存量收敛放行)
- 基线缺失时 current>0   -> RC=1 (fail-closed, CI 口径)
- --update 重冻结 -> 写回当前 error 行, RC=0
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import mypy_baseline_gate as gate  # noqa: E402


def _write_baseline(path: Path, error_lines) -> None:
    path.write_text("\n".join(error_lines) + "\n", encoding="utf-8")


def _wire(monkeypatch, tmp_path, baseline_lines, current_count, current_text, args):
    """将 gate 的基线文件/计数函数/sys.argv 替换为受控对象."""
    bf = tmp_path / "baseline.txt"
    if baseline_lines is not None:
        _write_baseline(bf, baseline_lines)
    monkeypatch.setattr(gate, "BASELINE_FILE", bf)
    monkeypatch.setattr(gate, "_count_current_errors",
                        lambda: (current_count, current_text))
    monkeypatch.setattr(sys, "argv", ["mypy_baseline_gate.py"] + args)
    return bf


def test_blocks_when_current_exceeds_baseline(monkeypatch, tmp_path):
    # 基线 2 行, 当前 3 行 -> 增量 +1, 必须拦截
    _wire(monkeypatch, tmp_path, ["a.py:1: error: x", "b.py:1: error: y"],
          3, "a.py:1: error: x\nb.py:1: error: y\nc.py:1: error: z\n", [])
    assert gate.main() == 1


def test_passes_when_current_equals_baseline(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, ["a.py:1: error: x", "b.py:1: error: y"],
          2, "a.py:1: error: x\nb.py:1: error: y\n", [])
    assert gate.main() == 0


def test_passes_when_current_below_baseline(monkeypatch, tmp_path):
    # 存量收敛: 当前 < 基线, 允许 (不阻断存量清理进程)
    _wire(monkeypatch, tmp_path, ["a.py:1: error: x", "b.py:1: error: y"],
          1, "a.py:1: error: x\n", [])
    assert gate.main() == 0


def test_baseline_missing_fail_closed(monkeypatch, tmp_path, capsys):
    # 基线文件不存在 -> _read_baseline 返回 0; 当前仍有 1 error -> 必须拦截 (fail-closed)
    _wire(monkeypatch, tmp_path, None,
          1, "a.py:1: error: x\n", [])
    assert gate.main() == 1
    assert "FAIL" in capsys.readouterr().out


def test_update_refreezes_baseline(monkeypatch, tmp_path):
    # --update: 把当前 3 个 error 行写回基线文件
    bf = _wire(monkeypatch, tmp_path, ["old:1: error: stale"],
               3, "a.py:1: error: x\nb.py:1: error: y\nc.py:1: error: z\n",
               ["--update"])
    assert gate.main() == 0
    written = bf.read_text(encoding="utf-8").splitlines()
    assert len(written) == 3
    assert all("error:" in ln for ln in written)


def test_note_lines_not_counted(monkeypatch, tmp_path):
    # note:/统计行不得计入 error 数 (与基线文件纯 error 行契约一致)
    _wire(monkeypatch, tmp_path, ["a.py:1: error: x"],
          1, "a.py:1: error: x\nnote: In function \"f\"\n"
             "Found 1 error in 1 file (checked 1 source file)\n", [])
    assert gate.main() == 0
