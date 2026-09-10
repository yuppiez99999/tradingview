"""覆盖率门禁 (_check_coverage_trend.py) 回归测试 — 审计 item 6 补齐 (2026-09-10)。

修复的缺陷 (全部为"门禁形同虚设/假 PASS"类):
    1. ``CRITICAL_MODULES`` 8 条里 4 条指向**并不存在**的 ``scripts/*.py``; 其余
       条目的键形态与 coverage.xml 的 filename 不一致 —— coverage.py 以
       .coveragerc ``source`` 根为基准产出 filename (``utils/execution/fills_store.py``
       实际写作 ``execution/fills_store.py``) → ``cls_map.get(旧键)`` 恒为 None →
       全部落入 "not in coverage report → non-blocking PASS" 的宽容分支。
       实测: 8 条关键模块校验**从未生效过**。
    2. ci.yml 的 pytest 写 ``根/coverage.xml``, 而门禁默认读 ``reports/coverage.xml``
       → 读不到本次 CI 产物 (缺失即 COV-0 FAIL, 或读到历史残留)。
    3. ``--min-line-rate`` 无口径守卫 (与 item 14 "不可达阈值" 同类陷阱)。

契约:
    - 关键模块文件不存在 → FAIL (清单陈旧, fail-loud)
    - 关键模块不在 .coveragerc source 内 → FAIL (不可测量 = 配置错误)
    - 关键模块存在但不在报告中 → FAIL (覆盖空洞, 不是"合法跳过")
    - 阈值不在 (0, 1] → 退出码 2, 拒绝执行
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "_check_coverage_trend.py"


def _load_gate() -> ModuleType:
    """按文件路径加载 scripts/_check_coverage_trend.py (scripts 非包)。"""
    spec = importlib.util.spec_from_file_location("_cov_trend_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_coverage_xml(path: Path, line_rate: float, classes: dict[str, float]) -> None:
    entries = "".join(
        f'<class filename="{fn}" line-rate="{lr}"/>' for fn, lr in classes.items()
    )
    path.write_text(
        (
            f'<?xml version="1.0" ?><coverage line-rate="{line_rate}">'
            f'<packages><package name="pkg"><classes>{entries}</classes></package>'
            "</packages></coverage>"
        ),
        encoding="utf-8",
    )


def _touch(root: Path, rel: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# stub\n", encoding="utf-8")
    return target


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """返回 (module, tmp_root, reports_dir); ROOT 已重定向到 tmp_path。"""
    mod = _load_gate()
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DEFAULT_COVERAGE_SOURCES", ("utils", "ms_strategy"))
    return mod, tmp_path, reports


def _run(mod, *argv) -> int:
    return mod.main(list(argv))


class TestThresholdGuard:
    """阈值口径守卫 — 拒绝不可达/无意义阈值, 而不是静默松弛。"""

    def test_min_line_rate_above_one_is_rejected(self, gate, tmp_path):
        mod, _root, reports = gate
        _write_coverage_xml(reports / "coverage.xml", 0.9, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "1.5",
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 2

    def test_min_line_rate_zero_is_rejected(self, gate, tmp_path):
        """0 阈值 = 永不失败 = 门禁形同虚设, 必须拒绝。"""
        mod, _root, reports = gate
        _write_coverage_xml(reports / "coverage.xml", 0.9, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0",
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 2


class TestCoverageXmlResolution:
    """coverage.xml 路径口径 — CI 写 reports/coverage.xml, 门禁必须读到同一份。"""

    def test_missing_report_fails(self, gate, tmp_path):
        mod, _root, reports = gate
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "missing.xml"),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 1
        payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        assert payload["results"][0]["cid"] == "COV-0"

    def test_auto_resolve_prefers_reports_dir(self, gate, tmp_path):
        mod, _root, reports = gate
        _write_coverage_xml(reports / "coverage.xml", 0.5, {})
        assert mod._resolve_coverage_xml(None) == reports / "coverage.xml"

    def test_auto_resolve_falls_back_to_repo_root(self, gate, tmp_path):
        """CI 早期写法 (根 coverage.xml) 仍能被读到, 避免口径漂移静默失败。"""
        mod, root, _reports = gate
        _write_coverage_xml(root / "coverage.xml", 0.5, {})
        assert mod._resolve_coverage_xml(None) == root / "coverage.xml"

    def test_auto_resolve_defaults_when_nothing_exists(self, gate):
        mod, _root, reports = gate
        assert mod._resolve_coverage_xml(None) == reports / "coverage.xml"


class TestCoverageSources:
    """从 .coveragerc 读取可测量根 (与 coverage 的 filename 基准一致)。"""

    def test_reads_source_list(self, gate):
        mod, root, _reports = gate
        (root / ".coveragerc").write_text(
            "[run]\nsource =\n    utils\n    ms_strategy\nomit =\n    tests/*\n",
            encoding="utf-8",
        )
        assert mod._load_coverage_sources() == ("utils", "ms_strategy")

    def test_falls_back_when_missing(self, gate):
        mod, _root, _reports = gate
        assert mod._load_coverage_sources() == ("utils", "ms_strategy")


class TestCriticalModulePolicy:
    """关键模块 fail-closed 策略 (修复前: 缺失/不匹配一律静默 PASS)。"""

    def test_suffix_match_on_report_filename(self, gate, tmp_path, monkeypatch):
        """coverage.xml 写作 execution/x.py, 清单写 utils/execution/x.py — 必须匹配。

        这是修复前恒为 None 的根因, 原实现用 ``cls_map.get(完整路径)``。
        """
        mod, root, reports = gate
        _touch(root, "utils/execution/fills_store.py")
        monkeypatch.setattr(mod, "CRITICAL_MODULES", ["utils/execution/fills_store.py"])
        _write_coverage_xml(
            reports / "coverage.xml", 0.5, {"execution/fills_store.py": 0.65}
        )
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.1",
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 0
        payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        crit = [r for r in payload["results"] if r["cid"].startswith("COV-utils")]
        assert crit and crit[0]["passed"] is True

    def test_stale_module_path_fails(self, gate, tmp_path, monkeypatch):
        """清单指向不存在的文件 (旧清单 4 条即此情形) → 必须 FAIL, 不再静默放过。"""
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", ["scripts/hedge_order_executor.py"])
        _write_coverage_xml(reports / "coverage.xml", 0.5, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.1",
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 1
        payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        crit = [r for r in payload["results"] if r["cid"].startswith("COV-scripts")]
        assert crit and crit[0]["passed"] is False
        assert "路径陈旧" in crit[0]["detail"]

    def test_module_outside_coverage_sources_fails(self, gate, tmp_path, monkeypatch):
        """文件存在但不属于 .coveragerc source → 不可能被测量, 属配置错误。"""
        mod, root, reports = gate
        _touch(root, "scripts/industrial_grade_check.py")
        monkeypatch.setattr(mod, "CRITICAL_MODULES", ["scripts/industrial_grade_check.py"])
        _write_coverage_xml(reports / "coverage.xml", 0.5, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.1",
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 1
        payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        crit = [r for r in payload["results"] if r["cid"].startswith("COV-scripts")]
        assert crit and crit[0]["passed"] is False
        assert "不在 .coveragerc source" in crit[0]["detail"]

    def test_module_existing_but_absent_from_report_fails(self, gate, tmp_path, monkeypatch):
        """文件在磁盘存在却不在 coverage.xml 中 = 无任何测试导入 (覆盖空洞)。

        修复前该分支返回 passed=True, 注释为 "non-blocking: maybe not imported
        by tests" —— 与 D1 "空场景静默 PASS" 同类, 属门禁假 PASS 高发地。
        """
        mod, root, reports = gate
        _touch(root, "utils/risk/trade_reconciliation_runner.py")
        monkeypatch.setattr(
            mod, "CRITICAL_MODULES", ["utils/risk/trade_reconciliation_runner.py"]
        )
        _write_coverage_xml(reports / "coverage.xml", 0.5, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.1",
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 1
        payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        crit = [r for r in payload["results"] if r["cid"].startswith("COV-utils")]
        assert crit and crit[0]["passed"] is False
        assert "覆盖空洞" in crit[0]["detail"]

    def test_repo_critical_modules_are_measurable(self):
        """生产清单自检: 每条都必须真实存在且落在 .coveragerc source 内。

        这条用例保证清单不会被改回"指向不存在路径"的状态 (旧清单的根因)。
        """
        mod = _load_gate()
        sources = mod._load_coverage_sources()
        for rel in mod.CRITICAL_MODULES:
            assert (PROJECT_ROOT / rel).exists(), f"关键模块路径不存在: {rel}"
            assert any(rel.startswith(s + "/") for s in sources), (
                f"关键模块不在 coverage source 内, 不可测量: {rel}"
            )


class TestOverallThreshold:
    def test_below_threshold_fails(self, gate, tmp_path, monkeypatch):
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        _write_coverage_xml(reports / "coverage.xml", 0.20, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.38",
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 1

    def test_above_threshold_passes(self, gate, tmp_path, monkeypatch):
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        _write_coverage_xml(reports / "coverage.xml", 0.43, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.38",
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 0


class TestCiWiring:
    """CI 接线一致性 — 防止再次出现"写一份、读另一份"的口径漂移。"""

    def test_ci_writes_and_reads_same_coverage_xml_path(self):
        ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        assert "--cov-report=xml:reports/coverage.xml" in ci, (
            "pytest 必须把 coverage.xml 写到 reports/coverage.xml (与门禁约定一致)"
        )
        assert "--coverage-xml reports/coverage.xml" in ci, (
            "门禁必须显式读取 reports/coverage.xml"
        )
        assert "--cov-report=xml:coverage.xml" not in ci, (
            "不允许再出现 根/coverage.xml 的旧写法 (门禁读不到)"
        )

    def test_ci_coverage_trend_step_is_gated_on_full_run(self):
        ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        assert "RUN_FULL" in ci, (
            "覆盖率硬门禁须按 run_full 区分: 子集口径覆盖率不可比, 应显式跳过"
        )
