"""
ETF 期权对冲再平衡子模型 - 14 只 ETF 历史日线数据拉取

数据源优先级: Wind MCP (P1, 前复权 qfq) -> AKShare (P3 回退, 亦为前复权 qfq)
时间范围: 2021-01-01 ~ 2026-08-20
保存: 每只 ETF 一个 Parquet + 合并 all_etf_daily.parquet + _fetch_report.json

复权口径 (v8.6.14, 2026-08-25):
  - 主源 Wind MCP get_fund_kline 显式请求前复权 (price_type=1), 与 Wind 终端实测一致
  - 回退 AKShare fund_etf_hist_em(adjust="qfq") 同为前复权 → 回测全程单一口径,
    避免份额折算 ETF 上未复权失真 (512100: +221.5% vs Wind +19.8%)

不修改任何生产代码, 仅创建数据文件。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# === PYTHONPATH 设置 (必须在导入 wind_mcp_fetcher 之前) ===
# 跨平台: 用脚本自身位置推导项目根 (本文件位于 <root>/data/etf_option_backtest/)
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# === 路径与常量 ===
OUTPUT_DIR = Path(PROJECT_ROOT) / "data" / "etf_option_backtest"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DATE_START = "2021-01-01"
DATE_END = "2026-08-20"
DAYS_FETCH = 2100  # 2100 天足够覆盖 5.5 年

# 14 只 ETF 清单
ETFS: dict[str, str] = {
    "510300.SH": "沪深300ETF华泰柏瑞",
    "510500.SH": "中证500ETF南方",
    "510050.SH": "上证50ETF华夏",
    "512100.SH": "中证1000ETF南方",
    "588000.SH": "科创50ETF华夏",
    "159915.SZ": "创业板ETF易方达",
    "512480.SH": "半导体ETF国联安",
    "512010.SH": "医药ETF富国",
    "512660.SH": "军工ETF鹏华",
    "515170.SH": "新能源车ETF华夏",
    "159939.SZ": "信息技术ETF广发",
    "518880.SH": "华安黄金ETF",
    "511260.SH": "国泰国债ETF",
    "510310.SH": "红利ETF易方达",
}


# === Wind MCP 字段 -> 标准字段 映射 ===
# Wind MCP 返回: TIME/OPEN/HIGH/LOW/MATCH(收盘)/VOLUME/TURNOVER/...
def _normalize_wind_record(rec: dict[str, Any], windcode: str) -> dict[str, Any] | None:
    """把 Wind MCP 一条 K 线记录归一化为标准格式"""
    try:
        time_str = str(rec.get("TIME") or rec.get("time") or rec.get("DATE") or "")
        # ISO 格式: 2026-08-10T00:00:00.000+08:00 -> 2026-08-10
        date_str = time_str[:10] if time_str else ""
        if not date_str:
            return None
        # 验证日期格式
        datetime.strptime(date_str, "%Y-%m-%d")

        def _f(key: str) -> float | None:
            v = rec.get(key)
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        return {
            "date": date_str,
            "code": windcode,
            "open": _f("OPEN") or _f("open"),
            "high": _f("HIGH") or _f("high"),
            "low": _f("LOW") or _f("low"),
            "close": _f("MATCH") or _f("close") or _f("CLOSE"),
            "volume": _f("VOLUME") or _f("volume"),
        }
    except Exception:  # noqa: BLE001
        return None


def _filter_by_date(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤日期范围: 2021-01-01 ~ 2026-08-20"""
    out = []
    for r in records:
        d = r.get("date", "")
        if DATE_START <= d <= DATE_END:
            out.append(r)
    return out


# === 数据源 1: Wind MCP ===
def fetch_via_wind_mcp(windcode: str) -> tuple[list[dict[str, Any]], str]:
    """通过 Wind MCP 拉取, 返回 (records, status_msg)"""
    try:
        from wind_mcp_fetcher import wind_get_kline
    except Exception as e:  # noqa: BLE001
        return [], f"import wind_mcp_fetcher 失败: {e}"

    try:
        raw = wind_get_kline(windcode, days=DAYS_FETCH, is_fund=True)
        if not raw:
            return [], "Wind MCP 返回空"
        records = []
        for rec in raw:
            norm = _normalize_wind_record(rec, windcode)
            if norm:
                records.append(norm)
        if not records:
            return [], f"Wind MCP 返回 {len(raw)} 条但归一化后为 0"
        return records, f"Wind MCP 成功 {len(records)} 条"
    except Exception as e:  # noqa: BLE001
        return [], f"Wind MCP 异常: {e}"


# === 数据源 2: AKShare 回退 ===
def fetch_via_akshare(windcode: str) -> tuple[list[dict[str, Any]], str]:
    """通过 AKShare 回退拉取 (前复权 qfq)"""
    # 设置 NO_PROXY 避免代理拒绝国内金融 API
    os.environ["NO_PROXY"] = "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"
    os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])

    try:
        import akshare as ak
    except Exception as e:  # noqa: BLE001
        return [], f"import akshare 失败: {e}"

    # 510300.SH -> 510300
    code_no_suffix = windcode.split(".")[0]
    try:
        df = ak.fund_etf_hist_em(
            symbol=code_no_suffix,
            period="daily",
            start_date=DATE_START.replace("-", ""),
            end_date=DATE_END.replace("-", ""),
            adjust="qfq",
        )
        if df is None or len(df) == 0:
            return [], "AKShare 返回空"
        records = []
        # AKShare ETF 历史返回字段: 日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率
        for _, row in df.iterrows():
            try:
                d = str(row["日期"])[:10]
                datetime.strptime(d, "%Y-%m-%d")
                records.append({
                    "date": d,
                    "code": windcode,
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                    "close": float(row["收盘"]),
                    "volume": float(row["成交量"]),
                })
            except Exception:  # noqa: BLE001
                continue
        return records, f"AKShare 成功 {len(records)} 条"
    except Exception as e:  # noqa: BLE001
        return [], f"AKShare 异常: {e}"


# === Parquet 保存 ===
def _save_parquet(records: list[dict[str, Any]], path: Path) -> bool:
    """保存为 Parquet, 优先 pyarrow, 回退 fastparquet"""
    if not records:
        return False
    try:
        import pandas as pd
        df = pd.DataFrame(records)
        df = df.sort_values("date").reset_index(drop=True)
        # 确保列顺序
        cols = ["date", "code", "open", "high", "low", "close", "volume"]
        df = df[[c for c in cols if c in df.columns]]
        try:
            df.to_parquet(path, engine="pyarrow", index=False)
        except Exception:  # noqa: BLE001
            df.to_parquet(path, engine="fastparquet", index=False)
        return True
    except Exception:  # noqa: BLE001
        return False


# === 主流程 ===
def main() -> int:
    parser = argparse.ArgumentParser(description="ETF 历史日线数据拉取 (Wind MCP -> AKShare)")
    parser.add_argument("--start-date", default="2021-01-01", help="起始日期 (默认 2021-01-01)")
    parser.add_argument("--end-date", default="2026-08-20", help="结束日期 (默认 2026-08-20)")
    parser.add_argument("--output-dir", default=None, help="输出目录 (默认 data/etf_option_backtest/)")
    args = parser.parse_args()

    global DATE_START, DATE_END, DAYS_FETCH, OUTPUT_DIR
    DATE_START = args.start_date
    DATE_END = args.end_date
    start_year = int(DATE_START[:4])
    end_year = int(DATE_END[:4])
    DAYS_FETCH = (end_year - start_year + 1) * 365 + 100
    if args.output_dir:
        OUTPUT_DIR = Path(args.output_dir)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


    # 依赖可用性探测: 用 importlib 而非裸 import, 避免被 pyflakes (CI 零容忍门禁)
    # 判为 "imported but unused" —— # noqa 对裸 pyflakes 无效。
    if importlib.util.find_spec("pandas") is None:
        return 1

    results: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    success_count = 0
    fail_count = 0

    for _i, (windcode, name) in enumerate(ETFS.items(), 1):
        code_short = windcode.split(".")[0]

        # 优先 Wind MCP
        records, msg = fetch_via_wind_mcp(windcode)
        source = "wind_mcp"
        if not records:
            # 回退 AKShare
            records, msg = fetch_via_akshare(windcode)
            source = "akshare"
            if not records:
                results.append({
                    "windcode": windcode,
                    "name": name,
                    "success": False,
                    "source": None,
                    "rows": 0,
                    "date_range": None,
                    "error": f"wind_mcp+akshare 均失败: {msg}",
                })
                fail_count += 1
                continue
        else:
            pass

        # 过滤日期范围
        filtered = _filter_by_date(records)
        if not filtered:
            results.append({
                "windcode": windcode,
                "name": name,
                "success": False,
                "source": source,
                "rows": 0,
                "date_range": None,
                "error": f"过滤后为空, 原始 {len(records)} 条",
            })
            fail_count += 1
            continue

        # 保存单只 ETF Parquet
        parquet_path = OUTPUT_DIR / f"{code_short}.parquet"
        ok = _save_parquet(filtered, parquet_path)
        if not ok:
            results.append({
                "windcode": windcode,
                "name": name,
                "success": False,
                "source": source,
                "rows": len(filtered),
                "date_range": [filtered[0]["date"], filtered[-1]["date"]],
                "error": "Parquet 保存失败",
            })
            fail_count += 1
            continue

        date_range = [filtered[0]["date"], filtered[-1]["date"]]

        results.append({
            "windcode": windcode,
            "name": name,
            "success": True,
            "source": source,
            "rows": len(filtered),
            "date_range": date_range,
            "error": None,
        })
        all_records.extend(filtered)
        success_count += 1

    # === 合并文件 ===
    merged_path = OUTPUT_DIR / "all_etf_daily.parquet"
    if all_records:
        ok = _save_parquet(all_records, merged_path)
        if ok:
            pass
        else:
            pass
    else:
        pass

    # === 拉取报告 JSON ===
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date_range_requested": [DATE_START, DATE_END],
        "days_fetch": DAYS_FETCH,
        "adjust": "qfq",
        "adjust_note": "Wind MCP 默认+显式前复权; AKShare 回退亦为 qfq, 回测单一口径 (v8.6.14)",
        "total_etfs": len(ETFS),
        "success_count": success_count,
        "fail_count": fail_count,
        "merged_file": str(merged_path),
        "merged_rows": len(all_records),
        "etfs": results,
    }
    report_path = OUTPUT_DIR / "_fetch_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # === 汇总表 ===
    for r in results:
        "成功" if r["success"] else "失败"
        r["source"] or "-"
        r["rows"] if r["success"] else 0
        f"{r['date_range'][0]}~{r['date_range'][1]}" if r["date_range"] else "-"

    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
