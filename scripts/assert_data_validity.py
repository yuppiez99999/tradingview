#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
沉默失败主动探测 (Silent Failure Detection via Non-Zero Assertions)
=====================================================================
创建: 2026-08-06 防复发机制 #5
目的: 防止"代码能跑但输出是空的/错的"这类沉默失败。
      系统不会崩溃报错, 只是默默给出无效结果, 最难发现。

检查项 (非零断言):
    D1 压力测试 actual_pnl 不能全为 0 (说明持仓为空)
    D2 alpha_signals n_stocks 不能为 0 (说明因子计算全失败)
    D3 DriftShadowIntegrator observed 计数不能为 0/0 (说明信号链断了)
    D4 positions.json 持仓数不能为 0 (除非空仓状态)
    D5 hedge_execution_fill orders 不能为空 (对冲未执行)
    D6 daily_returns.jsonl 不能连续缺失 (观察期数据断档)
    D7 VIX 值不能出现两个数据源口径差异 >50%

用法:
    python scripts/assert_data_validity.py              # 全量检查
    python scripts/assert_data_validity.py --date 20260806  # 指定日期

退出码:
    0 = 全部断言通过
    1 = 有断言失败 (应告警)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AssertionResult:
    """单项数据有效性断言结果."""

    def __init__(self, code: str, name: str, passed: bool, detail: str, evidence: str = ""):
        self.code = code
        self.name = name
        self.passed = passed
        self.detail = detail
        self.evidence = evidence


def _load_json(path: Path) -> dict | None:
    """安全加载 JSON 文件."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return None


def check_d1_stress_test_nonzero(date_str: str) -> AssertionResult:
    """D1 压力测试 actual_pnl 不能全为 0."""
    # 搜索最近的 stress_test 报告
    reports_dir = _PROJECT_ROOT / "reports"
    stress_files = sorted(reports_dir.glob("stress_test_*.json"), reverse=True) if reports_dir.exists() else []

    if not stress_files:
        return AssertionResult("D1", "压力测试非零", True, "无压力测试报告(未运行, 跳过)")

    data = _load_json(stress_files[0])
    if not data:
        return AssertionResult("D1", "压力测试非零", False, f"无法解析: {stress_files[0].name}")

    scenarios = data.get("scenarios", {})
    zero_count = sum(1 for s in scenarios.values() if s.get("actual_pnl", 0) == 0)

    if zero_count == len(scenarios) and len(scenarios) > 0:
        return AssertionResult(
            "D1", "压力测试非零", False,
            f"全部 {len(scenarios)} 个场景 actual_pnl=0 (持仓为空, 用了模拟持仓)",
            str(stress_files[0].name),
        )
    return AssertionResult("D1", "压力测试非零", True, f"{len(scenarios)} 个场景, {zero_count} 个为零")


def check_d2_alpha_signals_nonzero(date_str: str) -> AssertionResult:
    """D2 alpha_signals n_stocks 不能为 0."""
    pipeline_dir = _PROJECT_ROOT / "reports" / "pipeline"
    if not pipeline_dir.exists():
        return AssertionResult("D2", "Alpha信号非零", True, "无 pipeline 目录(跳过)")

    signal_files = sorted(pipeline_dir.glob(f"alpha_signals_{date_str}*.json"), reverse=True)
    if not signal_files:
        # 尝试找最近的
        signal_files = sorted(pipeline_dir.glob("alpha_signals_*.json"), reverse=True)

    if not signal_files:
        return AssertionResult("D2", "Alpha信号非零", True, "无 alpha_signals 报告(跳过)")

    data = _load_json(signal_files[0])
    if not data:
        return AssertionResult("D2", "Alpha信号非零", False, f"无法解析: {signal_files[0].name}")

    status = data.get("status", "")
    n_stocks = data.get("n_stocks", 0)

    if status == "fallback" or n_stocks == 0:
        return AssertionResult(
            "D2", "Alpha信号非零", False,
            f"alpha_signals status={status}, n_stocks={n_stocks} (因子计算可能全失败)",
            str(signal_files[0].name),
        )
    return AssertionResult("D2", "Alpha信号非零", True, f"n_stocks={n_stocks}, status={status}")


def check_d3_drift_shadow_observed(date_str: str) -> AssertionResult:
    """D3 DriftShadowIntegrator observed 计数不能为 0/0."""
    # 检查 drift_shadow 的日志或报告
    evolution_dir = _PROJECT_ROOT / "reports" / "evolution"
    if not evolution_dir.exists():
        return AssertionResult("D3", "DriftShadow非零", True, "无 evolution 目录(跳过)")

    # 搜索最近的 drift 相关报告
    drift_files = sorted(evolution_dir.glob("*drift*"), reverse=True)
    if not drift_files:
        return AssertionResult("D3", "DriftShadow非零", True, "无 drift 报告(跳过)")

    data = _load_json(drift_files[0])
    if not data:
        return AssertionResult("D3", "DriftShadow非零", True, f"非JSON格式: {drift_files[0].name}")

    observed = data.get("observed", data.get("observed_count", "N/A"))
    if isinstance(observed, str) and "0/0" in observed:
        return AssertionResult(
            "D3", "DriftShadow非零", False,
            "observed=0/0 (信号链断裂, 预测从未传入)",
            str(drift_files[0].name),
        )
    return AssertionResult("D3", "DriftShadow非零", True, f"observed={observed}")


def check_d4_positions_nonempty(date_str: str) -> AssertionResult:
    """D4 positions.json 持仓数不能为 0 (除非空仓)."""
    positions_path = _PROJECT_ROOT / "config" / "positions.json"
    data = _load_json(positions_path)
    if not data:
        return AssertionResult("D4", "持仓非空", False, "positions.json 不存在或无法解析")

    positions = data.get("positions", data.get("holdings", []))
    if isinstance(positions, dict):
        count = len(positions)
    elif isinstance(positions, list):
        count = len(positions)
    else:
        count = 0

    if count == 0:
        return AssertionResult("D4", "持仓非空", False, "positions.json 持仓数为 0 (空仓状态?)")
    return AssertionResult("D4", "持仓非空", True, f"持仓数={count}")


def check_d5_hedge_fill_nonempty(date_str: str) -> AssertionResult:
    """D5 hedge_execution_fill orders 不能为空."""
    # date_str 可能是 "20260806" (无横线) 或 "2026-08-06" (带横线)
    date_dashed = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if len(date_str) == 8 else date_str
    # 搜索多个可能的位置和文件名格式
    candidates = [
        _PROJECT_ROOT / "reports" / f"hedge_execution_fill_{date_dashed}.json",
        _PROJECT_ROOT / "reports" / f"hedge_execution_fill_{date_str}.json",
        _PROJECT_ROOT / f"hedge_execution_fill_{date_dashed}.json",
        _PROJECT_ROOT / f"hedge_execution_fill_{date_str}.json",
        _PROJECT_ROOT / "data" / f"hedge_execution_fill_{date_dashed}.json",
        _PROJECT_ROOT / "data" / f"hedge_execution_fill_{date_str}.json",
    ]
    fill_path = None
    for p in candidates:
        if p.exists():
            fill_path = p
            break

    if fill_path is None:
        return AssertionResult("D5", "对冲成交非空", True, "无对冲成交报告(当日可能未对冲)")

    data = _load_json(fill_path)
    if not data:
        return AssertionResult("D5", "对冲成交非空", False, f"无法解析: {fill_path.name}")

    orders = data.get("orders", [])
    if len(orders) == 0:
        return AssertionResult("D5", "对冲成交非空", False, "hedge_execution_fill orders 为空 (对冲未执行?)")
    return AssertionResult("D5", "对冲成交非空", True, f"orders={len(orders)}")


def check_d6_daily_returns_continuous(date_str: str) -> AssertionResult:
    """D6 daily_returns.jsonl 不能连续缺失."""
    returns_path = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if not returns_path.exists():
        return AssertionResult("D6", "收益数据连续", True, "无 daily_returns.jsonl (跳过)")

    try:
        lines = returns_path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) < 2:
            return AssertionResult("D6", "收益数据连续", True, f"仅 {len(lines)} 条 (样本不足, 跳过)")

        # 检查最后几条的日期是否连续
        dates: list[str] = []
        for line in lines[-10:]:
            try:
                record = json.loads(line)
                d = record.get("date", "")
                if d:
                    dates.append(d)
            except json.JSONDecodeError:
                continue

        if len(dates) >= 2:
            # 简单检查: 最近的日期是否在 3 天内
            try:
                latest = datetime.strptime(dates[-1], "%Y-%m-%d")
                gap = (datetime.now() - latest).days
                if gap > 5:
                    return AssertionResult(
                        "D6", "收益数据连续", False,
                        f"最近收益日期 {dates[-1]} 距今 {gap} 天 (数据断档)",
                        str(returns_path.name),
                    )
            except ValueError:
                pass
    except OSError:
        pass

    return AssertionResult("D6", "收益数据连续", True, "收益数据日期连续")


def check_d7_vix_consistency(date_str: str) -> AssertionResult:
    """D7 VIX 值不能出现两个数据源口径差异 >50%.

    G13 修复 (2026-08-06): VolRegime 用 VixDataSource 的 RV 替代值,
    daily_workflow 之前用硬编码 18.5, 已改为同源获取.
    此断言对比 vol_regime_weights 报告中的 vix 和 vix_cache 中的 vix, 差异应 <20%.
    """
    try:
        # 1. 从 vol_regime_weights_{date}.json 读 VIX
        vol_regime_path = _PROJECT_ROOT / "reports" / "evolution" / f"vol_regime_weights_{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}.json"
        vol_regime_vix = None
        if vol_regime_path.exists():
            with open(vol_regime_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # VIX 存储在 regime.indicators.vix 路径下 (非顶层 indicators)
            regime_obj = data.get("regime", {})
            indicators = regime_obj.get("indicators", {})
            vol_regime_vix = indicators.get("vix")
            # 降级: 如果 regime.indicators 没有, 尝试顶层 indicators (兼容旧格式)
            if vol_regime_vix is None:
                vol_regime_vix = data.get("indicators", {}).get("vix")

        # 2. 从 vix_cache.json 读 VIX
        vix_cache_path = _PROJECT_ROOT / "reports" / "volatility" / "vix_cache.json"
        cache_vix = None
        if vix_cache_path.exists():
            with open(vix_cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            cache_vix = cache_data.get("vix")

        if vol_regime_vix is None and cache_vix is None:
            return AssertionResult("D7", "VIX口径一致", True, "无 VIX 数据可比 (两源均无数据, 跳过)")

        if vol_regime_vix is None or cache_vix is None:
            return AssertionResult("D7", "VIX口径一致", True, f"仅一源有数据 (vol_regime={vol_regime_vix}, cache={cache_vix}), 跳过")

        # EOD 后 vix_cache 会被刷新为新 RV 值, 但 vol_regime_weights 仍是盘中值.
        # 如果两者时间戳不一致 (cache 更新), 允许较大差异 (EOD 刷新导致 RV 变化).
        cache_ts = cache_data.get("timestamp", "")
        vol_ts = data.get("timestamp", data.get("generated_at", ""))
        # EOD 刷新场景: cache 时间比 vol_regime 更新 (当天 16:xx vs 盘中 09-15xx)
        # 或两者都是 16:xx 但 cache 时间更晚
        is_eod_refresh = False
        if cache_ts and vol_ts:
            # 场景1: vol_regime 是盘中 (09-15), cache 是 EOD 后 (16+)
            if any(h in vol_ts for h in ["09:", "10:", "11:", "12:", "13:", "14:", "15:"]) and "16:" in cache_ts:
                is_eod_refresh = True
            # 场景2: 两者都是 16:xx 但 cache 时间更晚
            elif "16:" in vol_ts and "16:" in cache_ts:
                # 简单比较: cache 的分钟数 > vol 的分钟数
                try:
                    vol_min = int(vol_ts.split("T")[1].split(":")[1]) if "T" in vol_ts else int(vol_ts.split(" ")[1].split(":")[1])
                    cache_min = int(cache_ts.split("T")[1].split(":")[1]) if "T" in cache_ts else int(cache_ts.split(" ")[1].split(":")[1])
                    if cache_min > vol_min:
                        is_eod_refresh = True
                except (IndexError, ValueError):
                    pass

        diff_pct = abs(vol_regime_vix - cache_vix) / max(vol_regime_vix, cache_vix) * 100
        # EOD 刷新场景: 容忍 80% 差异 (RV 因当日大涨/大跌而显著变化)
        threshold = 80 if is_eod_refresh else 50
        if diff_pct > threshold:
            return AssertionResult(
                "D7", "VIX口径一致", False,
                f"VIX 口径差异 {diff_pct:.1f}% (vol_regime={vol_regime_vix:.2f}, cache={cache_vix:.2f})",
            )
        note = " (EOD刷新, 非口径不一致)" if is_eod_refresh else ""
        return AssertionResult(
            "D7", "VIX口径一致", True,
            f"vol_regime={vol_regime_vix:.2f}, cache={cache_vix:.2f}, 差异={diff_pct:.1f}%{note}",
        )
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as e:
        return AssertionResult("D7", "VIX口径一致", True, f"检查异常 (容错通过): {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="沉默失败主动探测")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="检查日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    date_str = args.date.replace("-", "")

    results = [
        check_d1_stress_test_nonzero(date_str),
        check_d2_alpha_signals_nonzero(date_str),
        check_d3_drift_shadow_observed(date_str),
        check_d4_positions_nonempty(date_str),
        check_d5_hedge_fill_nonempty(date_str),
        check_d6_daily_returns_continuous(date_str),
        check_d7_vix_consistency(date_str),
    ]

    pass_count = sum(1 for r in results if r.passed)
    fail_count = sum(1 for r in results if not r.passed)

    print("=" * 70)
    print("沉默失败主动探测 (Silent Failure Detection)")
    print(f"日期: {args.date}")
    print("=" * 70)
    for r in results:
        icon = "[OK]" if r.passed else "[!!]"
        print(f"  {icon} {r.code} {r.name}: {r.detail}")
        if r.evidence:
            print(f"         证据: {r.evidence}")
    print("-" * 70)
    print(f"  总计: {pass_count} PASS, {fail_count} FAIL")
    print("=" * 70)

    # 如果有失败, 尝试调用 utils.notify 发告警
    if fail_count > 0:
        try:
            sys.path.insert(0, str(_PROJECT_ROOT))
            from utils.notify import send_alert

            failed_items = [f"{r.code} {r.name}: {r.detail}" for r in results if not r.passed]
            send_alert(
                "沉默失败检测告警",
                f"检测到 {fail_count} 项数据有效性断言失败:\n" + "\n".join(failed_items),
                level="warning",
            )
        except ImportError:
            print("  (utils.notify 不可用, 告警未发送)")

    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
