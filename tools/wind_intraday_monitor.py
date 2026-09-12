"""
Wind 终端盘中标的抓取与研判
============================
数据源：Wind MCP（P1 强制回退）
功能：
  1. 抓取持仓/计划标的的实时行情
  2. 盘中异动检测（涨跌幅/成交量/价格偏离）
  3. 生成买/卖/持有研判结论
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from utils.datetime_utils import now_bj

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PLAN_FILE = os.path.join(_BASE_DIR, "500万建仓计划_20260706.json")
_REPORT_DIR = os.path.join(_BASE_DIR, "reports")
os.makedirs(_REPORT_DIR, exist_ok=True)


def _load_plan_codes() -> list[dict]:
    if not os.path.isfile(_PLAN_FILE):
        return []
    with open(_PLAN_FILE, encoding="utf-8") as f:
        data = json.load(f)
    codes = []
    for code, info in data.get("position_plan", {}).items():
        codes.append(
            {
                "code": code,
                "name": info.get("name", code),
                "style": info.get("style", ""),
                "type": info.get("type", ""),
                "target_weight": info.get("target_weight", 0),
                "target_amount": info.get("target_amount", 0),
                "est_price": info.get("est_price", 0),
                "stop_loss": info.get("stop_loss", 0),
            }
        )
    return codes


def _strip_prefix(code: str) -> str:
    s = str(code).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s


def _to_wind_code(code: str) -> str:
    s = _strip_prefix(code)
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if not s:
        return s
    if s.startswith(("51", "58")):
        return f"{s}.SH"
    if s.startswith(("15", "16")):
        return f"{s}.SZ"
    if s.startswith(("00", "30")):
        return f"{s}.SZ"
    if s.startswith(("6",)):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SH"


def _normalize_code(code: str) -> str:
    s = str(code).strip()
    if s.startswith(("sh", "sz", "bj", "SH", "SZ", "BJ")):
        return s
    prefix = "sh"
    if s.startswith(("0", "3")):
        prefix = "sz"
    elif s.startswith(("4", "8")):
        prefix = "bj"
    return f"{prefix}{s}"


import importlib.util  # noqa: E402

_WIND_FETCHER_PATH = os.path.join(os.path.dirname(_BASE_DIR), "wind_mcp_fetcher.py")


def _import_wind():
    if not os.path.isfile(_WIND_FETCHER_PATH):
        print(f"[WARN] 未找到 wind_mcp_fetcher.py: {_WIND_FETCHER_PATH}")
        return None, None, None
    try:
        spec = importlib.util.spec_from_file_location(
            "wind_mcp_fetcher", _WIND_FETCHER_PATH
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.wind_get_quote, mod.wind_get_batch_quotes, mod.wind_get_kline
    except Exception as e:
        print(f"[WARN] Wind MCP 导入失败: {e}")
        return None, None, None


def _estimate_fund_flow_from_quote(
    quote: dict, prev_close: float | None = None
) -> float:
    price = quote.get("price")
    change_pct = quote.get("change")
    volume = quote.get("volume")
    if price is None or change_pct is None or price <= 0:
        return 0.0
    if prev_close and prev_close > 0:
        base = prev_close
    else:
        base = price / (1 + change_pct / 100) if change_pct else price
    if base <= 0:
        return 0.0
    turnover_est = price * (volume or 0)
    if change_pct > 2:
        return round(turnover_est * 0.30 / 1e8, 2)
    if change_pct > 0.5:
        return round(turnover_est * 0.15 / 1e8, 2)
    if change_pct < -2:
        return round(-turnover_est * 0.30 / 1e8, 2)
    if change_pct < -0.5:
        return round(-turnover_est * 0.15 / 1e8, 2)
    return 0.0


def _judge(row: dict) -> str:
    cp = row.get("change_pct")
    if cp is None:
        return "观察"
    if cp >= 5:
        return "强势上攻"
    if cp >= 2:
        return "偏多"
    if cp <= -5:
        return "明显回调"
    if cp <= -2:
        return "偏空"
    return "震荡"


def _batch_fetch_klines(
    codes: list[str], is_fund_map: dict[str, bool], wind_get_kline, max_workers: int = 4
) -> dict[str, float | None]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    prev_close_map: dict[str, float | None] = {code: None for code in codes}
    if wind_get_kline is None:
        return prev_close_map

    def _fetch_one(code_and_fund):
        code, is_fund = code_and_fund
        try:
            klines = wind_get_kline(code, days=2, is_fund=is_fund)
            if klines and len(klines) >= 2:
                return code, float(
                    klines[-2].get("match", klines[-2].get("close", 0)) or 0
                )
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "获取 %s 前收盘价异常", code, exc_info=True
            )
        return code, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_fetch_one, (code, is_fund_map.get(code, False)))
            for code in codes
        ]
        for future in as_completed(futures):
            code, prev_close = future.result()
            prev_close_map[code] = prev_close
    return prev_close_map


def run(target_date: str | None = None) -> dict:
    now = now_bj()
    target_date = target_date or now.strftime("%Y-%m-%d")
    target_short = target_date.replace("-", "")

    plan_codes = _load_plan_codes()
    wind_get_quote, wind_get_batch_quotes, wind_get_kline = _import_wind()

    if wind_get_quote is None:
        print("❌ Wind MCP 不可用，无法生成盘中研判")
        return {"ok": False, "error": "wind_mcp_unavailable"}

    items: list[dict] = []
    clean_codes: list[str] = []
    is_fund_flags: list[bool] = []
    for item in plan_codes:
        raw_code = item["code"]
        clean_code = _strip_prefix(raw_code)
        is_fund = clean_code.startswith(("51", "58", "15", "16")) or (
            item.get("type", "") in ("ETF", "基金", "债券")
        )
        items.append(item)
        clean_codes.append(clean_code)
        is_fund_flags.append(is_fund)

    print(f"开始批量抓取 {len(clean_codes)} 只标的...")
    batch_quotes = wind_get_batch_quotes(clean_codes, is_fund=False)

    is_fund_map = {
        code: flag for code, flag in zip(clean_codes, is_fund_flags, strict=True)
    }
    prev_close_map = _batch_fetch_klines(
        clean_codes, is_fund_map, wind_get_kline, max_workers=4
    )

    rows: list[dict] = []
    for item, clean_code in zip(items, clean_codes, strict=True):
        raw_code = item["code"]
        quote = batch_quotes.get(clean_code)
        source = "wind_mcp" if quote else "无数据"
        if quote:
            print(
                f"  ✅ {clean_code} => price={quote.get('price')} change={quote.get('change')}"
            )
        else:
            print(f"  ⚠️  {clean_code} => None")

        if not quote:
            rows.append(
                {
                    "code": _normalize_code(raw_code),
                    "name": item.get("name", raw_code),
                    "style": item.get("style", ""),
                    "source": "wind_mcp",
                    "status": "获取失败",
                    "price": None,
                    "change_pct": None,
                    "volume": None,
                    "prev_close": None,
                    "net_flow_yi": None,
                    "est_price": item.get("est_price"),
                    "judgment": "观察",
                }
            )
            continue

        prev_close = prev_close_map.get(clean_code)
        change_pct = quote.get("change")
        price = quote.get("price")
        volume = quote.get("volume")
        net_flow_yi = _estimate_fund_flow_from_quote(quote, prev_close)

        rows.append(
            {
                "code": _normalize_code(raw_code),
                "name": item.get("name", raw_code),
                "style": item.get("style", ""),
                "source": source,
                "status": "正常",
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
                "prev_close": prev_close,
                "net_flow_yi": net_flow_yi,
                "est_price": item.get("est_price"),
                "judgment": _judge({"change_pct": change_pct}),
            }
        )

    style_stats: dict[str, list[float]] = {}
    abnormal_rows: list[dict] = []
    for r in rows:
        style = r.get("style") or "其他"
        style_stats.setdefault(style, []).append(r.get("change_pct") or 0.0)
        if r.get("judgment") in ("强势上攻", "明显回调", "偏多", "偏空"):
            abnormal_rows.append(r)
    abnormal_rows.sort(key=lambda x: abs(x.get("change_pct") or 0), reverse=True)

    report_path = os.path.join(_REPORT_DIR, f"wind_intraday_{target_short}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Wind 终端盘中标的抓取与研判\n\n")
        f.write(f"- **生成时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **目标日期**: {target_date}\n")
        f.write("- **数据来源**: Wind MCP\n")
        f.write(f"- **监控标的**: {len(rows)} 只\n\n")
        f.write("---\n\n")
        f.write("## 一、标的行情总览\n\n")
        f.write(
            "| 代码 | 名称 | 风格 | 最新价 | 估算价 | 价格偏离 | 涨跌幅 | 成交量 | 估算净流入(亿) | 状态 | 研判 |\n"
        )
        f.write(
            "|------|------|------|--------|--------|----------|--------|--------|----------------|------|------|\n"
        )
        for r in rows:
            price_str = f"{r['price']:.3f}" if r["price"] is not None else "-"
            est_price = r.get("est_price")
            est_str = f"{est_price:.3f}" if est_price else "-"
            if r["price"] is not None and est_price:
                deviation = (r["price"] - est_price) / est_price * 100
                deviation_str = f"{deviation:+.2f}%"
            else:
                deviation_str = "-"
            chg_str = f"{r['change_pct']:+.2f}%" if r["change_pct"] is not None else "-"
            vol_str = f"{r['volume']}" if r["volume"] is not None else "-"
            flow_str = (
                f"{r['net_flow_yi']:+.2f}" if r["net_flow_yi"] is not None else "-"
            )
            f.write(
                f"| {r['code']} | {r['name']} | {r['style'] or '-'} | {price_str} | {est_str} | {deviation_str} | {chg_str} | {vol_str} | {flow_str} | {r['status']} | {r['judgment']} |\n"  # noqa: E501
            )
        f.write("\n")
        f.write("---\n\n")
        f.write("## 二、风格涨跌分布\n\n")
        f.write("| 风格 | 平均涨跌幅 | 标的数 |\n")
        f.write("|------|------------|--------|\n")
        for style, changes in sorted(style_stats.items()):
            avg_chg = sum(changes) / len(changes) if changes else 0.0
            f.write(f"| {style} | {avg_chg:+.2f}% | {len(changes)} |\n")
        f.write("\n")
        f.write("---\n\n")
        f.write("## 三、异动与操作提示\n\n")
        if abnormal_rows:
            for r in abnormal_rows:
                f.write(
                    f"- **{r['name']}**({r['code']})：{r['judgment']}，涨跌幅 {r['change_pct']:+.2f}%\n"
                )
        else:
            f.write("当前无明显异动。\n")
        f.write("\n")
        f.write("---\n\n")
        f.write("*本报告由 Wind 终端盘中抓取脚本自动生成*\n")

    summary = {
        "ok": True,
        "target_date": target_date,
        "count": len(rows),
        "report_path": report_path,
        "rows": rows,
        "style_stats": {k: sum(v) / len(v) for k, v in style_stats.items() if v},
        "abnormal_count": len(abnormal_rows),
    }
    print(f"报告已保存: {report_path}")
    print(f"标的数量: {len(rows)}")
    return summary


if __name__ == "__main__":
    run()
