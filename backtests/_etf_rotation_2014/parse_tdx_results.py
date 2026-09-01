"""解析通达信 tdx_kline MCP 落盘结果文件 → 干净 OHLCV 面板。

用法: python parse_tdx_results.py <tool-results目录> <输出目录>
"""
import glob
import json
import logging
import os
import re
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def parse_files(res_dir: str) -> pd.DataFrame | None:
    """扫描 tool-results 目录, 解析 tdx_kline JSON 与 manual CSV 补充段, 返回 OHLCV DataFrame。"""
    records: list[dict[str, object]] = []
    files = sorted(
        glob.glob(os.path.join(res_dir, "mcp-connector-proxy-tdx-connector_tdx_kline-*.txt"))
        + glob.glob(os.path.join(res_dir, "call_01_*.txt"))
    )
    if not files:
        logger.warning("!! 未找到 tdx_kline 结果文件: %s", res_dir)
        return None
    for fp in files:
        with open(fp, encoding="utf-8", errors="replace") as f:
            text = f.read()
        m = re.search(r"详细K线数据:\s*\n?(\{)", text)
        if not m:
            logger.warning("!! 无JSON起始: %s", os.path.basename(fp))
            continue
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[m.start(1) :])
        except ValueError as e:
            # raw_decode 对非法 JSON 抛 JSONDecodeError (ValueError 子类)
            logger.warning("!! JSON解析失败: %s %s", os.path.basename(fp), e)
            continue
        code = str(obj.get("Code"))
        startxh = int(obj.get("Startxh", 0) or 0)
        rows = obj.get("Rows") or []
        if not rows:
            logger.warning("!! %s startxh=%s 空段(历史不足或超界)", code, startxh)
            continue
        for r in rows:
            records.append(
                dict(
                    code=code,
                    startxh=startxh,
                    date=str(r["Data"]),
                    open=float(r["Open"]),
                    high=float(r["High"]),
                    low=float(r["Low"]),
                    close=float(r["Close"]),
                    volume=float(r.get("Volume", 0)),
                    amount=float(r.get("Amount", 0)),
                )
            )
        logger.info(
            "  %s startxh=%4d rows=%4d range=%s..%s",
            code,
            startxh,
            len(rows),
            rows[0]["Data"],
            rows[-1]["Data"],
        )
    # 手动补充段（对话直返未落盘的数据），文件名 manual_<code>_<startxh>.csv
    for mf in sorted(glob.glob(os.path.join(res_dir, "manual_*.csv"))):
        base = os.path.basename(mf)
        parts = base.replace("manual_", "").replace(".csv", "").split("_")
        code, startxh = parts[0], int(parts[1])
        with open(mf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d, o, h, lo, c = line.split(",")
                records.append(
                    dict(
                        code=code,
                        startxh=startxh,
                        date=d.strip(),
                        open=float(o),
                        high=float(h),
                        low=float(lo),
                        close=float(c),
                        volume=0.0,
                        amount=0.0,
                    )
                )
        logger.info("  [manual] %s startxh=%4d rows=补入", code, startxh)
    return pd.DataFrame(records)


def main() -> None:
    """CLI 入口: 解析 → 去重 → 落盘 raw 面板 + close 透视面板 + 覆盖报告。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res_dir = (
        sys.argv[1]
        if len(sys.argv) > 1
        else r"C:\Users\Administrator\.workbuddy\projects\e-各种PY程序-28-终极量化交易系统8.4\e0b32441-aef1-499f-9b65-f13b6aaa4ef5\tool-results"
    )
    out_dir = (
        sys.argv[2]
        if len(sys.argv) > 2
        else str(Path(__file__).resolve().parent / "data")
    )
    os.makedirs(out_dir, exist_ok=True)
    df = parse_files(res_dir)
    if df is None or df.empty:
        logger.info("无数据")
        return
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    # 同段内可能出现重复，整体去重
    df = df.sort_values(["code", "date"]).drop_duplicates(["code", "date"])
    df.to_csv(os.path.join(out_dir, "etf_daily_raw.csv"), index=False)
    piv = df.pivot_table(index="date", columns="code", values="close")
    piv.to_csv(os.path.join(out_dir, "etf_close_panel.csv"))
    # 检查每只代码的覆盖
    logger.info("\n==== 覆盖情况 ====")
    for code, g in df.groupby("code"):
        logger.info(
            "%s: %d根  %s .. %s", code, len(g), g["date"].min().date(), g["date"].max().date()
        )
    logger.info("\n总记录: %d | 代码: %s", len(df), sorted(df.code.unique()))
    logger.info("日期范围: %s .. %s", df.date.min().date(), df.date.max().date())


if __name__ == "__main__":
    main()
