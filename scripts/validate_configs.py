#!/usr/bin/env python3
"""config/*.yaml|*.json 轻量 schema 校验 (AUTO-8, Issue #1)。

CI 变更检测: 任意 config 文件无法解析或顶层结构非法即失败 (exit 1)。
纯 stdlib + PyYAML (必装依赖)。退出 0 = 全部通过。

用法:
    python scripts/validate_configs.py [--config-dir config]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML 未安装 (pip install pyyaml)", file=sys.stderr)
    sys.exit(1)


def _validate_yaml(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError) as exc:
        return f"{path.name}: YAML 解析失败 — {exc}"
    if data is None:
        return f"{path.name}: 空文件 (无配置内容)"
    if not isinstance(data, (dict, list)):
        return f"{path.name}: 顶层应为 mapping/list, 实为 {type(data).__name__}"
    return None


def _validate_json(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        return f"{path.name}: JSON 解析失败 — {exc}"
    if not isinstance(data, (dict, list)):
        return f"{path.name}: 顶层应为 object/array, 实为 {type(data).__name__}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="config schema 轻量校验 (AUTO-8)")
    parser.add_argument("--config-dir", default="config", help="配置目录 (默认 config)")
    args = parser.parse_args()

    root = Path(args.config_dir)
    if not root.is_dir():
        print(f"ERROR: 配置目录不存在: {root}", file=sys.stderr)
        return 1

    files: list[Path] = []
    for pattern in ("*.yaml", "*.yml", "*.json"):
        files.extend(sorted(root.glob(pattern)))
    if not files:
        print(f"WARN: {root} 下无 yaml/json 文件", file=sys.stderr)
        return 0

    errors: list[str] = []
    for path in files:
        err = _validate_json(path) if path.suffix == ".json" else _validate_yaml(path)
        if err:
            errors.append(err)

    if errors:
        print("CONFIG SCHEMA VALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"CONFIG SCHEMA OK: {len(files)} 个文件解析通过 (yaml/json 顶层结构合法)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
