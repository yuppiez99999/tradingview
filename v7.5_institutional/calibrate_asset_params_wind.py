# -*- coding: utf-8 -*-
"""
使用 Wind MCP 拉取所有资产的历史 K 线数据, 校准 v3 优化中的资产参数。
================================================================================
资产清单 (按 v3 12 类资产归类):
    核心宽基ETF:    510300.SH (沪深300ETF), 510500.SH (中证500ETF),
                    512100.SH (中证1000ETF), 588000.SH (科创50ETF)
    科技成长个股:   688041.SH (海光信息), 300308.SZ (中际旭创)
    高端制造/基建:  002371.SZ (北方华创), 688981.SH (中芯国际), 300750.SZ (宁德时代)
    防御/红利:      600900.SH (长江电力), 600276.SH (恒瑞医药), 603259.SH (药明康德)
    商品/避险:      518880.SH (华安黄金ETF), 601088.SH (中国神华),
                    600019.SH (宝钢股份), 600219.SH (南山铝业), 000792.SZ (盐湖股份)
    半导体ETF(新):  512480.SH (半导体ETF)
    新能源ETF(新):  516160.SH (新能源ETF) 或 515030.SH (新能源车ETF)

输出: 真实历史年化收益 / 年化波动率 / 相关矩阵
"""
from __future__ import annotations

import os
import sys
import json
import subprocess
import time
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("data_calibrator")

WIND_SKILL_DIR = Path(r"C:\Users\Administrator\.agents\skills\wind-mcp-skill")
WIND_CLI = WIND_SKILL_DIR / "scripts" / "cli.mjs"

# 设置 API Key 环境变量 (仅当前进程及子进程可见, 不写入任何配置文件)
WIND_API_KEY = "ak_Tk4Y_UE-MfUof8DLLbKpHZZY-kh1q5KD"
os.environ["WIND_API_KEY"] = WIND_API_KEY

# 数据拉取参数
BEGIN_DATE = "20230705"   # 近 3 年历史
END_DATE = "20260705"

# 资产清单 (按 v3 12 类资产归类, 每类多标的取均值)
# server_type 必须是 Wind CLI 合法值: fund_data / stock_data (不是 fund / stock)
ASSET_MAP = {
    "核心宽基ETF": [
        ("510300.SH", "沪深300ETF", "fund_data"),
        ("510500.SH", "中证500ETF", "fund_data"),
        ("512100.SH", "中证1000ETF", "fund_data"),
        ("588000.SH", "科创50ETF", "fund_data"),
    ],
    "科技成长个股": [
        ("688041.SH", "海光信息", "stock_data"),
        ("300308.SZ", "中际旭创", "stock_data"),
    ],
    "高端制造/基建": [
        ("002371.SZ", "北方华创", "stock_data"),
        ("688981.SH", "中芯国际", "stock_data"),
        ("300750.SZ", "宁德时代", "stock_data"),
    ],
    "防御/红利": [
        ("600900.SH", "长江电力", "stock_data"),
        ("600276.SH", "恒瑞医药", "stock_data"),
        ("603259.SH", "药明康德", "stock_data"),
    ],
    "商品/避险": [
        ("518880.SH", "华安黄金ETF", "fund_data"),
        ("601088.SH", "中国神华", "stock_data"),
        ("600019.SH", "宝钢股份", "stock_data"),
        ("600219.SH", "南山铝业", "stock_data"),
        ("000792.SZ", "盐湖股份", "stock_data"),
    ],
    "半导体ETF": [
        ("512480.SH", "半导体ETF", "fund_data"),
    ],
    "新能源ETF": [
        ("516160.SH", "新能源ETF", "fund_data"),
    ],
}

# v3 12 类资产完整清单 (棉花期货/期权/现金缓冲无对应标的, 保持原估算)
V3_ASSET_NAMES = [
    "核心宽基ETF", "科技成长个股", "高端制造/基建", "防御/红利",
    "商品/避险", "现金缓冲_股票",
    "棉花期货", "棉花期权保护", "股票期权保护", "现金缓冲_对冲",
    "半导体ETF", "新能源ETF",
]


def call_wind_kline(windcode: str, server_type: str) -> dict:
    """调用 Wind MCP CLI 拉取日 K 线

    server_type: 'fund_data' 或 'stock_data' (Wind CLI 合法值)
    """
    tool_name = "get_fund_kline" if server_type == "fund_data" else "get_stock_kline"
    params = {
        "windcode": windcode,
        "begin_date": BEGIN_DATE,
        "end_date": END_DATE,
        "period": "10",  # 日K
    }
    params_json = json.dumps(params, ensure_ascii=False)

    cmd = ["node", "scripts/cli.mjs", "call", server_type, tool_name, params_json]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(WIND_SKILL_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            env=os.environ,  # 传入 WIND_API_KEY
        )
        output = result.stdout.strip()
        if not output:
            logger.warning(f"  [FAIL] {windcode}: returncode={result.returncode}, stdout 为空")
            if result.stderr:
                logger.warning(f"    stderr: {result.stderr[:300]}")
            return {}

        # Wind CLI 即使失败也把 error envelope 写到 stdout (不是 stderr)
        parsed = json.loads(output)

        if result.returncode != 0:
            # 解析 error envelope: {ok:false, error:{code, agent_action}}
            err = parsed.get("error") or {}
            err_code = err.get("code", "UNKNOWN")
            err_action = err.get("agent_action", "")
            logger.warning(f"  [FAIL] {windcode}: returncode={result.returncode}, "
                          f"error_code={err_code}")
            if err_action:
                # 只打印前 200 字避免刷屏
                logger.warning(f"    agent_action: {str(err_action)[:200]}")
            return {}

        return parsed
    except subprocess.TimeoutExpired:
        logger.warning(f"  [FAIL] {windcode}: 超时")
        return {}
    except json.JSONDecodeError as e:
        logger.warning(f"  [FAIL] {windcode}: JSON 解析失败 {e}")
        return {}


def parse_kline_data(resp: dict) -> pd.DataFrame:
    """解析 Wind 返回的 K 线数据, 转为 DataFrame[date, close]

    Wind MCP 实际返回结构:
    {
      "content": [{"type": "text", "text": "<嵌套JSON字符串>"}],
      "isError": false
    }
    其中 text 字段二次解析后:
    {
      "data": {
        "columns": [{"name": "TIME"}, {"name": "OPEN"}, {"name": "MATCH"}, ...],
        "rows": [["2023-07-05", "3.879", ...], ...],
        "excelTotalCount": 245
      },
      "error": null
    }
    MATCH 即收盘价
    """
    if not resp:
        return pd.DataFrame()

    # 检查 isError
    if resp.get("isError") is True:
        logger.warning(f"    Wind 返回 isError=True")
        return pd.DataFrame()

    # 获取 content[0].text
    content = resp.get("content")
    if not isinstance(content, list) or not content:
        return pd.DataFrame()

    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        return pd.DataFrame()

    text = first.get("text", "")
    if not text:
        return pd.DataFrame()

    # 二次解析 text 字段 (嵌套 JSON 字符串)
    try:
        inner = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"    text 字段 JSON 解析失败: {e}")
        return pd.DataFrame()

    # 提取 data.columns 和 data.rows
    data = inner.get("data") or {}
    columns = data.get("columns") or []
    rows = data.get("rows") or []

    if not columns or not rows:
        logger.warning(f"    columns 或 rows 为空")
        return pd.DataFrame()

    # 找到 TIME 和 MATCH (收盘价) 列的索引
    col_names = [c.get("name", "").upper() for c in columns]
    time_idx = None
    close_idx = None
    for i, name in enumerate(col_names):
        if name == "TIME" and time_idx is None:
            time_idx = i
        elif name in ("MATCH", "CLOSE", "CLOSE_PRICE", "收盘价") and close_idx is None:
            close_idx = i

    # 兜底: 如果没有 MATCH, 取 _DATE 作为日期, OPEN 之后的第二列作为收盘
    if time_idx is None:
        for i, name in enumerate(col_names):
            if name in ("_DATE", "DATE", "TRADE_DATE"):
                time_idx = i
                break
    if close_idx is None:
        # 取最后一列 (通常是 MATCH)
        close_idx = len(col_names) - 1

    if time_idx is None or close_idx is None:
        logger.warning(f"    无法定位日期/收盘价列, columns: {col_names}")
        return pd.DataFrame()

    # 构造 DataFrame
    records = []
    for row in rows:
        if len(row) <= max(time_idx, close_idx):
            continue
        date_val = row[time_idx]
        close_val = row[close_idx]
        records.append({"date": date_val, "close": close_val})

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)
    return df


def compute_metrics(prices: pd.Series) -> dict:
    """计算年化收益 / 波动率 / Sharpe / 最大回撤"""
    if len(prices) < 30:
        return {}
    returns = prices.pct_change().dropna()
    n_days = len(returns)
    ann_factor = 252

    ann_return = (prices.iloc[-1] / prices.iloc[0]) ** (ann_factor / n_days) - 1
    ann_vol = returns.std() * np.sqrt(ann_factor)
    sharpe = (ann_return - 0.02) / ann_vol if ann_vol > 0 else 0

    # 最大回撤
    cum = (1 + returns).cumprod()
    rolling_max = cum.expanding().max()
    dd = (cum - rolling_max) / rolling_max
    max_dd = dd.min()

    return {
        "ann_return": float(ann_return),
        "ann_vol": float(ann_vol),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_dd),
        "n_days": n_days,
        "start_date": str(prices.index[0].date()) if hasattr(prices.index[0], "date") else str(prices.index[0]),
        "end_date": str(prices.index[-1].date()) if hasattr(prices.index[-1], "date") else str(prices.index[-1]),
    }


def main():
    logger.info("=" * 60)
    logger.info("Wind MCP 真实历史数据校准 v3 资产参数")
    logger.info("=" * 60)
    logger.info(f"时间范围: {BEGIN_DATE} ~ {END_DATE}")
    logger.info(f"资产类别数: {len(ASSET_MAP)}")
    logger.info("")

    # 1. 拉取所有标的的 K 线
    all_klines = {}  # windcode -> DataFrame
    for asset_class, instruments in ASSET_MAP.items():
        logger.info(f"--- 拉取 [{asset_class}] ({len(instruments)} 个标的) ---")
        for windcode, name, server_type in instruments:
            logger.info(f"  → {windcode} {name}")
            resp = call_wind_kline(windcode, server_type)
            df = parse_kline_data(resp)
            if df.empty:
                logger.warning(f"    无数据, 跳过")
                continue
            df.set_index("date", inplace=True)
            all_klines[windcode] = {
                "name": name,
                "asset_class": asset_class,
                "df": df,
            }
            logger.info(f"    ✓ {len(df)} 条数据, 价格 {df['close'].iloc[0]:.4f} → {df['close'].iloc[-1]:.4f}")
            time.sleep(0.5)  # 避免限流

    logger.info(f"\n共拉取 {len(all_klines)} 个标的的数据")

    if not all_klines:
        logger.error("未拉取到任何数据, 退出")
        return

    # 2. 计算每个标的的指标
    instrument_metrics = {}
    for windcode, info in all_klines.items():
        m = compute_metrics(info["df"]["close"])
        if m:
            m["name"] = info["name"]
            m["asset_class"] = info["asset_class"]
            m["windcode"] = windcode
            instrument_metrics[windcode] = m

    # 3. 按资产类别聚合 (取等权平均作为类别代表)
    asset_class_metrics = {}
    for asset_class, instruments in ASSET_MAP.items():
        class_metrics = [instrument_metrics[wc] for wc, _, _ in instruments
                         if wc in instrument_metrics]
        if not class_metrics:
            continue
        avg_return = np.mean([m["ann_return"] for m in class_metrics])
        avg_vol = np.mean([m["ann_vol"] for m in class_metrics])
        avg_sharpe = np.mean([m["sharpe"] for m in class_metrics])
        avg_dd = np.mean([m["max_drawdown"] for m in class_metrics])
        asset_class_metrics[asset_class] = {
            "ann_return": float(avg_return),
            "ann_vol": float(avg_vol),
            "sharpe": float(avg_sharpe),
            "max_drawdown": float(avg_dd),
            "n_instruments": len(class_metrics),
            "instruments": [m["windcode"] + " " + m["name"] for m in class_metrics],
        }

    # 4. 构建资产类别相关矩阵 (基于等权组合的日收益序列)
    # 计算每个资产类别的等权日收益序列
    class_returns = {}
    for asset_class, instruments in ASSET_MAP.items():
        codes = [wc for wc, _, _ in instruments if wc in all_klines]
        if not codes:
            continue
        # 对齐日期
        dfs = [all_klines[wc]["df"]["close"] for wc in codes]
        combined = pd.concat(dfs, axis=1)
        combined.columns = codes
        combined = combined.dropna()
        if combined.empty:
            continue
        # 等权组合日收益
        daily_returns = combined.pct_change().mean(axis=1).dropna()
        class_returns[asset_class] = daily_returns

    # 5. 计算 12×12 相关矩阵 (填充缺失的类别保持原估算)
    # 完整 12 类资产名称
    full_asset_names = V3_ASSET_NAMES
    n_assets = len(full_asset_names)
    corr_matrix = np.eye(n_assets)

    # 实际有数据的资产类别
    available_classes = list(class_returns.keys())
    available_indices = {name: i for i, name in enumerate(full_asset_names)
                        if name in available_classes}

    # 计算可用类别的相关矩阵
    if len(available_classes) >= 2:
        returns_df = pd.DataFrame({name: class_returns[name] for name in available_classes})
        returns_df = returns_df.dropna()
        if len(returns_df) > 30:
            real_corr = returns_df.corr()
            for i, name1 in enumerate(available_classes):
                for j, name2 in enumerate(available_classes):
                    if name1 in available_indices and name2 in available_indices:
                        idx1 = available_indices[name1]
                        idx2 = available_indices[name2]
                        corr_matrix[idx1, idx2] = real_corr.loc[name1, name2]

    # 6. 输出结果
    logger.info("\n" + "=" * 60)
    logger.info("校准结果汇总")
    logger.info("=" * 60)
    logger.info("\n[资产类别指标 (真实历史数据)]")
    for name, m in asset_class_metrics.items():
        logger.info(f"  {name:18s}: 年化={m['ann_return']:+.2%}, "
                    f"波动={m['ann_vol']:.2%}, Sharpe={m['sharpe']:.3f}, "
                    f"回撤={m['max_drawdown']:.2%}, 标的数={m['n_instruments']}")

    logger.info("\n[单标的明细]")
    for wc, m in instrument_metrics.items():
        logger.info(f"  {wc} {m['name']:12s} [{m['asset_class']}]: "
                    f"年化={m['ann_return']:+.2%}, 波动={m['ann_vol']:.2%}, "
                    f"Sharpe={m['sharpe']:.3f}, 回撤={m['max_drawdown']:.2%}")

    # 7. 生成 v3 优化用的真实参数 (12 类资产)
    # 缺失的资产类别保持原 v3 估算
    v3_estimated_returns = {
        "核心宽基ETF": 0.07, "科技成长个股": 0.12, "高端制造/基建": 0.10,
        "防御/红利": 0.06, "商品/避险": 0.05, "现金缓冲_股票": 0.02,
        "棉花期货": 0.15, "棉花期权保护": -0.05, "股票期权保护": -0.03,
        "现金缓冲_对冲": 0.02, "半导体ETF": 0.15, "新能源ETF": 0.14,
    }
    v3_estimated_vols = {
        "核心宽基ETF": 0.16, "科技成长个股": 0.25, "高端制造/基建": 0.22,
        "防御/红利": 0.12, "商品/避险": 0.15, "现金缓冲_股票": 0.005,
        "棉花期货": 0.30, "棉花期权保护": 0.05, "股票期权保护": 0.05,
        "现金缓冲_对冲": 0.005, "半导体ETF": 0.30, "新能源ETF": 0.28,
    }

    calibrated_returns = []
    calibrated_vols = []
    for name in full_asset_names:
        if name in asset_class_metrics:
            calibrated_returns.append(asset_class_metrics[name]["ann_return"])
            calibrated_vols.append(asset_class_metrics[name]["ann_vol"])
        else:
            calibrated_returns.append(v3_estimated_returns[name])
            calibrated_vols.append(v3_estimated_vols[name])
            logger.info(f"  [保持估算] {name}: 年化={v3_estimated_returns[name]:+.2%}, "
                        f"波动={v3_estimated_vols[name]:.2%}")

    # 8. 保存结果
    archive_dir = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\每日报告归档\2026\07\06")
    archive_dir.mkdir(parents=True, exist_ok=True)

    output = {
        "calibration_time": datetime.now().isoformat(),
        "data_source": "Wind MCP",
        "begin_date": BEGIN_DATE,
        "end_date": END_DATE,
        "calibrated_asset_class_metrics": asset_class_metrics,
        "instrument_metrics": instrument_metrics,
        "v3_full_asset_names": full_asset_names,
        "calibrated_ann_returns": calibrated_returns,
        "calibrated_ann_vols": calibrated_vols,
        "calibrated_corr_matrix": corr_matrix.tolist(),
        "estimated_returns_fallback": v3_estimated_returns,
        "estimated_vols_fallback": v3_estimated_vols,
    }

    json_path = archive_dir / "asset_calibration_wind_20260706.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"\n校准结果 JSON: {json_path}")

    # 9. 生成 Markdown 报告
    md_path = archive_dir / "asset_calibration_wind_20260706.md"
    lines = [
        "# v3 资产参数校准报告 (Wind MCP 真实历史数据)",
        "",
        f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"**数据源**: Wind MCP",
        f"**时间范围**: {BEGIN_DATE} ~ {END_DATE}",
        f"**标的数**: {len(instrument_metrics)}",
        "",
        "## 1. 资产类别校准结果 (按 v3 12 类资产)",
        "",
        "| 资产类别 | 真实年化 | 估算年化 | 差异 | 真实波动 | 估算波动 | 差异 | 标的数 |",
        "|---------|---------|---------|------|---------|---------|------|--------|",
    ]
    for name in full_asset_names:
        if name in asset_class_metrics:
            real_r = asset_class_metrics[name]["ann_return"]
            real_v = asset_class_metrics[name]["ann_vol"]
            est_r = v3_estimated_returns[name]
            est_v = v3_estimated_vols[name]
            n = asset_class_metrics[name]["n_instruments"]
            lines.append(
                f"| {name} | {real_r:+.2%} | {est_r:+.2%} | "
                f"{real_r - est_r:+.2%} | {real_v:.2%} | {est_v:.2%} | "
                f"{real_v - est_v:+.2%} | {n} |"
            )
        else:
            est_r = v3_estimated_returns[name]
            est_v = v3_estimated_vols[name]
            lines.append(
                f"| {name} (估算) | {est_r:+.2%} | {est_r:+.2%} | - | "
                f"{est_v:.2%} | {est_v:.2%} | - | 0 |"
            )

    lines.extend([
        "",
        "## 2. 单标的明细",
        "",
        "| 代码 | 名称 | 资产类别 | 年化收益 | 波动率 | Sharpe | 最大回撤 |",
        "|------|------|---------|---------|--------|--------|---------|",
    ])
    for wc, m in instrument_metrics.items():
        lines.append(
            f"| {wc} | {m['name']} | {m['asset_class']} | "
            f"{m['ann_return']:+.2%} | {m['ann_vol']:.2%} | "
            f"{m['sharpe']:.3f} | {m['max_drawdown']:.2%} |"
        )

    lines.extend([
        "",
        "## 3. 校准后的 12×12 相关矩阵",
        "",
        "```",
        f"{np.array2string(corr_matrix, precision=3, suppress_small=True)}",
        "```",
        "",
        "## 4. 校准建议",
        "",
        "- 用真实历史数据替换 v3 优化中的估算资产参数",
        "- 重新运行 optimize_portfolio_v3.py 进行优化",
        "- 注意: 棉花期货/期权/现金缓冲无对应标的, 保持原估算",
        "- 部分新股 (如中芯国际 688981) 上市时间较短, 数据可能不足 3 年",
        "",
    ])

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"校准报告 MD: {md_path}")

    return output


if __name__ == "__main__":
    main()
