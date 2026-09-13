"""xtquant 能力级自检门禁回归 (v9.5 §5.7 F4, 2026-09-13)

覆盖 F4 的三条明文判据:
    T001  能力级判据必须成立 —— `find_spec` 为真**不算**通过 (AC-002 防假绿)
    T002  "无可用解释器" 必须给出**非 0** 退出码, 且原因可读 (空集合 != 通过)
    T003  机读产物可解析 (豁免/结论必须落盘可机读)
    T004  负向自检本身必须全绿 —— 含"判据能判真"一条, 防恒 FAIL 废门禁

编写口径 (遵循项目铁律):
    - 门禁类变更须附负向验证: 用例 3/4 在入口交付前必红 (文件不存在),
      交付后转绿; 且**显式指定不存在的解释器路径**, 与真实环境是否装 xtquant 解耦
      (否则一旦装上 xtquant, 断言会随环境漂移 —— 门禁必须能长期稳定判定).
    - "空集合 = 通过"是门禁假 PASS 头号来源: 用例 3 专门断言必须非 0 退出码.
    - 放置于 tests/unit/ (禁 tests/e2e/, 祖先目录关键字会令用例天生被 skip).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _PROJECT_ROOT / "scripts" / "check_xtquant_capability.py"

# 必然不存在的解释器 —— 与真实环境解耦的稳定输入
_NO_SUCH_PY = r"C:\__no_such_interpreter__\python.exe"

_EXIT_PREREQ_UNMET = 2


def _run(*args: str, timeout: int = 240) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_PROJECT_ROOT),
        timeout=timeout,
        check=False,
    )


# ============================================================
# T004 锚点: 入口必须存在 (先红后绿的交付物)
# ============================================================
def test_entry_delivered():
    assert _SCRIPT.exists(), (
        "F4 未交付: scripts/check_xtquant_capability.py 缺失 -> 无法回答"
        "'本机到底能不能跑 broker 侧代码'"
    )


# ============================================================
# T004: 负向自检必须全绿 (含"判据能判真")
# ============================================================
def test_selftest_all_green():
    """判据负向自检: 四类失败可区分 + 完整能力包必须判可用."""
    proc = _run("--selftest")
    assert proc.returncode == 0, (
        "能力判据自检失败 -> 判据可能恒 PASS 或恒 FAIL (两者都是废门禁)\n"
        f"stdout={proc.stdout[-1200:]}\nstderr={proc.stderr[-600:]}"
    )
    assert "5/5 通过" in proc.stdout, proc.stdout[-1200:]


# ============================================================
# T001: find_spec 为真不得作为通过依据 (AC-002 假绿防复发)
# ============================================================
def test_find_spec_is_diagnostic_only():
    """Py3.14 真实形态: wheel 装上且 `import xtquant` 成功, 子模块全废.

    该形态在自检的 `ns_only` 夹具中复现, 必须判**不可用**。
    这是本项目"门禁假 PASS"防复发的核心断言。
    """
    proc = _run("--selftest")
    assert "命名空间级 xtquant 必须判不可用" in proc.stdout, proc.stdout[-1200:]
    assert "WHEEL_MISMATCH" in proc.stdout, (
        "命名空间级安装未被归类为 wheel 版本不匹配 -> AC-002 会假 PASS"
    )


# ============================================================
# T002: 无可用解释器 -> 必须非 0 退出码 (空集合 != 通过)
# ============================================================
def test_no_capable_interpreter_exits_nonzero():
    proc = _run("--python", _NO_SUCH_PY, "--no-report")
    assert proc.returncode == _EXIT_PREREQ_UNMET, (
        "前置未满足却未以 2 退出 -> 会被自动化门禁误判为通过\n"
        f"rc={proc.returncode}\nstdout={proc.stdout[-1200:]}"
    )
    assert "前置未满足" in proc.stdout, proc.stdout[-1200:]
    # 原因必须非空 (不许空集合静默通过)
    assert "候选解释器不存在" in proc.stdout, proc.stdout[-1200:]


# ============================================================
# T003: 机读产物可解析
# ============================================================
def test_json_output_is_machine_readable():
    proc = _run("--python", _NO_SUCH_PY, "--json", "--no-report")
    assert proc.returncode == _EXIT_PREREQ_UNMET, proc.stdout[-1200:]

    payload = json.loads(proc.stdout)
    assert payload["has_capable_interpreter"] is False
    assert len(payload["candidates"]) == 1, "显式 --python 时只应测该解释器"
    cand = payload["candidates"][0]
    assert cand["ok"] is False
    assert cand["reason_class"] == "INTERPRETER_UNAVAILABLE"
    assert cand["reason"], "失败必须携带原因"


# ============================================================
# 三类失败必须可区分 (F4 明文判据)
# ============================================================
@pytest.mark.parametrize(
    ("cls", "text"),
    [
        ("NOT_INSTALLED", "未安装"),
        ("WHEEL_MISMATCH", "wheel 版本不匹配"),
        ("TERMINAL_DOWN", "QMT 终端未启动/连接失败"),
    ],
)
def test_failure_classes_are_distinguishable(cls: str, text: str):
    """三类业务失败的枚举与文案必须齐备且在源码中可查. """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert f'RC_NOT_INSTALLED = "{cls}"' in src or f'"{cls}"' in src, cls
    assert text in src, f"缺少可读文案: {text}"


# ============================================================
# 假 FAIL 防复发: 真实可用环境在**仓库外**, 只搜仓库必漏判
# ============================================================
def test_home_xtquant_env_is_in_candidate_chain():
    """2026-09-13 实测教训: 真实可用环境 = `C:\\Users\\Administrator\\xtquant_env`(仓库外).

    首版脚本只枚举仓库内路径 -> 漏判该环境 -> 产出"前置未满足"的**假 FAIL**
    (与假 PASS 同样有害)。此用例把该回归钉死。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_chk_xtq_cap", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cands = [str(p) for p in mod._resolve_candidates([])]  # type: ignore[attr-defined]
    assert cands, "候选链不得为空"
    assert any("xtquant_env" in c for c in cands), (
        "候选链缺少 xtquant_env (仓库内或用户目录下) -> 漏判真实可用环境, 产出假 FAIL\n"
        f"candidates={cands}"
    )
    home = str(Path.home())
    assert any(c.startswith(home) and "xtquant_env" in c for c in cands), (
        f"候选链缺少 <home>/xtquant_env (home={home}) -> 该环境在仓库外, 只搜仓库必漏判"
    )
