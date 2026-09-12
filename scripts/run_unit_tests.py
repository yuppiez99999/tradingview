"""失败清单落盘测试运行器 (2026-09-04)

背景: 09-03 治理批次四分片 13F 的失败清单未落盘, 台账归类凭记忆产生 7 个幻影失败
(3F sklearn + 4F qlib, 复核证据见 cairn/LOG.md 09-04 更正条目)。本脚本让全量/分片
测试运行强制产出证据链, 从机制上消除"失败数不绑定证据"问题。

用法 (必须用 .venv 解释器; 用其他解释器启动时自动重入 .venv):
  .venv\\Scripts\\python.exe scripts\\run_unit_tests.py                    # tests/unit 四分片
  .venv\\Scripts\\python.exe scripts\\run_unit_tests.py --split 1          # tests/unit 单进程全量
  .venv\\Scripts\\python.exe scripts\\run_unit_tests.py --split 2 \\
      --target tests/unit/test_a.py tests/unit/test_b.py                  # 自定义目标
  .venv\\Scripts\\python.exe scripts\\run_unit_tests.py -- -m "not network"  # `--` 后透传 pytest 参数

产出 (reports/test_runs/<run_id>/):
  junit_shard{i}.xml      原始 junit (证据源, 不可篡改口径)
  failures_shard{i}.txt   失败清单: nodeid + 首行错误信息 (台账归类入口)
  log_shard{i}.txt        pytest 完整输出
  summary.json            汇总 + 环境快照 (python/pytest/git HEAD)

约定 (强制):
  1. cairn/LOG.md 中任何 F 数字必须指回本脚本产出的 run 目录;
  2. 失败归类从 failures_shard{i}.txt 生成, 而非记忆。
细节: 运行加 -p no:cacheprovider — 治理运行不更新 .pytest_cache/lastfailed,
避免环境受损运行留下幻影缓存误导 --lf (09-04 已清理过 4341 项幻影)。

保留最近 --keep 个 run 目录 (默认 20, <1 表示不清理)。

退出码: 0 = 全部通过; 1 = 有失败/异常; 2 = 脚本用法错误
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.safe_xml import ParseError, safe_xml_parse  # noqa: E402 — 依赖 REPO_ROOT 注入 sys.path

EVIDENCE_ROOT = REPO_ROOT / "reports" / "test_runs"


def reexec_in_venv_if_needed() -> None:
    """非 .venv 解释器启动时自动重入 .venv (项目铁律: 一律用 .venv python)。"""
    if ".venv" in Path(sys.executable).resolve().parts:
        return
    venv_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        sys.exit(subprocess.run([str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
    print(f"[WARN] 未找到 .venv ({venv_py}), 继续用当前解释器: {sys.executable}")


def collect_files(targets: list[str]) -> list[Path]:
    """目标路径展开为测试文件清单 (文件原样保留, 目录递归收 test_*.py, 字典序稳定)。"""
    files: list[Path] = []
    for t in targets:
        p = Path(t)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.exists():
            print(f"[ERROR] 目标不存在: {t}")
            sys.exit(2)
        if p.is_file():
            files.append(p)
        else:
            files.extend(sorted(p.rglob("test_*.py")))
    # 去重 + 排序, 保证分片确定性
    return sorted({f.resolve() for f in files})


def make_shards(files: list[Path], split: int) -> list[list[Path]]:
    """文件级连续切块 (确定性, 同一份代码分片结果恒定)。"""
    if split <= 1:
        return [files]
    size = (len(files) + split - 1) // split
    return [files[i : i + size] for i in range(0, len(files), size)]


def parse_junit(junit_path: Path) -> dict:
    """从 junit xml 提取计数与失败明细 (证据源)。"""
    stats = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "note": ""}
    failures: list[str] = []
    try:
        root = safe_xml_parse(junit_path).getroot()
    except (ParseError, OSError) as e:
        stats["note"] = f"junit 缺失/损坏: {e}"
        return stats, failures
    for suite in root.iter("testsuite"):
        stats["failed"] += int(suite.get("failures", 0))
        stats["errors"] += int(suite.get("errors", 0))
        stats["skipped"] += int(suite.get("skipped", 0))
    total = int(root.get("tests", 0)) if root.tag == "testsuite" else sum(
        int(s.get("tests", 0)) for s in root.iter("testsuite")
    )
    stats["passed"] = max(total - stats["failed"] - stats["errors"] - stats["skipped"], 0)
    for tc in root.iter("testcase"):
        for kind in ("failure", "error"):
            el = tc.find(kind)
            if el is not None:
                msg = (el.get("message") or "").strip().replace("\n", " ")[:200]
                failures.append(f"{tc.get('classname', '')}::{tc.get('name', '')}\n    {kind}: {msg}")
    return stats, failures


def run_shard(run_dir: Path, idx: int, total_shards: int, files: list[Path],
              passthrough: list[str]) -> dict:
    """运行单个分片: junit + 失败清单 + 完整日志三件套落盘, 输出实时透传。"""
    # 仓库内用相对路径 (输出可读), 仓库外 (如临时验证文件) 保留绝对路径
    rel_files = [
        str(f.relative_to(REPO_ROOT)) if REPO_ROOT in f.parents else str(f)
        for f in files
    ]
    junit_path = run_dir / f"junit_shard{idx}.xml"
    log_path = run_dir / f"log_shard{idx}.txt"
    failures_path = run_dir / f"failures_shard{idx}.txt"

    cmd = [
        sys.executable, "-m", "pytest", *rel_files,
        f"--junitxml={junit_path}",
        "-p", "no:cacheprovider",
        "-q",
        *passthrough,
    ]
    print(f"[shard {idx}/{total_shards}] {len(files)} files ...", flush=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        log_lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_lines.append(line)
        proc.wait()
    except OSError as e:
        # 启动失败也要留痕 (crash 不能产出空壳证据目录)
        log_path.write_text(f"runner error: {e!r}", encoding="utf-8")
        return {
            "index": idx, "files": len(files), "duration_s": round(time.monotonic() - t0, 1),
            "exit_code": -1, "junit": None, "failures_txt": None, "log": log_path.name,
            "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
            "note": f"runner error: {e!r}",
        }
    duration = round(time.monotonic() - t0, 1)
    log_path.write_text("".join(log_lines), encoding="utf-8")

    stats, failures = parse_junit(junit_path)
    if failures:
        failures_path.write_text("\n".join(failures) + "\n", encoding="utf-8")
    stats.update({
        "index": idx, "files": len(files), "duration_s": duration,
        "exit_code": proc.returncode,
        "junit": junit_path.name if junit_path.exists() else None,
        "failures_txt": failures_path.name if failures else None,
        "log": log_path.name,
    })
    print(
        f"[shard {idx}/{total_shards}] => {stats['passed']} passed / "
        f"{stats['failed']} failed / {stats['errors']} errors / "
        f"{stats['skipped']} skipped ({duration}s, exit={proc.returncode})",
        flush=True,
    )
    return stats


def prune_old_runs(keep: int) -> None:
    """只保留最近 keep 个 run 目录 (按目录名时间前缀排序)。keep < 1 不清理。"""
    if keep < 1 or not EVIDENCE_ROOT.exists():
        return
    runs = sorted(d for d in EVIDENCE_ROOT.iterdir() if d.is_dir())
    for d in runs[:-keep] if len(runs) > keep else []:
        shutil.rmtree(d, ignore_errors=True)
        print(f"[prune] 清理过期证据目录: {d.name}")


def git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except OSError:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="失败清单落盘测试运行器 — 全量/分片运行强制产出 junit+失败清单+环境快照证据链",
    )
    parser.add_argument("--split", type=int, default=4, help="分片数 (默认 4, 1=单进程全量)")
    parser.add_argument("--target", nargs="+", default=["tests/unit"], help="目标文件/目录 (默认 tests/unit)")
    parser.add_argument("--keep", type=int, default=20, help="保留最近 N 个 run 目录 (默认 20, <1 不清理)")
    parser.add_argument("passthrough", nargs=argparse.REMAINDER, help="`--` 后的参数原样透传给 pytest")
    args = parser.parse_args()

    passthrough = args.passthrough
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    if any(a.startswith("--junitxml") for a in passthrough):
        print("[ERROR] 透传参数不得包含 --junitxml (证据落盘由本脚本统一管理)")
        return 2

    reexec_in_venv_if_needed()

    files = collect_files(args.target)
    if not files:
        print("[ERROR] 未收集到任何 test_*.py")
        return 2
    shards = [s for s in make_shards(files, args.split) if s]

    target_tag = Path(args.target[0]).name or "tests"
    run_id = f"{now_bj().strftime('%Y%m%d_%H%M%S')}_{target_tag}-split{args.split}"
    run_dir = EVIDENCE_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"  run_unit_tests: target={args.target} split={len(shards)}")
    print(f"  evidence: reports/test_runs/{run_id}")
    print("=" * 60, flush=True)

    shard_stats = [
        run_shard(run_dir, i, len(shards), chunk, passthrough)
        for i, chunk in enumerate(shards, 1)
    ]

    totals = {k: sum(s[k] for s in shard_stats) for k in ("passed", "failed", "errors", "skipped")}
    crashed = [s["index"] for s in shard_stats if s["exit_code"] not in (0, 1) or s["note"]]
    env = {
        "python": sys.version.split()[0],
        "pytest": _pytest_version(),
        "git_head": git_head(),
        "executable": sys.executable,
    }
    summary = {
        "run_id": run_id,
        "started": now_bj().isoformat(timespec="seconds"),
        "target": args.target,
        "split": args.split,
        "pytest_passthrough": passthrough,
        "env": env,
        "shards": shard_stats,
        "totals": totals,
        "shards_crashed_or_junit_missing": crashed,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    prune_old_runs(args.keep)

    status = "PASS" if totals["failed"] == 0 and totals["errors"] == 0 and not crashed else "FAIL"
    print("=" * 60)
    print(f"  [{status}] TOTAL: {totals['passed']} passed / {totals['failed']} failed / "
          f"{totals['errors']} errors / {totals['skipped']} skipped")
    if crashed:
        print(f"  [WARN] 分片 {crashed} 进程异常退出或 junit 缺失, 详见对应 log_shard*.txt")
    print(f"  evidence: reports/test_runs/{run_id}")
    print("  台账约定: cairn/LOG.md 的 F 数字必须指回该目录; 归类看 failures_shard*.txt")
    print("=" * 60)
    return 0 if status == "PASS" else 1


def _pytest_version() -> str:
    try:
        return version("pytest")
    except PackageNotFoundError:
        return "unknown"


if __name__ == "__main__":
    sys.exit(main())
