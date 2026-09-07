"""
CLI 模型切换器 — W.C.2 EchoBird 多 CLI 模型切换

一键切换当前 CLI 使用的 LLM 模型 (deepseek/glm/ollama),
同步更新 .env + IDE 配置 (.codebuddy/.trae).

Usage:
    python scripts/cli_model_switcher.py list
    python scripts/cli_model_switcher.py current
    python scripts/cli_model_switcher.py switch glm
    python scripts/cli_model_switcher.py switch glm --dry-run
    python scripts/cli_model_switcher.py switch ollama --no-backup

安全:
    - 切换前自动备份 .env 到 .env.bak.{timestamp}
    - 只改 model/base_url 等非敏感配置, 不碰 API Key
    - --dry-run 只显示将要做的改动, 不实际写入
    - IDE 配置同步是 best-effort (文件不存在时跳过)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore


def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "configs").exists() and (current / "utils").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return Path(__file__).resolve().parent.parent


_PROJECT_ROOT = _find_project_root()
_PROFILES_DIR = _PROJECT_ROOT / "configs" / "cli_profiles"
_ENV_FILE = _PROJECT_ROOT / ".env"


@dataclass
class CLIProfile:
    """CLI 模型配置 profile"""

    name: str
    display_name: str = ""
    description: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)
    fallback_chain: list[str] = field(default_factory=list)
    ide_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"CLIProfile(name={self.name!r}, display_name={self.display_name!r})"


@dataclass
class SwitchResult:
    """切换操作结果"""

    profile: CLIProfile
    dry_run: bool
    env_backup_path: str | None = None
    env_updated: bool = False
    ide_synced: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.name,
            "dry_run": self.dry_run,
            "env_backup_path": self.env_backup_path,
            "env_updated": self.env_updated,
            "ide_synced": self.ide_synced,
            "errors": self.errors,
        }


# ============================================================
# Profile 加载
# ============================================================


def list_profiles() -> list[CLIProfile]:
    """列出所有可用 CLI profile (从 configs/cli_profiles/*.yaml 加载)"""
    if yaml is None:
        raise RuntimeError("PyYAML 未安装, 无法加载 profile")
    profiles = []
    for yml_file in sorted(_PROFILES_DIR.glob("*.yaml")):
        try:
            with open(yml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            profiles.append(_parse_profile(data))
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
            print(f"警告: 加载 profile {yml_file.name} 失败: {e}", file=sys.stderr)
    return profiles


def get_profile(name: str) -> CLIProfile:
    """按名称获取 profile"""
    for p in list_profiles():
        if p.name == name:
            return p
    available = [p.name for p in list_profiles()]
    raise ValueError(f"Profile '{name}' 不存在, 可用: {available}")


def _parse_profile(data: dict[str, Any]) -> CLIProfile:
    return CLIProfile(
        name=data.get("name", ""),
        display_name=data.get("display_name", ""),
        description=data.get("description", ""),
        env_vars=data.get("env_vars", {}) or {},
        fallback_chain=data.get("fallback_chain", []) or [],
        ide_configs=data.get("ide_configs", {}) or {},
    )


# ============================================================
# .env 读写
# ============================================================


def _read_env(path: Path | None = None) -> dict[str, str]:
    """读取 .env 为 dict (保留行序由 OrderedDict 保证)"""
    if path is None:
        path = _ENV_FILE
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _update_env_file(updates: dict[str, str], path: Path | None = None) -> bool:
    """更新 .env 中指定键的值 (保留注释和结构, 不存在的键追加)

    Returns:
        True 如果文件被修改
    """
    if path is None:
        path = _ENV_FILE
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                updated_keys.add(key)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True


def backup_env(path: Path | None = None) -> Path | None:
    """备份 .env 到 .env.bak.{timestamp}"""
    if path is None:
        path = _ENV_FILE
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".env.bak.{timestamp}")
    shutil.copy2(path, backup_path)
    return backup_path


# ============================================================
# IDE 配置同步
# ============================================================


def _sync_ide_config(
    ide_dir: str, config: dict[str, Any], project_root: Path = _PROJECT_ROOT
) -> bool:
    """同步单个 IDE 配置 (best-effort)

    Returns:
        True 如果同步成功
    """
    ide_path = project_root / ide_dir
    if not ide_path.exists():
        return False

    config_type = config.get("type", "json")
    model_field = config.get("model_field", "model")
    model_value = config.get("model_value", "")

    if config_type == "json":
        settings_file = ide_path / "settings.local.json"
        if not settings_file.exists():
            settings_file = ide_path / "settings.json"
        if not settings_file.exists():
            return False
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            data[model_field] = model_value
            settings_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return True
        except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
            print(f"警告: 同步 {ide_dir} 配置失败: {e}", file=sys.stderr)
            return False

    return False


# ============================================================
# 切换 / 查询
# ============================================================


def current() -> CLIProfile | None:
    """从 .env 推断当前激活的 profile"""
    env = _read_env()
    model = env.get("DEEPSEEK_MODEL", "")
    for p in list_profiles():
        if p.env_vars.get("DEEPSEEK_MODEL") == model:
            return p
    return None


def switch(
    profile_name: str, dry_run: bool = False, backup: bool = True
) -> SwitchResult:
    """切换到指定 profile

    Args:
        profile_name: 目标 profile 名称
        dry_run: 只显示改动, 不实际写入
        backup: 切换前备份 .env

    Returns:
        SwitchResult
    """
    profile = get_profile(profile_name)
    result = SwitchResult(profile=profile, dry_run=dry_run)

    if dry_run:
        return result

    if backup and _ENV_FILE.exists():
        backup_path = backup_env()
        result.env_backup_path = str(backup_path) if backup_path else None

    result.env_updated = _update_env_file(profile.env_vars)

    for ide_dir, config in profile.ide_configs.items():
        result.ide_synced[ide_dir] = _sync_ide_config(ide_dir, config)

    return result


# ============================================================
# CLI 入口
# ============================================================


def _format_profile_table(
    profiles: list[CLIProfile], current_profile: CLIProfile | None
) -> str:
    lines = []
    for p in profiles:
        marker = " ← 当前" if current_profile and p.name == current_profile.name else ""
        lines.append(f"  {p.name:<12} {p.display_name:<30} {p.description}{marker}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CLI 模型切换器 (EchoBird W.C.2)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出所有可用 profile")
    sub.add_parser("current", help="查询当前激活的 profile")

    switch_parser = sub.add_parser("switch", help="切换到指定 profile")
    switch_parser.add_argument("profile", help="目标 profile 名称")
    switch_parser.add_argument(
        "--dry-run", action="store_true", help="只显示改动, 不实际写入"
    )
    switch_parser.add_argument(
        "--no-backup", action="store_true", help="跳过 .env 备份"
    )

    args = parser.parse_args(argv)

    if args.command == "list":
        profiles = list_profiles()
        cur = current()
        print(_format_profile_table(profiles, cur))
        return 0

    if args.command == "current":
        cur = current()
        if cur:
            print(f"当前: {cur.name} ({cur.display_name}) — {cur.description}")
        else:
            print("未匹配到当前 profile (DEEPSEEK_MODEL 值未对应任何 profile)")
        return 0

    if args.command == "switch":
        result = switch(args.profile, dry_run=args.dry_run, backup=not args.no_backup)
        if args.dry_run:
            print(
                f"[dry-run] 将切换到: {result.profile.name} ({result.profile.display_name})"
            )
            print(f"  .env 更新: {result.profile.env_vars}")
            print(f"  IDE 同步: {list(result.profile.ide_configs.keys())}")
        else:
            print(f"已切换到: {result.profile.name} ({result.profile.display_name})")
            print(f"  .env 备份: {result.env_backup_path or '跳过'}")
            print(f"  .env 更新: {'成功' if result.env_updated else '失败'}")
            for ide, ok in result.ide_synced.items():
                print(f"  IDE {ide}: {'同步成功' if ok else '跳过/失败'}")
            if result.errors:
                print(f"  错误: {result.errors}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
