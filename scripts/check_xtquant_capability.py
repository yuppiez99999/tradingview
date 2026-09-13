"""xtquant 能力级自检 (方案 v9.5 §5.7 F4, 2026-09-13)

为什么不能只 `import xtquant` (AC-002 假绿来源):
    xtquant wheel 标 `py3-none-any` ⇒ **任意** Python 都装得上; 但其二进制
    (`datacenter.*.pyd` / `xtpythonclient`) 只提供 cp36~cp313。
    在 Py>=3.14 下 `import xtquant` 顶层**会成功**, 而 `from xtquant import xtdata`
    与 `from xtquant.xttrader import XtQuantTrader` 双双失败 ⇒
    "装上且能 import" 与 "真的能用" 是两回事。
    ⇒ 本检查只认**能力级**判据; `find_spec` 仅作诊断输出, **绝不**作为通过依据。

与 scripts/verify_qmt_paper_chain.py 的分工 (互补, 勿合并):
    check_xtquant_capability.py  **只查环境能力**: 不连终端、不下单、不读 broker 门控。
                                 可随时运行 (含冻结窗), 回答"本机到底能不能跑 broker 侧代码"。
    verify_qmt_paper_chain.py    需真实终端 + 门控开启, 做 T1~T8 链路取证。

三类失败必须可区分 (F4 明文判据):
    1) NOT_INSTALLED   未安装           —— 目标解释器找不到 xtquant
    2) WHEEL_MISMATCH  wheel 版本不匹配  —— 顶层可见但能力子模块 / .pyd 加载失败
    3) TERMINAL_DOWN   QMT 终端未启动    —— 能力齐备, 但客户端路径缺失或 connect() != 0
    (另有环境级第 4 类 INTERPRETER_UNAVAILABLE = 候选解释器不存在/无法启动,
     属前置类, 不计入上述三类业务失败)

候选解释器 (按序; 2026-09-13 实测):
    ① 环境变量 QMT_PYTHON                      —— 显式覆盖, 最高优先
    ② <repo>/xtquant_env/Scripts/python.exe     —— 方案 §5.7 F4 原命名 (相对仓库, **不存在**)
    ③ <home>/xtquant_env/Scripts/python.exe     —— **实测可用** (本机 =
                                                  C:\\Users\\Administrator\\xtquant_env,
                                                  3.11.9 + xtquant, xtdata/xttrader/XtQuantTrader 全通)
                                                  ※ 该环境在**仓库外**, 只搜仓库必漏判 ⇒ 假 FAIL
    ④ <workspace>/_win_machine_assets/_py311/python.exe
                                                —— 实测存在 (Python 3.11.9 基座安装, 无 xtquant;
                                                   junction 目标 = C:\\Users\\Administrator\\py311)
    ⑤ 当前解释器 sys.executable
    传 `--python <exe>` 时**只测指定解释器** (不追加默认链), 便于 CI 构造稳定断言。

用法:
    .venv\\Scripts\\python.exe scripts\\check_xtquant_capability.py
    .venv\\Scripts\\python.exe scripts\\check_xtquant_capability.py --json
    .venv\\Scripts\\python.exe scripts\\check_xtquant_capability.py --connect
    .venv\\Scripts\\python.exe scripts\\check_xtquant_capability.py --python <py.exe>
    .venv\\Scripts\\python.exe scripts\\check_xtquant_capability.py --selftest

退出码 (与 verify_qmt_paper_chain.py 对齐):
    0  至少一个候选解释器**能力级可用**
    1  内部异常 / --selftest 失败
    2  前置未满足 (无可用解释器) —— **绝不静默通过**
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

_CST = timezone(timedelta(hours=8))
REPORT_DIR = _PROJECT_ROOT / "reports" / "execution"

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_PREREQ_UNMET = 2

# 三类业务失败 + 一类环境前置失败 (必须可区分)
RC_NOT_INSTALLED = "NOT_INSTALLED"
RC_WHEEL_MISMATCH = "WHEEL_MISMATCH"
RC_TERMINAL_DOWN = "TERMINAL_DOWN"
RC_INTERPRETER_UNAVAILABLE = "INTERPRETER_UNAVAILABLE"

_REASON_TEXT = {
    RC_NOT_INSTALLED: "未安装",
    RC_WHEEL_MISMATCH: "wheel 版本不匹配",
    RC_TERMINAL_DOWN: "QMT 终端未启动/连接失败",
    RC_INTERPRETER_UNAVAILABLE: "候选解释器不可用",
}

_PROBE_MARKER = "__PROBE_JSON__"

# 在**目标解释器**内运行的能力探测程序。
# 关键: 只能作诊断的 find_spec 与决定结论的能力导入严格分层。
_PROBE_SRC = r'''
import importlib.util as _u
import json as _j
import os as _o
import sys as _s

_r = {
    "python": _s.version.split()[0],
    "executable": _s.executable,
    "find_spec": None,
    "stage": "start",
    "ok": False,
    "reason_class": "",
    "reason": "",
}

def _emit():
    print("__PROBE_JSON__" + _j.dumps(_r, ensure_ascii=False))

try:
    _r["find_spec"] = _u.find_spec("xtquant") is not None
except BaseException:
    _r["find_spec"] = False

# ---- S1 顶层可见性 (命名空间级, 不构成通过依据) ----
_r["stage"] = "import xtquant"
try:
    import xtquant  # noqa: F401
except ModuleNotFoundError as _e:
    _r["reason_class"] = "NOT_INSTALLED"
    _r["reason"] = "xtquant 未安装: %s" % (_e,)
    _emit()
    raise SystemExit(0)
except BaseException as _e:
    _r["reason_class"] = "WHEEL_MISMATCH"
    _r["reason"] = "顶层 xtquant 导入失败 (%s): %s" % (type(_e).__name__, _e)
    _emit()
    raise SystemExit(0)

# ---- S2 能力子模块 (xtdata / xttrader) ----
for _m in ("xtdata", "xttrader"):
    _r["stage"] = "import xtquant.%s" % _m
    try:
        __import__("xtquant.%s" % _m)
    except BaseException as _e:
        _r["reason_class"] = "WHEEL_MISMATCH"
        _r["reason"] = (
            "%s 子模块不可用 (%s): %s -- wheel 标 py3-none-any, 但二进制仅 cp36~cp313, "
            "当前解释器不在支持范围内" % (_m, type(_e).__name__, _e)
        )
        _emit()
        raise SystemExit(0)

# ---- S3 关键符号 ----
_r["stage"] = "import XtQuantTrader"
try:
    from xtquant.xttrader import XtQuantTrader  # noqa: F401
except BaseException as _e:
    _r["reason_class"] = "WHEEL_MISMATCH"
    _r["reason"] = "XtQuantTrader 符号缺失 (%s): %s" % (type(_e).__name__, _e)
    _emit()
    raise SystemExit(0)

# ---- S4 可实例化 (构造不连终端, 安全) ----
_r["stage"] = "instantiate XtQuantTrader"
try:
    _t = XtQuantTrader("", 0)
    del _t
except BaseException as _e:
    _r["reason_class"] = "WHEEL_MISMATCH"
    _r["reason"] = "XtQuantTrader 实例化失败 (%s): %s" % (type(_e).__name__, _e)
    _emit()
    raise SystemExit(0)

# ---- S5 可选: 连终端 (仅 XTQ_PROBE_CONNECT=1 时) ----
if _o.environ.get("XTQ_PROBE_CONNECT", "") == "1":
    _r["stage"] = "connect QMT terminal"
    _path = (_o.environ.get("QMT_PATH", "") or "").strip()
    if not _path:
        _r["reason_class"] = "TERMINAL_DOWN"
        _r["reason"] = "QMT 客户端路径未配 (QMT_PATH) -- 能力齐备但无终端可连"
        _emit()
        raise SystemExit(0)
    try:
        _tr = XtQuantTrader(_path, int(_o.environ.get("QMT_SESSION_ID", "0") or 0))
        _tr.start()
        _rc = _tr.connect()
        try:
            _tr.stop()
        except BaseException:
            pass
    except BaseException as _e:
        _r["reason_class"] = "TERMINAL_DOWN"
        _r["reason"] = "连接 QMT 终端异常 (%s): %s (path=%s)" % (
            type(_e).__name__, _e, _path)
        _emit()
        raise SystemExit(0)
    if _rc != 0:
        _r["reason_class"] = "TERMINAL_DOWN"
        _r["reason"] = "connect() 返回 %s (非 0) -- QMT 终端未启动 (path=%s)" % (_rc, _path)
        _emit()
        raise SystemExit(0)

_r["ok"] = True
_r["stage"] = "done"
_r["reason"] = "xtquant 能力级可用 (xtdata / xttrader / XtQuantTrader 全部通过)"
_emit()
'''


def _now_str() -> str:
    return datetime.now(tz=_CST).strftime("%Y-%m-%d %H:%M:%S")


def _resolve_candidates(explicit: list[str]) -> list[Path]:
    """候选解释器列表 (去重保序). 显式指定时只测指定项."""
    raw: list[Path] = []
    if explicit:
        raw = [Path(p) for p in explicit]
    else:
        env_py = (os.environ.get("QMT_PYTHON", "") or "").strip()
        if env_py:
            raw.append(Path(env_py))
        raw.append(_PROJECT_ROOT / "xtquant_env" / "Scripts" / "python.exe")
        # 2026-09-13 实测: 真实可用环境在**仓库外**的用户目录下
        # (本机 C:\Users\Administrator\xtquant_env, Py3.11.9 + xtquant)。
        # 只搜仓库会漏判 -> 产出"前置未满足"的**假 FAIL**, 与假 PASS 同样有害。
        try:
            raw.append(Path.home() / "xtquant_env" / "Scripts" / "python.exe")
        except (RuntimeError, OSError):
            pass
        raw.append(
            _PROJECT_ROOT.parent
            / "_win_machine_assets"
            / "_py311"
            / "python.exe"
        )
        raw.append(Path(sys.executable))

    seen: set[str] = set()
    out: list[Path] = []
    for p in raw:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _probe(py: Path, connect: bool, timeout: int) -> dict:
    """在目标解释器内做能力级探测 (子进程, 结论只认能力导入)."""
    if not py.exists():
        return {
            "python": "",
            "executable": str(py),
            "find_spec": None,
            "stage": "spawn",
            "ok": False,
            "reason_class": RC_INTERPRETER_UNAVAILABLE,
            "reason": f"候选解释器不存在: {py}",
        }

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("NO_PROXY", "mcp.wind.com.cn")
    if connect:
        env["XTQ_PROBE_CONNECT"] = "1"

    try:
        proc = subprocess.run(
            [str(py), "-c", _PROBE_SRC],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=str(_PROJECT_ROOT),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "python": "",
            "executable": str(py),
            "find_spec": None,
            "stage": "spawn",
            "ok": False,
            "reason_class": RC_INTERPRETER_UNAVAILABLE,
            "reason": f"解释器无法启动 ({type(exc).__name__}): {exc}",
        }

    payload = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith(_PROBE_MARKER):
            payload = line[len(_PROBE_MARKER) :]
            break

    if not payload:
        return {
            "python": "",
            "executable": str(py),
            "find_spec": None,
            "stage": "parse",
            "ok": False,
            "reason_class": RC_INTERPRETER_UNAVAILABLE,
            "reason": (
                "探测程序未产出结果 (rc=%s) -- 解释器可能启动即崩: %s"
                % (proc.returncode, (proc.stderr or "").strip()[-200:])
            ),
        }

    try:
        result = json.loads(payload)
    except ValueError as exc:
        return {
            "python": "",
            "executable": str(py),
            "find_spec": None,
            "stage": "parse",
            "ok": False,
            "reason_class": RC_INTERPRETER_UNAVAILABLE,
            "reason": f"探测结果不可解析: {exc}",
        }

    result.setdefault("reason_class", "")
    result.setdefault("reason", "")
    result["ok"] = bool(result.get("ok"))
    return result


def _fmt_class(cls: str) -> str:
    return _REASON_TEXT.get(cls, cls or "-")


def _print_row(idx: int, py: Path, res: dict) -> None:
    mark = "PASS" if res.get("ok") else "FAIL"
    ver = res.get("python") or "?"
    cls = _fmt_class(res.get("reason_class", ""))
    print(f"  [{mark}] #{idx} {py}  (Python {ver})")
    print(f"         诊断 find_spec(xtquant) = {res.get('find_spec')}  (仅诊断, 不构成通过依据)")
    print(f"         判定: {cls}")
    print(f"         详情: {res.get('reason', '')}")


def _write_report(rows: list[tuple[Path, dict]], connect: bool) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=_CST)
    ok_n = sum(1 for _p, r in rows if r.get("ok"))
    lines = [
        "=" * 76,
        f"  xtquant 能力级自检报告 -- {stamp.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 76,
        "",
        "判据: 只认能力级导入 (xtdata / xttrader / XtQuantTrader), find_spec 仅诊断;",
        "      三类失败可区分 = 未安装 / wheel 版本不匹配 / QMT 终端未启动。",
        f"连终端探测: {'开' if connect else '关 (--connect 可开)'}",
        "",
        f"  {'候选解释器':<58}{'结果':<8}Python",
        "  " + "-" * 72,
    ]
    for py, res in rows:
        mark = "PASS" if res.get("ok") else "FAIL"
        lines.append(f"  {str(py):<58}{mark:<8}{res.get('python') or '?'}")
        lines.append(f"    - find_spec(xtquant) = {res.get('find_spec')} (仅诊断)")
        lines.append(
            f"    - 判定: {_fmt_class(res.get('reason_class', ''))}; {res.get('reason', '')}"
        )
    lines += [
        "  " + "-" * 72,
        f"  可用候选: {ok_n}/{len(rows)}",
        "",
        "后续: 1) 有可用解释器 → scripts/verify_qmt_paper_chain.py --preflight-only;",
        "      2) 无可用解释器 → 先落地 cp36~cp313 的 xtquant (勿装进主 venv Py3.14);",
        "      3) 索引见 specs/G1-qmt-live-order-wiring/quickstart.md。",
        "=" * 76,
    ]
    out = REPORT_DIR / f"xtquant_capability_{stamp.strftime('%Y%m%d')}.md"
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")

    js = REPORT_DIR / f"xtquant_capability_{stamp.strftime('%Y%m%d')}.json"
    payload = {
        "checked_at": stamp.isoformat(timespec="seconds"),
        "connect_probe": connect,
        "has_capable_interpreter": ok_n > 0,
        "candidates": [
            {
                "candidate": str(py),
                "ok": bool(r.get("ok")),
                "python": r.get("python", ""),
                "find_spec_diagnostic": r.get("find_spec"),
                "stage": r.get("stage", ""),
                "reason_class": r.get("reason_class", ""),
                "reason_class_text": _fmt_class(r.get("reason_class", "")),
                "reason": r.get("reason", ""),
            }
            for py, r in rows
        ],
    }
    with open(js, "w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return out


# ----------------------------------------------------------------------
# 负向自检 (--selftest)
# 门禁类变更须附"修复前会失败"证据; 且必须证明本检查**不是恒 FAIL**
# (恒 FAIL 与恒 PASS 一样是废门禁)。
# ----------------------------------------------------------------------
def _fixture(root: Path, name: str, init: str, xtdata: str | None, xttrader: str | None) -> Path:
    pkg = root / name / "xtquant"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(init, encoding="utf-8")
    if xtdata is not None:
        (pkg / "xtdata.py").write_text(xtdata, encoding="utf-8")
    if xttrader is not None:
        (pkg / "xttrader.py").write_text(xttrader, encoding="utf-8")
    return root / name


_OK_TRADER = (
    "class XtQuantTrader:\n"
    "    def __init__(self, *a, **k):\n"
    "        pass\n"
    "    def start(self):\n"
    "        pass\n"
    "    def connect(self):\n"
    "        return 0\n"
    "    def stop(self):\n"
    "        pass\n"
)
_DOWN_TRADER = (
    "class XtQuantTrader:\n"
    "    def __init__(self, *a, **k):\n"
    "        pass\n"
    "    def start(self):\n"
    "        pass\n"
    "    def connect(self):\n"
    "        return -1\n"
    "    def stop(self):\n"
    "        pass\n"
)


def _probe_with_pythonpath(py: Path, extra: Path, timeout: int, connect: bool) -> dict:
    """用 PYTHONPATH 前置伪造包, 验证判据本身 (不影响真实环境)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(extra)
    if connect:
        env["XTQ_PROBE_CONNECT"] = "1"
        env["QMT_PATH"] = str(extra / "userdata_mini")
        (extra / "userdata_mini").mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [str(py), "-c", _PROBE_SRC],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=str(_PROJECT_ROOT),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason_class": RC_INTERPRETER_UNAVAILABLE,
                "reason": f"解释器无法启动: {exc}"}

    for line in (proc.stdout or "").splitlines():
        if line.startswith(_PROBE_MARKER):
            try:
                return json.loads(line[len(_PROBE_MARKER) :])
            except ValueError:
                break
    return {"ok": False, "reason_class": RC_INTERPRETER_UNAVAILABLE,
            "reason": f"探测无输出 rc={proc.returncode}: {(proc.stderr or '')[-200:]}"}


def run_selftest(timeout: int) -> int:
    """四类夹具 + 判据可判真, 全部通过才返回 0."""
    import tempfile

    py = Path(sys.executable)
    checks: list[tuple[str, bool, str]] = []

    with tempfile.TemporaryDirectory(prefix="xtq_selftest_") as tmp:
        root = Path(tmp)

        # ① 命名空间级 (Py3.14 真实形态): 必须判不可用
        d1 = _fixture(root, "ns_only", "", None, None)
        r1 = _probe_with_pythonpath(py, d1, timeout, connect=False)
        checks.append((
            "命名空间级 xtquant 必须判不可用 (AC-002 假绿防复发)",
            r1.get("ok") is False and r1.get("reason_class") == RC_WHEEL_MISMATCH,
            f"ok={r1.get('ok')} class={r1.get('reason_class')} det={r1.get('reason')}",
        ))

        # ② 二进制加载失败 → wheel 版本不匹配
        d2 = _fixture(
            root, "broken_pyd", "",
            "raise OSError('DLL load failed while importing datacenter')", None,
        )
        r2 = _probe_with_pythonpath(py, d2, timeout, connect=False)
        checks.append((
            "子模块 .pyd 加载失败 必须归类 wheel 版本不匹配",
            r2.get("ok") is False and r2.get("reason_class") == RC_WHEEL_MISMATCH,
            f"ok={r2.get('ok')} class={r2.get('reason_class')} det={r2.get('reason')}",
        ))

        # ③ 完全可用 → 必须判可用 (证明判据不是恒 FAIL)
        d3 = _fixture(root, "complete", "", "", _OK_TRADER)
        r3 = _probe_with_pythonpath(py, d3, timeout, connect=False)
        checks.append((
            "完整能力包 必须判可用 (证明判据非恒 FAIL)",
            r3.get("ok") is True,
            f"ok={r3.get('ok')} class={r3.get('reason_class')} det={r3.get('reason')}",
        ))

        # ④ connect() != 0 → QMT 终端未启动
        d4 = _fixture(root, "term_down", "", "", _DOWN_TRADER)
        r4 = _probe_with_pythonpath(py, d4, timeout, connect=True)
        checks.append((
            "connect()!=0 必须归类 QMT 终端未启动",
            r4.get("ok") is False and r4.get("reason_class") == RC_TERMINAL_DOWN,
            f"ok={r4.get('ok')} class={r4.get('reason_class')} det={r4.get('reason')}",
        ))

        # ⑤ 能力齐备但未配 QMT_PATH → 终端未启动
        d5 = _fixture(root, "no_path", "", "", _OK_TRADER)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = str(d5)
        env["XTQ_PROBE_CONNECT"] = "1"
        env.pop("QMT_PATH", None)
        try:
            proc = subprocess.run(
                [str(py), "-c", _PROBE_SRC], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, env=env,
                cwd=str(_PROJECT_ROOT), check=False,
            )
            r5: dict = {"ok": False, "reason_class": ""}
            for line in (proc.stdout or "").splitlines():
                if line.startswith(_PROBE_MARKER):
                    r5 = json.loads(line[len(_PROBE_MARKER) :])
                    break
        except (OSError, subprocess.SubprocessError) as exc:
            r5 = {"ok": False, "reason_class": RC_INTERPRETER_UNAVAILABLE,
                  "reason": f"解释器无法启动: {exc}"}
        checks.append((
            "QMT_PATH 缺失 必须归类 QMT 终端未启动",
            r5.get("ok") is False and r5.get("reason_class") == RC_TERMINAL_DOWN,
            f"ok={r5.get('ok')} class={r5.get('reason_class')} det={r5.get('reason')}",
        ))

    print("=" * 72)
    print("xtquant 能力判据 负向自检 (--selftest)")
    print("=" * 72)
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        print(f"         {detail}")
    passed = sum(1 for _n, ok, _d in checks if ok)
    print("-" * 72)
    print(f"自检结果: {passed}/{len(checks)} 通过")
    return EXIT_OK if passed == len(checks) else EXIT_INTERNAL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="xtquant 能力级自检 (v9.5 §5.7 F4)"
    )
    parser.add_argument(
        "--python", action="append", default=[],
        help="只测该解释器 (可多次); 缺省走默认候选链",
    )
    parser.add_argument("--connect", action="store_true",
                        help="额外尝试连接 QMT 终端 (需 QMT_PATH)")
    parser.add_argument("--json", action="store_true", help="输出机读 JSON")
    parser.add_argument("--no-report", action="store_true", help="不落盘报告")
    parser.add_argument("--timeout", type=int, default=90, help="单次探测超时秒")
    parser.add_argument("--selftest", action="store_true",
                        help="跑判据负向自检 (门禁类必须的负向验证)")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest(args.timeout)

    try:
        candidates = _resolve_candidates(args.python)
        rows: list[tuple[Path, dict]] = []
        for py in candidates:
            rows.append((py, _probe(py, args.connect, args.timeout)))
    except Exception as exc:  # noqa: BLE001  入口需完整记录内部异常
        print(f"[FAIL] 内部异常: {exc!r}")
        return EXIT_INTERNAL

    ok_rows = [(p, r) for p, r in rows if r.get("ok")]

    if args.json:
        payload = {
            "checked_at": _now_str(),
            "connect_probe": args.connect,
            "has_capable_interpreter": bool(ok_rows),
            "candidates": [
                {
                    "candidate": str(p),
                    "ok": bool(r.get("ok")),
                    "python": r.get("python", ""),
                    "find_spec_diagnostic": r.get("find_spec"),
                    "stage": r.get("stage", ""),
                    "reason_class": r.get("reason_class", ""),
                    "reason_class_text": _fmt_class(r.get("reason_class", "")),
                    "reason": r.get("reason", ""),
                }
                for p, r in rows
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print("xtquant 能力级自检 --", _now_str())
        print("=" * 72)
        print("\n候选解释器探测 (判据只认能力级导入; find_spec 仅诊断):")
        for i, (py, res) in enumerate(rows, start=1):
            _print_row(i, py, res)
        print("\n" + "=" * 72)

    if not args.no_report:
        try:
            out = _write_report(rows, args.connect)
            if not args.json:
                print("报告已写入:", out)
        except OSError as exc:
            print(f"[WARN] 报告落盘失败 (不改变结论): {exc}")

    if ok_rows:
        if not args.json:
            print(f"结果: 能力级可用 —— {len(ok_rows)}/{len(rows)} 个候选通过")
            print("后续: scripts/verify_qmt_paper_chain.py --preflight-only")
        return EXIT_OK

    if not args.json:
        reasons = [
            f"{py} -> {_fmt_class(r.get('reason_class', ''))}: {r.get('reason', '')}"
            for py, r in rows
        ]
        print("结果: 前置未满足 —— 无任何候选解释器具备 xtquant 能力 (退出码 2, 绝不静默通过):")
        for line in reasons:
            print("   -", line)
        print("处置: 在 Python<=3.13 的解释器内安装 xtquant (勿装进主 venv Py3.14);")
        print("      见 specs/G1-qmt-live-order-wiring/quickstart.md")
    return EXIT_PREREQ_UNMET


if __name__ == "__main__":
    sys.exit(main())
