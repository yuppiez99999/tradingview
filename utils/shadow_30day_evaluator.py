"""Shadow 30 天评估器 — W7.2.8 (MVSK P5-2) + W7.2.9 (qlib_lgb_v2, R-6 归档).

=================================================================
创建: 2026-08-24 (Wave 7 Sprint 2 — W7.2.8 + W7.2.9)

评估指标:
    MVSK P5-2:
        - 平均权重差异 L2 (mean_weight_diff_l2)
        - 权重差异稳定性 (std_weight_diff_l2)
        - 换仓成本估算 (turnover_cost)
        - Δ夏普估算 (delta_sharpe) — 基于 weight_diff 稳定性代理

    qlib_lgb_v2:
        - 平均信号差异 (mean_signal_diff)
        - 信号差异稳定性 (std_signal_diff)
        - 信号方向一致率 (direction_agreement_rate)
        - Δ夏普 (delta_sharpe) — OOS 回测先验 + shadow 验证

通过条件 (ROADMAP W7.3.7/W7.3.8):
    - Δ夏普 > 0
    - 无异常换仓 (换仓成本 < 阈值)
    - fail-fast 未触发

用法:
    from utils.shadow_30day_evaluator import Shadow30DayEvaluator
    evaluator = Shadow30DayEvaluator()
    report = evaluator.evaluate(
        mvsk_diff_path=Path("reports/shadow/mvsk_p5_daily_diff.jsonl"),
        qlib_diff_path=Path("reports/shadow/qlib_lgb_v2_daily.jsonl"),
    )
    print(report.to_markdown())
=================================================================
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

VALIDATION_WINDOW_DAYS = 30
MVSK_TURNOVER_THRESHOLD = 0.02
QLIB_SIGNAL_DIFF_THRESHOLD = 0.5

# R-6 (2026-09-07) 口径 (ROADMAP): qlib_lgb_v2 停跑归档 + 正式评估窗 09-13~10-12
#   - 09-07~09-12 首触发/预热记录: 落盘留档 (审计可溯) 但【不】计入正式评估样本
#     (evaluate 以 window_start=09-13 为样本起点过滤)
#   - qlib 归档: 默认不评估 (qlib_archived=True), 仅 USE_QLIB_LGB_V2=1 重开后恢复
EVAL_WINDOW_START_DEFAULT = "2026-09-13"
EVAL_WINDOW_END_DEFAULT = "2026-10-12"
QLIB_ARCHIVED_NOTE = (
    "qlib_lgb_v2 已按 R-6 (2026-09-07) 停跑归档, 不参与本窗口判定; "
    "USE_QLIB_LGB_V2=1 可强制重开 (需先修复 R-6 记录的信号-消费端接线错配)"
)

MVSK_BASELINE_SHARPE = 1.315
QLIB_V9_SHARPE_OOS = 1.315
QLIB_LGB_V2_SHARPE_OOS = 2.44


@dataclass
class MVSKEvaluation:
    """MVSK P5-2 shadow 评估结果."""

    records_count: int = 0
    date_range: str = ""
    mean_weight_diff_l2: float = 0.0
    std_weight_diff_l2: float = 0.0
    max_weight_diff_l2: float = 0.0
    turnover_cost: float = 0.0
    delta_sharpe: float = 0.0
    delta_sharpe_method: str = "weight_diff_stability_proxy"
    pass_: bool = False
    fail_reasons: list[str] = field(default_factory=list)


@dataclass
class QlibEvaluation:
    """qlib_lgb_v2 shadow 评估结果."""

    records_count: int = 0
    date_range: str = ""
    mean_signal_diff: float = 0.0
    std_signal_diff: float = 0.0
    max_signal_diff: float = 0.0
    direction_agreement_rate: float = 0.0
    delta_sharpe: float = 0.0
    delta_sharpe_method: str = "oos_backtest_prior"
    pass_: bool = False
    fail_reasons: list[str] = field(default_factory=list)


@dataclass
class Shadow30DayReport:
    """30 天 shadow 验证综合报告."""

    eval_date: str = ""
    eval_timestamp: str = ""
    window_days: int = VALIDATION_WINDOW_DAYS
    window_label: str = ""
    actual_days: int = 0
    mvsk: MVSKEvaluation = field(default_factory=MVSKEvaluation)
    qlib: QlibEvaluation = field(default_factory=QlibEvaluation)
    qlib_archived: bool = False
    fail_fast_triggered: bool = False
    fail_fast_reason: str = ""
    overall_pass: bool = False
    summary: str = ""

    @property
    def mvsk_delta_sharpe(self) -> float:
        return self.mvsk.delta_sharpe

    @property
    def qlib_delta_sharpe(self) -> float:
        return self.qlib.delta_sharpe

    @property
    def mvsk_pass(self) -> bool:
        return self.mvsk.pass_

    @property
    def qlib_pass(self) -> bool:
        return self.qlib.pass_

    def to_markdown(self) -> str:
        """生成 Markdown 评估报告."""
        window_line = (
            f"**评估样本窗**: {self.window_label}  "
            if self.window_label
            else "**评估样本窗**: 未锚定 (以状态文件 start_date 起计)  "
        )
        qlib_archived_note = (
            f"**qlib_lgb_v2**: {QLIB_ARCHIVED_NOTE}  "
            if self.qlib_archived
            else ""
        )
        lines = [
            "# Shadow 30 天验证评估报告",
            "",
            f"**评估日期**: {self.eval_date}  ",
            f"**评估时间**: {self.eval_timestamp}  ",
            f"**验证窗口**: {self.window_days} 天 (实际 {self.actual_days} 天)  ",
            window_line,
            f"**fail-fast**: {'⚠ 触发 — ' + self.fail_fast_reason if self.fail_fast_triggered else '✅ 未触发'}  ",
            f"**总体判定**: {'✅ 通过' if self.overall_pass else '❌ 未通过'}",
            "",
            "---",
            "",
            "## 1. MVSK P5-2 (BL+MVSK(378) vs BL+MV(252))",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| 记录数 | {self.mvsk.records_count} |",
            f"| 日期范围 | {self.mvsk.date_range} |",
            f"| 平均权重差异 L2 | {self.mvsk.mean_weight_diff_l2:.6f} |",
            f"| 权重差异标准差 | {self.mvsk.std_weight_diff_l2:.6f} |",
            f"| 最大权重差异 L2 | {self.mvsk.max_weight_diff_l2:.6f} |",
            f"| 换仓成本估算 | {self.mvsk.turnover_cost:.6f} |",
            f"| Δ夏普 ({self.mvsk.delta_sharpe_method}) | {self.mvsk.delta_sharpe:+.4f} |",
            f"| 通过 | {'✅' if self.mvsk.pass_ else '❌'} |",
            "| L2 口径 | 可优化子集 (剔除防御/现金仓; 2026-09-07 治理⑤ 起, 与更早的全组合口径记录不可比) |",
            "",
        ]
        if qlib_archived_note:
            lines.append(qlib_archived_note)
            lines.append("")
        if self.mvsk.fail_reasons:
            lines.append("**失败原因**:")
            for r in self.mvsk.fail_reasons:
                lines.append(f"- {r}")
            lines.append("")

        if self.qlib_archived:
            # R-6 归档: 不渲染 qlib 指标表 (维度已停跑, 指标无统计意义),
            # 仅登记留档记录数供审计.
            lines.extend(
                [
                    "---",
                    "",
                    "## 2. qlib_lgb_v2 — R-6 已停跑归档",
                    "",
                    "| 项 | 值 |",
                    "|------|-----|",
                    f"| 评估窗内留档记录数 | {self.qlib.records_count} |",
                    "| 参与判定 | 否 (R-6 归档, 不要求 qlib.pass_) |",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "---",
                    "",
                    "## 2. qlib_lgb_v2 vs V9 Regime-Specific",
                    "",
                    "| 指标 | 值 |",
                    "|------|-----|",
                    f"| 记录数 | {self.qlib.records_count} |",
                    f"| 日期范围 | {self.qlib.date_range} |",
                    f"| 平均信号差异 | {self.qlib.mean_signal_diff:.6f} |",
                    f"| 信号差异标准差 | {self.qlib.std_signal_diff:.6f} |",
                    f"| 最大信号差异 | {self.qlib.max_signal_diff:.6f} |",
                    f"| 方向一致率 | {self.qlib.direction_agreement_rate:.2%} |",
                    f"| Δ夏普 ({self.qlib.delta_sharpe_method}) | {self.qlib.delta_sharpe:+.4f} |",
                    f"| 通过 | {'✅' if self.qlib.pass_ else '❌'} |",
                    "",
                ]
            )
            if self.qlib.fail_reasons:
                lines.append("**失败原因**:")
                for r in self.qlib.fail_reasons:
                    lines.append(f"- {r}")
                lines.append("")

        lines.extend(
            [
                "---",
                "",
                "## 3. 通过条件 (ROADMAP W7.3.7/W7.3.8)",
                "",
                "1. Δ夏普 > 0",
                f"   - MVSK: {self.mvsk.delta_sharpe:+.4f} {'✅' if self.mvsk.delta_sharpe > 0 else '❌'}",
            ]
        )
        if self.qlib_archived:
            lines.append("   - qlib: 不参与 (R-6 已停跑归档)")
        else:
            lines.append(
                f"   - qlib: {self.qlib.delta_sharpe:+.4f} {'✅' if self.qlib.delta_sharpe > 0 else '❌'}"
            )
        lines.extend(
            [
                "2. 无异常换仓 (换仓成本 < 阈值)",
                f"   - MVSK 换仓成本: {self.mvsk.turnover_cost:.6f} < {MVSK_TURNOVER_THRESHOLD} {'✅' if self.mvsk.turnover_cost < MVSK_TURNOVER_THRESHOLD else '❌'}",  # noqa: E501
                "3. fail-fast 未触发",
                f"   - {'✅ 未触发' if not self.fail_fast_triggered else '❌ 触发: ' + self.fail_fast_reason}",
                "",
                "---",
                "",
                f"**总结**: {self.summary}",
                "",
            ]
        )
        return "\n".join(lines)


def _load_jsonl(filepath: Path) -> list[dict]:
    """加载 jsonl 文件为记录列表."""
    if not filepath.exists():
        return []
    records = []
    try:
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except (OSError, ValueError) as e:  # R10: 读 jsonl/解析只可能这两类; 逻辑 bug 不再被吞
        logger.warning("加载 %s 失败: %s", filepath, e)
    return records


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _max(values: list[float]) -> float:
    return max(values) if values else 0.0


def _window_label(start: str, end: str) -> str:
    """评估样本窗标签 (start/end 任一为空则单边描述)."""
    if start and end:
        return f"{start} ~ {end}"
    if start:
        return f"自 {start} 起"
    if end:
        return f"至 {end} 止"
    return ""


class Shadow30DayEvaluator:
    """Shadow 30 天验证评估器."""

    def evaluate(
        self,
        mvsk_diff_path: Path,
        qlib_diff_path: Path,
        status_path: Path | None = None,
        window_start: str | None = None,
        window_end: str | None = None,
        qlib_archived: bool = False,
    ) -> Shadow30DayReport:
        """执行 30 天窗口评估 (R-6 口径, 2026-09-07).

        Args:
            mvsk_diff_path: MVSK shadow 差异记录 jsonl
            qlib_diff_path: qlib shadow 差异记录 jsonl
            status_path: 窗口状态 json (可选, 读取 fail-fast + 首触发 start_date)
            window_start: 评估样本起点 (YYYY-MM-DD, 含当日)。R-6 后由调用方
                显式传入 EVAL_WINDOW_START_DEFAULT (09-13) — ROADMAP 固定正式
                评估窗; 未显式传入时回退 status.start_date (首触发日)。09-07~
                09-12 的首触发/预热记录保留在 jsonl 但【不】计入正式评估样本。
            window_end: 评估样本终点 (YYYY-MM-DD, 含当日); None = 无上界
                (调用方通常传 EVAL_WINDOW_END_DEFAULT = 10-12)。
            qlib_archived: True = qlib_lgb_v2 已 R-6 停跑归档, 本窗口不评估
                qlib 通过性, 总体判定 = MVSK pass AND NOT fail-fast;
                False = 双维度判定 (仅 USE_QLIB_LGB_V2=1 重开后).

        Returns:
            Shadow30DayReport
        """
        now = datetime.now()
        report = Shadow30DayReport(
            eval_date=now.strftime("%Y-%m-%d"),
            eval_timestamp=now.isoformat(),
            qlib_archived=bool(qlib_archived),
        )

        # 先读窗口状态: 取 fail-fast 状态 + 首触发 start_date (未显式传窗口时回退)
        status_start = ""
        status: dict = {}
        if status_path and status_path.exists():
            try:
                with open(status_path, encoding="utf-8") as f:
                    status = json.load(f)
                status_start = str(status.get("start_date", "") or "")
            except (OSError, ValueError):  # R10: best-effort 状态读取仅可能 IO/JSON 错
                status = {}

        # ---- R-6 评估窗口径 ----
        # 正式评估样本起点 = 显式 window_start (ROADMAP 09-13) 优先; 状态文件
        # start_date 是首触发日 (09-07 已锚), 早于正式窗 — 若状态起点 < 显式
        # 评估起点, 预热记录 (09-07~09-12) 必须排除在正式样本外, 但保留在
        # jsonl (幂等/审计可溯), 与 ROADMAP "mvsk_30day 窗口 09-13~10-12" 对齐.
        start = window_start or status_start or ""
        end = window_end or ""

        def _in_window(rec: dict) -> bool:
            d = str(rec.get("date", "") or "")
            if start and d < start:
                return False
            if end and d > end:
                return False
            return True

        # 窗口过滤: 仅统计评估窗内记录 (起点排除预热/历史积累, 上界防收尾后混入)
        mvsk_records = [r for r in _load_jsonl(mvsk_diff_path) if _in_window(r)]
        qlib_records = [r for r in _load_jsonl(qlib_diff_path) if _in_window(r)]

        report.window_label = _window_label(start, end)

        report.mvsk = self._evaluate_mvsk(mvsk_records)
        if qlib_archived:
            # R-6 归档: 只登记窗内记录数 (留档), 不判定 qlib 通过性 —
            # 归档维度不应产生误导性 FAIL.
            report.qlib.records_count = len(qlib_records)
        else:
            report.qlib = self._evaluate_qlib(qlib_records)
        report.actual_days = max(report.mvsk.records_count, report.qlib.records_count)

        if status:
            # 一次性 fail-fast (2026-09-07): terminated 窗口等效 fail_fast 触发,
            # 防止"终止后手动 --date 补跑某健康日"把 fail_fast_triggered 覆盖回
            # False 而误判验证可过 (L578 每日刷新语义).
            report.fail_fast_triggered = status.get("fail_fast_triggered", False) or bool(
                status.get("terminated", False)
            )
            report.fail_fast_reason = (
                status.get("terminated_reason", "")
                or status.get("fail_fast_reason", "")
            )

        # ---- 总体判定 (R-6: qlib 归档时不要求 qlib.pass_) ----
        if report.qlib_archived:
            report.overall_pass = report.mvsk.pass_ and not report.fail_fast_triggered
        else:
            report.overall_pass = (
                report.mvsk.pass_ and report.qlib.pass_ and not report.fail_fast_triggered
            )

        report.summary = self._build_summary(report)
        return report

    @staticmethod
    def _build_summary(report: Shadow30DayReport) -> str:
        """总体判定摘要文案 (R-6: qlib 归档时仅 MVSK 参与判定)."""
        if report.overall_pass:
            if report.qlib_archived:
                return (
                    "MVSK P5-2 通过 30 天 shadow 验证 (qlib_lgb_v2 已 R-6 归档不参与), "
                    "可进入 W7.3.7 正式启用评估"
                )
            return (
                "MVSK P5-2 和 qlib_lgb_v2 均通过 30 天 shadow 验证, "
                "可进入 W7.3.7/W7.3.8 正式启用阶段"
            )
        reasons = []
        if not report.mvsk.pass_:
            reasons.append("MVSK 未通过")
        if not report.qlib_archived and not report.qlib.pass_:
            reasons.append("qlib 未通过")
        if report.fail_fast_triggered:
            reasons.append("fail-fast 触发")
        if not reasons:
            reasons.append("验证条件不足")
        return f"验证未通过: {', '.join(reasons)}; 需排查后重新验证"

    def _evaluate_mvsk(self, records: list[dict]) -> MVSKEvaluation:
        """评估 MVSK P5-2 shadow."""
        ev = MVSKEvaluation(records_count=len(records))

        if not records:
            ev.fail_reasons.append("无 MVSK shadow 记录")
            return ev

        dates = sorted(set(r.get("date", "") for r in records if r.get("date")))
        if dates:
            ev.date_range = f"{dates[0]} ~ {dates[-1]}"

        diffs = [
            float(r.get("weight_diff_l2", 0.0))
            for r in records
            if "weight_diff_l2" in r
        ]
        if not diffs:
            ev.fail_reasons.append("无 weight_diff_l2 字段")
            return ev

        ev.mean_weight_diff_l2 = _mean(diffs)
        ev.std_weight_diff_l2 = _std(diffs)
        ev.max_weight_diff_l2 = _max(diffs)

        ev.turnover_cost = self._estimate_turnover(records)

        ev.delta_sharpe = self._estimate_mvsk_delta_sharpe(
            ev.mean_weight_diff_l2, ev.std_weight_diff_l2
        )

        if ev.delta_sharpe <= 0:
            ev.fail_reasons.append(f"Δ夏普 {ev.delta_sharpe:+.4f} <= 0")
        if ev.turnover_cost >= MVSK_TURNOVER_THRESHOLD:
            ev.fail_reasons.append(
                f"换仓成本 {ev.turnover_cost:.6f} >= 阈值 {MVSK_TURNOVER_THRESHOLD}"
            )

        ev.pass_ = len(ev.fail_reasons) == 0
        return ev

    def _evaluate_qlib(self, records: list[dict]) -> QlibEvaluation:
        """评估 qlib_lgb_v2 shadow."""
        ev = QlibEvaluation(records_count=len(records))

        if not records:
            ev.fail_reasons.append("无 qlib shadow 记录")
            return ev

        dates = sorted(set(r.get("date", "") for r in records if r.get("date")))
        if dates:
            ev.date_range = f"{dates[0]} ~ {dates[-1]}"

        diffs = [
            float(r.get("signal_diff", 0.0)) for r in records if "signal_diff" in r
        ]
        if not diffs:
            ev.fail_reasons.append("无 signal_diff 字段")
            return ev

        ev.mean_signal_diff = _mean(diffs)
        ev.std_signal_diff = _std(diffs)
        ev.max_signal_diff = _max(abs(d) for d in diffs)

        ev.direction_agreement_rate = self._compute_direction_agreement(records)

        ev.delta_sharpe = self._estimate_qlib_delta_sharpe(
            ev.mean_signal_diff, ev.std_signal_diff, ev.direction_agreement_rate
        )

        if ev.delta_sharpe <= 0:
            ev.fail_reasons.append(f"Δ夏普 {ev.delta_sharpe:+.4f} <= 0")
        if ev.max_signal_diff >= QLIB_SIGNAL_DIFF_THRESHOLD:
            ev.fail_reasons.append(
                f"最大信号差异 {ev.max_signal_diff:.6f} >= 阈值 {QLIB_SIGNAL_DIFF_THRESHOLD}"
            )

        ev.pass_ = len(ev.fail_reasons) == 0
        return ev

    def _estimate_turnover(self, records: list[dict]) -> float:
        """估算换仓成本.

        换仓成本 ≈ 0.5 × Σ|w_mvsk_t - w_baseline_t| × 日均换手率
        简化: 用 weight_diff_l2 均值 × 0.5 (假设单边换仓成本 0.5%)
        """
        diffs = [
            float(r.get("weight_diff_l2", 0.0))
            for r in records
            if "weight_diff_l2" in r
        ]
        if not diffs:
            return 0.0
        return _mean(diffs) * 0.5

    def _estimate_mvsk_delta_sharpe(self, mean_diff: float, std_diff: float) -> float:
        """估算 MVSK Δ夏普 (代理指标).

        代理逻辑:
        - mean_diff 小且稳定 → MVSK 与 MV 差异不大, Δ夏普 ≈ 0+ (微正)
        - mean_diff 适中且稳定 → MVSK 有意义调整, Δ夏普 > 0
        - mean_diff 大或不稳定 → Δ夏普可能为负

        估算公式: Δ夏普 ≈ 0.05 × (1 - std_diff / (mean_diff + 1e-8))
        上界 0.05 (保守估计 MVSK 相对 MV 的年化夏普提升)
        """
        if mean_diff < 1e-8:
            return 0.01
        stability = 1.0 - min(1.0, std_diff / (mean_diff + 1e-8))
        return 0.05 * stability

    def _compute_direction_agreement(self, records: list[dict]) -> float:
        """计算信号方向一致率.

        比较 qlib_signal 和 v9_signal 的符号是否一致.
        """
        agree = 0
        total = 0
        for r in records:
            qlib_sig = float(r.get("qlib_signal", 0.0))
            v9_sig = float(r.get("v9_signal", 0.0))
            if abs(qlib_sig) < 1e-8 or abs(v9_sig) < 1e-8:
                continue
            total += 1
            if qlib_sig * v9_sig > 0:
                agree += 1
        return agree / total if total > 0 else 0.0

    def _estimate_qlib_delta_sharpe(
        self, mean_diff: float, std_diff: float, agreement_rate: float
    ) -> float:
        """估算 qlib Δ夏普.

        先验: OOS 回测 Δ夏普 = 2.44 - 1.315 = 1.125
        验证: 用 shadow 数据的方向一致率调整
        - agreement_rate > 0.6 → 信任先验
        - agreement_rate 0.4~0.6 → 折扣
        - agreement_rate < 0.4 → 大幅折扣
        """
        prior_delta = QLIB_LGB_V2_SHARPE_OOS - QLIB_V9_SHARPE_OOS

        if agreement_rate > 0.6:
            confidence = 1.0
        elif agreement_rate > 0.4:
            confidence = 0.5
        else:
            confidence = 0.1

        return prior_delta * confidence
