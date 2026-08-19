#!/usr/bin/env python3
"""G9 FeatureStore 117 因子全注册 — W7.3.2 推进.

将 utils/alpha_factor/ 下 127 个固定因子注册到 FeatureStore Registry,
为 W7.3.2 正式落地 (10-13~10-31) 提供因子注册表基础.

因子来源: explore agent 扫描 utils/alpha_factor/ 全模块 (2026-08-14)
实际 127 个 (Technical 类 v8.6.15 从 9 扩展到 20, 非 library.py 注释的 9)

用法:
    py -X utf8 scripts/register_factors_to_feature_store.py
    py -X utf8 scripts/register_factors_to_feature_store.py --dry-run  # 只打印不写盘
    py -X utf8 scripts/register_factors_to_feature_store.py --stats    # 只统计已注册
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.feature_store import FactorMeta, FeatureStoreConfig, Registry

FACTOR_VERSION = "1.0"

FACTOR_CATALOG: dict[str, list[str]] = {
    "Momentum": [
        "MOM_20D", "MOM_60D", "MOM_120D", "MOM_252D", "MOM_12_1M",
        "MOM_REVERSAL_5D", "MOM_REVERSAL_20D", "MOM_VOLUME_ADJ",
        "MOM_UP_DOWN", "MOM_INDUSTRY_ADJ",
    ],
    "LowVolatility": [
        "VOL_20D", "VOL_120D", "VOL_60D", "VOL_252D",
        "VOL_BETA", "VOL_DOWNSIDE", "VOL_IDIO", "VOL_SKEW",
    ],
    "Size": [
        "SIZE_LOG_MCAP", "SIZE_LOG_NS", "SIZE_LOG_REV", "SIZE_LOG_ASSETS",
        "SIZE_SMALL_LARGE_RATIO", "SIZE_NON_LINEAR", "SIZE_CUBIC",
    ],
    "Liquidity": [
        "LIQ_TURNOVER_20D", "LIQ_TURNOVER_60D", "LIQ_AMIHUD", "LIQ_SPREAD",
        "LIQ_DEPTH", "LIQ_RSVP", "LIQ_ZERO_RET_DAYS", "LIQ_VOLUME_ZSCORE",
    ],
    "Value": [
        "EP", "BP", "SP", "CFP", "DP", "FCFP",
        "EV_EBITDA", "SP_EV", "PE_EX", "PEG", "PB_INT",
    ],
    "Growth": [
        "REV_Q", "REV_YOY", "REV_TTM", "NP_Q", "NP_YOY", "NP_TTM",
        "OCF_Q", "OCF_YOY", "OCF_TTM", "ROE_Q", "ROE_YOY", "ROE_TTM",
        "ROA_Q", "ROA_YOY", "ROA_TTM",
    ],
    "Quality": [
        "ROE", "ROA", "ROIC", "GROSS_MARGIN", "NET_MARGIN", "EBITDA_MARGIN",
        "CASH_NP", "ACCRUALS", "DEBT_TO_EQUITY", "CURRENT_RATIO",
        "ACCRUAL_CHG", "QUICK_RATIO", "GROSS_MARGIN_STAB",
    ],
    "Leverage": [
        "EQ_MULT", "INT_DEBT", "DE_RATIO", "LT_DEBT", "QUICK", "INT_COV",
    ],
    "Operation": [
        "ASSET_TURN", "INV_TURN", "AR_TURN", "AP_TURN", "CASH_CYCLE",
    ],
    "Technical": [
        "GTJA_004", "GTJA_019", "GTJA_026", "GTJA_131",
        "GTJA_022", "GTJA_025", "GTJA_028", "GTJA_132", "GTJA_178",
        "GTJA_006", "GTJA_012", "GTJA_054", "GTJA_085",
        "GTJA_030", "GTJA_033", "GTJA_040", "GTJA_043",
        "GTJA_057", "GTJA_144", "GTJA_101",
    ],
    "Expectation": [
        "SUE", "SUE_REVISION", "CONSENSUS_2Y", "RD_RATIO",
        "MS_TAIL_VOL", "MS_OPEN_BIG",
    ],
    "LeadLag": [
        "CHAIN_MOM_20D", "CHAIN_MOM_60D", "CHAIN_REVERSAL_5D",
        "CHAIN_NEIGHBOR_DIFF", "CHAIN_CONCENTRATION",
    ],
    "ChipDistribution": [
        "CYQ_PROFIT_RATIO", "CYQ_CONCENTRATION",
        "CYQ_COST_DEVIATION", "CYQ_PEAK_POSITION",
    ],
    "FactorMining": [
        "FM_RET_1D", "FM_MOM_5D", "FM_MOM_20D",
        "FM_IDIO_VOL", "FM_AMIHUD_AMT", "FM_CIRC_MCAP",
    ],
    "EigenAlpha": [
        "FM_DEMO_VOL_WEIGHTED_MOM", "FM_DEMO_ZERO_TRADE_DAYS", "FM_DEMO_ROE_SMOOTHED",
    ],
}

CATEGORY_TO_MODULE: dict[str, str] = {
    "Momentum": "price_volume.py",
    "LowVolatility": "price_volume.py",
    "Size": "price_volume.py",
    "Liquidity": "price_volume.py",
    "Value": "fundamental.py",
    "Growth": "fundamental.py",
    "Quality": "fundamental.py",
    "Leverage": "fundamental.py",
    "Operation": "fundamental.py",
    "Technical": "technical.py",
    "Expectation": "expectation.py",
    "LeadLag": "graph.py",
    "ChipDistribution": "chip_distribution.py",
    "FactorMining": "price_volume.py",
    "EigenAlpha": "price_volume.py",
}

CATEGORY_TO_FREQUENCY: dict[str, str] = {
    "Momentum": "daily",
    "LowVolatility": "daily",
    "Size": "daily",
    "Liquidity": "daily",
    "Value": "daily",
    "Growth": "daily",
    "Quality": "daily",
    "Leverage": "daily",
    "Operation": "daily",
    "Technical": "daily",
    "Expectation": "daily",
    "LeadLag": "daily",
    "ChipDistribution": "daily",
    "FactorMining": "daily",
    "EigenAlpha": "daily",
}


def register_all_factors(dry_run: bool = False) -> dict[str, int]:
    config = FeatureStoreConfig()
    registry = Registry(config)

    stats = {"total": 0, "registered": 0, "duplicate": 0, "failed": 0, "by_category": {}}

    for category, names in FACTOR_CATALOG.items():
        cat_count = 0
        for name in names:
            stats["total"] += 1
            meta = FactorMeta(
                name=name,
                version=FACTOR_VERSION,
                category=category,
                calc_frequency=CATEGORY_TO_FREQUENCY.get(category, "daily"),
                storage_tier="both",
                description=f"{category} factor from {CATEGORY_TO_MODULE.get(category, 'unknown')}",
            )
            if dry_run:
                stats["registered"] += 1
                cat_count += 1
                continue

            ok, msg = registry.register(meta)
            if ok:
                stats["registered"] += 1
                cat_count += 1
            elif "duplicate" in msg:
                stats["duplicate"] += 1
            else:
                stats["failed"] += 1
                print(f"  FAIL: {name} — {msg}")
        stats["by_category"][category] = cat_count

    return stats


def print_stats(stats: dict[str, int]) -> None:
    print(f"\n{'='*60}")
    print("G9 FeatureStore 因子注册统计")
    print(f"{'='*60}")
    print(f"总因子数:   {stats['total']}")
    print(f"成功注册:   {stats['registered']}")
    print(f"重复跳过:   {stats['duplicate']}")
    print(f"失败:       {stats['failed']}")
    print("\n按类别分布:")
    for cat, cnt in stats["by_category"].items():
        print(f"  {cat:20s} {cnt:3d}")


def main() -> None:
    parser = argparse.ArgumentParser(description="G9 FeatureStore 因子全注册")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    parser.add_argument("--stats", action="store_true", help="只统计已注册因子")
    args = parser.parse_args()

    if args.stats:
        config = FeatureStoreConfig()
        registry = Registry(config)
        all_factors = registry.list_factors()
        print(f"已注册因子总数: {len(all_factors)}")
        by_cat: dict[str, int] = {}
        for f in all_factors:
            by_cat[f.category] = by_cat.get(f.category, 0) + 1
        for cat, cnt in sorted(by_cat.items()):
            print(f"  {cat:20s} {cnt:3d}")
        return

    print(f"开始注册 {sum(len(v) for v in FACTOR_CATALOG.values())} 个因子到 FeatureStore Registry...")
    if args.dry_run:
        print("[DRY-RUN] 不写盘")

    stats = register_all_factors(dry_run=args.dry_run)
    print_stats(stats)

    if stats["failed"] == 0:
        print(f"\n✅ 全部成功: {stats['registered']}/{stats['total']}")
    else:
        print(f"\n⚠️ 有失败: {stats['failed']} 个")


if __name__ == "__main__":
    main()
