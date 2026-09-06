"""fetch_klines.py — 通过 westock-data CLI 在线拉取 v5.10 组合 23 只标的的前复权日K线。

数据源: 腾讯自选股行情接口 (westock-data skill, node scripts/index.js)
输出: klines_qfq.csv  (symbol, date, open, high, low, close, volume)
已存在且非空时跳过拉取 (缓存)。
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

NODE = shutil.which("node")
if not NODE:
    _pf = os.environ.get("PROGRAMFILES")
    NODE = str(Path(_pf) / "nodejs" / "node.exe") if _pf else "node"
_EXPERT_CACHE = Path.home() / ".workbuddy" / "plugins" / "cache" / "experts"
WESTOCK = str(
    _EXPERT_CACHE / "strategy-backtest-expert/1.0.0/skills/westock-data/scripts/index.js"
)

# westock代码 -> (标准代码, 名称)
SYMBOLS = {
    "sh510300": ("510300.SH", "沪深300ETF"),
    "sh510500": ("510500.SH", "中证500ETF"),
    "sh512100": ("512100.SH", "中证1000ETF"),
    "sh588000": ("588000.SH", "科创50ETF"),
    "sz159915": ("159915.SZ", "创业板ETF"),
    "sh688041": ("688041.SH", "海光信息"),
    "sz300308": ("300308.SZ", "中际旭创"),
    "sz300274": ("300274.SZ", "阳光电源"),
    "sz002371": ("002371.SZ", "北方华创"),
    "sh688981": ("688981.SH", "中芯国际"),
    "sh600276": ("600276.SH", "恒瑞医药"),
    "sh603019": ("603019.SH", "中科曙光"),
    "sh600089": ("600089.SH", "特变电工"),
    "sh600875": ("600875.SH", "东方电气"),
    "sh601088": ("601088.SH", "中国神华"),
    "sh600219": ("600219.SH", "南山铝业"),
    "sh600019": ("600019.SH", "宝钢股份"),
    "sh518880": ("518880.SH", "华安黄金ETF"),
    "sz000792": ("000792.SZ", "盐湖股份"),
    "sh600900": ("600900.SH", "长江电力"),
    "sz000858": ("000858.SZ", "五粮液"),
    "sh601318": ("601318.SH", "中国平安"),
    "sh600036": ("600036.SH", "招商银行"),
}

LIMIT = 800  # ~3.3年日K, 覆盖 2023-09 以来的评估窗口
OUT = Path(__file__).with_name("klines_qfq.csv")


def main() -> int:
    if OUT.exists() and OUT.stat().st_size > 1000:
        print(f"[cache] {OUT} 已存在, 跳过拉取")
        return 0

    codes = ",".join(SYMBOLS)
    cmd = [NODE, WESTOCK, "kline", codes, "--period", "day",
           "--limit", str(LIMIT), "--fq", "qfq"]
    print(f"[fetch] {len(SYMBOLS)} symbols x {LIMIT} bars ...")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:], file=sys.stderr)
        return 1

    rows = []
    seen = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 8 or cells[0] in ("symbol", "---") or set(cells[0]) <= {"-"}:
            continue
        sym, d, o, h, low, c, v = cells[0], cells[1], cells[2], cells[4], cells[5], cells[3], cells[6]
        if sym not in SYMBOLS:
            continue
        try:
            o, h, low, c, v = float(o), float(h), float(low), float(c), float(v)
        except ValueError:
            continue
        rows.append((sym, d, o, h, low, c, v))
        seen.add(sym)

    missing = set(SYMBOLS) - seen
    if missing:
        print(f"[WARN] 以下标的未取到数据: {sorted(missing)}", file=sys.stderr)

    rows.sort(key=lambda r: (r[1], r[0]))
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    print(f"[done] {len(rows)} rows, {len(seen)}/{len(SYMBOLS)} symbols -> {OUT}")
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
