#!/usr/bin/env python3
"""
env_to_secret.py — 从 .env 生成 K8s Secret YAML (stdout)

用法:
    python scripts/cloud/env_to_secret.py .env > cloud/k8s/secret-real.yaml
    kubectl apply -f cloud/k8s/secret-real.yaml

或直接 apply:
    python scripts/cloud/env_to_secret.py .env | kubectl apply -f -

只输出非空值, 空值跳过 (避免覆盖已配置的 key)。
"""
from __future__ import annotations

import sys
from pathlib import Path


def parse_env(path: Path) -> dict[str, str]:
    """解析 .env, 返回 {KEY: VALUE}, 跳过空值/注释."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value:  # 只收非空
            result[key] = value
    return result


def to_secret_yaml(env: dict[str, str], name: str = "quant-secret", namespace: str = "quant") -> str:
    """生成 K8s Secret YAML (stringData, 明文, kubectl 会 base64)."""
    lines = [
        "apiVersion: v1",
        "kind: Secret",
        "metadata:",
        f"  name: {name}",
        f"  namespace: {namespace}",
        "type: Opaque",
        "stringData:",
    ]
    for key in sorted(env):
        lines.append(f"  {key}: {env[key]}")
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: env_to_secret.py <.env路径> [secret名称]", file=sys.stderr)
        sys.exit(1)
    env_path = Path(sys.argv[1])
    secret_name = sys.argv[2] if len(sys.argv) > 2 else "quant-secret"
    if not env_path.exists():
        print(f"错误: {env_path} 不存在", file=sys.stderr)
        sys.exit(1)
    env = parse_env(env_path)
    if not env:
        print("警告: .env 中无非空值, 输出空 Secret", file=sys.stderr)
    print(to_secret_yaml(env, secret_name))


if __name__ == "__main__":
    main()
