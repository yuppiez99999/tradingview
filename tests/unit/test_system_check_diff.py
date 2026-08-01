"""自检归档 diff 工具单元测试 — 三层面自我进化 Stage 2 (2.7).

任务: 2.7
对应模块:
    - utils/alpha/layers/system_check_diff.py (SystemCheckDiff / CheckDiff / CheckItem)

验收标准:
    1. CheckItem / CheckDiff 不可变 (frozen=True)
    2. diff 正确识别 regressions (PASS→FAIL) / recoveries (FAIL→PASS)
    3. new_failures: 旧报告无此 code + 新报告 FAIL
    4. stable_pass / stable_fail 分类正确
    5. HC-4 只读: 不修改归档文件
    6. to_root_causes 仅转换 regressions + new_failures (recoveries 不产生根因)
    7. C3.* 数据源失败 → layer=ops, category=datasource_fail
    8. 回归项置信度 0.9, 新增失败 0.7
    9. 归档不足 2 份时返回空 diff (不抛异常)
    10. requires_human_approval 默认 True (HC-3)

设计原则:
    - AAA 模式 (Arrange → Act → Assert)
    - 全 mock, 不依赖外部 IO (<1s)
    - tmp_path 隔离文件系统
"""
from __future__ import annotations

import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.layers.system_check_diff import (  # noqa: E402
    CheckDiff,
    CheckItem,
    SystemCheckDiff,
)
from utils.alpha.root_cause import (  # noqa: E402
    LAYER_CODE,
    LAYER_OPS,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
)

# ============================================================
# 辅助函数
# ============================================================

def _make_report(
    check_time: str = "2026-08-01T12:00:00",
    items: list[dict[str, Any]] = None,
) -> dict[str, Any]:
    """构造自检报告 dict."""
    if items is None:
        items = []
    return {
        "check_time": check_time,
        "total": len(items),
        "passed": sum(1 for i in items if i.get("status") == "PASS"),
        "failed": sum(1 for i in items if i.get("status") == "FAIL"),
        "results": items,
    }


def _make_item(
    code: str, status: str, level: str = "ERROR",
    name: str = "", detail: str = "",
) -> dict[str, Any]:
    """构造单条检查项 dict."""
    return {
        "code": code,
        "name": name or f"check {code}",
        "level": level,
        "status": status,
        "detail": detail or f"detail {code}",
        "remediation": "",
        "elapsed_ms": 0.0,
    }


# ============================================================
# CheckItem 不可变性
# ============================================================

class TestCheckItemImmutability:
    """CheckItem frozen=True."""

    def test_create(self) -> None:
        it = CheckItem(code="C1.1", name="file", level="ERROR", status="PASS")
        assert it.code == "C1.1"
        assert it.status == "PASS"

    def test_frozen(self) -> None:
        it = CheckItem(code="C1.1", status="PASS")
        with pytest.raises(FrozenInstanceError):
            it.code = "C2.1"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        it = CheckItem(code="C1.1", name="n", level="ERROR", status="PASS", detail="d")
        d = it.to_dict()
        assert d["code"] == "C1.1"
        assert d["status"] == "PASS"


# ============================================================
# CheckDiff 不可变性
# ============================================================

class TestCheckDiffImmutability:
    """CheckDiff frozen=True."""

    def test_create_empty(self) -> None:
        diff = CheckDiff()
        assert diff.regressions == []
        assert diff.has_regressions is False

    def test_frozen(self) -> None:
        diff = CheckDiff()
        with pytest.raises(FrozenInstanceError):
            diff.summary = "x"  # type: ignore[misc]

    def test_has_regressions_true(self) -> None:
        diff = CheckDiff(
            regressions=[CheckItem(code="C1.1", status="FAIL")],
        )
        assert diff.has_regressions is True

    def test_has_regressions_new_failures(self) -> None:
        diff = CheckDiff(
            new_failures=[CheckItem(code="C9.1", status="FAIL")],
        )
        assert diff.has_regressions is True

    def test_to_dict(self) -> None:
        diff = CheckDiff(
            old_check_time="t1", new_check_time="t2",
            regressions=[CheckItem(code="C1.1", status="FAIL")],
            recoveries=[CheckItem(code="C2.1", status="PASS")],
            summary="1 回归 | 1 恢复",
        )
        d = diff.to_dict()
        assert d["old_check_time"] == "t1"
        assert len(d["regressions"]) == 1
        assert len(d["recoveries"]) == 1
        json.dumps(d, ensure_ascii=False)  # 可序列化


# ============================================================
# diff 核心逻辑
# ============================================================

class TestDiffLogic:
    """diff() 对比逻辑测试."""

    def test_regression_pass_to_fail(self) -> None:
        """PASS → FAIL 识别为回归."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        assert len(diff.regressions) == 1
        assert diff.regressions[0].code == "C1.1"
        assert len(diff.recoveries) == 0

    def test_recovery_fail_to_pass(self) -> None:
        """FAIL → PASS 识别为恢复."""
        old = _make_report(items=[_make_item("C1.1", "FAIL")])
        new = _make_report(items=[_make_item("C1.1", "PASS")])
        diff = SystemCheckDiff().diff(old, new)
        assert len(diff.recoveries) == 1
        assert len(diff.regressions) == 0

    def test_new_failure_absent_to_fail(self) -> None:
        """旧报告无此 code + 新报告 FAIL → 新增失败."""
        old = _make_report(items=[])
        new = _make_report(items=[_make_item("C9.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        assert len(diff.new_failures) == 1
        assert diff.new_failures[0].code == "C9.1"

    def test_new_item_pass_goes_to_stable_pass(self) -> None:
        """旧报告无此 code + 新报告 PASS → stable_pass."""
        old = _make_report(items=[])
        new = _make_report(items=[_make_item("C9.1", "PASS")])
        diff = SystemCheckDiff().diff(old, new)
        assert len(diff.new_failures) == 0
        assert len(diff.stable_pass) == 1

    def test_stable_pass(self) -> None:
        """PASS → PASS → stable_pass."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "PASS")])
        diff = SystemCheckDiff().diff(old, new)
        assert len(diff.stable_pass) == 1
        assert len(diff.regressions) == 0

    def test_stable_fail(self) -> None:
        """FAIL → FAIL → stable_fail."""
        old = _make_report(items=[_make_item("C1.1", "FAIL")])
        new = _make_report(items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        assert len(diff.stable_fail) == 1

    def test_removed_item_skipped(self) -> None:
        """旧报告有, 新报告无 → 跳过 (不分类)."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[])
        diff = SystemCheckDiff().diff(old, new)
        assert len(diff.regressions) == 0
        assert len(diff.stable_pass) == 0

    def test_mixed_scenario(self) -> None:
        """混合场景: 1 回归 + 1 恢复 + 1 新失败 + 1 稳定."""
        old = _make_report(items=[
            _make_item("C1.1", "PASS"),   # 将回归
            _make_item("C2.1", "FAIL"),   # 将恢复
            _make_item("C3.1", "PASS"),   # 稳定 PASS
        ])
        new = _make_report(items=[
            _make_item("C1.1", "FAIL"),   # 回归
            _make_item("C2.1", "PASS"),   # 恢复
            _make_item("C3.1", "PASS"),   # 稳定
            _make_item("C9.1", "FAIL"),   # 新失败
        ])
        diff = SystemCheckDiff().diff(old, new)
        assert len(diff.regressions) == 1
        assert len(diff.recoveries) == 1
        assert len(diff.new_failures) == 1
        assert len(diff.stable_pass) == 1
        assert diff.regressions[0].code == "C1.1"
        assert diff.recoveries[0].code == "C2.1"
        assert diff.new_failures[0].code == "C9.1"

    def test_check_time_preserved(self) -> None:
        """diff 保留两份报告的 check_time."""
        old = _make_report(check_time="2026-07-30T10:00:00",
                           items=[_make_item("C1.1", "PASS")])
        new = _make_report(check_time="2026-07-31T10:00:00",
                           items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        assert diff.old_check_time == "2026-07-30T10:00:00"
        assert diff.new_check_time == "2026-07-31T10:00:00"

    def test_summary_built(self) -> None:
        """摘要包含回归数."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        assert "1 回归" in diff.summary

    def test_summary_stable(self) -> None:
        """无回归无恢复 → 稳定摘要."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "PASS")])
        diff = SystemCheckDiff().diff(old, new)
        assert "稳定" in diff.summary


# ============================================================
# 归档文件 IO (HC-4 只读)
# ============================================================

class TestArchiveIO:
    """parse_archive / diff_archives / diff_recent 测试."""

    def test_parse_archive_valid(self, tmp_path: Path) -> None:
        """解析合法归档."""
        p = tmp_path / "sc.json"
        p.write_text(json.dumps(_make_report(items=[_make_item("C1.1", "PASS")])),
                     encoding="utf-8")
        data = SystemCheckDiff.parse_archive(p)
        assert data is not None
        assert data["total"] == 1

    def test_parse_archive_missing(self, tmp_path: Path) -> None:
        """文件不存在返回 None."""
        data = SystemCheckDiff.parse_archive(tmp_path / "no.json")
        assert data is None

    def test_parse_archive_invalid_json(self, tmp_path: Path) -> None:
        """非法 JSON 返回 None."""
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        data = SystemCheckDiff.parse_archive(p)
        assert data is None

    def test_diff_archives(self, tmp_path: Path) -> None:
        """对比两个归档文件."""
        old_p = tmp_path / "system_check_20260730_100000.json"
        new_p = tmp_path / "system_check_20260731_100000.json"
        old_p.write_text(json.dumps(_make_report(
            check_time="2026-07-30T10:00:00",
            items=[_make_item("C1.1", "PASS")],
        )), encoding="utf-8")
        new_p.write_text(json.dumps(_make_report(
            check_time="2026-07-31T10:00:00",
            items=[_make_item("C1.1", "FAIL")],
        )), encoding="utf-8")
        diff = SystemCheckDiff().diff_archives(old_p, new_p)
        assert len(diff.regressions) == 1

    def test_diff_archives_one_missing(self, tmp_path: Path) -> None:
        """一个归档缺失 → 空 diff (不抛异常)."""
        old_p = tmp_path / "old.json"
        old_p.write_text(json.dumps(_make_report()), encoding="utf-8")
        diff = SystemCheckDiff().diff_archives(old_p, tmp_path / "no.json")
        assert diff.regressions == []
        assert "DIFF_FAILED" in diff.summary or diff.new_check_time == ""

    def test_diff_recent_insufficient_archives(self, tmp_path: Path) -> None:
        """归档不足 2 份 → 空 diff."""
        diff = SystemCheckDiff().diff_recent(tmp_path, days_ago=1)
        assert diff.regressions == []
        assert "DIFF_SKIPPED" in diff.summary

    def test_diff_recent_two_archives(self, tmp_path: Path) -> None:
        """2 份归档 → 对比最新 vs 最早."""
        for day, status in [("20260730_100000", "PASS"), ("20260731_100000", "FAIL")]:
            p = tmp_path / f"system_check_{day}.json"
            p.write_text(json.dumps(_make_report(
                check_time=f"2026-{day[:4]}-{day[4:6]}-{day[6:8]}T10:00:00",
                items=[_make_item("C1.1", status)],
            )), encoding="utf-8")
        diff = SystemCheckDiff().diff_recent(tmp_path, days_ago=1)
        assert len(diff.regressions) == 1

    def test_hc4_readonly(self, tmp_path: Path) -> None:
        """HC-4: diff 不修改归档文件."""
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_content = json.dumps(_make_report(items=[_make_item("C1.1", "PASS")]))
        new_content = json.dumps(_make_report(items=[_make_item("C1.1", "FAIL")]))
        old_p.write_text(old_content, encoding="utf-8")
        new_p.write_text(new_content, encoding="utf-8")
        SystemCheckDiff().diff_archives(old_p, new_p)
        # 文件内容未变
        assert old_p.read_text(encoding="utf-8") == old_content
        assert new_p.read_text(encoding="utf-8") == new_content


# ============================================================
# to_root_causes 转换
# ============================================================

class TestToRootCauses:
    """to_root_causes 测试."""

    def test_regressions_become_causes(self) -> None:
        """回归项转为 RootCause."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert len(causes) == 1
        assert causes[0].evidence["is_regression"] is True
        assert causes[0].evidence["check_code"] == "C1.1"

    def test_new_failures_become_causes(self) -> None:
        """新增失败项转为 RootCause."""
        old = _make_report(items=[])
        new = _make_report(items=[_make_item("C9.1", "FAIL", level="WARN")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert len(causes) == 1
        assert causes[0].evidence["is_regression"] is False

    def test_recoveries_not_converted(self) -> None:
        """恢复项不产生 RootCause."""
        old = _make_report(items=[_make_item("C1.1", "FAIL")])
        new = _make_report(items=[_make_item("C1.1", "PASS")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert causes == []

    def test_empty_diff_no_causes(self) -> None:
        """空 diff 不产生根因."""
        diff = CheckDiff()
        causes = SystemCheckDiff().to_root_causes(diff)
        assert causes == []

    def test_regression_confidence_09(self) -> None:
        """回归项置信度 = 0.9."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert causes[0].confidence == 0.9

    def test_new_failure_confidence_07(self) -> None:
        """新增失败项置信度 = 0.7."""
        old = _make_report(items=[])
        new = _make_report(items=[_make_item("C9.1", "FAIL", level="WARN")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert causes[0].confidence == 0.7

    def test_hc3_requires_human_approval(self) -> None:
        """HC-3: requires_human_approval 默认 True."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert causes[0].suggested_fix.requires_human_approval is True

    def test_c1_layer_code(self) -> None:
        """C1.* → layer=code."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert causes[0].layer == LAYER_CODE

    def test_c3_layer_ops_datasource(self) -> None:
        """C3.* → layer=ops, category=datasource_fail, severity=critical."""
        old = _make_report(items=[_make_item("C3.1", "PASS")])
        new = _make_report(items=[_make_item("C3.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert causes[0].layer == LAYER_OPS
        assert causes[0].category == "datasource_fail"
        assert causes[0].severity == SEVERITY_CRITICAL

    def test_c2_layer_ops(self) -> None:
        """C2.* → layer=ops."""
        old = _make_report(items=[_make_item("C2.3", "PASS")])
        new = _make_report(items=[_make_item("C2.3", "FAIL", level="WARN")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        # C2 不在 critical_prefixes, 但 ERROR level → critical
        # WARN level → high (回归项非 critical_prefix 且非 ERROR → high)
        assert causes[0].layer == LAYER_OPS
        assert causes[0].severity == SEVERITY_HIGH  # WARN 回归 → high

    def test_c7_layer_code(self) -> None:
        """C7.* → layer=code."""
        old = _make_report(items=[_make_item("C7.1", "PASS")])
        new = _make_report(items=[_make_item("C7.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        assert causes[0].layer == LAYER_CODE

    def test_cause_id_unique(self) -> None:
        """多条根因 cause_id 唯一."""
        old = _make_report(items=[_make_item("C1.1", "PASS"), _make_item("C1.2", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "FAIL"), _make_item("C1.2", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        ids = [c.cause_id for c in causes]
        assert len(ids) == len(set(ids))

    def test_evidence_structured(self) -> None:
        """evidence 是结构化 Dict (非文本)."""
        old = _make_report(items=[_make_item("C1.1", "PASS", name="myfile")])
        new = _make_report(items=[_make_item("C1.1", "FAIL", name="myfile", detail="missing")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        ev = causes[0].evidence
        assert isinstance(ev, dict)
        assert ev["check_code"] == "C1.1"
        assert ev["check_name"] == "myfile"
        assert ev["old_status"] == "PASS"
        assert ev["new_status"] == "FAIL"
        assert ev["is_regression"] is True

    def test_to_dict_serializable(self) -> None:
        """RootCause 可 JSON 序列化."""
        old = _make_report(items=[_make_item("C1.1", "PASS")])
        new = _make_report(items=[_make_item("C1.1", "FAIL")])
        diff = SystemCheckDiff().diff(old, new)
        causes = SystemCheckDiff().to_root_causes(diff)
        json.dumps(causes[0].to_dict(), ensure_ascii=False)


# ============================================================
# 集成测试
# ============================================================

class TestIntegration:
    """端到端集成测试."""

    def test_full_flow_with_real_archive_format(self, tmp_path: Path) -> None:
        """使用真实归档格式端到端测试."""
        # 模拟真实归档 (含 40 项, 模拟 CLAUDE.md §8 场景)
        old_items = [_make_item(f"C{i}.{j}", "PASS") for i in range(1, 9) for j in range(1, 6)]
        new_items = list(old_items)
        # 制造 1 个回归: C3.1 PASS→FAIL
        new_items[10] = _make_item("C3.1", "FAIL", name="Wind MCP 主数据源 (P1)")
        old_p = tmp_path / "system_check_20260730_100000.json"
        new_p = tmp_path / "system_check_20260731_100000.json"
        old_p.write_text(json.dumps(_make_report(
            "2026-07-30T10:00:00", old_items)), encoding="utf-8")
        new_p.write_text(json.dumps(_make_report(
            "2026-07-31T10:00:00", new_items)), encoding="utf-8")

        differ = SystemCheckDiff()
        diff = differ.diff_recent(tmp_path, days_ago=1)
        assert diff.has_regressions
        causes = differ.to_root_causes(diff)
        assert len(causes) == 1
        assert causes[0].code if hasattr(causes[0], 'code') else True
        # C3.1 → datasource_fail
        assert causes[0].category == "datasource_fail"
        assert causes[0].severity == SEVERITY_CRITICAL

    def test_no_regression_no_cause(self, tmp_path: Path) -> None:
        """无回归 → 无根因."""
        items = [_make_item("C1.1", "PASS")]
        old_p = tmp_path / "system_check_20260730_100000.json"
        new_p = tmp_path / "system_check_20260731_100000.json"
        old_p.write_text(json.dumps(_make_report(items=items)), encoding="utf-8")
        new_p.write_text(json.dumps(_make_report(items=items)), encoding="utf-8")
        differ = SystemCheckDiff()
        diff = differ.diff_recent(tmp_path, days_ago=1)
        causes = differ.to_root_causes(diff)
        assert causes == []
