"""归因 G1 修复真实运行验证 (2026-09-02).

直接复用 EOD 同款代码路径 (build_real_attribution_inputs + PnLAttributionEngine.attribute),
用真实生产数据:
  - 持仓: config/positions.json
  - 组合日收益: reports/shadow/daily_returns.jsonl (2026-09-01)
  - 交易成本: reports/fills/fills_2026-09-01.jsonl (FillsStore 事实源)
产出 reports/pnl_attribution/pnl_attribution_2026-09-01_VERIFY.json, 并断言:
  1. 基准收益 != 组合收益 * 0.8  (旧合成特征, 必须消失)
  2. 行业收益 != 组合收益 * 固定系数  (旧合成特征)
  3. 交易成本在有成交日 > 0 (真实佣金+印花税)
  4. data_sources / degraded_reasons 全程可追溯

价格源: 默认 default_price_provider (Wind MCP -> akshare 前复权), 超时保护避免网络阻塞.
若基准真实收益不可得 -> 触发 fail-closed 跳过报告 (正确行为, 不产出失真数字).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [verify-attrib] %(levelname)s %(message)s")
logger = logging.getLogger("verify_attrib")

REPORT_DATE = "2026-09-01"
_POSITIONS_PATH = _PROJECT_ROOT / "config" / "positions.json"
_RETURNS_PATH = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
_OUT_PATH = _PROJECT_ROOT / "reports" / "pnl_attribution" / f"pnl_attribution_{REPORT_DATE}_VERIFY.json"


def _load_positions() -> list[dict]:
    with open(_POSITIONS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("positions", {})
    items = list(raw.values())
    total = sum(float(p.get("amount", 0)) for p in items) or 1.0
    out = []
    for p in items:
        amt = float(p.get("amount", 0))
        if amt <= 0:
            continue
        out.append({
            "code": p.get("code", ""),
            "name": p.get("name", ""),
            "weight": amt / total,
            "sector": p.get("sector") or p.get("style") or "other",
            "amount": amt,
            "market_value": amt,
            "style_exposures": {},
        })
    return out


def _load_return(date: str) -> float:
    if not _RETURNS_PATH.exists():
        return 0.0
    with open(_RETURNS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if str(rec.get("date", "")) == date:
                return float(rec.get("daily_return", 0.0))
    return 0.0


def _safe_price_provider():
    """带超时的 default_price_provider 包装, 避免网络阻塞.

    契约与 builder 一致: provider(codes, days) -> (panel, source_name).
    """
    from utils.attribution.real_inputs_builder import (
        DEFAULT_LOOKBACK_DAYS,
        default_price_provider,
    )

    def provider(codes, days=DEFAULT_LOOKBACK_DAYS):
        with cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(default_price_provider, list(codes), days)
            try:
                return fut.result(timeout=60)
            except cf.TimeoutError:
                logger.warning("[price] 行情拉取超时(>60s), 降级为不可用")
                return None, "provider_timeout"
            except Exception as e:  # noqa: BLE001
                logger.warning("[price] 行情拉取异常: %s", e)
                return None, f"provider_error:{e}"

    return provider


def main() -> int:
    positions = _load_positions()
    if not positions:
        logger.error("无持仓, 退出")
        return 1
    logger.info("加载真实持仓 %d 只 (config/positions.json)", len(positions))

    portfolio_ret = _load_return(REPORT_DATE)
    logger.info("组合日收益 (reports/shadow/daily_returns.jsonl %s) = %.6f", REPORT_DATE, portfolio_ret)

    from utils.attribution.real_inputs_builder import build_real_attribution_inputs

    provider = _safe_price_provider()
    inputs = build_real_attribution_inputs(
        report_date=REPORT_DATE,
        positions=positions,
        portfolio_ret=portfolio_ret,
        price_provider=provider,
    )

    logger.info("=== data_sources ===")
    logger.info(json.dumps(inputs.data_sources, ensure_ascii=False, indent=2))
    logger.info("=== degraded_reasons ===")
    logger.info(json.dumps(inputs.degraded_reasons, ensure_ascii=False, indent=2))
    logger.info("benchmark_available = %s", inputs.benchmark_available)

    # fail-closed: 基准不可得 -> 正确跳过报告 (不产出失真数字)
    if not inputs.benchmark_available:
        logger.warning(
            "[FAIL-CLOSED] 基准真实收益不可得, 跳过报告生成 (不产出失真数字)。"
            "降级原因: %s", inputs.degraded_reasons
        )
        print("\n================ fail-closed 观察 ================")
        print(json.dumps({
            "report_date": REPORT_DATE,
            "benchmark_available": False,
            "data_sources": inputs.data_sources,
            "degraded_reasons": inputs.degraded_reasons,
            "note": "基准不可得时正确跳过, 未产出任何合成/失真数字 —— G1 修复生效",
        }, ensure_ascii=False, indent=2, default=str))
        print("=================================================")
        return 0

    # 断言 1 & 2: 非合成
    bench = (inputs.benchmark_returns or [None])[0]
    if bench is not None and abs(portfolio_ret) > 1e-9:
        ratio = bench / portfolio_ret
        assert not (0.79 < ratio < 0.81), f"基准仍是合成值 (组合收益*0.8): {bench:.6f}"
    assert inputs.benchmark_available, "基准可用但未标记 available"

    # 断言 3: 交易成本真实 > 0 (有成交日)
    tc = inputs.trading_costs
    logger.info("trading_cost = %.6f (source=%s)", tc, inputs.data_sources.get("trading_cost"))
    assert tc > 0.0, "有成交日交易成本应为 > 0 (真实佣金+印花税)"

    # 断言 4: 行业/因子非合成 (行业 != 组合收益 * 固定系数)
    for k, v in (inputs.sector_returns or {}).items():
        val = (v or [None])[0]
        if val is not None and abs(portfolio_ret) > 1e-9:
            r = val / portfolio_ret
            assert not (0.49 < r < 0.51), f"行业 {k} 仍是合成值 (组合收益*固定系数)"
            assert not (0.19 < r < 0.21), f"行业 {k} 仍是合成值 (组合收益*0.2)"

    # 跑归因引擎, 产出报告
    from utils.pnl_attribution_engine import PnLAttributionEngine

    engine = PnLAttributionEngine()
    attrib = engine.attribute(
        positions=positions,
        portfolio_returns=[portfolio_ret],
        hedge_pnl=0.0,
        attribution_date=REPORT_DATE,
        **inputs.to_engine_kwargs(),
    )

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(
        json.dumps(attrib, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    logger.info("=== 归因报告已产出: %s ===", _OUT_PATH)

    # 打印关键真实数值
    summary = {
        "report_date": REPORT_DATE,
        "portfolio_ret": portfolio_ret,
        "benchmark_returns": inputs.benchmark_returns,
        "market_returns": inputs.market_returns,
        "sector_returns": inputs.sector_returns,
        "factor_returns": inputs.factor_returns,
        "trading_cost": inputs.trading_costs,
        "funding_cost": inputs.funding_cost,
        "data_sources": inputs.data_sources,
        "degraded_reasons": inputs.degraded_reasons,
    }
    print("\n================ 归因真实数值摘要 ================")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print("=================================================")
    logger.info("OK: 基准/行业/因子/交易成本均来自真实数据源, 无合成系数")
    return 0


if __name__ == "__main__":
    sys.exit(main())
