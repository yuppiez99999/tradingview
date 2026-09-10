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
            "覆盖率硬门禁须按 run_full 区分: 子集口径覆盖率不可比, 应显式豁免"
        )


# ====================================================================
# gate-hardening (2026-09-10 第二轮, 审计 item 6 续)
#
# 修复前状态: ci.yml 的子集分支是
#     if ($env:RUN_FULL -ne 'true') { Write-Host "...跳过"; exit 0 }
# —— **不调用门禁、不写任何产物**: "无记录 = 通过", 与"缺数据 = 通过"同类。
# 现要求: 全量硬阻断 + 子集必须调用门禁落盘显式豁免 (waived), 且子集不得固化基线。
# ====================================================================


def _coverage_step_body() -> str:
    """截取 ci.yml 中覆盖率门禁步骤的正文 (便于断言接线顺序)。"""
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    start = ci.index("- name: Check coverage trend")
    end = ci.index("- name: Upload coverage artifact", start)
    return ci[start:end]


def _coverage_step_code() -> str:
    """同 _coverage_step_body, 但剔除注释行。

    注释里会引用"修复前的旧写法"(例如 ``if ($env:RUN_FULL -ne 'true') { ... exit 0 }``),
    若对全文断言会把说明文字误当接线, 故结构性断言只针对可执行代码行。
    """
    lines = [
        line
        for line in _coverage_step_body().splitlines()
        if not line.strip().startswith("#")
    ]
    return "\n".join(lines)


class TestSubsetScopeWaiver:
    """子集口径: 测量类判定显式豁免并落盘, 结构性错误不豁免。"""

    def _run_scope(self, mod, root, reports, scope: str, *, rate: float, tmp_path):
        """helper: 以给定 scope 跑一次门禁, 返回 (rc, payload)。"""
        _write_coverage_xml(reports / "coverage.xml", rate, {})
        out = tmp_path / f"out_{scope}.json"
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.38",
            "--scope",
            scope,
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(out),
        )
        return rc, json.loads(out.read_text(encoding="utf-8"))

    def test_subset_records_explicit_waiver_instead_of_silent_pass(
        self, gate, tmp_path, monkeypatch
    ):
        """子集口径: 阈值未达 → 退出码 0 但必须落盘 waived + waived_checks + 理由。"""
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        rc, payload = self._run_scope(
            mod, root, reports, "subset", rate=0.10, tmp_path=tmp_path
        )
        assert rc == 0, "子集口径的测量类判定应豁免 (不阻断)"
        assert payload["scope"] == "subset"
        assert payload["waived"] is True, "豁免必须是显式记录, 不能是无产物"
        assert "COV-1" in payload["waived_checks"]
        assert payload["waiver_reason"], "豁免必须带理由 (机器可读)"
        assert payload["fail"] == 0, "blocking fail 应为 0"
        # 关键: 判定本身仍是真值 (未达阈值就是 False), 豁免 ≠ 改写成通过
        cov1 = [r for r in payload["results"] if r["cid"] == "COV-1"][0]
        assert cov1["passed"] is False
        assert cov1["waivable"] is True

    def test_full_scope_blocks_the_same_report(self, gate, tmp_path, monkeypatch):
        """对照组: 同一份报告在全量口径下必须硬阻断 (证明豁免只发生在 subset)。"""
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        rc, payload = self._run_scope(
            mod, root, reports, "full", rate=0.10, tmp_path=tmp_path
        )
        assert rc == 1
        assert payload["scope"] == "full"
        assert payload["waived"] is False
        assert payload["waived_checks"] == []

    def test_subset_still_blocks_on_missing_report(self, gate, tmp_path):
        """子集口径**不豁免**报告缺失 —— 缺数据不等于通过。"""
        mod, _root, reports = gate
        out = tmp_path / "out.json"
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "missing.xml"),
            "--min-line-rate",
            "0.38",
            "--scope",
            "subset",
            "--output",
            str(out),
        )
        assert rc == 1
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["waived"] is False
        cov0 = [r for r in payload["results"] if r["cid"] == "COV-0"][0]
        assert cov0["passed"] is False
        assert cov0["waivable"] is False, "报告缺失属结构性错误, 任何口径都不豁免"

    def test_subset_still_blocks_on_stale_critical_module(
        self, gate, tmp_path, monkeypatch
    ):
        """子集口径**不豁免**关键模块清单陈旧 (配置错误, 与覆盖率高低无关)。"""
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", ["scripts/hedge_order_executor.py"])
        _write_coverage_xml(reports / "coverage.xml", 0.90, {})
        out = tmp_path / "out.json"
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.38",
            "--scope",
            "subset",
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(out),
        )
        assert rc == 1
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["waived"] is False
        assert all(not c.startswith("COV-scripts") for c in payload["waived_checks"])

    def test_subset_does_not_freeze_baseline(self, gate, tmp_path, monkeypatch):
        """子集覆盖率与全量不可比 —— 严禁写入基线 (否则基线被永久拉低)。"""
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        baseline = root / "reports" / "ci" / "coverage_baseline.json"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(
            json.dumps({"line_rate": 0.50, "updated": "SENTINEL"}), encoding="utf-8"
        )
        _write_coverage_xml(reports / "coverage.xml", 0.50, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.38",
            "--scope",
            "subset",
            "--baseline-json",
            str(baseline),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 0
        frozen = json.loads(baseline.read_text(encoding="utf-8"))
        assert frozen["updated"] == "SENTINEL", "子集口径不得固化基线"

    def test_full_scope_freezes_baseline_when_passing(self, gate, tmp_path, monkeypatch):
        """对照组: 全量口径且无阻断失败时才固化基线 (证明上条的守卫生效而非死代码)。"""
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        baseline = root / "reports" / "ci" / "coverage_baseline.json"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(
            json.dumps({"line_rate": 0.50, "updated": "SENTINEL"}), encoding="utf-8"
        )
        _write_coverage_xml(reports / "coverage.xml", 0.50, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.38",
            "--scope",
            "full",
            "--baseline-json",
            str(baseline),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 0
        frozen = json.loads(baseline.read_text(encoding="utf-8"))
        assert frozen["updated"] != "SENTINEL", "全量口径通过时应固化基线"

    def test_missing_baseline_is_recorded_not_silently_dropped(
        self, gate, tmp_path, monkeypatch
    ):
        """基线缺失/损坏时原实现是 `except: pass` 静默丢检查 —— 现必须显式可见。"""
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        _write_coverage_xml(reports / "coverage.xml", 0.50, {})
        out = tmp_path / "out.json"
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--min-line-rate",
            "0.38",
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(out),
        )
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        entries = [r for r in payload["results"] if r["cid"] == "COV-base"]
        assert entries, "基线不可用时也必须留下一条显式记录 (不能静默丢弃检查)"
        assert entries[0]["passed"] is True
        assert "SKIP" in entries[0]["detail"]


class TestThresholdSource:
    """阈值口径单一事实源 (.coveragerc fail_under) — 消除"本地门禁 ≠ CI 门禁"。"""

    def test_loads_fail_under_from_coveragerc(self, gate):
        mod, root, _reports = gate
        (root / ".coveragerc").write_text(
            "[run]\nsource =\n    utils\n\n[report]\nprecision = 2\nfail_under = 38\n",
            encoding="utf-8",
        )
        assert mod._load_fail_under() == pytest.approx(0.38)

    def test_accepts_fractional_fail_under(self, gate):
        mod, root, _reports = gate
        (root / ".coveragerc").write_text("[report]\nfail_under = 0.42\n", encoding="utf-8")
        assert mod._load_fail_under() == pytest.approx(0.42)

    def test_none_when_absent(self, gate):
        mod, _root, _reports = gate
        assert mod._load_fail_under() is None

    def test_default_threshold_follows_coveragerc(self, gate, tmp_path, monkeypatch):
        """缺省阈值须跟随 .coveragerc (38%), 而非硬编码 0.80。

        判别性用例: 50% 的报告在"跟随 fail_under=38%"下必须通过;
        改造前 argparse 缺省 0.80 → 必然 FAIL (本地门禁与 CI 门禁口径不一致)。
        """
        mod, root, reports = gate
        (root / ".coveragerc").write_text(
            "[run]\nsource =\n    utils\n\n[report]\nfail_under = 38\n", encoding="utf-8"
        )
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        _write_coverage_xml(reports / "coverage.xml", 0.50, {})
        rc = _run(
            mod,
            "--coverage-xml",
            str(reports / "coverage.xml"),
            "--baseline-json",
            str(root / "nope.json"),
            "--output",
            str(tmp_path / "out.json"),
        )
        assert rc == 0, "缺省阈值应为 .coveragerc fail_under=0.38, 50% 报告应通过"


class TestCiWiringHardening:
    """CI 接线: 子集分支必须调用门禁落盘豁免, 不得裸 exit 0。"""

    def test_subset_branch_invokes_gate_before_exit(self):
        body = _coverage_step_body()
        subset_branch = body[body.index("if ($scope -eq 'subset') {") :]
        first_exit = subset_branch.index("exit 0")
        assert "scripts/_check_coverage_trend.py --scope subset" in subset_branch[:first_exit], (
            "子集分支必须先调用门禁落盘显式豁免, 不能直接 exit 0 (无记录 = 通过)"
        )
        assert "::warning title=coverage-gate-waived::" in subset_branch[:first_exit], (
            "子集豁免须在 CI 界面显式标注 (绿灯 ≠ 覆盖率已过全量判定)"
        )

    def test_full_branch_uses_full_scope_and_blocks(self):
        body = _coverage_step_body()
        assert "scripts/_check_coverage_trend.py --scope full" in body
        assert "exit 1" in body, "全量口径失败须硬阻断"

    def test_ci_uploads_gate_artifact(self):
        ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        assert "reports/ci/coverage_trend.json" in ci, (
            "门禁判定结果 (含 waived 豁免记录) 必须作为 CI 产物可审计"
        )
        assert "reports/coverage.xml" in ci, (
            "须上传门禁实际读取的 reports/coverage.xml"
        )


class TestUnknownScopeIsFailClosed:
    """口径未知 (RUN_FULL 缺失/异常) 必须按全量硬门禁, 不得静默豁免。

    修复前状态: 子集判据是 ``if ($env:RUN_FULL -ne 'true')`` —— RUN_FULL 为空/null
    (select-tests 步骤失败、输出未回填、新增调用方) 时同样落入子集分支并 ``exit 0``,
    即"缺数据 = 通过"。修复后只有**明确** ``run_full=false`` 才允许豁免。
    """

    def test_scope_defaults_to_full_and_only_false_allows_subset(self):
        body = _coverage_step_code()
        assert "$scope = 'full'" in body, "口径默认值必须是 full (fail-closed)"
        assert "$scope = 'subset'" in body
        false_guard_at = body.index("if ($env:RUN_FULL -eq 'false') {")
        subset_assign_at = body.index("$scope = 'subset'")
        assert false_guard_at < subset_assign_at, (
            "子集口径只能由明确的 run_full=false 决定 (fail-closed)"
        )
        assert "$env:RUN_FULL -ne 'true'" in body, "口径未知告警分支应保留"
        assert body.count("$env:RUN_FULL -ne 'true'") == body.count(
            "elseif ($env:RUN_FULL -ne 'true') {"
        ), (
            "`-ne 'true'` 只能作为 elseif(口径未知) 告警分支出现, "
            "不得再用来反推子集口径 (缺数据 = 通过); 未知口径须走 full 硬门禁"
        )

    def test_unknown_scope_emits_warning_and_hard_gate(self):
        body = _coverage_step_body()
        assert "::warning title=coverage-scope-unknown::" in body, (
            "口径未知须在 CI 界面显式告警, 保留可见性"
        )
        # 口径未知时走的是 full 分支 (硬阻断), 而不是 subset 的豁免 exit 0
        unknown_at = body.index("::warning title=coverage-scope-unknown::")
        subset_at = body.index("if ($scope -eq 'subset') {")
        assert unknown_at < subset_at, "口径解析必须发生在子集分支判定之前 (先定口径再执行)"

    def test_default_scope_at_script_level_blocks_below_threshold(
        self, gate, tmp_path, monkeypatch
    ):
        """脚本层同一致: 不传 --scope 即 full, 低于阈值必须 exit 1。"""
        mod, root, reports = gate
        monkeypatch.setattr(mod, "CRITICAL_MODULES", [])
        _write_coverage_xml(reports / "coverage.xml", 0.05, {})
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
        assert rc == 1, "缺省口径 = full; 低于阈值不得放行"
        payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        assert payload["scope"] == "full"
