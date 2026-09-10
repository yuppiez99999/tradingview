"""港口煤炭库存 (Wind MCP EDB)

把动力煤日报里长期挂"待接入数据源"的港口库存, 接到 Wind EDB (economic_data 域)。

数据源: Wind MCP `economic_data.query_economic_indicator_data`, 指标代码由
`search_economic_indicator` 实跑确认 (2026-09-10):

| 代码 | 指标 | 频率 | 来源 |
|---|---|---|---|
| S5103725 | 中国:秦皇岛港:库存量:煤炭 | 日 | 秦皇岛煤炭网 |
| S5118163 | 中国:曹妃甸港:库存量:煤炭 | 日 | 秦皇岛煤炭网 |
| S5131051 | 中国:黄骅港:场存量:煤炭 | 日 | 根据新闻整理 |
| J4296449 | 中国:广州港:场存数量:煤炭 | 日 | 根据新闻整理 |
| C7904276 | 中国:京唐港:库存量:煤炭 | 周 | 中国煤炭市场网 |
| Z8948284 | 中国:环渤海港:煤炭调度:库存量 | 日 | 中国煤炭市场网 (合计口径) |
| S5134688 | 中国:沿海港口:库存量:煤炭 | 周 | 中国煤炭市场网 (合计口径) |

设计原则:
  - 观测路径 fail-open: Wind 不可用/无 key/解析失败 → ok=False + 空 items, 由调用方
    保留占位行, 不阻断报告生成
  - 一次批量取全部代码 (合同: question 支持英文逗号分隔多代码), 缺码再逐个补取
  - 实测 (2026-09-10): 后端忽略 observation, 日频固定返回约 5 周窗口 (35-43 期),
    故"窗口内累计变化"以实际返回期数 n 标注, 不谎称近 N 期
  - 事实源落盘: dump_json() 写 JSON 到报告归档目录, 便于下游复用与审计

CLI: python tools/coal_port_inventory.py [--observation 6] [--json out.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __name__ == "__main__":  # 直接运行时补 sys.path (cron/CLI 入口铁律)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.wind_mcp_fetcher import wind_query_economic_indicator  # noqa: E402

# aggregate=True 表示本身是合计口径 (与单港并列会重复计数, 不进"样本港口合计")
PORT_INVENTORY_INDICATORS: list[dict[str, Any]] = [
    {
        "code": "S5103725",
        "port": "秦皇岛港",
        "name": "中国:秦皇岛港:库存量:煤炭",
        "freq": "日",
        "source": "秦皇岛煤炭网",
        "aggregate": False,
    },
    {
        "code": "S5118163",
        "port": "曹妃甸港",
        "name": "中国:曹妃甸港:库存量:煤炭",
        "freq": "日",
        "source": "秦皇岛煤炭网",
        "aggregate": False,
    },
    {
        "code": "S5131051",
        "port": "黄骅港",
        "name": "中国:黄骅港:场存量:煤炭",
        "freq": "日",
        "source": "根据新闻整理",
        "aggregate": False,
    },
    {
        "code": "J4296449",
        "port": "广州港",
        "name": "中国:广州港:场存数量:煤炭",
        "freq": "日",
        "source": "根据新闻整理",
        "aggregate": False,
    },
    {
        "code": "C7904276",
        "port": "京唐港",
        "name": "中国:京唐港:库存量:煤炭",
        "freq": "周",
        "source": "中国煤炭市场网",
        "aggregate": False,
    },
    {
        "code": "Z8948284",
        "port": "环渤海港",
        "name": "中国:环渤海港:煤炭调度:库存量",
        "freq": "日",
        "source": "中国煤炭市场网",
        "aggregate": True,
    },
    {
        "code": "S5134688",
        "port": "沿海港口",
        "name": "中国:沿海港口:库存量:煤炭",
        "freq": "周",
        "source": "中国煤炭市场网",
        "aggregate": True,
    },
]

_INDICATOR_BY_CODE = {i["code"]: i for i in PORT_INVENTORY_INDICATORS}


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_item(metric: dict, meta_local: dict[str, Any]) -> dict[str, Any] | None:
    """把一条 EDB metric 归一化成本模块输出结构; 无有效数值返回 None"""
    meta = metric.get("meta") or {}
    code = str(meta.get("code") or meta_local.get("code") or "")
    dates = [str(d) for d in (metric.get("date") or [])]
    values = [_to_float(v) for v in (metric.get("value") or [])]
    # strict=False: EDB 日期/数值并列返回, 异常长度时按较短截断 (fail-open)
    pairs = [(d, v) for d, v in zip(dates, values, strict=False) if v is not None]
    if not pairs:
        return None

    d_last, v_last = pairs[-1]
    v_prev = pairs[-2][1] if len(pairs) >= 2 else None
    change = (v_last - v_prev) if v_prev is not None else None
    change_pct = (change / v_prev * 100) if (v_prev and change is not None) else None
    # 窗口首末差 (近 N 期累库/去库方向)
    change_window = (v_last - pairs[0][1]) if len(pairs) >= 2 else None

    unit = meta.get("unit") or "万吨"
    return {
        "code": code,
        "port": meta_local.get("port") or meta.get("name") or code,
        "name": meta.get("name") or meta_local.get("name") or "",
        "unit": unit,
        "value": v_last,
        "date": d_last,
        "prev_value": v_prev,
        "change": change,
        "change_pct": change_pct,
        "change_window": change_window,
        "window_periods": len(pairs),
        "freq": meta.get("freq") or meta_local.get("freq") or "",
        "source": meta.get("source") or meta_local.get("source") or "",
        "update_date": str(meta.get("updateDate") or ""),
        "aggregate": bool(meta_local.get("aggregate")),
        "series": [{"date": d, "value": v} for d, v in pairs],
    }


def fetch_port_coal_inventory(observation: int = 6) -> dict[str, Any]:
    """拉取港口煤炭库存 (Wind EDB)

    Args:
        observation: 近 N 期 (默认 6, 够算环比与 5 期趋势)

    Returns:
        {
          "ok": bool, "items": [...], "as_of": "YYYYMMDD"|"",
          "total": {"value": float|None, "change": float|None, "ports": [...]},
          "error": str|""
        }
    """
    out: dict[str, Any] = {
        "ok": False,
        "items": [],
        "as_of": "",
        "total": {"value": None, "change": None, "ports": []},
        "error": "",
        "source": "wind_mcp_edb",
    }
    try:
        codes = ",".join(i["code"] for i in PORT_INVENTORY_INDICATORS)
        metrics = wind_query_economic_indicator(codes, observation=str(int(observation)))
    except Exception as e:  # fail-open: 网络/鉴权/解析异常一律降级
        out["error"] = f"fetch_failed: {type(e).__name__}: {e}"
        return out

    if not metrics:
        out["error"] = "no_metrics_returned"
        return out

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in metrics:
        meta = (m or {}).get("meta") or {}
        code = str(meta.get("code") or "")
        local = _INDICATOR_BY_CODE.get(code)
        if local is None:
            continue
        item = _build_item(m, local)
        if item:
            items.append(item)
            seen.add(code)

    # 缺码逐个补取 (批量返回被截断/单码失败时的兜底, 最多 3 次)
    missing = [i for i in PORT_INVENTORY_INDICATORS if i["code"] not in seen][:3]
    for ind in missing:
        try:
            one = wind_query_economic_indicator(ind["code"], observation=str(int(observation)))
        except Exception:
            continue
        for m in one:
            item = _build_item(m, ind)
            if item:
                items.append(item)

    if not items:
        out["error"] = "no_valid_items"
        return out

    items.sort(key=lambda x: (x["aggregate"], x["port"]))
    out["items"] = items
    out["ok"] = True
    out["as_of"] = max((i["date"] for i in items), default="")

    single = [i for i in items if not i["aggregate"] and i["freq"] == "日"]
    if single:
        total = sum(i["value"] for i in single)
        prev = [i["prev_value"] for i in single if i["prev_value"] is not None]
        out["total"] = {
            "value": round(total, 1),
            "change": round(total - sum(prev), 1) if len(prev) == len(single) else None,
            "ports": [i["port"] for i in single],
        }
    return out


def render_port_inventory_md(result: dict[str, Any]) -> list[str]:
    """渲染港口库存明细 markdown 行 (调用方负责拼进报告)"""
    lines: list[str] = [
        "### 港口煤炭库存明细 (Wind MCP EDB)\n\n",
        "| 港口 | 最新库存 | 环比 | 数据日期 | 频率 | 来源 |\n",
        "|------|---------|------|---------|------|------|\n",
    ]
    for i in result.get("items", []):
        unit = i.get("unit") or "万吨"
        chg = i.get("change")
        if chg is None:
            chg_txt = "-"
        else:
            pct = i.get("change_pct")
            pct_txt = f" ({pct:+.1f}%)" if pct is not None else ""
            chg_txt = f"{chg:+.1f}{pct_txt}"
        port = i["port"] + ("(合计口径)" if i["aggregate"] else "")
        lines.append(
            f"| {port} | {i['value']:.1f} {unit} | {chg_txt} | {i['date']} | "
            f"{i.get('freq','')} | {i.get('source','')} |\n"
        )

    total = result.get("total") or {}
    if total.get("value") is not None:
        chg = total.get("change")
        chg_txt = f"{chg:+.1f}" if chg is not None else "-"
        daily_single = [
            i for i in result.get("items", []) if not i["aggregate"] and i["freq"] == "日"
        ]
        dmin = min((i["date"] for i in daily_single), default="")
        dmax = result.get("as_of", "")
        lines.append(
            f"| **{'+'.join(total.get('ports', []))} 合计** | **{total['value']:.1f} 万吨** | "
            f"**{chg_txt}** | {dmin} | 日 | Wind EDB 汇总 |\n"
        )
        date_note = (
            f"; 各港数据日期可能不同 (最早 {dmin} / 最新 {dmax})"
            if dmin and dmax and dmin != dmax
            else ""
        )
        lines.append(
            "\n> 合计仅累加单港日频口径, 不含环渤海港/沿海港口等合计口径指标 (避免重复计数)"
            + date_note
            + "\n"
        )

    trends = [i for i in result.get("items", []) if i.get("change_window") is not None]
    if trends:
        up = sum(1 for i in trends if i["change_window"] > 0)
        down = sum(1 for i in trends if i["change_window"] < 0)
        n = max((i.get("window_periods") or 0) for i in trends)
        label = "累库" if up > down else ("去库" if down > up else "持平")
        detail = "; ".join(f"{i['port']} {i['change_window']:+.1f}" for i in trends)
        lines.append(
            f"\n- 取数窗口 {n} 期内累计变化: **{label}** "
            f"(累库 {up} / 去库 {down} 个口径) — {detail}\n"
        )

    lines.append("\n> 数据来源于万得 Wind 金融数据服务。\n")
    return lines


def dump_json(result: dict[str, Any], path: str | Path) -> str:
    """事实源落盘, 便于下游复用与审计; 写失败不抛异常"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(p)
    except Exception:
        return ""


def _main() -> int:
    obs = 6
    out_json = ""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--observation" and i + 1 < len(argv):
            obs = int(argv[i + 1])
        elif a == "--json" and i + 1 < len(argv):
            out_json = argv[i + 1]

    res = fetch_port_coal_inventory(observation=obs)
    if not res.get("ok"):
        print("港口煤炭库存获取失败: " + str(res.get("error")))
        return 1
    for line in render_port_inventory_md(res):
        print(line, end="")
    if out_json:
        print("\nJSON 落盘: " + dump_json(res, out_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
