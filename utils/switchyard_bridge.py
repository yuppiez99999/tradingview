"""Switchyard 跨版本桥接脚本 (Python 3.12 执行).

本脚本在 Python 3.12 环境中运行 (nemo-switchyard 需要 >=3.12),
接收 JSON 参数, 调用 switchyard_rust 执行路由, 返回 JSON 结果.

主系统 (Python 3.11) 通过 subprocess 调用本脚本.

用法 (由 switchyard_adapter.py 自动调用):
    python3.12 switchyard_bridge.py '{"messages":[...],"model":"auto",...}'
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    if len(sys.argv) < 2:
        sys.stdout.write(json.dumps({"content": "", "error": "缺少参数"}))
        return

    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"content": "", "error": f"JSON 解析失败: {e}"}))
        return

    try:
        from switchyard_rust import server  # type: ignore
        response = server.route_request(
            messages=params.get("messages", []),
            model=params.get("model", "auto"),
            strategy=params.get("strategy", "cost_aware"),
            max_tokens=params.get("max_tokens", 3000),
            temperature=params.get("temperature", 0.3),
            server_url=params.get("server_url", "http://localhost:7777"),
            api_key=params.get("api_key", ""),
            timeout=params.get("timeout", 30),
        )
        sys.stdout.write(json.dumps(response, ensure_ascii=False))
    except (ImportError, RuntimeError, ValueError, ConnectionError, TimeoutError, OSError) as e:
        sys.stdout.write(json.dumps({"content": "", "error": f"switchyard 路由失败: {e}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
