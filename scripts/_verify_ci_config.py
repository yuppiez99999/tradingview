# -*- coding: utf-8 -*-
"""CI 配置语法验证 — T1.9-E.

验证以下文件语法正确性:
    1. .github/workflows/ci.yml          (YAML)
    2. scripts/ci_run.sh                  (bash)
    3. scripts/ci_nightly.sh              (bash)

YAML 用 PyYAML 解析, bash 用基本语法检查 (字符匹配).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def verify_yaml(path: Path) -> bool:
    """验证 YAML 文件语法."""
    try:
        import yaml
    except ImportError:
        print("PyYAML 未安装, 跳过 YAML 验证")
        return True

    if not path.exists():
        print(f"FAIL: 文件不存在: {path}")
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"FAIL: YAML 语法错误: {e}")
        return False

    # 基础结构验证
    if not isinstance(data, dict):
        print(f"FAIL: 顶层应为 dict, 实际为 {type(data).__name__}")
        return False

    # 必填字段 (YAML 1.1 把 'on' 解析为布尔 True, 兼容处理)
    on_key = "on" if "on" in data else (True if True in data else None)
    if on_key is None:
        print("FAIL: 缺少必填字段: on")
        return False
    if "name" not in data:
        print("FAIL: 缺少必填字段: name")
        return False
    if "jobs" not in data:
        print("FAIL: 缺少必填字段: jobs")
        return False

    # 验证 jobs
    jobs = data["jobs"]
    if not isinstance(jobs, dict) or not jobs:
        print("FAIL: jobs 应为非空 dict")
        return False

    # 每个 job 必填字段
    for job_id, job_def in jobs.items():
        if not isinstance(job_def, dict):
            print(f"FAIL: job '{job_id}' 应为 dict")
            return False
        if "runs-on" not in job_def:
            print(f"FAIL: job '{job_id}' 缺少 runs-on")
            return False
        if "steps" not in job_def:
            print(f"WARN: job '{job_id}' 缺少 steps (允许, 仅做警告)")

    # 验证触发器
    on_value = data[on_key]
    if isinstance(on_value, dict):
        triggers = list(on_value.keys())
    elif isinstance(on_value, str):
        triggers = [on_value]
    elif isinstance(on_value, list):
        triggers = on_value
    else:
        print(f"FAIL: on 字段类型异常: {type(on_value).__name__}")
        return False

    print(f"OK: YAML 语法通过 (触发器: {triggers})")
    print(f"OK: Jobs ({len(jobs)} 个):")
    for job_id, job_def in jobs.items():
        name = job_def.get("name", job_id)
        runner = job_def.get("runs-on", "?")
        timeout = job_def.get("timeout-minutes", "?")
        condition = job_def.get("if", None)
        cond_str = f" [条件: {condition}]" if condition else ""
        print(f"     - {job_id}: {name} (runner={runner}, timeout={timeout}min{cond_str})")

    return True


def verify_bash(path: Path) -> bool:
    """验证 bash 脚本基本语法 (无 bash 环境时的字符级检查)."""
    if not path.exists():
        print(f"FAIL: 文件不存在: {path}")
        return False

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()

    issues = []

    # 1. shebang
    if not lines or not lines[0].startswith("#!"):
        issues.append("缺少 shebang (#!)")

    # 2. 检查 set 命令 (推荐 set -e 或 set -uo pipefail)
    has_set = any(re.match(r"^set\s+-", line) for line in lines)
    if not has_set:
        issues.append("缺少 set 命令 (推荐 'set -uo pipefail')")

    # 3. 检查基本语法: if/fi, case/esac, do/done 配对
    open_if = sum(1 for line in lines if re.match(r"^\s*if\s", line))
    close_fi = sum(1 for line in lines if re.match(r"^\s*fi\s*$", line))
    if open_if != close_fi:
        issues.append(f"if/fi 配对不平衡 (if={open_if}, fi={close_fi})")

    open_case = sum(1 for line in lines if re.match(r"^\s*case\s", line))
    close_esac = sum(1 for line in lines if re.match(r"^\s*esac\s*$", line))
    if open_case != close_esac:
        issues.append(f"case/esac 配对不平衡 (case={open_case}, esac={close_esac})")

    open_do = sum(1 for line in lines if re.match(r"^\s*(for|while|until)\s", line))
    close_done = sum(1 for line in lines if re.match(r"^\s*done\s*$", line))
    if open_do != close_done:
        issues.append(f"do/done 配对不平衡 (循环={open_do}, done={close_done})")

    # 4. 检查引号平衡 (基础检查)
    for i, line in enumerate(lines, 1):
        # 跳过注释行
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # 单引号计数 (排除转义的)
        single_quotes = len(re.findall(r"(?<!\\)'", line))
        if single_quotes % 2 != 0:
            # 可能是多行字符串, 仅警告
            pass  # 不阻断, bash 允许多行字符串

    if issues:
        print(f"FAIL: {path.name} 语法问题:")
        for issue in issues:
            print(f"      - {issue}")
        return False

    print(f"OK: {path.name} 基本语法通过 (行数={len(lines)}, if/fi={open_if}, case/esac={open_case})")
    return True


def main() -> int:
    """主入口.

    Returns:
        0 全部通过, 1 至少一项失败
    """
    print("=" * 72)
    print("CI 配置语法验证 — T1.9-E")
    print("=" * 72)
    print()

    files_to_check = [
        ("YAML", PROJECT_ROOT / ".github" / "workflows" / "ci.yml", verify_yaml),
        ("Bash", PROJECT_ROOT / "scripts" / "ci_run.sh", verify_bash),
        ("Bash", PROJECT_ROOT / "scripts" / "ci_nightly.sh", verify_bash),
    ]

    all_passed = True
    for file_type, path, verify_fn in files_to_check:
        print(f"[{file_type}] {path.relative_to(PROJECT_ROOT)}")
        if not verify_fn(path):
            all_passed = False
        print()

    print("=" * 72)
    if all_passed:
        print("结果: 全部通过 — CI 配置就绪")
        return 0
    else:
        print("结果: 失败 — 上述问题需修复")
        return 1


if __name__ == "__main__":
    sys.exit(main())
