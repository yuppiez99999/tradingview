"""
风险前置过滤器 — 对冲基金 Universe 标准做法

在因子计算之前剔除不可交易股票，避免无效计算：
- ST/*ST/退市风险股
- 停牌
- 上市不足 N 日
- 流动性不足（成交额过低）
- 仙股（价格过低）
- 异常换手率
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RiskFilterConfig:
    """风险过滤配置（对冲基金机构标准）"""

    # 流动性
    min_amount_20d: float = 50_000_000.0  # 20日平均成交额 ≥ 5000万
    min_price: float = 2.0  # 最低价格 ≥ 2元
    max_price: float = 500.0  # 最高价格 ≤ 500元
    # 换手率
    min_turnover_today: float = 0.001  # 当日换手率 ≥ 0.1%
    max_turnover_today: float = 0.30  # 当日换手率 ≤ 30%
    # 上市天数
    min_listed_days: int = 60  # 上市至少 60 个交易日
    # 名称过滤
    exclude_st: bool = True  # 排除 ST/*ST/退市
    exclude_pt: bool = True  # 排除 PT 股
    # 涨跌停过滤（当日涨跌停股票无法成交）
    exclude_limit_up: bool = True  # 排除涨停
    exclude_limit_down: bool = True  # 排除跌停


# ST/退市名称正则
_ST_PATTERN = re.compile(r"(ST|\*ST|退市|PT|S\s)", re.IGNORECASE)


def _is_st_stock(name: str) -> bool:
    """判断是否为 ST/退市股"""
    if not name or not isinstance(name, str):
        return False
    return bool(_ST_PATTERN.search(name))


def _is_limit_up(row: pd.Series) -> bool:
    """判断当日是否涨停"""
    try:
        change_ratio = float(row.get("涨跌幅", 0) or 0)
        # 主板10%，科创板/创业板20%，北交所30%
        return change_ratio >= 9.8
    except (ValueError, TypeError):
        return False


def _is_limit_down(row: pd.Series) -> bool:
    """判断当日是否跌停"""
    try:
        change_ratio = float(row.get("涨跌幅", 0) or 0)
        return change_ratio <= -9.8
    except (ValueError, TypeError):
        return False


def filter_universe(
    universe_df: pd.DataFrame,
    spot_df: pd.DataFrame,
    config: RiskFilterConfig | None = None,
) -> pd.DataFrame:
    """对股票池执行风险前置过滤

    Args:
        universe_df: 股票池（columns: code, name, index）
        spot_df: 全市场实时快照（来自 akshare.stock_zh_a_spot_em）
        config: 过滤配置，None 使用默认

    Returns:
        DataFrame: 过滤后的股票池，附加行情字段
            columns: code, name, index, price, amount, volume, turnover_ratio, change_ratio
    """
    if config is None:
        config = RiskFilterConfig()

    if universe_df.empty or spot_df.empty:
        logger.error("输入数据为空")
        return pd.DataFrame()

    # 标准化 spot_df 列名（akshare 不同版本有差异）
    spot_df = spot_df.copy()
    rename_map = {
        "代码": "code",
        "名称": "name",
        "最新价": "price",
        "涨跌幅": "change_ratio",
        "成交额": "amount",
        "成交量": "volume",
        "换手率": "turnover_ratio",
        "总市值": "market_cap",
        "流通市值": "circ_market_cap",
    }
    spot_df = spot_df.rename(
        columns={k: v for k, v in rename_map.items() if k in spot_df.columns}
    )
    spot_df["code"] = spot_df["code"].astype(str).str.zfill(6)

    # 合并股票池与行情
    universe_df = universe_df.copy()
    universe_df["code"] = universe_df["code"].astype(str).str.zfill(6)
    merged = universe_df.merge(
        spot_df[
            [
                "code",
                "name",
                "price",
                "amount",
                "volume",
                "turnover_ratio",
                "change_ratio",
            ]
            + (
                ["market_cap", "circ_market_cap"]
                if "market_cap" in spot_df.columns
                else []
            )
        ],
        on="code",
        how="left",
        suffixes=("_universe", "_spot"),
    )

    initial_count = len(merged)
    stats = {"initial": initial_count}

    # ============================================================
    # 1. 剔除无行情数据（停牌或未上市）
    # ============================================================
    mask = merged["price"].notna() & (merged["price"] > 0)
    removed = (~mask).sum()
    merged = merged[mask].copy()
    stats["no_quote"] = int(removed)
    logger.info(f"  [1/6] 剔除停牌/无行情: -{removed}  剩余: {len(merged)}")

    # ============================================================
    # 2. 剔除 ST/退市
    # ============================================================
    if config.exclude_st:
        name_col = "name_spot" if "name_spot" in merged.columns else "name"
        mask = ~merged[name_col].apply(_is_st_stock)
        removed = (~mask).sum()
        merged = merged[mask].copy()
        stats["st_removed"] = int(removed)
        logger.info(f"  [2/6] 剔除 ST/退市: -{removed}  剩余: {len(merged)}")

    # ============================================================
    # 3. 剔除价格过低/过高
    # ============================================================
    mask = (merged["price"] >= config.min_price) & (merged["price"] <= config.max_price)
    removed = (~mask).sum()
    merged = merged[mask].copy()
    stats["price_filtered"] = int(removed)
    logger.info(
        f"  [3/6] 剔除价格异常 (≤{config.min_price} 或 ≥{config.max_price}): -{removed}  剩余: {len(merged)}"
    )

    # ============================================================
    # 4. 剔除流动性不足（当日成交额）
    # ============================================================
    # 注意: 完整规则需 20 日均额，此处用当日额近似
    mask = merged["amount"].fillna(0) >= config.min_amount_20d * 0.5  # 当日至少一半阈值
    removed = (~mask).sum()
    merged = merged[mask].copy()
    stats["low_liquidity"] = int(removed)
    logger.info(f"  [4/6] 剔除流动性不足: -{removed}  剩余: {len(merged)}")

    # ============================================================
    # 5. 剔除换手率异常
    # ============================================================
    if "turnover_ratio" in merged.columns:
        # akshare 换手率单位是 %
        turnover_pct = merged["turnover_ratio"].fillna(0) / 100.0
        mask = (turnover_pct >= config.min_turnover_today) & (
            turnover_pct <= config.max_turnover_today
        )
        removed = (~mask).sum()
        merged = merged[mask].copy()
        stats["turnover_filtered"] = int(removed)
        logger.info(f"  [5/6] 剔除换手率异常: -{removed}  剩余: {len(merged)}")

    # ============================================================
    # 6. 剔除涨跌停（无法成交）
    # ============================================================
    if config.exclude_limit_up or config.exclude_limit_down:
        mask = pd.Series(True, index=merged.index)
        if config.exclude_limit_up:
            mask &= ~merged.apply(_is_limit_up, axis=1)
        if config.exclude_limit_down:
            mask &= ~merged.apply(_is_limit_down, axis=1)
        removed = (~mask).sum()
        merged = merged[mask].copy()
        stats["limit_filtered"] = int(removed)
        logger.info(f"  [6/6] 剔除涨跌停: -{removed}  剩余: {len(merged)}")

    # ============================================================
    # 汇总
    # ============================================================
    final_count = len(merged)
    stats["final"] = final_count
    stats["total_removed"] = initial_count - final_count
    stats["pass_rate"] = (
        f"{final_count / initial_count * 100:.1f}%" if initial_count else "0%"
    )
    logger.info(
        f"风险过滤完成: {initial_count} → {final_count}  通过率: {stats['pass_rate']}"
    )

    # 清理列名
    if "name_universe" in merged.columns:
        merged = merged.rename(columns={"name_universe": "name"}).drop(
            columns=["name_spot"], errors="ignore"
        )
    elif "name_spot" in merged.columns:
        merged = merged.rename(columns={"name_spot": "name"})

    merged = merged.reset_index(drop=True)
    merged.attrs["filter_stats"] = stats
    return merged


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 自测
    logger.info(RiskFilterConfig())
