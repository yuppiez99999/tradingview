"""从 pyproject.toml 生成派生 requirements*.txt — 依赖单一真源 (审计 item 13)。

真源: pyproject.toml [project] dependencies + [project.optional-dependencies]
派生: requirements.txt / requirements-core.txt / requirements-cloud.txt /
      requirements-win.txt / requirements-optional.txt / requirements_dev.txt

用法:
  python scripts/sync_requirements.py           # 生成: 覆盖 6 个派生文件
  python scripts/sync_requirements.py --check   # CI 校验: 内存生成 vs 磁盘, 不一致退出 1
  python scripts/sync_requirements.py --audit   # 审计: 打印 pyproject ↔ 磁盘差异 (不写盘)

映射规则:
  requirements.txt        = dependencies 全量 (开发机一键全装)
  requirements-core.txt   = dependencies − WIN_ONLY (跨平台, 云/Win 共用)
  requirements-cloud.txt  = extras["cloud"]    (云 Linux 增量, 在 core 之上)
  requirements-win.txt    = extras["win"]      (Win 实盘机增量, 在 core 之上)
  requirements-optional.txt = extras["optional"] (可选降级依赖)
  requirements_dev.txt    = "-r requirements.txt" + extras["dev"]

已知豁免 (不迁移, 保持差异):
  - flake8: 已被 ruff 替代为主 linter, 不再纳入 dev extras (2026-09-09 决策)
  - requirements_py38_backup.txt: 历史归档, 不参与生成
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"

# Windows 专属包 (从 core 排除; requirements.txt 全量保留)
# 真源声明在 pyproject [project.dependencies], 此处仅做 core 拆分标记
WIN_ONLY = {"pyautogui", "pywinauto"}

HEADER = """\
# ============================================================
# {name} — AUTO-GENERATED FROM pyproject.toml, 请勿手改
# 生成: python scripts/sync_requirements.py
# 校验: python scripts/sync_requirements.py --check (CI 门禁)
# 手写依赖请编辑 pyproject.toml [project] dependencies / optional-dependencies
# ============================================================
"""


def _pkg_name(req: str) -> str:
    """提取依赖声明的包名 (小写, 去除版本约束)。"""
    name = req.strip()
    for sep in (">=", "<=", "==", "!=", "~=", ">", "<", "[", ";"):
        idx = name.find(sep)
        if idx != -1:
            name = name[:idx]
    return name.strip().lower()


def _load_pyproject() -> tuple[list[str], dict[str, list[str]]]:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    deps = list(data["project"]["dependencies"])
    extras = {k: list(v) for k, v in data["project"].get("optional-dependencies", {}).items()}
    return deps, extras


def _render(name: str, reqs: list[str], prefix: list[str] | None = None) -> str:
    lines = [HEADER.format(name=name)]
    if prefix:
        lines.append("\n# === 前置依赖 ===")
        lines.extend(prefix)
    if reqs:
        lines.append("\n# === 依赖清单 (字母序) ===")
        lines.extend(sorted(reqs, key=_pkg_name))
    else:
        lines.append("\n# (空)")
    lines.append("")
    return "\n".join(lines)


def build_targets() -> dict[str, str]:
    """返回 {目标文件名: 生成内容}。"""
    deps, extras = _load_pyproject()

    core = [r for r in deps if _pkg_name(r) not in WIN_ONLY]
    win_extra = [r for r in deps if _pkg_name(r) in WIN_ONLY]

    targets: dict[str, str] = {
        "requirements.txt": _render("requirements.txt (全量, 开发机)", deps),
        "requirements-core.txt": _render("requirements-core.txt (跨平台核心, 云/Win 共用)", core),
        "requirements-cloud.txt": _render("requirements-cloud.txt (云 Linux 增量, 在 core 之上)", extras.get("cloud", [])),
        "requirements-win.txt": _render(
            "requirements-win.txt (Win 实盘机增量, 在 core 之上)",
            win_extra + extras.get("win", []),
        ),
        "requirements-optional.txt": _render("requirements-optional.txt (可选降级依赖)", extras.get("optional", [])),
        "requirements_dev.txt": _render(
            "requirements_dev.txt (开发环境, 含全量生产依赖)",
            extras.get("dev", []),
            prefix=["-r requirements.txt"],
        ),
    }
    return targets


def _parse_on_disk(path: Path) -> set[str]:
    """解析磁盘上 requirements 文件的包名集合 (忽略注释/-r/include 行)。"""
    names: set[str] = set()
    if not path.exists():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-", " ")) or line.startswith("-r"):
            continue
        names.add(_pkg_name(line))
    return names


def audit() -> int:
    """打印 pyproject 派生结果与磁盘文件差异, 不写盘。"""
    targets = build_targets()
    diffs = 0
    for name, content in targets.items():
        disk = PROJECT_ROOT / name
        gen_names = {_pkg_name(l) for l in content.splitlines() if l.strip() and not l.startswith("#") and not l.startswith("-r")}
        disk_names = _parse_on_disk(disk)
        only_gen = gen_names - disk_names
        only_disk = disk_names - gen_names
        if disk.exists() and content == disk.read_text(encoding="utf-8"):
            print(f"[audit] {name}: 一致 (含注释)")
            continue
        diffs += 1
        print(f"[audit] {name}: 差异")
        if only_gen:
            print(f"    仅生成版有 (需确认是否漏登/冗余): {sorted(only_gen)}")
        if only_disk:
            print(f"    仅磁盘版有 (将丢失, 需确认): {sorted(only_disk)}")
        if not only_gen and not only_disk:
            print("    (仅注释/顺序差异)")
    print(f"\n[audit] {diffs}/{len(targets)} 个文件存在差异; 生成模式将覆盖。")
    return 0


def check() -> int:
    """CI 门禁: 内存生成 vs 磁盘逐字节比对。"""
    targets = build_targets()
    failed = []
    for name, content in targets.items():
        disk = PROJECT_ROOT / name
        if not disk.exists() or disk.read_text(encoding="utf-8") != content:
            failed.append(name)
    if failed:
        print(f"[check] FAIL: 以下派生文件与 pyproject.toml 不一致: {failed}")
        print("[check] 修复: python scripts/sync_requirements.py && git add requirements*.txt")
        return 1
    print(f"[check] PASS: {len(targets)} 个派生文件与 pyproject.toml 一致。")
    return 0


def generate() -> int:
    """覆盖生成 6 个派生文件。"""
    targets = build_targets()
    for name, content in targets.items():
        path = PROJECT_ROOT / name
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"[gen] {name}: {len(content.splitlines())} 行")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--check":
        return check()
    if mode == "--audit":
        return audit()
    if mode in ("", "--generate", "generate"):
        return generate()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
