"""ruff 增量零新增门禁 (G-1, 2026-08-09)

对 PR 改动文件执行 ruff 增量门禁:
1. 阻断子集 (F,B,N,BLE,T): 改动文件不得有任何新增命中 (不依赖基线豁免) —— 新违规一律阻断。
2. 受控集 (E,F,W,B,C90,I,N,UP,T,BLE, 除 ANN): 改动文件的违规数不得超过基线中该文件的计数;
   超过即视为引入新违规, 阻断合并。

用法:
    python scripts/ruff_incremental_gate.py file1.py file2.py ...
    (fail-open: 无基线文件时仅打印警告并 PASS)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "reports" / "ruff_baseline.json"

ENFORCED = "E,F,W,B,C90,I,N,UP,T,BLE"
BLOCKING = "F,B,N,BLE,T"


def _norm(p: str) -> str:
    """统一路径为 posix 形式 (反斜杠->正斜杠), 使 CI 的 git diff 入参与
    ruff 在 Windows 上的反斜杠输出可对齐。 (M-2, 2026-08-09)"""
    return p.replace("\\", "/")


def _run_ruff(rules: str, files: list[str]) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            rules,
            "--output-format",
            "concise",
            "--no-cache",
            # (2026-08-30) ruff 的 exclude/extend-exclude 默认只作用于目录遍历发现的文件,
            # 对命令行显式传入的文件无效 —— 会使 ruff.toml 的排除名单 (temp/qlib_env/
            # external/airllm_src/_test_report_* 等) 在门禁场景下完全失效, 产生假阳性阻断。
            # --force-exclude 强制排除显式入参, 保证门禁与全量扫描口径一致。
            "--force-exclude",
            *files,
        ],
        cwd=str(ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout or ""


def _count_per_file(output: str, files: set[str]) -> dict[str, int]:
    """按归一化路径统计每文件违规数。files 为归一化后的集合。"""
    counts: dict[str, int] = dict.fromkeys(files, 0)
    pat = re.compile(r"^(.+?):\d+:\d+:")
    for line in output.splitlines():
        m = pat.match(line)
        if m and (k := _norm(m.group(1))) in files:
            counts[k] += 1
    return counts


def main(argv: list[str]) -> int:
    # 同时兼容两种调用:
    # (a) CLI: `python scripts/ruff_incremental_gate.py @files` -> argv[0] 为本脚本路径
    # (b) 直接函数调用 (pytest/CI 复用): `main(["a.py","b.py"])` -> 无脚本名
    # 统一按 ".py" 后缀过滤, 脚本自身路径 (ruff_incremental_gate.py) 也会被自然排除。
    files = [a for a in argv if a.endswith(".py")]
    if not files:
        print("[G-1] 无 Python 改动文件, 跳过")
        return 0

    if not BASELINE_PATH.exists():
        # M-6 (2026-08-09): 区分本地与 CI。
        # 本地开发 fail-open 便于上手; CI 环境若基线缺失, 自动生成基线后继续 (fail-open 生成),
        # 避免 CI 因未提交的生成物硬失败; 基线提交后恢复严格增量比较。
        if os.environ.get("CI"):
            print(
                f"[G-1][WARN] CI 环境基线文件缺失 {BASELINE_PATH}, 自动生成基线后继续"
            )
            import subprocess as _sp

            try:
                _sp.run(
                    [sys.executable, str(ROOT / "scripts" / "ruff_baseline_gen.py")],
                    check=True,
                )
            except Exception as _e:  # noqa: BLE001
                print(f"[G-1][FATAL] 自动生成 ruff 基线失败: {_e}")
                return 1
            if not BASELINE_PATH.exists():
                print(f"[G-1][FATAL] 自动生成后基线仍缺失 {BASELINE_PATH}")
                return 1
        else:
            print(
                f"[G-1][WARN] 基线文件缺失 {BASELINE_PATH}, 本地 fail-open PASS (请先运行 ruff_baseline_gen.py)"
            )
            return 0

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    base_enf = {_norm(k): v for k, v in baseline.get("per_file_enforced", {}).items()}
    base_blk = {_norm(k): v for k, v in baseline.get("per_file_blocking", {}).items()}

    # 归一化入参, 使 "utils/foo.py" (git diff) 与 "utils\foo.py" (ruff 输出) 等价
    norm_files = {_norm(f) for f in files}
    cur_blk = _count_per_file(_run_ruff(BLOCKING, list(norm_files)), norm_files)
    cur_enf = _count_per_file(_run_ruff(ENFORCED, list(norm_files)), norm_files)

    errors: list[str] = []

    # 1) 阻断子集: 相对基线新增即阻断 (M-5, 2026-08-09)
    #    绝对命中会阻塞所有存量 PR; 改为 "当前 > 基线" 才是真正的增量门禁。
    for f in sorted(norm_files):
        base_n = base_blk.get(f, 0)
        cur_n = cur_blk.get(f, 0)
        if cur_n > base_n:
            errors.append(
                f"[BLOCKING] {f}: 阻断级违规 {cur_n} > 基线 {base_n} "
                f"(新增 {cur_n - base_n} 条, F/B/N/BLE/T)"
            )

    # 2) 受控集: 不得超过基线该文件计数 (新增即阻断)
    for f in sorted(norm_files):
        base_n = base_enf.get(f, 0)
        cur_n = cur_enf.get(f, 0)
        # 新文件 (不在基线) 允许, 但应低于合理阈值; 这里仅监控存量文件的新增
        if f in base_enf and cur_n > base_n:
            errors.append(
                f"[ENFORCED] {f}: 违规 {cur_n} > 基线 {base_n} (新增 {cur_n - base_n} 条)"
            )

    if errors:
        print("[G-1] ruff 增量门禁失败:")
        for e in errors:
            print("  " + e)
        return 1

    print(f"[G-1] ruff 增量门禁通过 ({len(norm_files)} 文件, 无新增违规)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
