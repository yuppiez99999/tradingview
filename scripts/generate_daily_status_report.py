"""S12 影子账户每日运行状态报告生成器 (Phase 3 P3.2 配套, 2026-09-02).

每交易日 17:10 (S12_Shadow_EOD 16:30 及其重试窗口之后) 由计划任务
S12_DailyReport 调用, 幂等生成当日运行状态报告 (同日重跑覆盖).

报告内容: 影子账户指标 / 回测基准对照 / 调度健康 / 里程碑 / 异常检查.
落盘: reports/<YYYY-MM-DD>/每日运行状态报告_<YYYYMMDD>.md

用法:
  python scripts/generate_daily_status_report.py            # 生成当日报告
  python scripts/generate_daily_status_report.py --print    # 生成并打印
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

STATE_PATH = _PROJECT_ROOT / "output" / "shadow_account" / "s12_shadow_state.json"
CONFIG_PATH = _PROJECT_ROOT / "config" / "s12_shadow_config.json"
SHADOW_TASK = "S12_Shadow_EOD"
HEALTH_SCORE_DIR = _PROJECT_ROOT / "reports" / "health_score"

TRADING_DAYS_TARGET = 30  # P3.2/P3.3 评估窗口 (交易日)
RISK_FREE_RATE = 0.02    # 年化无风险利率 (与回测一致)


def query_task_info(task_name: str) -> dict:
    """查询计划任务执行状态 (fail-open, 失败返回 {})."""
    ps = (
        f"(Get-ScheduledTaskInfo -TaskName '{task_name}') | "
        "Select-Object @{n='LastRunTime';e={$_.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')}}, "
        "LastTaskResult, "
        "@{n='NextRunTime';e={$_.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}}, "
        "NumberOfMissedRuns | ConvertTo-Json"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            out = json.loads(r.stdout)
            return out if isinstance(out, dict) else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return {}


# ============================================================
# 指标计算 (纯函数, 便于单测)
# ============================================================
def compute_metrics(daily_nav: list[dict]) -> dict:
    """从 daily_nav 序列算: 累计收益 / 最大回撤 / 年化 / Sharpe."""
    navs = [float(e["nav"]) for e in daily_nav]
    rets = [float(e["daily_return"]) for e in daily_nav if e.get("daily_return")]
    out = {
        "n_days": len(navs),
        "nav": navs[-1] if navs else 1.0,
        "total_return": (navs[-1] - 1.0) if navs else 0.0,
        "max_drawdown": 0.0,
        "annualized": None,
        "sharpe": None,
        "daily_ret_today": rets[-1] if rets else 0.0,
    }
    peak, mdd = 1.0, 0.0
    for n in navs:
        peak = max(peak, n)
        if peak > 0:
            mdd = max(mdd, (peak - n) / peak)
    out["max_drawdown"] = mdd
    if len(navs) >= 2 and navs[-1] > 0:
        out["annualized"] = navs[-1] ** (252 / len(navs)) - 1.0
    if len(rets) >= 10:
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        if sd > 1e-12:
            out["sharpe"] = (mean - RISK_FREE_RATE / 252) / sd * math.sqrt(252)
    return out


def next_rebalance_days(trading_day_count: int, rebal_freq: int = 21) -> int:
    """距下次再平衡的交易日数 (再平衡发生在 k 满足 (k-1)%freq==0 且 k>1)."""
    k = trading_day_count
    nxt = ((k - 1) // rebal_freq + 1) * rebal_freq + 1
    return nxt - k


def render_health_section(hs: dict) -> list[str]:
    """渲染系统健康评分节 (health_score JSON → markdown 行)."""
    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(hs.get("status"), "⚪")
    L = ["## 零、系统健康评分", ""]
    L.append(f"**{icon} {hs.get('total_score', 'N/A')} / 100 ({hs.get('status', 'N/A')})**")
    L.append("")
    L.append("| 维度 | 得分 | 权重 | 状态 |")
    L.append("|---|---|---|---|")
    for name, d in (hs.get("dimensions") or {}).items():
        if d.get("exempted"):
            st = "shadow豁免"
        elif d.get("degraded"):
            st = "degraded"
        else:
            st = "ok"
        L.append(f"| {name} | {d.get('score')} | {d.get('weight')} | {st} |")
    L.append("")
    if hs.get("degraded_dimensions"):
        L.append(f"> 降级维度: {', '.join(hs['degraded_dimensions'])} "
                 "(60 分中性值, 数据不可得)")
        L.append("")
    if hs.get("exempted_dimensions"):
        L.append(f"> 豁免维度: {', '.join(hs['exempted_dimensions'])} "
                 "(shadow 阶段主链产物不适用, 不计入总分)")
        L.append("")
    return L


def build_report(state: dict, config: dict, task_info: dict, run_date: str,
                 health_score: dict | None = None) -> str:
    """渲染当日状态报告 (纯函数)."""
    bench = config.get("backtest_benchmark", {})
    m = compute_metrics(state.get("daily_nav", []))
    recorded = state.get("recorded_dates", [])
    k = state.get("trading_day_count", 0)
    trades = state.get("trade_log", [])
    recent = trades[-3:] if trades else []
    weights = state.get("weights", {})
    src = state.get("price_source", "N/A")

    today_ok = run_date in recorded
    task_result = task_info.get("LastTaskResult")
    task_ok = task_result in (0, None)

    # 异常标记
    flags = []
    if not today_ok:
        flags.append("⚠️ 当日未记录 — 非交易日 / 数据未更新 / 任务失败, 次日自动补漏")
    if src == "akshare_sina_unadjusted":
        flags.append("⚠️ 数据源降级至 sina (未复权) — 关注后续 Wind 恢复")
    if task_result not in (0, None):
        flags.append(f"⚠️ S12_Shadow_EOD 上次退出码 {task_result} — 检查任务日志")
    if state.get("fail_fast_triggered"):
        flags.append("🚨 fail-fast 已触发 — 账户终止, 人工介入")
    if k >= TRADING_DAYS_TARGET:
        flags.append(f"✅ {TRADING_DAYS_TARGET} 交易日窗口已满 — 执行 P3.3 评估")

    L = []
    L.append(f"# 每日运行状态报告 — {run_date}")
    L.append("")
    L.append(f"> 生成时间: {now_bj().isoformat(timespec='seconds')} · "
             f"账户: {state.get('account_id', 'N/A')} · 数据源: {src}")
    L.append("")
    if health_score is not None:
        L.extend(render_health_section(health_score))
    L.append("## 一、影子账户 (Phase 3 进度)")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 交易日进度 | **{k} / {TRADING_DAYS_TARGET}** |")
    L.append(f"| NAV | {m['nav']:.6f} |")
    L.append(f"| 累计收益 | {m['total_return'] * 100:+.3f}% |")
    L.append(f"| 当日收益 | {m['daily_ret_today'] * 100:+.3f}% |")
    L.append(f"| 最大回撤 | {m['max_drawdown'] * 100:.3f}% |")
    if m["annualized"] is not None:
        L.append(f"| 年化 (外推, {m['n_days']} 日) | {m['annualized'] * 100:+.2f}% |")
    if m["sharpe"] is not None:
        L.append(f"| Sharpe (年化, ≥10 日起算) | {m['sharpe']:.3f} |")
    L.append(f"| 当前权重 | {weights} |")
    L.append(f"| 距下次再平衡 | {next_rebalance_days(k)} 交易日 |")
    L.append(f"| fail-fast | {'🚨 触发' if state.get('fail_fast_triggered') else '未触发'} |")
    L.append("")
    L.append("## 二、与回测基准对照")
    L.append("")
    L.append("| 指标 | 回测基准 (2021-2026) | 影子当前 |")
    L.append("|---|---|---|")
    ann_shadow = f"{m['annualized'] * 100:.2f}%" if m["annualized"] is not None else "N/A"
    sharpe_shadow = f"{m['sharpe']:.3f}" if m["sharpe"] is not None else "N/A"
    L.append(f"| 年化 | {bench.get('annual_return', 0) * 100:.2f}% | {ann_shadow} |")
    L.append(f"| 最大回撤 | {bench.get('max_drawdown', 0) * 100:.2f}% | {m['max_drawdown'] * 100:.2f}% |")
    L.append(f"| Sharpe | {bench.get('sharpe', 0):.2f} | {sharpe_shadow} |")
    L.append("")
    L.append("> 注: 30 交易日窗口过短, 影子年化/Sharpe 为外推参考, P3.3 验收以"
             "「累计收益正向 / 回撤<15% / 与回测偏差<20%」为准。")
    L.append("")
    L.append("## 三、调度健康")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| S12_Shadow_EOD 上次运行 | {task_info.get('LastRunTime', 'N/A')} |")
    L.append(f"| 上次退出码 | {task_result if task_result is not None else 'N/A'}"
             f" ({'OK' if task_ok else '异常'}) |")
    L.append(f"| 下次运行 | {task_info.get('NextRunTime', 'N/A')} |")
    L.append(f"| 错过次数 | {task_info.get('NumberOfMissedRuns', 'N/A')} |")
    L.append(f"| 当日记录 | {'✅ 已记录' if today_ok else '❌ 未记录'} |")
    L.append("")
    L.append("## 四、近期交易动作")
    L.append("")
    if recent:
        for t in recent:
            if t["action"] == "init":
                L.append(f"- {t['date']} init 等权建仓")
            else:
                L.append(f"- {t['date']} rebalance turnover={t['turnover']:.6f} "
                         f"cost={t['cost'] * 100:.4f}% → {t['new_weights']}")
    else:
        L.append("- 无 (等权建仓后每 21 交易日再平衡)")
    L.append("")
    L.append("## 五、异常检查")
    L.append("")
    if flags:
        L.extend(f"- {f}" for f in flags)
    else:
        L.append("- 无异常")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*自动生成: S12_DailyReport 计划任务 · "
             "状态源: output/shadow_account/s12_shadow_state.json · "
             "基准源: config/s12_shadow_config.json*")
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description="S12 影子账户每日状态报告")
    parser.add_argument("--print", action="store_true", help="同时打印到 stdout")
    args = parser.parse_args()

    run_date = now_bj().strftime("%Y-%m-%d")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    if not STATE_PATH.exists():
        print(f"[ERROR] 状态文件不存在: {STATE_PATH}", file=sys.stderr)
        return 1
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    task_info = query_task_info(SHADOW_TASK)

    health_score = None
    hs_path = HEALTH_SCORE_DIR / f"health_score_{run_date}.json"
    if hs_path.exists():
        try:
            health_score = json.loads(hs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            health_score = None

    report = build_report(state, config, task_info, run_date, health_score)

    out_dir = _PROJECT_ROOT / "reports" / run_date.replace("-", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"每日运行状态报告_{run_date.replace('-', '')}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"[OK] 报告落盘: {out_path}")
    if args.print:
        # Windows 控制台默认 GBK, 报告含 emoji (🔴 等) 时需 UTF-8 输出
        enc = (sys.stdout.encoding or "").lower()
        if enc not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
