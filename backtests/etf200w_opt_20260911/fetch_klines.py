"""从 westock CLI 拉取 200万ETF组合 13 只 ETF + 沪深300 指数的前复权日K。

输出: klines_qfq.csv (symbol,date,open,high,low,close,volume)
运行: py fetch_klines.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
START = "2023-08-01"   # 早于评估窗口起点, 用于取首日 prev_close
END = "2026-09-11"

# code -> 名称
CODES: dict[str, str] = {
    "sh510300": "沪深300ETF",
    "sh510500": "中证500ETF",
    "sh513100": "纳指ETF",
    "sh512890": "红利低波ETF",
    "sh588000": "科创50ETF",
    "sh512760": "半导体芯片ETF",
    "sh515070": "人工智能ETF",
    "sh516160": "新能源ETF",
    "sh515790": "光伏ETF",
    "sh513180": "恒生科技ETF",
    "sz159920": "恒生ETF",
    "sh511010": "国债ETF",
    "sh511880": "银华日利",
    "sh000300": "沪深300指数",
}

WESTOCK = "westock.exe"


def run_cli(args: list[str]) -> str:
    # 2026-09-12 (bandit B602): 去 shell=True —— 参数已全部以列表形式传入, 无 shell
    # 解析需求; 保留 shell 等于把 code/日期等参数再交给 cmd.exe 解释一遍
    # (空格/引号/& 等元字符可越权), 纯风险无收益。
    proc = subprocess.run(
        [WESTOCK, *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"westock 调用失败 {args}: {proc.stderr[:500]}")
    return proc.stdout


ROW = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|(.*)\|\s*$")


def parse_table(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        rows.append([m.group(1), *cells])
    return rows


def fetch_one(code: str) -> pd.DataFrame:
    out = run_cli([
        "kline", code, "--period", "day",
        "--start", START, "--end", END, "--fq", "qfq",
        "--limit", "2000",
    ])
    rows = parse_table(out)
    if not rows:
        raise SystemExit(f"{code} 无数据返回:\n{out[:500]}")
    # 表头: date | open | last | high | low | volume | amount | exchange | change_pct
    df = pd.DataFrame(rows, columns=[
        "date", "open", "close", "high", "low",
        "volume", "amount", "exchange", "change_pct",
    ])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"])
    df["symbol"] = code
    df = df[["symbol", "date", "open", "high", "low", "close", "volume"]]
    return df.sort_values("date").reset_index(drop=True)


def main() -> None:
    frames = []
    for code, name in CODES.items():
        df = fetch_one(code)
        frames.append(df)
        print(f"{code} {name}: {len(df)} 行  {df.date.min()} ~ {df.date.max()}")
    all_df = pd.concat(frames, ignore_index=True)
    out = HERE / "klines_qfq.csv"
    all_df.to_csv(out, index=False, encoding="utf-8")
    print(f"\n写入 {out}  共 {len(all_df)} 行")
    print("缺失标的:", set(CODES) - set(all_df.symbol.unique()) or "无")


if __name__ == "__main__":
    sys.exit(main())
