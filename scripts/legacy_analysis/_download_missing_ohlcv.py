# -*- coding: utf-8 -*-
"""
批量下载缺失标的 OHLCV 数据 (5年日K线)
========================================

下载 symbol_universe.py 中 105 个标的的 5 年日K线数据
保存格式: data_cache/historical_{code}_5y_base.parquet (与 V9 回测一致)

使用 baostock (免费, 无配额限制)
"""
from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("download_ohlcv.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("download")

BASE_DIR = Path(__file__).resolve().parent
OHLCV_DIR = BASE_DIR / "data_cache"
OHLCV_DIR.mkdir(parents=True, exist_ok=True)

# 5 年天数 (含节假日)
DOWNLOAD_DAYS = 1825


def load_symbol_universe() -> List[Tuple[str, str]]:
    """从 symbol_universe.py 加载所有标的 (code, code_with_exchange)"""
    su_file = BASE_DIR / "cache" / "symbol_universe.py"
    with open(su_file, "r", encoding="utf-8") as f:
        content = f.read()

    # 提取所有 "XXXXXX_SZ" 或 "XXXXXX_SH" 格式的代码
    matches = re.findall(r'"(\d{6}_[SZSH]+)"', content)
    unique = list(dict.fromkeys(matches))  # 去重保持顺序

    result = []
    for sym in unique:
        code = sym.split("_")[0]
        result.append((code, sym))
    return result


def get_missing_symbols() -> List[Tuple[str, str]]:
    """获取缺失 OHLCV 数据的标的"""
    all_symbols = load_symbol_universe()
    missing = []
    for code, sym_with_exchange in all_symbols:
        parquet_file = OHLCV_DIR / f"historical_{code}_5y_base.parquet"
        if not parquet_file.exists():
            missing.append((code, sym_with_exchange))
    return missing


def to_baostock_code(local_sym: str) -> str:
    """本地格式 (600519_SH) → baostock 格式 (sh.600519)"""
    parts = local_sym.split("_")
    code = parts[0]
    exchange = parts[1].lower() if len(parts) > 1 else ""
    if exchange == "sh":
        return f"sh.{code}"
    elif exchange == "sz":
        return f"sz.{code}"
    else:
        # 根据代码规则推断
        if code.startswith(("60", "68", "51", "58")):
            return f"sh.{code}"
        else:
            return f"sz.{code}"


def download_one(symbol_with_exchange: str, code: str) -> bool:
    """下载单个标的的 5 年 OHLCV 数据"""
    parquet_path = OHLCV_DIR / f"historical_{code}_5y_base.parquet"

    # 已存在则跳过
    if parquet_path.exists():
        try:
            df_existing = pd.read_parquet(parquet_path)
            if len(df_existing) >= 200:  # 至少 200 天
                return True
        except Exception:
            pass

    import baostock as bs

    bs_code = to_baostock_code(symbol_with_exchange)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=DOWNLOAD_DAYS)

    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,code,open,high,low,close,volume,amount",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag="2",  # 前复权
        )

        if rs.error_code != "0":
            logger.warning("  %s (%s) 失败: %s", symbol_with_exchange, bs_code, rs.error_msg)
            return False

        rows = []
        while (rs.error_code == "0") and rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            logger.warning("  %s (%s) 返回 0 行", symbol_with_exchange, bs_code)
            return False

        df = pd.DataFrame(rows, columns=rs.fields)
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df.dropna(subset=["close"])

        if len(df) == 0:
            logger.warning("  %s (%s) 清理后 0 行", symbol_with_exchange, bs_code)
            return False

        # 保存为 V9 回测兼容格式
        df.to_parquet(parquet_path)
        logger.info(
            "  ✓ %s (%s) | %d 天 | %s ~ %s",
            symbol_with_exchange, bs_code, len(df),
            df.index[0].date(), df.index[-1].date(),
        )
        return True

    except Exception as e:
        logger.error("  %s (%s) 异常: %s", symbol_with_exchange, bs_code, e)
        return False


def main() -> None:
    """主入口: 批量下载缺失 OHLCV"""
    logger.info("=" * 70)
    logger.info("批量下载缺失 OHLCV 数据")
    logger.info("=" * 70)

    missing = get_missing_symbols()
    logger.info("缺失标的: %d 个", len(missing))

    if not missing:
        logger.info("✓ 所有标的已有 OHLCV 数据, 无需下载")
        return

    # 打印缺失列表
    logger.info("缺失标的列表 (前20):")
    for code, sym in missing[:20]:
        logger.info("  %s (%s)", code, sym)
    if len(missing) > 20:
        logger.info("  ... 共 %d 个", len(missing))

    # 登录 baostock
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        logger.error("baostock 登录失败: %s", lg.error_msg)
        sys.exit(1)
    logger.info("baostock 登录成功")

    success = 0
    failed = []
    t0 = time.time()

    try:
        for i, (code, sym) in enumerate(missing, 1):
            ok = download_one(sym, code)
            if ok:
                success += 1
            else:
                failed.append(sym)

            # 进度报告
            if i % 10 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(missing) - i) / rate if rate > 0 else 0
                logger.info(
                    "--- 进度: %d/%d (%.0f%%) | 成功 %d | 失败 %d | 用时 %.0fs | ETA %.0fs ---",
                    i, len(missing), i / len(missing) * 100,
                    success, len(failed), elapsed, eta,
                )

            # 每 20 只 sleep 0.5s 避免被限流
            if i % 20 == 0:
                time.sleep(0.5)

    finally:
        bs.logout()

    elapsed = time.time() - t0
    logger.info("=" * 70)
    logger.info("下载完成 | 成功 %d | 失败 %d | 耗时 %.1f 分钟", success, len(failed), elapsed / 60)
    if failed:
        logger.warning("失败标的: %s", failed)
    logger.info("=" * 70)

    # 验证总覆盖率
    all_symbols = load_symbol_universe()
    total = len(all_symbols)
    covered = sum(1 for code, _ in all_symbols if (OHLCV_DIR / f"historical_{code}_5y_base.parquet").exists())
    logger.info("OHLCV 覆盖率: %d/%d = %.1f%%", covered, total, covered / total * 100)


if __name__ == "__main__":
    main()
