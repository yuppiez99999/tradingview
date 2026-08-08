"""自检归档 diff 工具 — 三层面自我进化 Stage 2 增强工具.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第2阶段 (2.7)
用途: 对比两份 system_check 归档报告, 识别回归点 (PASS→FAIL) 与恢复点 (FAIL→PASS)

设计原则:
    1. 只读归档: 不修改 reports/system_check/ 下任何文件 (HC-4)
    2. 容错降级: 解析失败返回空 diff, 不阻塞上游
    3. 结构化输出: CheckDiff 含 regressions/recoveries/stable 三类
    4. 不可变: CheckDiff / CheckItem frozen=True
    5. 可选集成: OpsDiagnoser / CodeDiagnoser 可调用 to_root_causes() 补充诊断

对比维度 (CLAUDE.md §8.3 "出问题时对比昨天哪些项是 PASS, 今天变 FAIL"):
    - regressions: PASS → FAIL (回归, 最关键, 优先转 RootCause)
    - recoveries:  FAIL → PASS (恢复, 记录成功模式)
    - new_failures: 旧报告无此 code + 新报告 FAIL (新增失败项)
    - stable:       状态未变 (含持续 FAIL / 持续 PASS)

归档 JSON schema (scripts/run_p0_startup_check.py --archive):
    {
      "check_time": "2026-07-31T12:43:44",
      "total": 40, "passed": 35, "failed": 5, "blocking_failures": 0,
      "results": [
        {"code": "C1.1", "name": "...", "level": "ERROR",
         "status": "PASS", "detail": "...", "remediation": ""},
        ...
      ]
    }

用法:
    from utils.alpha.layers.system_check_diff import SystemCheckDiff

    differ = SystemCheckDiff()
    # 方式1: 直接对比两个归档文件
    diff = differ.diff_archives(old_path, new_path)
    # 方式2: 对比最近 N 天 (默认对比 最新 vs 1天前)
    diff = differ.diff_recent(archives_dir, days_ago=1)
    # 转 RootCause (仅回归项)
    causes = differ.to_root_causes(diff)
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.root_cause import (  # noqa: E402
    ACTION_MANUAL,
    LAYER_CODE,
    LAYER_OPS,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    FixSuggestion,
    RootCause,
)

# ============================================================
# 常量
# ============================================================

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"

# C* 检查项前缀 → 层面映射 (用于 RootCause.layer 归属)
# C1 文件存在性 / C4 Schema / C5 依赖 / C7 模块导入 → 代码层
# C2 环境变量 / C3 数据源 / C6 权限 / C8 历史完整性 → 运维层
_PREFIX_LAYER_MAP: dict[str, str] = {
    "C1": LAYER_CODE,
    "C2": LAYER_OPS,
    "C3": LAYER_OPS,
    "C4": LAYER_CODE,
    "C5": LAYER_CODE,
    "C6": LAYER_OPS,
    "C7": LAYER_CODE,
    "C8": LAYER_OPS,
}

# ERROR 级别回归 → severity 映射
# C3.* 数据源失败单独提级为 high (影响实时交易)
_CRITICAL_PREFIXES = {"C3"}  # 数据源失败影响面大


# ============================================================
# 数据类 (不可变)
# ============================================================


@dataclass(frozen=True)
class CheckItem:
    """单条检查项快照 (不可变).

    Attributes:
        code: 检查代码 (如 "C1.1")
        name: 检查项名称
        level: 级别 (ERROR/WARN/INFO)
        status: 状态 (PASS/FAIL)
        detail: 详情
        remediation: 修复建议
    """
    code: str
    name: str = ""
    level: str = ""
    status: str = ""
    detail: str = ""
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "level": self.level,
            "status": self.status,
            "detail": self.detail,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class CheckDiff:
    """两份自检报告的对比结果 (不可变).

    Attributes:
        old_check_time: 旧报告检查时间
        new_check_time: 新报告检查时间
        regressions: 回归项 (PASS → FAIL), 最关键
        recoveries: 恢复项 (FAIL → PASS)
        new_failures: 新增失败项 (旧报告无此 code + 新报告 FAIL)
        stable_pass: 持续 PASS 项
        stable_fail: 持续 FAIL 项 (未变化, 但仍失败)
        summary: 一句话摘要
    """
    old_check_time: str = ""
    new_check_time: str = ""
    regressions: list[CheckItem] = field(default_factory=list)
    recoveries: list[CheckItem] = field(default_factory=list)
    new_failures: list[CheckItem] = field(default_factory=list)
    stable_pass: list[CheckItem] = field(default_factory=list)
    stable_fail: list[CheckItem] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_check_time": self.old_check_time,
            "new_check_time": self.new_check_time,
            "regressions": [c.to_dict() for c in self.regressions],
            "recoveries": [c.to_dict() for c in self.recoveries],
            "new_failures": [c.to_dict() for c in self.new_failures],
            "stable_pass_count": len(self.stable_pass),
            "stable_fail_count": len(self.stable_fail),
            "summary": self.summary,
        }

    @property
    def has_regressions(self) -> bool:
        """是否存在回归."""
        return len(self.regressions) > 0 or len(self.new_failures) > 0


# ============================================================
# Diff 工具
# ============================================================


class SystemCheckDiff:
    """自检归档对比工具 (只读, 不修改归档文件).

    接口:
        diff_archives(old_path, new_path) → CheckDiff
        diff_recent(archives_dir, days_ago=1) → CheckDiff
        to_root_causes(diff) → List[RootCause]
        parse_archive(path) → Dict (解析单份归档)
    """

    def __init__(self) -> None:
        """初始化 (无状态)."""
        pass

    # ============================================================
    # 归档解析
    # ============================================================
    @staticmethod
    def parse_archive(path: Path) -> dict[str, Any] | None:
        """解析单份自检归档 JSON.

        Args:
            path: 归档文件路径

        Returns:
            dict (含 check_time / results 等), 失败返回 None
        """
        try:
            if not path.exists():
                logger.warning("自检归档不存在: %s", path)
                return None
            text = path.read_text(encoding="utf-8", errors="ignore")
            data = json.loads(text)
            if not isinstance(data, dict):
                logger.warning("自检归档非 dict: %s", path)
                return None
            return data
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("解析自检归档失败 %s: %s", path, e)
            return None

    @staticmethod
    def _extract_items(report: dict[str, Any]) -> dict[str, CheckItem]:
        """从报告 dict 提取 CheckItem 字典 (按 code 索引).

        Args:
            report: parse_archive 返回的 dict

        Returns:
            {code: CheckItem}, 解析失败返回空 dict
        """
        items: dict[str, CheckItem] = {}
        try:
            results = report.get("results", [])
            if not isinstance(results, list):
                return items
            for r in results:
                if not isinstance(r, dict):
                    continue
                code = str(r.get("code", "")).strip()
                if not code:
                    continue
                try:
                    items[code] = CheckItem(
                        code=code,
                        name=str(r.get("name", "")),
                        level=str(r.get("level", "")),
                        status=str(r.get("status", "")).upper(),
                        detail=str(r.get("detail", "")),
                        remediation=str(r.get("remediation", "")),
                    )
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    continue
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("提取检查项失败 (降级为空): %s", e)
        return items

    # ============================================================
    # 核心: diff 两份报告
    # ============================================================
    def diff(
        self,
        old_report: dict[str, Any],
        new_report: dict[str, Any],
    ) -> CheckDiff:
        """对比两份自检报告 dict.

        Args:
            old_report: 旧报告 (parse_archive 返回值)
            new_report: 新报告

        Returns:
            CheckDiff (不可变)
        """
        old_time = str(old_report.get("check_time", ""))
        new_time = str(new_report.get("check_time", ""))

        old_items = self._extract_items(old_report)
        new_items = self._extract_items(new_report)

        regressions: list[CheckItem] = []
        recoveries: list[CheckItem] = []
        new_failures: list[CheckItem] = []
        stable_pass: list[CheckItem] = []
        stable_fail: list[CheckItem] = []

        all_codes = set(old_items.keys()) | set(new_items.keys())
        for code in sorted(all_codes):
            old_it = old_items.get(code)
            new_it = new_items.get(code)
            # 新报告无此项 → 跳过 (检查项被移除)
            if new_it is None:
                continue
            old_status = old_it.status if old_it else ""
            new_status = new_it.status

            if old_it is None:
                # 旧报告无此 code
                if new_status == STATUS_FAIL:
                    new_failures.append(new_it)
                else:
                    stable_pass.append(new_it)
            elif old_status == STATUS_PASS and new_status == STATUS_FAIL:
                # 回归 (最关键)
                regressions.append(new_it)
            elif old_status == STATUS_FAIL and new_status == STATUS_PASS:
                # 恢复
                recoveries.append(new_it)
            elif new_status == STATUS_PASS:
                stable_pass.append(new_it)
            else:
                stable_fail.append(new_it)

        summary = self._build_summary(regressions, recoveries, new_failures)
        return CheckDiff(
            old_check_time=old_time,
            new_check_time=new_time,
            regressions=regressions,
            recoveries=recoveries,
            new_failures=new_failures,
            stable_pass=stable_pass,
            stable_fail=stable_fail,
            summary=summary,
        )

    def diff_archives(
        self, old_path: Path, new_path: Path
    ) -> CheckDiff:
        """对比两个归档文件.

        Args:
            old_path: 旧归档路径
            new_path: 新归档路径

        Returns:
            CheckDiff (任一文件解析失败返回空 diff)
        """
        old_report = self.parse_archive(old_path)
        new_report = self.parse_archive(new_path)
        if old_report is None or new_report is None:
            logger.warning(
                "归档对比失败 (old=%s, new=%s), 返回空 diff",
                old_report is not None, new_report is not None,
            )
            return CheckDiff(
                old_check_time=str(old_report.get("check_time", "")) if old_report else "",
                new_check_time=str(new_report.get("check_time", "")) if new_report else "",
                summary="DIFF_FAILED: 归档解析失败",
            )
        return self.diff(old_report, new_report)

    def diff_recent(
        self,
        archives_dir: Path,
        days_ago: int = 1,
    ) -> CheckDiff:
        """对比最近归档 vs N 天前的归档.

        按 CLAUDE.md §8.3 用途: "对比昨天哪些项是 PASS, 今天变 FAIL".

        Args:
            archives_dir: 归档目录 (reports/system_check/)
            days_ago: 对比多少天前 (默认 1 = 昨天)

        Returns:
            CheckDiff. 归档不足 2 份时返回空 diff (不抛异常).
        """
        try:
            if not archives_dir.exists():
                return CheckDiff(summary=f"DIFF_SKIPPED: 目录不存在 {archives_dir}")
            archives = sorted(archives_dir.glob("system_check_*.json"))
            if len(archives) < 2:
                return CheckDiff(
                    summary=f"DIFF_SKIPPED: 归档不足 2 份 (实际 {len(archives)})"
                )
            new_path = archives[-1]  # 最新
            # 尝试找 N 天前的归档; 若不足则取最早一份
            old_path = archives[-1 - days_ago] if len(archives) > days_ago else archives[0]
            return self.diff_archives(old_path, new_path)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("diff_recent 失败 (降级为空): %s", e)
            return CheckDiff(summary=f"DIFF_FAILED: {e}")

    # ============================================================
    # 转 RootCause (仅回归 + 新增失败)
    # ============================================================
    def to_root_causes(
        self,
        diff: CheckDiff,
        now: str | None = None,
    ) -> list[RootCause]:
        """将 diff 的回归项转为 RootCause (供 OpsDiagnoser/CodeDiagnoser 使用).

        仅转换 regressions + new_failures (recoveries 不产生根因).

        Args:
            diff: CheckDiff 对比结果
            now: 检测时间 (None=当前时间)

        Returns:
            List[RootCause]
        """
        if now is None:
            now = datetime.now(timezone.utc).isoformat()

        causes: list[RootCause] = []
        # 回归项 (PASS→FAIL) 优先, 置信度更高
        for item in diff.regressions:
            cause = self._item_to_root_cause(item, now, is_regression=True)
            if cause is not None:
                causes.append(cause)
        # 新增失败项
        for item in diff.new_failures:
            cause = self._item_to_root_cause(item, now, is_regression=False)
            if cause is not None:
                causes.append(cause)
        return causes

    @staticmethod
    def _item_to_root_cause(
        item: CheckItem, now: str, is_regression: bool
    ) -> RootCause | None:
        """单条 CheckItem → RootCause.

        Args:
            item: 检查项
            now: 检测时间
            is_regression: True=回归 (PASS→FAIL), False=新增失败
        """
        try:
            prefix = item.code.split(".")[0] if "." in item.code else item.code
            layer = _PREFIX_LAYER_MAP.get(prefix, LAYER_OPS)
            # severity 推断
            if is_regression:
                # 回归项: ERROR+critical_prefix → critical, 否则按 level
                if prefix in _CRITICAL_PREFIXES or item.level == "ERROR":
                    severity = SEVERITY_CRITICAL
                else:
                    severity = SEVERITY_HIGH
                confidence = 0.9  # 回归置信度高 (有历史对照)
                category = "system_check_regression"
            else:
                # 新增失败项: 按 level 推断
                if item.level == "ERROR":
                    severity = SEVERITY_HIGH
                elif item.level == "WARN":
                    severity = SEVERITY_MEDIUM
                else:
                    severity = SEVERITY_LOW
                confidence = 0.7
                category = "system_check_fail"

            # 数据源失败单独标记 (供 CausalChain 识别)
            if prefix == "C3":
                category = "datasource_fail"

            cause_id = (
                f"{'regression' if is_regression else 'newfail'}-"
                f"{item.code}-{now}"
            )
            return RootCause(
                cause_id=cause_id,
                layer=layer,
                category=category,
                severity=severity,
                evidence={
                    "check_code": item.code,
                    "check_name": item.name,
                    "level": item.level,
                    "old_status": STATUS_PASS if is_regression else "ABSENT",
                    "new_status": STATUS_FAIL,
                    "detail": item.detail,
                    "remediation": item.remediation,
                    "is_regression": is_regression,
                    "source": "system_check_diff",
                },
                suggested_fix=FixSuggestion(
                    action_type=ACTION_MANUAL,
                    target_file="",
                    description=(
                        f"自检回归 {item.code} ({item.name}): "
                        f"{item.detail}"
                    ),
                    estimated_risk=0.6,
                    requires_human_approval=True,
                    remediation_commands=[
                        f"# 修复 {item.code}: {item.remediation or item.detail}",
                    ],
                ),
                confidence=confidence,
                detected_at=now,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("CheckItem 转 RootCause 失败 (跳过 %s): %s", item.code, e)
            return None

    # ============================================================
    # 摘要
    # ============================================================
    @staticmethod
    def _build_summary(
        regressions: list[CheckItem],
        recoveries: list[CheckItem],
        new_failures: list[CheckItem],
    ) -> str:
        """构建一句话摘要."""
        if not regressions and not new_failures and not recoveries:
            return "无回归无恢复 (状态稳定)"
        parts: list[str] = []
        if regressions:
            codes = ", ".join(c.code for c in regressions[:5])
            parts.append(f"{len(regressions)} 回归 [{codes}]")
        if new_failures:
            codes = ", ".join(c.code for c in new_failures[:5])
            parts.append(f"{len(new_failures)} 新失败 [{codes}]")
        if recoveries:
            parts.append(f"{len(recoveries)} 恢复")
        return " | ".join(parts)
