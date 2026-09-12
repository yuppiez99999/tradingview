"""
v7.5 收益预测动态校准模块
==========================

每个交易日 07:00 由 daily_workflow.py 自动调用，三步串联：

    Step 1: update_returns_history()
        - 调用 Wind MCP 拉取最新日K（25 标的）
        - 更新 config/returns_history.json（持仓标的收益矩阵）
        - 更新 config/market_returns.json（沪深300ETF 基准）

    Step 2: calc_realized_returns()
        - 基于最新历史数据计算已实现年化收益率
        - 输出每标的累计/年化/波动/夏普
        - 计算持仓组合加权年化

    Step 3: update_projection()
        - 校准 portfolio_return_projection.json 概率权重
        - 重算 expected_annualized / expected_final_amount
        - 写入校准日志与历史快照

用法:
    # 单独运行（开发/调试）
    python calibrate_returns_projection.py
    python calibrate_returns_projection.py --dry-run
    python calibrate_returns_projection.py --skip-fetch   # 跳过 Wind 拉取，仅用现有数据校准

输出文件:
    config/returns_history.json           # 更新
    config/market_returns.json             # 更新
    portfolio_return_projection.json       # 更新 (expected_annualized + calibrated_weights)
    logs/calibration_history.jsonl        # 历史校准日志
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from utils.datetime_utils import now_bj

# ============================================================
# 路径与配置
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CONFIG_DIR = PROJECT_ROOT / "config"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Wind MCP CLI
WIND_SKILL_DIR = Path.home() / ".agents" / "skills" / "wind-mcp-skill"
if not WIND_SKILL_DIR.exists():
    _project_skill = PROJECT_ROOT / "skills" / "wind-mcp-skill"
    if _project_skill.exists():
        WIND_SKILL_DIR = _project_skill
WIND_CLI = WIND_SKILL_DIR / "scripts" / "cli.mjs"

# API Key (环境变量优先, 兜底硬编码仅本机使用)
WIND_API_KEY = os.environ.get("WIND_API_KEY", "ak_Tk4Y_UE-MfUof8DLLbKpHZZY-kh1q5KD")
os.environ["WIND_API_KEY"] = WIND_API_KEY

# 持仓权重 (来自 portfolio_return_projection.json asset_detail.weight)
POSITION_WEIGHTS = {
    "588000": 0.1375,
    "512480": 0.1317,
    "516160": 0.1317,
    "515030": 0.126,
    "159915": 0.115,
    "159992": 0.0947,
    "512400": 0.0929,
    "512010": 0.0664,
    "601088": 0.0638,
    "518880": 0.0307,
    "511260": 0.005,
    "511520": 0.0037,
    "511360": 0.001,
}

# 标的清单: (代码, 后缀, server_type)
# server_type 必须是 Wind CLI 合法值: fund_data / stock_data
TARGET_SYMBOLS = [
    # 持仓标的
    ("588000", ".SH", "fund_data"),  # 科创50ETF
    ("512480", ".SH", "fund_data"),  # 半导体ETF
    ("516160", ".SH", "fund_data"),  # 高端装备ETF
    ("515030", ".SH", "fund_data"),  # 新能源车ETF
    ("159915", ".SZ", "fund_data"),  # 创业板ETF
    ("159992", ".SZ", "fund_data"),  # 创新药ETF
    ("512400", ".SH", "fund_data"),  # 有色金属ETF
    ("512010", ".SH", "fund_data"),  # 医药ETF
    ("601088", ".SH", "stock_data"),  # 中国神华
    ("518880", ".SH", "fund_data"),  # 黄金ETF
    ("511260", ".SH", "fund_data"),  # 十年国债ETF
    ("511520", ".SH", "fund_data"),  # 政金债ETF
    ("511360", ".SH", "fund_data"),  # 短融ETF
    # 基准标的（不持仓, 仅作市场参照）
    ("510300", ".SH", "fund_data"),  # 沪深300ETF（基准）
    # 扩展观测标的（用于补齐 returns_history.json 25标的)
    ("510500", ".SH", "fund_data"),
    ("512100", ".SH", "fund_data"),
    ("688041", ".SH", "stock_data"),
    ("300308", ".SZ", "stock_data"),
    ("300274", ".SZ", "stock_data"),
    ("002371", ".SZ", "stock_data"),
    ("688017", ".SH", "stock_data"),
    ("600276", ".SH", "stock_data"),
    ("600089", ".SH", "stock_data"),
    ("600875", ".SH", "stock_data"),
    ("000425", ".SZ", "stock_data"),
    ("600406", ".SH", "stock_data"),
    ("600989", ".SH", "stock_data"),
    ("600036", ".SH", "stock_data"),
    ("600900", ".SH", "stock_data"),
    ("688981", ".SH", "stock_data"),
    ("603019", ".SH", "stock_data"),
    ("600219", ".SH", "stock_data"),
    ("600019", ".SH", "stock_data"),
]

# 校准日志路径
CALIBRATION_LOG = LOG_DIR / "calibration_history.jsonl"
# 候选标的评估报告路径
CANDIDATE_REPORT = LOG_DIR / "candidate_pool_evaluation.json"

# 日志
logger = logging.getLogger("v75.calibrate_returns")


# ============================================================
# Step 1: 更新历史数据 (Wind MCP)
# ============================================================
def call_wind_kline(
    windcode: str, server_type: str, begin_date: str, end_date: str
) -> dict | None:
    """调用 Wind MCP CLI 拉取日 K 线

    Args:
        windcode: 6位代码+后缀, 如 "510300.SH"
        server_type: "fund_data" 或 "stock_data"
        begin_date: "YYYYMMDD"
        end_date: "YYYYMMDD"
    """
    tool_name = "get_fund_kline" if server_type == "fund_data" else "get_stock_kline"
    # G9 修复: fund_data.get_fund_kline 合约 (tool-contracts.md) 仅接受
    # {windcode, begin_date, end_date}, 不含 period; 多传 period 会触发
    # Wind MCP 服务端 PARAM_VALIDATION_ERROR。stock_data.get_stock_kline
    # 合约才支持 period/count/aftime 可选扩展字段。
    params = {
        "windcode": windcode,
        "begin_date": begin_date,
        "end_date": end_date,
    }
    if server_type == "stock_data":
        params["period"] = "10"  # 日K

    def _run_once(p: dict) -> tuple[dict | None, str]:
        pj = json.dumps(p, ensure_ascii=False)
        cmd = ["node", "scripts/cli.mjs", "call", server_type, tool_name, pj]
        try:
            r = subprocess.run(
                cmd,
                cwd=str(WIND_SKILL_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
                env=os.environ,
            )
        except Exception as e:  # pragma: no cover  # fail-safe
            return None, f"exception:{e}"
        out = r.stdout.strip()
        if not out:
            return None, "empty_stdout"
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError as e:
            return None, f"json_decode:{e}"
        if r.returncode != 0:
            err = parsed.get("error") or {}
            return None, err.get("code", "UNKNOWN")
        return parsed, "OK"

    resp, code = _run_once(params)
    if resp is None and code == "PARAM_VALIDATION_ERROR" and "period" in params:
        # 防御: 服务端拒绝扩展字段时, 去掉 period 重试一次 (日K为默认周期)
        logger.warning(f"  [RETRY] {windcode}: 去掉 period 重试 (原 code={code})")
        retry = {k: v for k, v in params.items() if k != "period"}
        resp, code = _run_once(retry)
    if resp is None:
        logger.warning(f"  [FAIL] {windcode}: code={code}")
        return None
    return resp


def parse_kline_to_returns(resp: dict) -> tuple[list[str], list[float]]:
    """解析 Wind 返回的 K 线, 转为 (日期列表, 日收益率列表)

    返回:
        dates: ISO 格式日期列表
        returns: 日收益率列表 (与 dates 等长, 第一日为 0.0)
    """
    if not resp or resp.get("isError") is True:
        return [], []

    content = resp.get("content")
    if not isinstance(content, list) or not content:
        return [], []

    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        return [], []

    text = first.get("text", "")
    if not text:
        return [], []

    try:
        inner = json.loads(text)
    except json.JSONDecodeError:
        return [], []

    data = inner.get("data") or {}
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    if not columns or not rows:
        return [], []

    # 找 TIME 与 MATCH/CLOSE 列
    col_names = [c.get("name", "").upper() for c in columns]
    time_idx = None
    close_idx = None
    for i, name in enumerate(col_names):
        if name == "TIME" and time_idx is None:
            time_idx = i
        elif name in ("MATCH", "CLOSE", "CLOSE_PRICE") and close_idx is None:
            close_idx = i

    if time_idx is None or close_idx is None:
        return [], []

    # 解析每行, 转换 close 为 float
    dates = []
    closes = []
    for row in rows:
        if len(row) <= max(time_idx, close_idx):
            continue
        try:
            d = str(row[time_idx])[:10]  # "2023-07-05"
            c = float(row[close_idx])
            dates.append(d)
            closes.append(c)
        except (ValueError, TypeError):
            continue

    if len(closes) < 2:
        return dates, [0.0] * len(closes)

    # 计算日收益率: r_t = (c_t / c_{t-1}) - 1
    returns = [0.0]
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev > 0:
            returns.append(closes[i] / prev - 1.0)
        else:
            returns.append(0.0)

    # 跳过第一日（无前值）
    return dates[1:], returns[1:]


def update_returns_history(
    begin_date: str, end_date: str, benchmark_code: str = "510300"
) -> dict[str, Any]:
    """更新 config/returns_history.json + market_returns.json

    Args:
        begin_date: "YYYYMMDD"
        end_date: "YYYYMMDD"
        benchmark_code: 基准代码 (沪深300ETF)

    Returns:
        统计信息字典
    """
    logger.info("=" * 60)
    logger.info(f"Step 1: 更新历史数据 (Wind MCP, {begin_date} → {end_date})")
    logger.info("=" * 60)

    all_returns: dict[str, tuple[list[str], list[float]]] = {}
    success_count = 0
    fail_count = 0

    for code, suffix, server_type in TARGET_SYMBOLS:
        windcode = f"{code}{suffix}"
        resp = call_wind_kline(windcode, server_type, begin_date, end_date)
        if resp is None:
            fail_count += 1
            continue
        dates, rets = parse_kline_to_returns(resp)
        if not dates:
            logger.warning(f"  [EMPTY] {windcode}: 无有效数据")
            fail_count += 1
            continue
        all_returns[code] = (dates, rets)
        success_count += 1
        logger.info(f"  [OK] {windcode}: {len(dates)} 个交易日")

    if not all_returns:
        logger.error("Step 1 失败: 所有标的拉取失败")
        return {"status": "FAIL", "success": 0, "fail": fail_count}

    # 对齐所有标的的日期索引（取并集）
    all_dates_set = set()
    for dates, _ in all_returns.values():
        all_dates_set.update(dates)
    all_dates = sorted(list(all_dates_set))
    logger.info(f"对齐日期索引: {len(all_dates)} 个交易日")

    # 构建列: 每个 code 一列, 缺失填 NaN
    columns = list(all_returns.keys())
    data_matrix = []
    for d in all_dates:
        row = []
        for code in columns:
            dates, rets = all_returns[code]
            if d in dates:
                idx = dates.index(d)
                row.append(rets[idx])
            else:
                row.append(float("nan"))
        data_matrix.append(row)

    # 写入 returns_history.json
    rh_path = CONFIG_DIR / "returns_history.json"
    rh_data = {
        "columns": columns,
        "index": [f"{d}T16:00:00.000Z" for d in all_dates],
        "data": data_matrix,
    }
    # 备份原文件
    if rh_path.exists():
        bak = rh_path.with_suffix(f".json.bak_{now_bj():%Y%m%d_%H%M%S}")
        rh_path.rename(bak)
        logger.info(f"备份原 returns_history.json → {bak.name}")
    with open(rh_path, "w", encoding="utf-8") as f:
        json.dump(rh_data, f, ensure_ascii=False)
    logger.info(f"已写入 {rh_path} ({len(columns)} 标的, {len(all_dates)} 日)")

    # 写入 market_returns.json (基准)
    mr_path = CONFIG_DIR / "market_returns.json"
    if benchmark_code in all_returns:
        bm_dates, bm_rets = all_returns[benchmark_code]
        # 对齐到 all_dates
        bm_aligned = []
        for d in all_dates:
            if d in bm_dates:
                idx = bm_dates.index(d)
                bm_aligned.append(bm_rets[idx])
            else:
                bm_aligned.append(float("nan"))
        mr_data = {
            "name": "close",
            "index": [f"{d}T16:00:00.000Z" for d in all_dates],
            "data": bm_aligned,
        }
        if mr_path.exists():
            bak = mr_path.with_suffix(f".json.bak_{now_bj():%Y%m%d_%H%M%S}")
            mr_path.rename(bak)
            logger.info(f"备份原 market_returns.json → {bak.name}")
        with open(mr_path, "w", encoding="utf-8") as f:
            json.dump(mr_data, f, ensure_ascii=False)
        logger.info(f"已写入 {mr_path} (基准 {benchmark_code})")
    else:
        logger.warning(f"基准 {benchmark_code} 拉取失败, market_returns.json 未更新")

    stats = {
        "status": "OK",
        "success": success_count,
        "fail": fail_count,
        "total_days": len(all_dates),
        "total_symbols": len(columns),
    }
    logger.info(f"Step 1 完成: {success_count} 成功, {fail_count} 失败")
    return stats


# ============================================================
# Step 2: 计算已实现年化收益率
# ============================================================
def calc_realized_returns() -> dict[str, Any]:
    """基于 returns_history.json + market_returns.json 计算真实年化"""
    logger.info("=" * 60)
    logger.info("Step 2: 计算已实现年化收益率")
    logger.info("=" * 60)

    rh_path = CONFIG_DIR / "returns_history.json"
    mr_path = CONFIG_DIR / "market_returns.json"

    if not rh_path.exists():
        return {"status": "FAIL", "error": "returns_history.json 不存在"}

    with open(rh_path, encoding="utf-8") as f:
        rh = json.load(f)
    with open(mr_path, encoding="utf-8") as f:
        mr = json.load(f)

    codes = rh["columns"]
    dates = rh["index"]
    data = np.array(rh["data"])

    # 强制按日期字符串排序，确保 start_date <= end_date
    # 兼容带 T... 时区后缀与不带后缀的混合格式
    sorted_dates = sorted(dates, key=lambda d: str(d)[:10])
    if dates != sorted_dates:
        logger.warning("检测到 returns_history.json 日期顺序异常，已自动纠正为升序")
        # 同步重排 data 矩阵
        date_to_row = {d: i for i, d in enumerate(dates)}
        try:
            reordered = [data[date_to_row[d]] for d in sorted_dates]
            data = np.array(reordered)
        except Exception as _exc:  # fail-safe
            logger.warning("重排 data 矩阵失败，将使用原始顺序: %s", _exc)
        dates = sorted_dates

    start_date = dates[0][:10]
    end_date = dates[-1][:10]
    days = (
        datetime.strptime(end_date, "%Y-%m-%d")
        - datetime.strptime(start_date, "%Y-%m-%d")
    ).days
    if days <= 0:
        logger.error(
            "日期区间异常: start_date=%s, end_date=%s, days=%s",
            start_date,
            end_date,
            days,
        )
        return {
            "status": "FAIL",
            "error": f"日期区间异常: {start_date} → {end_date} (days={days})",
        }
    years = max(days / 365.25, 0.01)

    logger.info(f"区间: {start_date} → {end_date} ({days} 天 ≈ {years:.3f} 年)")

    # 每标的分析
    per_asset = []
    MIN_VALID_POINTS = 5
    MAX_ANNUALIZED = (
        2.0  # 年化上限 +200% (原 5000% 过于宽松, 300308 等短期暴涨股会失真)
    )
    MIN_ANNUALIZED = -0.99  # 年化下限 -99%
    BAYESIAN_PRIOR = 0.15  # 贝叶斯收缩先验: 15% 年化 (A股长期权益收益率中枢)
    for i, code in enumerate(codes):
        returns = data[:, i]
        valid = returns[~np.isnan(returns)]
        if len(valid) < MIN_VALID_POINTS:
            logger.warning(
                f"  [SKIP] {code}: 有效数据点不足 ({len(valid)} < {MIN_VALID_POINTS})"
            )
            continue

        cum = float(np.prod(1 + valid) - 1)

        # 异常值保护：累计收益率接近 -1 时拒绝开方
        if (1 + cum) <= 1e-6:
            logger.warning(
                f"  [SKIP] {code}: 累计收益率异常 (cum={cum:.6f})，可能数据缺失/倒挂"
            )
            continue

        annualized = float((1 + cum) ** (1 / years) - 1)

        # 短周期贝叶斯收缩: 样本期 < 2 年时, 极端年化向 15% 均值回归
        # (300308 等短期暴涨股 1 年内 10 倍会导致年化 > 1000%, 不可持续)
        if years < 2.0 and abs(annualized) > 0.5:
            shrink_weight = max(0.0, min(0.7, 1.0 - years / 2.0))
            original_ann = annualized
            annualized = (
                annualized * (1 - shrink_weight) + BAYESIAN_PRIOR * shrink_weight
            )
            logger.info(
                f"  [{code}] 短周期贝叶斯收缩: {original_ann*100:+.1f}% → "
                f"{annualized*100:+.1f}% (收缩强度 {shrink_weight*100:.0f}%)"
            )

        if not (MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED):
            logger.warning(
                f"  [SKIP] {code}: 年化收益率异常 ({annualized*100:+.2f}%)，超出阈值 [{MIN_ANNUALIZED*100:.0f}%, {MAX_ANNUALIZED*100:.0f}%]"  # noqa: E501
            )
            continue

        daily_vol = float(np.std(valid))
        annual_vol = daily_vol * float(np.sqrt(252))
        sharpe = annualized / annual_vol if annual_vol > 1e-6 else 0.0
        weight = POSITION_WEIGHTS.get(code, 0.0)

        per_asset.append(
            {
                "code": code,
                "cum_return": cum,
                "annualized_return": annualized,
                "annual_volatility": annual_vol,
                "sharpe": sharpe,
                "weight": weight,
            }
        )
        logger.info(
            f"  {code}: 年化 {annualized*100:+.2f}%, "
            f"波动 {annual_vol*100:.2f}%, 夏普 {sharpe:.2f}, 权重 {weight*100:.2f}%"
        )

    # 持仓组合加权年化
    total_weight = sum(r["weight"] for r in per_asset)
    if total_weight > 0:
        weighted_annualized = (
            sum(r["annualized_return"] * r["weight"] for r in per_asset) / total_weight
        )
        weighted_cum = (
            sum(r["cum_return"] * r["weight"] for r in per_asset) / total_weight
        )
    else:
        weighted_annualized = 0.0
        weighted_cum = 0.0

    # 基准
    market_data = np.array([x for x in mr["data"] if x is not None and not np.isnan(x)])
    market_cum = float(np.prod(1 + market_data) - 1) if len(market_data) > 0 else 0.0
    market_annualized = (
        float((1 + market_cum) ** (1 / years) - 1) if (1 + market_cum) > 0 else -0.99
    )
    market_vol = (
        float(np.std(market_data) * np.sqrt(252)) if len(market_data) > 0 else 0.0
    )
    market_sharpe = market_annualized / market_vol if market_vol > 0 else 0.0

    result = {
        "status": "OK",
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "years": years,
        "per_asset": per_asset,
        "portfolio_weight_total": total_weight,
        "portfolio_weighted_annualized": weighted_annualized,
        "portfolio_weighted_cumulative": weighted_cum,
        "market_cumulative": market_cum,
        "market_annualized": market_annualized,
        "market_volatility": market_vol,
        "market_sharpe": market_sharpe,
    }

    logger.info(
        f"持仓组合加权年化: {weighted_annualized*100:+.2f}% "
        f"(覆盖权重 {total_weight*100:.2f}%)"
    )
    logger.info(
        f"基准 {mr.get('name','510300')} 年化: {market_annualized*100:+.2f}%, "
        f"夏普 {market_sharpe:.2f}"
    )
    return result


# ============================================================
# Step 2.5: 候选标的池评估 (AI 决策整合)
# ============================================================
def evaluate_candidate_pool() -> dict[str, Any]:
    """Step 2.5: 评估候选标的池, 输出建议报告

    基于 macro_policy_scoring.CANDIDATE_POOL 对当前持仓进行补位评估
    输出: candidate_pool_evaluation.json
    """
    logger.info("=" * 60)
    logger.info("Step 2.5: 候选标的池评估 (十五五+康波缺口分析)")
    logger.info("=" * 60)

    # 读取当前持仓
    positions_path = PROJECT_ROOT / "config" / "positions.json"
    if not positions_path.exists():
        logger.warning("config/positions.json 不存在, 跳过候选评估")
        return {"status": "SKIP", "reason": "positions.json not found"}

    with open(positions_path, encoding="utf-8") as f:
        pos_data = json.load(f)
    # macro_policy_scoring.evaluate_candidate_pool 期望 list[str] (如 ['sz588000', 'sh688041'])
    # positions.json 的 positions 是 dict, key 格式 "588080.SH" 需转为 "sh588080" 以匹配候选池 code
    positions_raw = pos_data.get("positions", {})
    current_positions: list[str] = []
    if isinstance(positions_raw, dict):
        for code_key in positions_raw.keys():
            num, _, market = str(code_key).partition(".")
            mk = market.lower()
            current_positions.append(
                f"{mk}{num}" if mk in ("sh", "sz", "bj") else str(code_key).lower()
            )
    elif isinstance(positions_raw, list):
        current_positions = [str(c) for c in positions_raw]

    # 调用 macro_policy_scoring 评估
    # 修复 (2026-08-04): macro_policy_scoring 实际位于 ms_strategy/src/macro/,
    # 非 v8.3_institutional/src/macro/ (旧路径 BASE_DIR/"src"/"macro" 不存在).
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy" / "src" / "macro"))
        from macro_policy_scoring import evaluate_candidate_pool as _eval_pool
    except ImportError as e:
        logger.warning(f"无法导入 macro_policy_scoring: {e}")
        return {"status": "SKIP", "reason": f"import error: {e}"}

    evaluations = _eval_pool(current_positions)

    # 构造报告
    candidates_json = []
    add_recs = []
    watch_recs = []
    for ev in evaluations:
        item = {
            "code": ev.code,
            "name": ev.name,
            "priority": ev.priority,
            "fifteen_five_score": ev.fifteen_five_score,
            "kondratiev_score": ev.kondratiev_score,
            "combined_score": ev.combined_score,
            "suggested_weight": ev.suggested_weight,
            "reason": ev.reason,
            "in_position": ev.in_position,
            "recommendation": ev.recommendation,
        }
        candidates_json.append(item)
        if ev.recommendation == "ADD":
            add_recs.append(item)
        elif ev.recommendation == "WATCH":
            watch_recs.append(item)

    report = {
        "evaluated_at": now_bj().isoformat(),
        "current_positions_count": len(current_positions),
        "current_positions": current_positions,
        "add_recommendations": add_recs,
        "watch_recommendations": watch_recs,
        "all_candidates": candidates_json,
        "summary": {
            "add_count": len(add_recs),
            "watch_count": len(watch_recs),
            "total_candidates": len(candidates_json),
        },
        "note": "AI 决策整合: 由 daily_workflow Phase 1.5 每日生成, 不自动纳入 positions.json",
    }

    with open(CANDIDATE_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"候选标的评估报告: {CANDIDATE_REPORT}")
    logger.info(f"  ADD 推荐: {len(add_recs)} 个")
    for r in add_recs:
        logger.info(
            f"    - {r['code']} {r['name']}  综合分={r['combined_score']:.4f}  "
            f"建议权重={r['suggested_weight']*100:.1f}%  ({r['reason']})"
        )
    logger.info(f"  WATCH 推荐: {len(watch_recs)} 个")
    for r in watch_recs:
        logger.info(f"    - {r['code']} {r['name']}  综合分={r['combined_score']:.4f}")

    return {
        "status": "OK",
        "add_count": len(add_recs),
        "watch_count": len(watch_recs),
        "report_path": str(CANDIDATE_REPORT),
    }


# ============================================================
# Step 3: 校准预测投影
# ============================================================
def update_projection(realized: dict[str, Any]) -> dict[str, Any]:
    """根据真实年化校准 portfolio_return_projection.json

    校准逻辑：
        - 若 portfolio_weighted_annualized > base 场景年化: bull 概率上调
        - 若 portfolio_weighted_annualized < bear 场景年化: bear/black_swan 概率上调
        - 否则保持原权重, 微调
    """
    logger.info("=" * 60)
    logger.info("Step 3: 校准 portfolio_return_projection.json")
    logger.info("=" * 60)

    proj_path = PROJECT_ROOT / "portfolio_return_projection.json"
    if not proj_path.exists():
        return {"status": "FAIL", "error": "portfolio_return_projection.json 不存在"}

    with open(proj_path, encoding="utf-8") as f:
        projection = json.load(f)

    # 原权重
    original_weights = projection.get(
        "probability_weights",
        {"bull": 0.2, "base": 0.4, "bear": 0.3, "black_swan": 0.1},
    )

    # 场景年化
    bull_annualized = projection["scenarios"]["bull"]["weighted_annualized"] / 100
    base_annualized = projection["scenarios"]["base"]["weighted_annualized"] / 100
    bear_annualized = projection["scenarios"]["bear"]["weighted_annualized"] / 100
    swan_annualized = projection["scenarios"]["black_swan"]["weighted_annualized"] / 100

    realized_annualized = realized.get("portfolio_weighted_annualized", 0.0)

    # 动态校准规则
    if realized_annualized > base_annualized * 1.2:
        # 真实显著优于 base → bull 概率上调
        calibrated = {"bull": 0.25, "base": 0.45, "bear": 0.25, "black_swan": 0.05}
        calibration_reason = "realized > base*1.2, bull 概率上调"
    elif realized_annualized < bear_annualized:
        # 真实劣于 bear → bear/black_swan 概率上调
        calibrated = {"bull": 0.15, "base": 0.30, "bear": 0.40, "black_swan": 0.15}
        calibration_reason = "realized < bear, bear/black_swan 概率上调"
    else:
        # 中性: 微调
        calibrated = {"bull": 0.22, "base": 0.43, "bear": 0.28, "black_swan": 0.07}
        calibration_reason = "realized 在 base/bear 之间, 微调"

    # 计算新期望
    calibrated_expected = (
        bull_annualized * calibrated["bull"]
        + base_annualized * calibrated["base"]
        + bear_annualized * calibrated["bear"]
        + swan_annualized * calibrated["black_swan"]
    )

    horizon_years = projection.get("horizon_years", 1.5)
    initial_capital = projection.get("initial_capital", 5_000_000)
    calibrated_cumulative = (1 + calibrated_expected) ** horizon_years - 1
    calibrated_final = initial_capital * (1 + calibrated_cumulative)
    calibrated_profit = calibrated_final - initial_capital

    # 备份并写入
    bak = proj_path.with_suffix(f".json.bak_{now_bj():%Y%m%d_%H%M%S}")
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(projection, f, ensure_ascii=False, indent=2)
    logger.info(f"备份原 projection → {bak.name}")

    # 更新 projection
    projection["probability_weights"] = calibrated
    projection["probability_weights_original"] = original_weights
    projection["expected"] = {
        "expected_annualized": round(calibrated_expected * 100, 2),
        "expected_cumulative": round(calibrated_cumulative * 100, 2),
        "expected_final_amount": round(calibrated_final, 0),
        "expected_profit": round(calibrated_profit, 0),
    }
    projection["calibration"] = {
        "calibrated_at": now_bj().isoformat(),
        "realized_annualized": round(realized_annualized * 100, 2),
        "realized_period": f"{realized.get('start_date','')} → {realized.get('end_date','')}",
        "calibration_reason": calibration_reason,
        "market_annualized": round(realized.get("market_annualized", 0) * 100, 2),
        "market_sharpe": round(realized.get("market_sharpe", 0), 2),
    }

    with open(proj_path, "w", encoding="utf-8") as f:
        json.dump(projection, f, ensure_ascii=False, indent=2)
    logger.info(f"已更新 {proj_path}")

    logger.info(f"校准原因: {calibration_reason}")
    logger.info(
        f"原期望年化: {projection.get('expected',{}).get('expected_annualized',0):.2f}%"
    )
    logger.info(f"新期望年化: {calibrated_expected*100:.2f}%")
    logger.info(f"新期望期末金额: ¥{calibrated_final:,.0f}")

    return {
        "status": "OK",
        "original_weights": original_weights,
        "calibrated_weights": calibrated,
        "calibration_reason": calibration_reason,
        "original_expected_annualized": projection.get("expected", {}).get(
            "expected_annualized", 0
        ),
        "calibrated_expected_annualized": round(calibrated_expected * 100, 2),
        "calibrated_expected_final": round(calibrated_final, 0),
    }


# ============================================================
# 校准日志（追加 JSONL）
# ============================================================
def append_calibration_log(step1: dict, step2: dict, step3: dict) -> None:
    """追加校准历史日志"""
    record = {
        "timestamp": now_bj().isoformat(),
        "trade_date": now_bj().strftime("%Y-%m-%d"),
        "step1_update": step1,
        "step2_realized": {
            "start_date": step2.get("start_date"),
            "end_date": step2.get("end_date"),
            "days": step2.get("days"),
            "years": step2.get("years"),
            "portfolio_weighted_annualized": step2.get("portfolio_weighted_annualized"),
            "market_annualized": step2.get("market_annualized"),
            "market_sharpe": step2.get("market_sharpe"),
            "portfolio_weight_total": step2.get("portfolio_weight_total"),
        },
        "step3_calibration": {
            "original_weights": step3.get("original_weights"),
            "calibrated_weights": step3.get("calibrated_weights"),
            "calibration_reason": step3.get("calibration_reason"),
            "calibrated_expected_annualized": step3.get(
                "calibrated_expected_annualized"
            ),
        },
    }
    with open(CALIBRATION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info(f"校准日志已追加: {CALIBRATION_LOG}")


# ============================================================
# 主入口
# ============================================================
def run_calibration(
    begin_date: str | None = None,
    end_date: str | None = None,
    skip_fetch: bool = False,
) -> dict[str, Any]:
    """执行完整三步校准

    Args:
        begin_date: "YYYYMMDD", 默认 = 今日 - 400 天
        end_date: "YYYYMMDD", 默认 = 今日
        skip_fetch: True = 跳过 Wind 拉取, 仅用现有 returns_history.json 校准

    Returns:
        综合结果字典
    """
    if end_date is None:
        end_date = now_bj().strftime("%Y%m%d")
    if begin_date is None:
        # 默认拉取近 400 天（约 1.5 年交易日）
        bd = now_bj() - timedelta(days=400)
        begin_date = bd.strftime("%Y%m%d")

    logger.info("#" * 60)
    logger.info("# v7.5 收益预测动态校准")
    logger.info(f"# 区间: {begin_date} → {end_date}")
    logger.info(f"# skip_fetch: {skip_fetch}")
    logger.info("#" * 60)

    # Step 1
    if skip_fetch:
        step1 = {
            "status": "SKIP",
            "success": 0,
            "fail": 0,
            "total_days": 0,
            "total_symbols": 0,
        }
        logger.info("Step 1 跳过 (使用现有 returns_history.json)")
    else:
        step1 = update_returns_history(begin_date, end_date)
        if step1.get("status") != "OK":
            # Wind 配额耗尽时降级到使用现有 returns_history.json
            logger.warning("Step 1 Wind 拉取失败, 降级到使用现有历史数据")
            logger.warning("可能原因: Wind MCP QUOTA_ERROR (每日配额耗尽)")
            logger.warning("建议: 检查 WIND_API_KEY 或等待次日配额重置")
            step1["status"] = "DEGRADED"
            step1["degraded_reason"] = (
                "Wind MCP 拉取失败, 使用现有 returns_history.json"
            )

    # Step 2
    step2 = calc_realized_returns()
    if step2.get("status") != "OK":
        logger.error("Step 2 失败, 终止校准")
        return {"status": "FAIL", "step1": step1, "step2": step2}

    # Step 2.5: 候选标的池评估 (AI 决策整合)
    step2_5 = evaluate_candidate_pool()

    # Step 3
    step3 = update_projection(step2)
    if step3.get("status") != "OK":
        logger.error("Step 3 失败")
        return {"status": "FAIL", "step1": step1, "step2": step2, "step3": step3}

    # 日志
    append_calibration_log(step1, step2, step3)

    logger.info("#" * 60)
    logger.info("# 校准完成")
    logger.info("#" * 60)

    return {
        "status": "OK",
        "step1_update": step1,
        "step2_realized": step2,
        "step2_5_candidate_pool": step2_5,
        "step3_calibration": step3,
    }


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="v7.5 收益预测动态校准",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--begin-date", default=None, help="起始日期 YYYYMMDD (默认 400 天前)"
    )
    parser.add_argument("--end-date", default=None, help="结束日期 YYYYMMDD (默认今日)")
    parser.add_argument(
        "--skip-fetch", action="store_true", help="跳过 Wind 拉取, 仅用现有数据校准"
    )
    args = parser.parse_args()

    # 配置根日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(
                LOG_DIR / f"calibrate_{now_bj():%Y%m%d}.log",
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )

    result = run_calibration(
        begin_date=args.begin_date,
        end_date=args.end_date,
        skip_fetch=args.skip_fetch,
    )

    exit_code = 0 if result.get("status") == "OK" else 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
