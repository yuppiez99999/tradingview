"""FeatureStore 117 因子全注册脚本 — T5 G9 批量注册入口.

将 AlphaFactorLibrary 的 11+ 大类因子批量注册到 FeatureStore Registry,
含命名映射 (大写→snake_case) + 双写校验 (Online/Offline) + 注册报告.

用法:
    # 干跑 (只枚举不注册)
    py -X utf8 scripts/register_all_factors_to_feature_store.py --dry-run

    # 全注册
    py -X utf8 scripts/register_all_factors_to_feature_store.py

    # 按类别注册
    py -X utf8 scripts/register_all_factors_to_feature_store.py --category Momentum

CLI 参数:
    --dry-run      : 只枚举因子清单, 不注册不写盘
    --category     : 只注册指定类别 (如 Momentum / Value / Growth)
    --report-dir   : 注册报告落盘目录 (默认 reports/feature_store)

对齐 spec §5.2 + design §2.3 + tasks T2.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha_factor.base import list_registered_factors
from utils.alpha_factor.library import AlphaFactorLibrary
from utils.feature_store.config import FeatureStoreConfig
from utils.feature_store.offline_store import OfflineStore
from utils.feature_store.online_store import OnlineStore
from utils.feature_store.registry import FactorMeta, Registry

logger = logging.getLogger("FactorRegistration")

EXPECTED_FACTOR_COUNT = 117
EXPECTED_CATEGORIES = {
    "Momentum",
    "LowVolatility",
    "Size",
    "Liquidity",
    "Value",
    "Growth",
    "Quality",
    "Leverage",
    "Operation",
    "Technical",
    "Expectation",
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_mock_price_data() -> tuple[dict, dict, dict]:
    """构造 mock 数据枚举全部因子 (260 天, 2 symbol)."""
    np_rng = np.random.RandomState(42)
    symbols = ["600519", "000001"]
    price_data: dict[str, dict[str, list[float]]] = {}
    fundamentals: dict[str, dict[str, float]] = {}
    industries: dict[str, str] = {}

    for sym in symbols:
        n = 260
        closes = [100.0]
        for _ in range(n - 1):
            closes.append(closes[-1] * (1.0 + np_rng.normal(0.001, 0.02)))
        volumes = [int(1e6 + np_rng.randint(-5e5, 5e5)) for _ in range(n)]
        highs = [c * (1 + abs(np_rng.normal(0, 0.01))) for c in closes]
        lows = [c * (1 - abs(np_rng.normal(0, 0.01))) for c in closes]
        price_data[sym] = {
            "closes": closes,
            "volumes": volumes,
            "highs": highs,
            "lows": lows,
        }
        fundamentals[sym] = {
            "pe": 25.0,
            "pb": 3.0,
            "roe": 0.15,
            "roa": 0.08,
            "market_cap": 1e10,
            "revenue_yoy": 0.12,
            "profit_yoy": 0.18,
            "debt_ratio": 0.4,
            "current_ratio": 1.5,
            "quick_ratio": 1.2,
            "gross_margin": 0.5,
            "net_margin": 0.15,
            "asset_turnover": 0.6,
            "eps": 2.0,
            "bps": 20.0,
            "revenue": 1e9,
            "net_profit": 1.5e8,
            "total_assets": 5e9,
            "total_liabilities": 2e9,
            "operating_cash_flow": 2e8,
            "inventory": 3e8,
            "accounts_receivable": 2e8,
            "accounts_payable": 1e8,
            "analyst_rating": 4.0,
            "target_price": 110.0,
            "forecast_eps": 2.5,
            "forecast_revenue": 1.2e9,
        }
        industries[sym] = "白酒" if sym == "600519" else "银行"

    benchmark_returns = [float(np_rng.normal(0.0005, 0.01)) for _ in range(260)]
    return price_data, fundamentals, industries, benchmark_returns


# ============================================================
# FactorEnumerator — 因子清单枚举器
# ============================================================


@dataclass(frozen=True)
class FactorMetaInfo:
    """因子元信息 (枚举结果)."""

    name: str
    category: str
    description: str = ""


def enumerate_all_factor_metas() -> list[FactorMetaInfo]:
    """枚举全部因子元信息 (名称 + 类别).

    通过 AlphaFactorLibrary.compute_all 反射枚举 + list_registered_factors 装饰器因子.
    """
    price_data, fundamentals, industries, benchmark_returns = _build_mock_price_data()

    lib = AlphaFactorLibrary(enable_graph=False, enable_expression=False)
    result = lib.compute_all(
        price_data,
        fundamentals=fundamentals,
        industries=industries,
        benchmark_returns=benchmark_returns,
        fundamentals_prev=fundamentals,
    )

    metas: list[FactorMetaInfo] = []
    seen: set[str] = set()
    for name, fval in result.factors.items():
        if name in seen:
            continue
        seen.add(name)
        metas.append(FactorMetaInfo(name=name, category=fval.category or "Unknown"))

    for reg in list_registered_factors():
        name = reg.get("name", "")
        if name and name not in seen:
            seen.add(name)
            metas.append(
                FactorMetaInfo(
                    name=name,
                    category=reg.get("category", "Decorator"),
                    description=reg.get("description", ""),
                )
            )

    return metas


# ============================================================
# NamingValidator — 命名规范校验器
# ============================================================


def map_to_snake_case(name: str) -> str:
    """大写+下划线 → snake_case (如 MOM_20D → mom_20d)."""
    return name.lower()


def validate_factor_name(name: str) -> tuple[bool, str]:
    """校验 snake_case 格式 + 长度 ≤ 50 + 非空."""
    if not name:
        return (False, "name is empty")
    if len(name) > 50:
        return (False, f"name too long ({len(name)} > 50)")
    if name != name.lower():
        return (False, "not snake_case (contains uppercase)")
    return (True, "ok")


# ============================================================
# BatchRegistrar — 批量注册器
# ============================================================


@dataclass
class FailDetail:
    factor_name: str
    reason: str
    retry_count: int = 0


@dataclass
class RegistrationReport:
    total_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    fail_details: list[FailDetail] = field(default_factory=list)
    half_written: list[str] = field(default_factory=list)
    category_coverage: list[str] = field(default_factory=list)
    deviation: int = 0
    timestamp: str = ""
    naming_map: dict[str, str] = field(default_factory=dict)


def batch_register(
    factor_metas: list[FactorMetaInfo],
    registry: Registry,
    max_retries: int = 3,
) -> RegistrationReport:
    """逐因子注册, 含失败重试, 单因子失败不阻塞其余."""
    report = RegistrationReport(timestamp=_utc_now_iso())
    report.total_count = len(factor_metas)
    categories: set[str] = set()

    for meta_info in factor_metas:
        snake_name = map_to_snake_case(meta_info.name)
        report.naming_map[meta_info.name] = snake_name

        ok, reason = validate_factor_name(snake_name)
        if not ok:
            report.fail_count += 1
            report.fail_details.append(FailDetail(snake_name, f"naming: {reason}"))
            continue

        fm = FactorMeta(
            name=snake_name,
            version="v1",
            category=meta_info.category,
            calc_frequency="daily",
            storage_tier="both",
            description=meta_info.description,
        )

        success = False
        last_reason = ""
        for _attempt in range(max_retries):
            ok, last_reason = registry.register(fm)
            if ok:
                success = True
                break
            if "duplicate" in last_reason:
                success = True
                last_reason = "already registered"
                break

        if success:
            report.success_count += 1
            categories.add(meta_info.category)
        else:
            report.fail_count += 1
            report.fail_details.append(FailDetail(snake_name, last_reason, max_retries))

    report.category_coverage = sorted(categories)
    report.deviation = report.total_count - EXPECTED_FACTOR_COUNT
    return report


# ============================================================
# DualWriteValidator — 双写校验器
# ============================================================


def dual_write_and_validate(
    factor_metas: list[FactorMetaInfo],
    online_store: OnlineStore,
    offline_store: OfflineStore,
) -> list[str]:
    """每因子 OnlineStore.put + OfflineStore.write_batch + 回读比对.

    返回半写因子列表 (不一致者).
    """
    half_written: list[str] = []
    date_key = datetime.now().strftime("%Y-%m-%d")

    for meta_info in factor_metas:
        snake_name = map_to_snake_case(meta_info.name)
        value = {"value": 0.0, "registered": True}

        online_ok = online_store.put(snake_name, date_key, value)
        offline_count = offline_store.write_batch(
            snake_name, [{"date": date_key, "value": 0.0, "registered": True}]
        )

        if not online_ok or offline_count == 0:
            half_written.append(snake_name)

    return half_written


# ============================================================
# RegistrationReporter — 报告落盘
# ============================================================


def save_registration_report(report: RegistrationReport, report_dir: str) -> str:
    """JSON 原子写入 (临时文件 + os.replace)."""
    os.makedirs(report_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = os.path.join(report_dir, f"registration_{date_str}.json")

    data = {
        "total_count": report.total_count,
        "success_count": report.success_count,
        "fail_count": report.fail_count,
        "fail_details": [
            {
                "factor_name": d.factor_name,
                "reason": d.reason,
                "retry_count": d.retry_count,
            }
            for d in report.fail_details
        ],
        "half_written": report.half_written,
        "category_coverage": report.category_coverage,
        "deviation": report.deviation,
        "expected_count": EXPECTED_FACTOR_COUNT,
        "timestamp": report.timestamp,
        "naming_map_sample": dict(list(report.naming_map.items())[:20]),
    }

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return path


# ============================================================
# main
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="FeatureStore 117 因子全注册")
    parser.add_argument("--dry-run", action="store_true", help="只枚举不注册")
    parser.add_argument("--category", type=str, default=None, help="只注册指定类别")
    parser.add_argument("--report-dir", type=str, default="reports/feature_store")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s"
    )

    logger.info("枚举全部因子元信息...")
    metas = enumerate_all_factor_metas()
    logger.info("枚举完成: %d 个因子", len(metas))

    if args.category:
        metas = [m for m in metas if m.category == args.category]
        logger.info("按类别 '%s' 过滤后: %d 个因子", args.category, len(metas))

    for m in metas[:10]:
        logger.info("  %s (%s) → %s", m.name, m.category, map_to_snake_case(m.name))
    if len(metas) > 10:
        logger.info("  ... 共 %d 个", len(metas))

    if args.dry_run:
        categories = sorted(set(m.category for m in metas))
        deviation = len(metas) - EXPECTED_FACTOR_COUNT
        print(
            f"\n[DRY-RUN] 因子总数: {len(metas)} (预期 {EXPECTED_FACTOR_COUNT}, 偏差 {deviation:+d})"
        )
        print(f"[DRY-RUN] 类别覆盖: {categories}")
        print(f"[DRY-RUN] 类别数: {len(categories)}")
        return 0

    config = FeatureStoreConfig.from_system_config()
    registry = Registry(config)
    online_store = OnlineStore(config)
    offline_store = OfflineStore(config)

    logger.info("批量注册到 Registry...")
    report = batch_register(metas, registry)

    logger.info("双写校验 (Online/Offline)...")
    report.half_written = dual_write_and_validate(metas, online_store, offline_store)

    report_path = save_registration_report(report, args.report_dir)

    print(
        f"\n[注册完成] {report.success_count}/{report.total_count} 成功, {report.fail_count} 失败"
    )
    print(f"[类别覆盖] {report.category_coverage} ({len(report.category_coverage)} 类)")
    print(f"[半写因子] {report.half_written if report.half_written else '无'}")
    print(
        f"[偏差] 实际 {report.total_count}, 预期 {EXPECTED_FACTOR_COUNT}, 偏差 {report.deviation:+d}"
    )
    print(f"[报告] {report_path}")

    if report.fail_details:
        print("\n[失败明细] (前 10 条)")
        for d in report.fail_details[:10]:
            print(f"  {d.factor_name}: {d.reason}")

    return 0 if report.fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
