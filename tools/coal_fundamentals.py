"""动力煤/黑色系基本面 — Wind MCP EDB 统一接入

把动力煤日报第三章四项监控 (港口库存 / 电厂日耗 / 螺纹钢价格 / 焦煤焦炭价差)
统一接到 Wind EDB (`economic_data` 域), 一次批量取全部指标代码。

指标代码均由 `search_economic_indicator` 实跑确认 (2026-09-10):
    港口库存  S5103725 秦皇岛(日) S5118163 曹妃甸(日) S5131051 黄骅(日)
              J4296449 广州港(日) C7904276 京唐(周)
              Z8948284 环渤海(日,合计口径) S5134688 沿海港口(周,合计口径)
    电厂日耗  Q0149884 中国:日耗量:煤炭重点电厂 (周)
    螺纹钢    S5707798 中国:价格:螺纹钢(HRB400E,20mm) (日, Wind)
              S0179664 中国:现货价:螺纹钢(φ25mm) (日, 商务部)
    焦煤焦炭  U4421912 中国:现货领先价格:焦煤 (日, Wind)
              T2987959 中国:现货领先价格:冶金焦炭 (日, Wind)

优化点 (相对首版端口库存接入):
  1. 单次批量: 全部代码逗号拼接一次调用 (不再按组多次往返)
  2. 日期范围: beginDate/endDate 精确生效 → 窗口可控;
     而 observation 参数后端实测忽略 (传 6 也返 35-43 期)
  3. 当日缓存: 同一自然日重复运行直接复用缓存, 省 Wind 额度
  4. 陈旧标记: 序列最新日落后 today 超 STALE_DAYS 天 → stale=True, 报告显式标注
  5. 派生指标: 港口合计 / 焦煤焦炭价差与比价 在模块内算好并随事实源落盘

纪律: 观测路径 fail-open —— Wind 不可用/取数失败/部分缺码均不抛异常,
      由调用方决定降级 (保留占位行), 绝不阻断日报生成。

CLI: python tools/coal_fundamentals.py [--days 30] [--no-cache] [--json out.json]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from utils.datetime_utils import now_bj
from pathlib import Path
from typing import Any

if __name__ == "__main__":  # 直接运行时补 sys.path (cron/CLI 入口铁律)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.wind_mcp_fetcher import wind_query_economic_indicator  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = ROOT / "reports" / "output" / "data_cache"

# 陈旧判定按频率分档 (周频天然滞后, 阈值放宽)
STALE_DAYS_BY_FREQ = {"日": 7, "周": 21, "月": 40}
STALE_DAYS = 7

INDICATOR_GROUPS: list[dict[str, Any]] = [
    {
        "key": "port_inventory",
        "title": "港口煤炭库存",
        "indicators": [
            {
                "code": "S5103725",
                "label": "秦皇岛港",
                "name": "中国:秦皇岛港:库存量:煤炭",
                "freq": "日",
                "source": "秦皇岛煤炭网",
            },
            {
                "code": "S5118163",
                "label": "曹妃甸港",
                "name": "中国:曹妃甸港:库存量:煤炭",
                "freq": "日",
                "source": "秦皇岛煤炭网",
            },
            {
                "code": "S5131051",
                "label": "黄骅港",
                "name": "中国:黄骅港:场存量:煤炭",
                "freq": "日",
                "source": "根据新闻整理",
            },
            {
                "code": "J4296449",
                "label": "广州港",
                "name": "中国:广州港:场存数量:煤炭",
                "freq": "日",
                "source": "根据新闻整理",
            },
            {
                "code": "C7904276",
                "label": "京唐港",
                "name": "中国:京唐港:库存量:煤炭",
                "freq": "周",
                "source": "中国煤炭市场网",
            },
            {
                "code": "Z8948284",
                "label": "环渤海港",
                "name": "中国:环渤海港:煤炭调度:库存量",
                "freq": "日",
                "source": "中国煤炭市场网",
                "aggregate": True,  # 合计口径, 不并入单港合计
            },
            {
                "code": "S5134688",
                "label": "沿海港口",
                "name": "中国:沿海港口:库存量:煤炭",
                "freq": "周",
                "source": "中国煤炭市场网",
                "aggregate": True,
            },
        ],
    },
    {
        "key": "power_daily_use",
        "title": "电厂日耗",
        "indicators": [
            {
                "code": "Q0149884",
                "label": "重点电厂",
                "name": "中国:日耗量:煤炭重点电厂",
                "freq": "周",
                "source": "根据新闻整理",
            },
        ],
    },
    {
        "key": "rebar_price",
        "title": "螺纹钢价格",
        "indicators": [
            {
                "code": "S5707798",
                "label": "螺纹钢HRB400E 20mm",
                "name": "中国:价格:螺纹钢(HRB400E,20mm)",
                "freq": "日",
                "source": "Wind",
            },
            {
                "code": "S0179664",
                "label": "螺纹钢现货φ25mm",
                "name": "中国:现货价:螺纹钢(φ25mm)",
                "freq": "日",
                "source": "商务部",
            },
        ],
    },
    {
        "key": "coke_spread",
        "title": "焦煤/焦炭价差",
        "indicators": [
            {
                "code": "T2987959",
                "label": "冶金焦炭",
                "name": "中国:现货领先价格:冶金焦炭",
                "freq": "日",
                "source": "Wind",
                "role": "coke",
            },
            {
                "code": "U4421912",
                "label": "焦煤",
                "name": "中国:现货领先价格:焦煤",
                "freq": "日",
                "source": "Wind",
                "role": "coking_coal",
            },
        ],
    },
]

# 组内展示顺序按注册表定义顺序 (EDB 返回顺序不稳定)
for _grp in INDICATOR_GROUPS:
    for _i, _ind in enumerate(_grp["indicators"]):
        _ind.setdefault("order", _i)

# 趋势措辞按组语义 (库存=累库/去库, 价格=上行/下行, 日耗=上升/下降)
TREND_WORDS: dict[str, tuple[str, str]] = {
    "port_inventory": ("累库", "去库"),
    "power_daily_use": ("上升", "下降"),
    "rebar_price": ("上行", "下行"),
    "coke_spread": ("上行", "下行"),
}

# 港口库存组键 (供 shim 与调用方复用)
PORT_KEY = "port_inventory"
PORT_INVENTORY_INDICATORS = [
    ind for grp in INDICATOR_GROUPS if grp["key"] == PORT_KEY for ind in grp["indicators"]
]

# code → (group_key, indicator 定义)
_INDICATOR_INDEX: dict[str, tuple[str, dict[str, Any]]] = {
    ind["code"]: (grp["key"], ind)
    for grp in INDICATOR_GROUPS
    for ind in grp["indicators"]
}


def _to_float(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(d: str) -> datetime | None:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(d), fmt)
        except (ValueError, TypeError):
            continue
    return None


def _build_item(metric: dict, local: dict[str, Any], today: datetime) -> dict[str, Any] | None:
    """归一化一条 EDB metric; 无有效数值返回 None"""
    meta = metric.get("meta") or {}
    dates = [str(d) for d in (metric.get("date") or [])]
    values = [_to_float(v) for v in (metric.get("value") or [])]
    pairs = [(d, v) for d, v in zip(dates, values, strict=False) if v is not None]
    if not pairs:
        return None

    d_last, v_last = pairs[-1]
    v_prev = pairs[-2][1] if len(pairs) >= 2 else None
    change = (v_last - v_prev) if v_prev is not None else None
    change_pct = (change / v_prev * 100) if (v_prev and change is not None) else None

    dt_last = _parse_date(d_last)
    lag_days = (today - dt_last).days if dt_last else None
    freq = meta.get("freq") or local.get("freq") or ""
    stale_after = STALE_DAYS_BY_FREQ.get(freq, STALE_DAYS)
    return {
        "code": str(meta.get("code") or local.get("code") or ""),
        "label": local.get("label") or meta.get("name") or "",
        "name": meta.get("name") or local.get("name") or "",
        "unit": meta.get("unit") or "",
        "order": int(local.get("order") or 0),
        "value": v_last,
        "date": d_last,
        "prev_value": v_prev,
        "change": change,
        "change_pct": change_pct,
        "change_window": (v_last - pairs[0][1]) if len(pairs) >= 2 else None,
        "window_periods": len(pairs),
        "freq": freq,
        "source": meta.get("source") or local.get("source") or "",
        "update_date": str(meta.get("updateDate") or ""),
        "aggregate": bool(local.get("aggregate")),
        "role": local.get("role") or "",
        "lag_days": lag_days,
        "stale": bool(lag_days is not None and lag_days > stale_after),
        "series": [{"date": d, "value": v} for d, v in pairs],
    }


def _derive(group_key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """组内派生指标: 港口合计 / 焦煤焦炭价差与比价"""
    if group_key == "port_inventory":
        single = [i for i in items if not i["aggregate"] and i["freq"] == "日"]
        if not single:
            return {}
        prev = [i["prev_value"] for i in single if i["prev_value"] is not None]
        total = sum(i["value"] for i in single)
        return {
            "total_value": round(total, 1),
            "total_change": round(total - sum(prev), 1) if len(prev) == len(single) else None,
            "total_ports": [i["label"] for i in single],
            "total_date": min(i["date"] for i in single),
        }

    if group_key == "coke_spread":
        coke = next((i for i in items if i["role"] == "coke"), None)
        coal = next((i for i in items if i["role"] == "coking_coal"), None)
        if not coke or not coal:
            return {}
        return {
            "coke_price": coke["value"],
            "coking_coal_price": coal["value"],
            "spread": round(coke["value"] - coal["value"], 1),
            "ratio": round(coke["value"] / coal["value"], 3) if coal["value"] else None,
            "as_of": min(coke["date"], coal["date"]),
            "unit": coke.get("unit") or "元/吨",
        }
    return {}


def _cache_path(cache_dir: Path, day: str) -> Path:
    return cache_dir / f"coal_fundamentals_{day}.json"


def _load_cache(cache_dir: Path, day: str) -> dict[str, Any] | None:
    p = _cache_path(cache_dir, day)
    try:
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if data.get("_cached_date") == day else None


def fetch_coal_fundamentals(
    days: int = 30,
    use_cache: bool = True,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """一次批量取全部基本面指标 (Wind EDB)

    Args:
        days: 取数窗口 (自然日, 回看区间), 覆盖日频与周频序列
        use_cache: 同日命中缓存则直接复用 (省 Wind 额度)
        cache_dir: 缓存目录, 默认 reports/output/data_cache

    Returns:
        {"ok": bool, "as_of": str, "groups": {key: {"title","items","derived"}}, "error": str}
    """
    today = now_bj()
    day = today.strftime("%Y%m%d")
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    out: dict[str, Any] = {
        "ok": False,
        "as_of": "",
        "groups": {},
        "error": "",
        "source": "wind_mcp_edb",
        "window_days": int(days),
    }

    if use_cache:
        cached = _load_cache(cdir, day)
        if cached:
            cached["from_cache"] = True
            return cached

    begin = (today - timedelta(days=int(days))).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    codes = ",".join(_INDICATOR_INDEX.keys())
    try:
        metrics = wind_query_economic_indicator(
            codes, begin_date=begin, end_date=end
        )
    except Exception as e:  # fail-open
        out["error"] = f"fetch_failed: {type(e).__name__}: {e}"
        return out

    if not metrics:
        out["error"] = "no_metrics_returned"
        return out

    items_by_group: dict[str, list[dict[str, Any]]] = {g["key"]: [] for g in INDICATOR_GROUPS}
    for m in metrics or []:
        code = str(((m or {}).get("meta") or {}).get("code") or "")
        hit = _INDICATOR_INDEX.get(code)
        if hit is None:
            continue
        gkey, local = hit
        item = _build_item(m, local, today)
        if item:
            items_by_group[gkey].append(item)

    for _items in items_by_group.values():
        _items.sort(key=lambda x: x.get("order", 0))

    groups: dict[str, Any] = {}
    for grp in INDICATOR_GROUPS:
        key = grp["key"]
        items = items_by_group.get(key) or []
        if not items:
            continue
        groups[key] = {
            "title": grp["title"],
            "items": items,
            "derived": _derive(key, items),
        }

    if not groups:
        out["error"] = "no_valid_items"
        return out

    out["groups"] = groups
    out["ok"] = True
    out["as_of"] = max(
        (i["date"] for g in groups.values() for i in g["items"]), default=""
    )
    out["_cached_date"] = day
    if use_cache:
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            _cache_path(cdir, day).write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass  # 缓存写失败不影响主流程
    return out


def _fmt_change(item: dict[str, Any]) -> str:
    chg = item.get("change")
    if chg is None:
        return "-"
    pct = item.get("change_pct")
    return f"{chg:+.1f} ({pct:+.1f}%)" if pct is not None else f"{chg:+.1f}"


def render_group_md(key: str, group: dict[str, Any]) -> list[str]:
    """渲染单个监控组的 markdown (表 + 派生行 + 趋势行)"""
    items = group.get("items") or []
    lines = [
        f"### {group.get('title', key)} (Wind MCP EDB)\n\n",
        "| 指标 | 最新值 | 环比 | 数据日期 | 频率 | 来源 |\n",
        "|------|-------|------|---------|------|------|\n",
    ]
    for i in items:
        unit = i.get("unit") or ""
        label = i["label"] + ("(合计口径)" if i["aggregate"] else "")
        if i.get("stale"):
            label += f"(滞后{i.get('lag_days')}天)"
        lines.append(
            f"| {label} | {i['value']:.1f} {unit} | {_fmt_change(i)} | {i['date']} | "
            f"{i.get('freq','')} | {i.get('source','')} |\n"
        )

    d = group.get("derived") or {}
    if key == "port_inventory" and d.get("total_value") is not None:
        chg = d.get("total_change")
        chg_txt = f"{chg:+.1f}" if chg is not None else "-"
        lines.append(
            f"| **{'+'.join(d.get('total_ports', []))} 合计** | "
            f"**{d['total_value']:.1f} 万吨** | **{chg_txt}** | "
            f"{d.get('total_date') or '-'} | 日 | Wind EDB 汇总 |\n"
        )
        lines.append(
            "\n> 合计仅累加单港日频口径, 不含环渤海港/沿海港口等合计口径 (避免重复计数)\n"
        )
    if key == "coke_spread" and d.get("spread") is not None:
        lines.append(
            f"\n- 焦炭-焦煤价差: **{d['spread']:.1f} {d.get('unit','')}** "
            f"(焦炭 {d.get('coke_price')} / 焦煤 {d.get('coking_coal_price')}, "
            f"比价 {d.get('ratio')}), 截至 {d.get('as_of','')}\n"
        )

    trends = [i for i in items if i.get("change_window") is not None]
    if trends:
        up = sum(1 for i in trends if i["change_window"] > 0)
        down = sum(1 for i in trends if i["change_window"] < 0)
        n = max((i.get("window_periods") or 0) for i in trends)
        word_up, word_down = TREND_WORDS.get(key, ("上行", "下行"))
        label = word_up if up > down else (word_down if down > up else "持平")
        detail = "; ".join(f"{i['label']} {i['change_window']:+.1f}" for i in trends)
        lines.append(
            f"\n- 取数窗口 {n} 期内累计变化: **{label}** "
            f"({word_up} {up} / {word_down} {down} 个口径) — {detail}\n"
        )
    return lines


def render_fundamentals_md(result: dict[str, Any]) -> list[str]:
    """渲染全部监控组; 顺序与 INDICATOR_GROUPS 一致"""
    lines: list[str] = []
    groups = result.get("groups") or {}
    for grp in INDICATOR_GROUPS:
        g = groups.get(grp["key"])
        if g:
            lines.extend(render_group_md(grp["key"], g))
    if lines:
        lines.append("\n> 数据来源于万得 Wind 金融数据服务。\n")
    return lines


def dump_json(result: dict[str, Any], path: str | Path) -> str:
    """事实源落盘 (报告归档用), 写失败不抛异常"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)
    except Exception:
        return ""


def _main() -> int:
    days = 30
    use_cache = True
    out_json = ""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--days" and i + 1 < len(argv):
            days = int(argv[i + 1])
        elif a == "--json" and i + 1 < len(argv):
            out_json = argv[i + 1]
        elif a == "--no-cache":
            use_cache = False

    res = fetch_coal_fundamentals(days=days, use_cache=use_cache)
    if not res.get("ok"):
        print("基本面取数失败: " + str(res.get("error")))
        return 1
    if res.get("from_cache"):
        print("(命中当日缓存)")
    for line in render_fundamentals_md(res):
        print(line, end="")
    if out_json:
        print("\nJSON 落盘: " + dump_json(res, out_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
