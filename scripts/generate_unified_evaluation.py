"""统一评估周报告合并器 — 五源合并为一页决策材料 (2026-09-05, 策略排期 §七-3 / C5).

用途: 10-09 ~ 10-16 统一评估周产出一份合并评估报告, 作为生产切换窗
(01-02~01-09, R-3) 四项 Go/No-Go 的共同输入。

五源 (每源 best-effort 收集, 缺失/未满期标 PENDING, 不重复计算重活):
  1. P3.3 S12 影子评估   — 最新 p33_shadow_evaluation_*.json; 无则 state 快速指标
  2. MVSK P5-2 Δ对比     — shadow_30day_status.json 窗口进度 + mvsk diff 统计
  3. qlib W7.2.9         — 直接引用缺口分析结论 (双设计缺陷 FAIL, 2026-09-05)
  4. GNN S6 纸交易       — s6_paper_trading.jsonl 统计 (骨架比例暴露)
  5. Sprint 2 收尾       — 状态核对表 (人工核对项引用)

设计原则:
  - 只做收集与汇总, 不重复实现各线评估逻辑 (评估器各自独立不变, C5 口径)
  - 窗口未满 / 数据不足 → PENDING, 绝不臆造 PASS
  - qlib 线按 2026-09-05 缺口分析固定输出 FAIL(设计口径), 不再积累无效对比
  - fail-open: 任一源读取失败降级为该源 PENDING + 原因, 不阻塞其余源

用法:
  python scripts/generate_unified_evaluation.py            # 生成报告
  python scripts/generate_unified_evaluation.py --as-of 2026-10-13   # 指定评估基准日
输出: data/etf_option_backtest/unified_evaluation_<date>.md + .json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SHADOW_DIR = PROJECT_ROOT / "reports" / "shadow"
P33_DIR = PROJECT_ROOT / "data" / "etf_option_backtest"
S6_JSONL = PROJECT_ROOT / "reports" / "gnn_factor" / "s6_paper_trading.jsonl"
GAP_DOC = "docs/qlib_w729_gap_analysis_20260905.md"
OUT_DIR = P33_DIR

QLIB_VERDICT = ("FAIL", "双设计缺陷(接线错配+信号静态), Δ夏普对比统计上无意义 — 详见 " + GAP_DOC)


def _latest_p33_json() -> Path | None:
    matches = sorted(P33_DIR.glob("p33_shadow_evaluation_*.json"))
    return matches[-1] if matches else None


def collect_p33() -> dict:
    """源 1: P3.3 S12 — 最新 p33 JSON; 无则 state 快速指标."""
    jp = _latest_p33_json()
    if jp:
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            checks = {**data.get("checks", {}), **data.get("consistency_checks", {})}
            # forced 预演产物 (样本不足) 不构成 FAIL — 判定口径与 p33 脚本一致
            if data.get("forced"):
                return {
                    "verdict": "PENDING",
                    "detail": (
                        f"最新产物 {jp.name} 为 forced 预演 ({data.get('trading_days')} 交易日, "
                        "样本不足) — 正式评估待 30 交易日 (run_p33_evaluation.py)"
                    ),
                    "source": str(jp.name),
                }
            all_pass = bool(data.get("all_pass"))
            return {
                "verdict": "PASS" if all_pass else "FAIL",
                "detail": (
                    f"最新评估 {jp.name} ({data.get('trading_days')} 交易日, "
                    f"forced={data.get('forced')}) — 验收 {sum(1 for c in data.get('checks', {}).values() if c.get('pass'))}"
                    f"/{len(data.get('checks', {}))} + 一致性 "
                    f"{sum(1 for c in data.get('consistency_checks', {}).values() if c.get('pass'))}"
                    f"/{len(data.get('consistency_checks', {}))} PASS"
                ),
                "checks": {k: bool(v.get("pass")) for k, v in checks.items()},
                "source": str(jp.name),
            }
        except (json.JSONDecodeError, OSError):
            pass
    # 降级: state 快速指标
    state_path = PROJECT_ROOT / "output" / "shadow_account" / "s12_shadow_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            k = state.get("trading_day_count", 0)
            return {
                "verdict": "PENDING",
                "detail": f"尚无 p33 评估产物; 影子 {k}/30 交易日 — 评估待 run_p33_evaluation.py",
                "source": "s12_shadow_state.json",
            }
        except (json.JSONDecodeError, OSError):
            pass
    return {"verdict": "PENDING", "detail": "p33 产物与影子状态均不可读", "source": ""}


def collect_mvsk() -> dict:
    """源 2: MVSK P5-2 — 窗口进度 + weight_diff 统计."""
    status_path = SHADOW_DIR / "shadow_30day_status.json"
    diff_path = SHADOW_DIR / "mvsk_p5_daily_diff.jsonl"
    out = {"verdict": "PENDING", "detail": "", "source": ""}
    try:
        st = json.loads(status_path.read_text(encoding="utf-8"))
        elapsed = st.get("days_elapsed", 0)
        diffs = []
        if diff_path.exists():
            diffs = [
                float(json.loads(line).get("weight_diff_l2", 0.0))
                for line in diff_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        max_diff = max(diffs) if diffs else 0.0
        out["detail"] = (
            f"窗口 {elapsed}/30 交易日 (start={st.get('start_date') or '未启动'}), "
            f"diff 记录 {len(diffs)} 条 / max weight_diff_l2 {max_diff:.4f}, "
            f"fail_fast={st.get('fail_fast_triggered')}"
        )
        if elapsed >= 30 and not st.get("fail_fast_triggered"):
            out["verdict"] = "READY"  # 数据就绪, Δ夏普判定交评估周人工/评估器
        out["source"] = "shadow_30day_status.json"
    except (json.JSONDecodeError, OSError) as e:
        out["detail"] = f"读取失败: {e}"
    return out


def collect_qlib() -> dict:
    """源 3: qlib — 固定引用缺口分析结论 (2026-09-05)."""
    n = 0
    try:
        qp = SHADOW_DIR / "qlib_lgb_v2_daily.jsonl"
        n = sum(1 for line in qp.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        pass
    return {
        "verdict": QLIB_VERDICT[0],
        "detail": QLIB_VERDICT[1] + f" (shadow 记录 {n} 条为降级路径审计痕迹)",
        "source": GAP_DOC,
    }


def collect_s6() -> dict:
    """源 4: GNN S6 — 记录统计 + 骨架比例暴露 (qlib 同型风险)."""
    try:
        lines = [json.loads(line) for line in S6_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as e:
        return {"verdict": "PENDING", "detail": f"jsonl 读取失败: {e}", "source": ""}
    if not lines:
        return {"verdict": "PENDING", "detail": "无记录 (09-13 cron 启动后产出)", "source": ""}
    skeleton = sum(1 for r in lines if r.get("status") == "skeleton")
    real = len(lines) - skeleton
    dates = [r.get("date", "") for r in lines if r.get("date")]
    span = f"{min(dates)}~{max(dates)}" if dates else ""
    if real == 0:
        return {
            "verdict": "PENDING",
            "detail": (
                f"记录 {len(lines)} 条全部为 skeleton 降级模式 (因子数据不可用) "
                f"({span}) — 若 09-13 后仍全骨架, S6 线 30 天观察将无效 (qlib 同型风险)"
            ),
            "source": str(S6_JSONL.relative_to(PROJECT_ROOT)),
        }
    return {
        "verdict": "PENDING",
        "detail": (
            f"记录 {len(lines)} 条 (真实 {real} / 骨架 {skeleton}) {span} — 30 天观察满期后由评估周判定 CPCV/稳定性"
        ),
        "source": str(S6_JSONL.relative_to(PROJECT_ROOT)),
    }


def collect_sprint2() -> dict:
    """源 5: Sprint 2 收尾 — 状态核对表 (人工核对项)."""
    return {
        "verdict": "MANUAL",
        "detail": (
            "人工核对项 (ROADMAP §CURRENT STATE 引用): T15-T18 实盘四件套模块就绪(08-12) / "
            "T15 QMT paper (W7.2.1, 09-13~09-26 执行) / 工程基础层 Phase 0-1 "
            "(W7.2.6 ✅ 08-27) / Sprint 2 收尾判定 10-12"
        ),
        "source": "cairn/ROADMAP.md",
    }


def build_report(sources: dict, as_of: str) -> str:
    L = []
    L.append("=" * 78)
    L.append("  统一评估周合并报告 (10-09 ~ 10-16 → 生产切换窗 Go/No-Go 共同输入)")
    L.append("=" * 78)
    L.append(f"  基准日: {as_of} | 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    L.append("")
    L.append("  === 五源判定汇总 ===")
    for name, s in sources.items():
        L.append(f"  [{s['verdict']:<7}] {name}: {s['detail']}")
    L.append("")
    n_pass = sum(1 for s in sources.values() if s["verdict"] == "PASS")
    n_fail = sum(1 for s in sources.values() if s["verdict"] == "FAIL")
    L.append("  === 汇总口径 ===")
    L.append(f"  PASS {n_pass} / FAIL {n_fail} / PENDING+MANUAL {len(sources) - n_pass - n_fail}")
    L.append("  - FAIL 项 = 切换窗该项不实施 (qlib: 不切换; 其余按各线 Kill Criteria)")
    L.append("  - PENDING 项 = 窗口未满/数据不足, 不构成 FAIL, 评估周复核")
    L.append("  - 本报告不改变任何 RELEASE GATE (Release Gate != Research Gate 铁律)")
    L.append("=" * 78)
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description="统一评估周合并报告")
    parser.add_argument("--as-of", default="", help="评估基准日 YYYY-MM-DD (默认今天)")
    args = parser.parse_args()
    as_of = args.as_of or _date.today().isoformat()

    sources = {
        "P3.3_S12影子评估": collect_p33(),
        "MVSK_P5-2": collect_mvsk(),
        "qlib_W7.2.9": collect_qlib(),
        "GNN_S6纸交易": collect_s6(),
        "Sprint2收尾": collect_sprint2(),
    }
    report = build_report(sources, as_of)
    print(report)  # allow-print

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mp = OUT_DIR / f"unified_evaluation_{as_of.replace('-', '')}_{ts}.md"
    mp.write_text(report, encoding="utf-8")
    jp = OUT_DIR / f"unified_evaluation_{as_of.replace('-', '')}_{ts}.json"
    jp.write_text(
        json.dumps(
            {"as_of": as_of, "generated_at": datetime.now().isoformat(), "sources": sources},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"报告: {mp.name} / {jp.name}")  # allow-print
    return 0


if __name__ == "__main__":
    sys.exit(main())
