"""后向兼容 shim: 港口煤炭库存单组接入

完整四组接入 (港口库存/电厂日耗/螺纹钢/焦煤焦炭价差) 见
`tools/coal_fundamentals.py::fetch_coal_fundamentals`。本文件仅保留原公共 API
(`fetch_port_coal_inventory` / `render_port_inventory_md` / `dump_json` / CLI)，
供旧调用方与脚本过渡使用, 内部委托 coal_fundamentals 的 port_inventory 组。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":  # 直接运行时补 sys.path (cron/CLI 入口铁律)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.coal_fundamentals import (  # noqa: E402
    PORT_KEY,
    dump_json,
    fetch_coal_fundamentals,
    render_group_md,
)

__all__ = ["fetch_port_coal_inventory", "render_port_inventory_md", "dump_json"]


def fetch_port_coal_inventory(observation: int = 6) -> dict[str, object]:
    """兼容旧签名: 仅取港口库存组, 返回与原结构一致的 dict。

    注: observation 在原实现里被后端忽略, 这里折算成天数窗口 (至少 30 天)。
    """
    res = fetch_coal_fundamentals(days=max(30, int(observation) * 5), use_cache=True)
    if not res.get("ok"):
        return {
            "ok": False,
            "items": [],
            "as_of": "",
            "total": {"value": None, "change": None, "ports": []},
            "error": str(res.get("error", "")),
        }
    grp = res["groups"].get(PORT_KEY, {})
    items = grp.get("items", [])
    d = grp.get("derived", {})
    return {
        "ok": True,
        "items": items,
        "as_of": res.get("as_of", ""),
        "total": {
            "value": d.get("total_value"),
            "change": d.get("total_change"),
            "ports": d.get("total_ports", []),
        },
        "error": "",
        "source": "wind_mcp_edb",
    }


def render_port_inventory_md(result: dict[str, object]) -> list[str]:
    """兼容旧签名: 渲染港口组明细 (复用 coal_fundamentals)"""
    grp = {
        "title": "港口煤炭库存",
        "items": result.get("items", []),  # type: ignore[arg-type]
        "derived": {
            "total_value": (result.get("total") or {}).get("value"),
            "total_change": (result.get("total") or {}).get("change"),
            "total_ports": (result.get("total") or {}).get("ports", []),
            "total_date": result.get("as_of", ""),
        },
    }
    return render_group_md(PORT_KEY, grp)


def _main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="港口煤炭库存 (Wind MCP EDB)")
    p.add_argument("--observation", type=int, default=6)
    p.add_argument("--json", default="")
    args = p.parse_args()

    res = fetch_port_coal_inventory(observation=args.observation)
    if not res.get("ok"):
        print("港口煤炭库存获取失败: " + str(res.get("error")))
        return 1
    for line in render_port_inventory_md(res):
        print(line, end="")
    if args.json:
        print("\nJSON 落盘: " + dump_json(res, args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
