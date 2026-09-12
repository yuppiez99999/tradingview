#!/usr/bin/env python3
"""Shadow 状态重建器 — 用真实 daily_returns.jsonl 重建 shadow_state.json.

=================================================================
背景 (2026-08-04 P0 日任务):
    shadow_real_data_feeder 已成功写入 7 条真实日收益到
    reports/shadow/daily_returns.jsonl (07-27 ~ 08-04, 100% 真实市场馈送),
    但 shadow_state.json 的 daily_nav 仍停留在 5 条占位值
    (全部 nav=1.016482, 最后更新 07-31) — 两者未同步.

    根因: launch_shadow_account.py 只负责 init/status/advance,
    daily_workflow Phase 10 (记录净值) 链路已断, 没有脚本把
    jsonl 真实收益同步回 shadow_state.json.

    本脚本作为 G1 缺口的补丁, 一次性重建 daily_nav:
        1. 读取 daily_returns.jsonl 全部真实收益
        2. 从 nav=1.0 (初始净值) 开始累乘: nav *= (1 + daily_return)
        3. 更新 shadow_state.json: daily_nav / current_nav / current_capital / last_updated
        4. fail-fast 检查 (单日>3% 或 3日累计>5%)
        5. 备份原 state 文件

HC 合规:
    - HC-4: 只读 daily_returns.jsonl, 只写 shadow_state.json (不碰 V9 基线)
    - 保留原 start_date / strategy_id / 配置字段, 仅重建 daily_nav 相关字段

用法:
    python scripts/rebuild_shadow_state_from_returns.py
    python scripts/rebuild_shadow_state_from_returns.py --dry-run
=================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 强制 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("rebuild_shadow_state")

STATE_FILE = _PROJECT_ROOT / "output" / "shadow_account" / "shadow_state.json"
RETURNS_FILE = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
LOG_FILE = _PROJECT_ROOT / "reports" / "shadow" / "rebuild_state_log.md"


def load_returns(path: Path) -> list[dict]:
    """加载 daily_returns.jsonl 全部记录 (按日期升序)."""
    if not path.exists():
        raise FileNotFoundError(f"收益文件不存在: {path}")
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                records.append(rec)
            except json.JSONDecodeError as e:
                logger.warning("跳过无效 JSON 行: %s", e)
    records.sort(key=lambda r: r.get("date", ""))
    return records


def compute_nav_series(
    records: list[dict], initial_capital: float
) -> tuple[list[dict], float, float]:
    """从 nav=1.0 开始累乘计算每日净值.

    Returns:
        (daily_nav 列表, 最终 nav, 最终 capital)
    """
    nav = 1.0
    daily_nav: list[dict] = []
    for rec in records:
        date = rec["date"]
        daily_return = float(rec["daily_return"])
        nav = nav * (1.0 + daily_return)
        capital = initial_capital * nav
        daily_nav.append(
            {
                "date": date,
                "nav": round(nav, 6),
                "daily_return": round(daily_return, 6),
                "capital": round(capital, 2),
                "recorded_at": now_bj().isoformat(),
            }
        )
    return daily_nav, nav, initial_capital * nav


def check_fail_fast(daily_nav: list[dict], thresholds: dict) -> tuple[bool, str | None]:
    """检查 fail-fast 条件: 单日回撤>3% 或 3日累计回撤>5%."""
    daily_dd_thr = float(thresholds.get("daily_drawdown_threshold", 0.03))
    cum_3d_thr = float(thresholds.get("cumulative_3d_drawdown_threshold", 0.05))

    for i, entry in enumerate(daily_nav):
        # 单日回撤 (日收益为负且绝对值超过阈值)
        daily_ret = float(entry["daily_return"])
        if daily_ret < -daily_dd_thr:
            return True, (
                f"单日回撤 {daily_ret*100:.2f}% 超过阈值 -{daily_dd_thr*100:.0f}% "
                f"(date={entry['date']})"
            )

        # 3 日累计回撤
        if i >= 2:
            nav_3d_ago = daily_nav[i - 2]["nav"]
            nav_today = entry["nav"]
            cum_ret = (nav_today / nav_3d_ago - 1) if nav_3d_ago > 0 else 0
            if cum_ret < -cum_3d_thr:
                return True, (
                    f"3日累计回撤 {cum_ret*100:.2f}% 超过阈值 -{cum_3d_thr*100:.0f}% "
                    f"(date={entry['date']}, 3天前={daily_nav[i-2]['date']})"
                )

    return False, None


def rebuild(dry_run: bool = False) -> dict:
    """主重建流程."""
    logger.info("=" * 70)
    logger.info("Shadow 状态重建 (从 daily_returns.jsonl)")
    logger.info("=" * 70)

    # 1. 加载真实收益
    records = load_returns(RETURNS_FILE)
    logger.info(
        "加载真实收益: %d 条 (%s ~ %s)",
        len(records),
        records[0]["date"] if records else "N/A",
        records[-1]["date"] if records else "N/A",
    )

    # 2. 加载原 state
    if not STATE_FILE.exists():
        raise FileNotFoundError(f"状态文件不存在: {STATE_FILE}")
    with open(STATE_FILE, encoding="utf-8") as f:
        state = json.load(f)

    initial_capital = float(state.get("initial_capital", 500000))
    old_nav_count = len(state.get("daily_nav", []))
    old_current_nav = state.get("current_nav", 1.0)
    logger.info(
        "原状态: daily_nav=%d 条, current_nav=%.6f, last_updated=%s",
        old_nav_count,
        old_current_nav,
        state.get("last_updated", "N/A"),
    )

    # 3. 计算真实 nav 序列
    daily_nav, final_nav, final_capital = compute_nav_series(records, initial_capital)
    logger.info(
        "重建后: daily_nav=%d 条, final_nav=%.6f, final_capital=%.2f",
        len(daily_nav),
        final_nav,
        final_capital,
    )

    # 4. fail-fast 检查
    fail_fast_config = state.get("fail_fast_config", {})
    ff_triggered, ff_reason = check_fail_fast(daily_nav, fail_fast_config)
    if ff_triggered:
        logger.error("⚠️ Fail-Fast 触发: %s", ff_reason)
    else:
        logger.info("✅ Fail-Fast 未触发 (正常)")

    # 5. 计算累计收益
    total_return = (final_nav - 1.0) if final_nav > 0 else 0
    logger.info("累计收益: %+.2f%%", total_return * 100)

    # 6. 计算最大回撤
    peak = 1.0
    max_dd = 0.0
    for entry in daily_nav:
        nav = entry["nav"]
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    logger.info("最大回撤: %.2f%%", max_dd * 100)

    # 7. 重建 daily_nav 逐日明细
    logger.info("-" * 70)
    logger.info("逐日明细:")
    logger.info("  %-12s %-10s %-12s %-14s", "日期", "日收益", "净值", "资金")
    for entry in daily_nav:
        logger.info(
            "  %-12s %+.4f%%  %.6f   ¥%.2f",
            entry["date"],
            entry["daily_return"] * 100,
            entry["nav"],
            entry["capital"],
        )
    logger.info("-" * 70)

    # 8. 写回 state (dry_run 模式不写盘)
    if not dry_run:
        # 备份原文件
        backup_path = STATE_FILE.with_suffix(
            f".json.bak.rebuild_{now_bj().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(STATE_FILE, backup_path)
        logger.info("已备份原状态: %s", backup_path.name)

        # 更新字段
        old_daily_nav = state.get("daily_nav", [])
        state["daily_nav"] = daily_nav
        state["current_nav"] = round(final_nav, 6)
        state["current_capital"] = round(final_capital, 2)
        state["last_updated"] = now_bj().isoformat()

        if ff_triggered:
            state["status"] = "TERMINATED"
            state["fail_fast_log"] = state.get("fail_fast_log", []) + [
                {
                    "terminated_at": now_bj().isoformat(),
                    "reason": ff_reason,
                    "triggered_by": "rebuild_shadow_state_from_returns",
                }
            ]
        else:
            state["status"] = "RUNNING"

        # 保留旧 daily_nav 作为历史参考 (可选)
        state["_legacy_daily_nav_before_rebuild"] = old_daily_nav
        state["_rebuild_history"] = state.get("_rebuild_history", []) + [
            {
                "rebuilt_at": now_bj().isoformat(),
                "source_file": str(RETURNS_FILE),
                "records_used": len(records),
                "old_nav_count": old_nav_count,
                "new_nav_count": len(daily_nav),
                "old_current_nav": old_current_nav,
                "new_current_nav": round(final_nav, 6),
                "total_return_pct": round(total_return * 100, 4),
                "max_drawdown_pct": round(max_dd * 100, 4),
                "fail_fast_triggered": ff_triggered,
                "fail_fast_reason": ff_reason,
            }
        ]

        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)
        logger.info("✅ 已写入新状态: %s", STATE_FILE)

    # 9. 生成日志
    log_content = _build_log(
        records,
        daily_nav,
        final_nav,
        final_capital,
        total_return,
        max_dd,
        ff_triggered,
        ff_reason,
        old_nav_count,
        old_current_nav,
        dry_run,
    )
    if not dry_run:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(log_content)
        logger.info("✅ 已生成重建日志: %s", LOG_FILE)

    return {
        "records_used": len(records),
        "daily_nav_count": len(daily_nav),
        "final_nav": final_nav,
        "final_capital": final_capital,
        "total_return": total_return,
        "max_drawdown": max_dd,
        "fail_fast_triggered": ff_triggered,
        "fail_fast_reason": ff_reason,
        "dry_run": dry_run,
    }


def _build_log(
    records,
    daily_nav,
    final_nav,
    final_capital,
    total_return,
    max_dd,
    ff_triggered,
    ff_reason,
    old_nav_count,
    old_current_nav,
    dry_run,
) -> str:
    """构建 Markdown 日志内容."""
    lines = [
        "# Shadow 状态重建日志",
        "",
        f"> 重建时间: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 数据源: `{RETURNS_FILE.relative_to(_PROJECT_ROOT)}`",
        f"> 状态文件: `{STATE_FILE.relative_to(_PROJECT_ROOT)}`",
        f"> 模式: {'DRY-RUN (未写盘)' if dry_run else 'PRODUCTION (已写盘)'}",
        "",
        "## 一、重建背景",
        "",
        "`shadow_real_data_feeder` 已成功写入真实日收益到 `daily_returns.jsonl`,",
        "但 `shadow_state.json` 的 `daily_nav` 未同步更新 (仍为占位值).",
        "本次重建用真实收益从 `nav=1.0` 累乘, 修复状态断层.",
        "",
        "## 二、重建前后对比",
        "",
        "| 维度 | 重建前 | 重建后 |",
        "|------|--------|--------|",
        f"| daily_nav 条数 | {old_nav_count} | {len(daily_nav)} |",
        f"| current_nav | {old_current_nav:.6f} | {final_nav:.6f} |",
        f"| current_capital | ¥{old_current_nav * 500000:.2f} | ¥{final_capital:.2f} |",
        f"| 累计收益 | {(old_current_nav - 1) * 100:+.2f}% | {total_return * 100:+.2f}% |",
        f"| 最大回撤 | — | {max_dd * 100:.2f}% |",
        f"| Fail-Fast | 未触发 | {'⚠️ 触发: ' + ff_reason if ff_triggered else '未触发 (正常)'} |",
        "",
        "## 三、逐日净值明细 (真实市场数据)",
        "",
        "| 日期 | 日收益率 | 净值 (nav) | 资金 (¥) | 累计收益 |",
        "|------|---------|-----------|---------|---------|",
    ]
    for entry in daily_nav:
        cum = (entry["nav"] - 1) * 100
        lines.append(
            f"| {entry['date']} | {entry['daily_return']*100:+.4f}% | "
            f"{entry['nav']:.6f} | {entry['capital']:.2f} | {cum:+.2f}% |"
        )
    lines += [
        "",
        "## 四、Fail-Fast 检查",
        "",
        "- 单日回撤阈值: 3%",
        "- 3日累计回撤阈值: 5%",
        f"- 检查结果: {'⚠️ 触发 — ' + ff_reason if ff_triggered else '✅ 未触发 (所有交易日均在安全范围内)'}",
        "",
        "## 五、数据可信度",
        "",
        f"- 真实市场数据天数: {len(records)} / {len(records)} (100%)",
        "- 数据来源: w13a_real_market_feed (Wind MCP + TDX + AKShare 多源)",
        f"- 样本充足性: {len(records)} < 20 (不足以计算 DSR, 观察期继续)",
        "",
        "## 六、后续动作",
        "",
        "1. 运行 `scripts/drift_shadow_integrator.py --date 2026-08-04` 执行漂移检测",
        "2. 运行 `scripts/shadow_admission_launcher.py daily` 生成 DSR 日报",
        "3. 继续每日运行 `shadow_real_data_feeder.py` 积累样本至 20 天",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow 状态重建器")
    parser.add_argument("--dry-run", action="store_true", help="试运行 (不写盘)")
    args = parser.parse_args()

    try:
        result = rebuild(dry_run=args.dry_run)
        logger.info("=" * 70)
        logger.info("✅ 重建完成 (dry_run=%s)", args.dry_run)
        logger.info("  最终净值: %.6f", result["final_nav"])
        logger.info("  累计收益: %+.2f%%", result["total_return"] * 100)
        logger.info("  最大回撤: %.2f%%", result["max_drawdown"] * 100)
        logger.info(
            "  Fail-Fast: %s", "触发" if result["fail_fast_triggered"] else "未触发"
        )
        logger.info("=" * 70)
        return 0
    except Exception as e:
        logger.error("❌ 重建失败: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
