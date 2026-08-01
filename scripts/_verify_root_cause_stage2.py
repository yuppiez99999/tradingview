# -*- coding: utf-8 -*-
"""三层面自我进化 Stage 2 验证脚本.

按 ARCHITECTURE_三层面进化 §第2阶段验收标准执行 12 项验证:
    1.  模块导入成功
    2.  Flag 默认 False (HC-1)
    3.  system_check 失败 → 识别 code 层根因, evidence 含 CheckResult
    4.  drift_alert → 识别 strategy 层根因
    5.  data_quality 异常 → 识别 ops 层根因
    6.  跨层因果链: Wind MCP 失败 + IC 衰减 → CausalChain 含 2+ 节点
    7.  结构化输出: evidence 是 Dict (非文本)
    8.  修复建议含 remediation_commands
    9.  requires_human_approval 默认 True
    10. 持久化: root_causes.jsonl 增加一行
    11. 不修改生产 (positions.json / daily_returns.jsonl 不变)
    12. 知识库读取: get_recent_causes(7) 返回 List

硬约束:
    - HC-1: Feature Flag 默认 False
    - HC-3: requires_human_approval 默认 True
    - HC-4: 不修改 positions.json / daily_returns.jsonl / V9 基线
    - HC-5: 配置走 ConfigManager
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# 加入项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

PASS = 0
FAIL = 0
RESULTS: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一项检查结果."""
    global PASS, FAIL
    RESULTS.append((name, ok, detail))
    if ok:
        PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def file_hash(path: Path) -> str:
    """计算文件 SHA256 (用于检测生产数据是否被修改)."""
    try:
        if not path.exists():
            return "NOT_EXISTS"
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except Exception:
        return "ERROR"


# ============================================================
# 生产文件基线 (HC-4 验证)
# ============================================================
POSITIONS_PATH = _PROJECT_ROOT / "config" / "positions.json"
DAILY_RETURNS_PATH = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"

_pos_hash_before = file_hash(POSITIONS_PATH)
_dr_hash_before = file_hash(DAILY_RETURNS_PATH)


# ============================================================
# 测试 1: 模块导入
# ============================================================
print("=" * 70)
print("测试 1: 模块导入")
print("=" * 70)
try:
    from utils.alpha.root_cause import (
        UnifiedRootCauseAnalyzer,
        RootCause,
        FixSuggestion,
        RootCauseReport,
        LAYER_CODE,
        LAYER_STRATEGY,
        LAYER_OPS,
        SEVERITY_CRITICAL,
        SEVERITY_HIGH,
        SEVERITY_MEDIUM,
        SEVERITY_LOW,
    )
    from utils.alpha.layers.code_diagnoser import CodeDiagnoser
    from utils.alpha.layers.strategy_diagnoser import StrategyDiagnoser
    from utils.alpha.layers.ops_diagnoser import OpsDiagnoser
    from utils.alpha.causal_chain import CausalChainBuilder
    check("模块导入成功", True)
except Exception as e:
    check("模块导入成功", False, str(e))
    print(f"\n[致命] 无法导入模块, 后续测试跳过. 错误: {e}")
    print("\n" + "=" * 70)
    print(f"总计: {PASS} PASS / {FAIL} FAIL")
    sys.exit(1)


# ============================================================
# 测试 2: Flag 默认 False (HC-1)
# ============================================================
print("\n" + "=" * 70)
print("测试 2: Flag 默认 False (HC-1)")
print("=" * 70)
try:
    from utils.infra.feature_flags import is_enabled
    flag_on = is_enabled("USE_ROOT_CAUSE_ANALYZER")
    check("USE_ROOT_CAUSE_ANALYZER 默认 False", flag_on is False, f"实际={flag_on}")
except Exception as e:
    check("USE_ROOT_CAUSE_ANALYZER 默认 False", False, str(e))

try:
    analyzer = UnifiedRootCauseAnalyzer()
    check("UnifiedRootCauseAnalyzer._enabled == False", analyzer.enabled is False)
except Exception as e:
    check("UnifiedRootCauseAnalyzer._enabled == False", False, str(e))


# ============================================================
# 测试 3: system_check 失败 → 识别 code 层根因
# ============================================================
print("\n" + "=" * 70)
print("测试 3: system_check 失败 → 识别 code 层根因")
print("=" * 70)
try:
    # 构造 mock SystemCheckReport (含 FAIL 项)
    class _MockCheckResult:
        def __init__(self, code, name, status, level, detail, remediation):
            self.code = code
            self.name = name
            # 用字符串模拟 Enum
            class _S:
                value = status
            self.status = _S()
            class _L:
                value = level
            self.level = _L()
            self.detail = detail
            self.remediation = remediation

    class _MockCheckReport:
        def __init__(self, results):
            self.results = results

    mock_results = [
        _MockCheckResult(
            code="C1.1", name="positions.json 存在性",
            status="FAIL", level="ERROR",
            detail="positions.json 不存在",
            remediation="创建 config/positions.json",
        ),
        _MockCheckResult(
            code="C3.1", name="Wind MCP 连通性",
            status="FAIL", level="ERROR",
            detail="Wind MCP 不可用",
            remediation="检查 WIND_API_KEY 环境变量",
        ),
        _MockCheckResult(
            code="C5.1", name="pandas 依赖",
            status="PASS", level="INFO",
            detail="", remediation="",
        ),
    ]
    mock_report = _MockCheckReport(mock_results)

    # 用 patch 让 CodeDiagnoser._run_check 返回 mock_report
    with patch.object(CodeDiagnoser, "_run_check", return_value=mock_report):
        diagnoser = CodeDiagnoser(run_system_check=True)
        causes = diagnoser.diagnose(None)

    code_causes = [c for c in causes if c.layer == LAYER_CODE]
    check("识别 code 层根因", len(code_causes) >= 1, f"count={len(code_causes)}")
    if code_causes:
        c = code_causes[0]
        check("evidence 含 check_code", "check_code" in c.evidence,
              f"keys={list(c.evidence.keys())}")
        check("evidence 含 source", c.evidence.get("source") == "SystemChecker")
        check("category == system_check_fail", c.category == "system_check_fail")
except Exception as e:
    check("识别 code 层根因", False, f"异常: {e}")


# ============================================================
# 测试 4: drift_alert → 识别 strategy 层根因
# ============================================================
print("\n" + "=" * 70)
print("测试 4: drift_alert → 识别 strategy 层根因")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        drift_dir = Path(tmpdir) / "drift_alerts"
        drift_dir.mkdir()
        # 创建一条漂移告警 JSONL
        alert = {
            "severity": "high",
            "drift_type": "feature_drift",
            "model_name": "v9_lgb",
            "feature_name": "MOM_5D",
            "drift_score": 0.25,
            "psi": 0.35,
            "recorded_at": "2026-08-01T10:00:00Z",
        }
        alert_file = drift_dir / "v9_lgb_2026-08-01.jsonl"
        alert_file.write_text(json.dumps(alert) + "\n", encoding="utf-8")

        diagnoser = StrategyDiagnoser(drift_alerts_dir=drift_dir)
        causes = diagnoser.diagnose(None)

    drift_causes = [c for c in causes if c.category == "drift_alert"]
    check("识别 strategy 层漂移根因", len(drift_causes) >= 1,
          f"count={len(drift_causes)}")
    if drift_causes:
        c = drift_causes[0]
        check("layer == strategy", c.layer == LAYER_STRATEGY)
        check("evidence 含 drift_score", "drift_score" in c.evidence)
        check("evidence 含 model_name", c.evidence.get("model_name") == "v9_lgb")
except Exception as e:
    check("识别 strategy 层漂移根因", False, f"异常: {e}")


# ============================================================
# 测试 5: data_quality 异常 → 识别 ops 层根因
# ============================================================
print("\n" + "=" * 70)
print("测试 5: data_quality 异常 → 识别 ops 层根因")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建 system_check 归档 (含 C3 FAIL)
        sc_dir = Path(tmpdir) / "system_check"
        sc_dir.mkdir()
        archive = {
            "results": [
                {
                    "code": "C3.1", "name": "Wind MCP 连通性",
                    "status": "FAIL", "level": "ERROR",
                    "detail": "Wind MCP 不可用",
                    "remediation": "检查 WIND_API_KEY",
                },
            ],
        }
        (sc_dir / "system_check_20260801_120000.json").write_text(
            json.dumps(archive), encoding="utf-8"
        )

        diagnoser = OpsDiagnoser(
            report_dirs={
                "system_check": sc_dir,
                "data_quality": Path(tmpdir) / "data_quality",  # 不存在 → 根因
                "drift_alerts": Path(tmpdir) / "drift_alerts",
                "flag_audit": Path(tmpdir) / "flag_audit",
                "risk_bus": Path(tmpdir) / "risk_bus",
            }
        )
        causes = diagnoser.diagnose(None)

    ops_causes = [c for c in causes if c.layer == LAYER_OPS]
    check("识别 ops 层根因", len(ops_causes) >= 1, f"count={len(ops_causes)}")
    # 至少有 datasource_fail 或 data_quality_no_monitoring
    categories = {c.category for c in ops_causes}
    check(
        "含 datasource_fail 或 data_quality_no_monitoring",
        "datasource_fail" in categories or "data_quality_no_monitoring" in categories,
        f"categories={categories}",
    )
    # 检查 datasource_fail 的 evidence
    ds_causes = [c for c in ops_causes if c.category == "datasource_fail"]
    if ds_causes:
        check("datasource_fail evidence 含 check_code",
              ds_causes[0].evidence.get("check_code") == "C3.1")
except Exception as e:
    check("识别 ops 层根因", False, f"异常: {e}")


# ============================================================
# 测试 6: 跨层因果链 (CausalChain 含 2+ 节点)
# ============================================================
print("\n" + "=" * 70)
print("测试 6: 跨层因果链 (Wind MCP 失败 + IC 衰减)")
print("=" * 70)
try:
    # 构造三层根因 (模拟 Wind MCP 失败 → C3 检查失败 → 策略漂移)
    now = "2026-08-01T12:00:00Z"
    causes = [
        RootCause(
            cause_id="ops-1", layer=LAYER_OPS, category="datasource_fail",
            severity=SEVERITY_HIGH,
            evidence={"check_code": "C3.1", "check_name": "Wind MCP 连通性"},
            confidence=0.9, detected_at=now,
        ),
        RootCause(
            cause_id="code-1", layer=LAYER_CODE, category="system_check_fail",
            severity=SEVERITY_CRITICAL,
            evidence={"check_code": "C3.1", "source": "SystemChecker"},
            confidence=0.9, detected_at=now,
        ),
        RootCause(
            cause_id="strat-1", layer=LAYER_STRATEGY, category="drift_alert",
            severity=SEVERITY_MEDIUM,
            evidence={"model_name": "v9_lgb", "drift_score": 0.25},
            confidence=0.85, detected_at=now,
        ),
    ]
    builder = CausalChainBuilder()
    chains = builder.build(causes)
    check("生成跨层因果链", len(chains) >= 1, f"count={len(chains)}")
    if chains:
        chain = chains[0]
        check("CausalChain 含 2+ 节点", len(chain.nodes) >= 2,
              f"nodes={len(chain.nodes)}")
        check("CausalChain 含 3 节点 (ops→code→strategy)", len(chain.nodes) == 3,
              f"nodes={len(chain.nodes)}")
        # 验证因果顺序
        layers = [n.layer for n in chain.nodes]
        check("因果顺序 ops→code→strategy",
              layers == [LAYER_OPS, LAYER_CODE, LAYER_STRATEGY],
              f"layers={layers}")
        check("chain_id 非空", len(chain.chain_id) > 0)
        check("description 非空", len(chain.description) > 0)
        check("confidence ∈ (0, 1]", 0 < chain.confidence <= 1.0,
              f"confidence={chain.confidence}")
except Exception as e:
    check("跨层因果链", False, f"异常: {e}")


# ============================================================
# 测试 7: 结构化输出 (evidence 是 Dict 非文本)
# ============================================================
print("\n" + "=" * 70)
print("测试 7: 结构化输出 (evidence 是 Dict)")
print("=" * 70)
try:
    cause = RootCause(
        cause_id="test-1", layer=LAYER_CODE, category="test",
        severity=SEVERITY_LOW,
        evidence={"key1": "value1", "key2": 123, "key3": [1, 2, 3]},
    )
    check("evidence 是 dict", isinstance(cause.evidence, dict))
    check("evidence 含多类型值", isinstance(cause.evidence.get("key1"), str)
          and isinstance(cause.evidence.get("key2"), int)
          and isinstance(cause.evidence.get("key3"), list))
    # to_dict 序列化
    d = cause.to_dict()
    check("to_dict() 返回 dict", isinstance(d, dict))
    check("to_dict() 含 evidence", isinstance(d.get("evidence"), dict))
    # JSON 可序列化
    json_str = json.dumps(d, ensure_ascii=False)
    check("to_dict() 可 JSON 序列化", len(json_str) > 0)
except Exception as e:
    check("结构化输出", False, f"异常: {e}")


# ============================================================
# 测试 8: 修复建议含 remediation_commands
# ============================================================
print("\n" + "=" * 70)
print("测试 8: 修复建议含 remediation_commands")
print("=" * 70)
try:
    fix = FixSuggestion(
        action_type="manual",
        description="测试修复",
        remediation_commands=["python script.py", "echo done"],
    )
    check("FixSuggestion 创建成功", isinstance(fix, FixSuggestion))
    check("remediation_commands 非空", len(fix.remediation_commands) == 2)
    d = fix.to_dict()
    check("to_dict() 含 remediation_commands",
          isinstance(d.get("remediation_commands"), list))
    check("remediation_commands 内容正确",
          d["remediation_commands"] == ["python script.py", "echo done"])
except Exception as e:
    check("修复建议含 remediation_commands", False, f"异常: {e}")


# ============================================================
# 测试 9: requires_human_approval 默认 True (HC-3)
# ============================================================
print("\n" + "=" * 70)
print("测试 9: requires_human_approval 默认 True (HC-3)")
print("=" * 70)
try:
    # 默认 FixSuggestion (仅 action_type)
    fix_default = FixSuggestion(action_type="manual")
    check("默认 requires_human_approval == True",
          fix_default.requires_human_approval is True,
          f"实际={fix_default.requires_human_approval}")

    # RootCause 默认 suggested_fix
    cause_default = RootCause(
        cause_id="test-2", layer=LAYER_CODE, category="test",
        severity=SEVERITY_LOW,
    )
    check("RootCause 默认 suggested_fix.requires_human_approval == True",
          cause_default.suggested_fix.requires_human_approval is True)

    # 显式设为 False (仅限低风险动作, 但默认仍 True)
    fix_false = FixSuggestion(action_type="datasource_switch",
                              requires_human_approval=False)
    check("可显式设为 False", fix_false.requires_human_approval is False)
except Exception as e:
    check("requires_human_approval 默认 True", False, f"异常: {e}")


# ============================================================
# 测试 10: 持久化 (root_causes.jsonl 增加一行)
# ============================================================
print("\n" + "=" * 70)
print("测试 10: 持久化 (root_causes.jsonl 增加一行)")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        persist_path = Path(tmpdir) / "root_causes.jsonl"
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            analyzer = UnifiedRootCauseAnalyzer(persistence_path=persist_path)
            check("采集前文件不存在", not persist_path.exists())
            # analyze 会持久化
            report = analyzer.analyze(None)
            check("采集后文件存在", persist_path.exists())
            content = persist_path.read_text(encoding="utf-8").strip()
            lines = [l for l in content.splitlines() if l.strip()]
            check("文件有 1 行", len(lines) == 1, f"行数={len(lines)}")
            # 验证行是有效 JSON
            parsed = json.loads(lines[0])
            check("行是有效 JSON", isinstance(parsed, dict))
            check("JSON 含 causes", "causes" in parsed)
            check("JSON 含 causal_chains", "causal_chains" in parsed)
            check("JSON 含 analyzed_at", "analyzed_at" in parsed)
except Exception as e:
    check("持久化", False, f"异常: {e}")


# ============================================================
# 测试 11: 不修改生产 (HC-4)
# ============================================================
print("\n" + "=" * 70)
print("测试 11: 不修改生产 (HC-4)")
print("=" * 70)
try:
    _pos_hash_after = file_hash(POSITIONS_PATH)
    _dr_hash_after = file_hash(DAILY_RETURNS_PATH)
    check("positions.json 未修改", _pos_hash_before == _pos_hash_after,
          f"before={_pos_hash_before}, after={_pos_hash_after}")
    check("daily_returns.jsonl 未修改", _dr_hash_before == _dr_hash_after,
          f"before={_dr_hash_before}, after={_dr_hash_after}")
except Exception as e:
    check("不修改生产", False, f"异常: {e}")


# ============================================================
# 测试 12: 知识库读取 (get_recent_causes 返回 List)
# ============================================================
print("\n" + "=" * 70)
print("测试 12: 知识库读取 (get_recent_causes)")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        persist_path = Path(tmpdir) / "root_causes.jsonl"
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            analyzer = UnifiedRootCauseAnalyzer(persistence_path=persist_path)
            # 先生成一份报告
            analyzer.analyze(None)
            # 读取历史
            recent = analyzer.get_recent_causes(7)
            check("get_recent_causes(7) 返回 List", isinstance(recent, list))
            # 读取报告
            reports = analyzer.get_recent_reports(7)
            check("get_recent_reports(7) 返回 List", isinstance(reports, list))
            check("历史报告数 >= 1", len(reports) >= 1, f"count={len(reports)}")
            if reports:
                check("历史元素是 RootCauseReport",
                      isinstance(reports[0], RootCauseReport))
            # get_status
            status = analyzer.get_status()
            check("get_status() 返回 dict", isinstance(status, dict))
            check("status 含 enabled", "enabled" in status)
            check("status 含 persistence_path", "persistence_path" in status)
except Exception as e:
    check("知识库读取", False, f"异常: {e}")


# ============================================================
# 总结
# ============================================================
print("\n" + "=" * 70)
print(f"总计: {PASS} PASS / {FAIL} FAIL")
print("=" * 70)

if FAIL > 0:
    print("\n失败项:")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  - {name}: {detail}")
    sys.exit(1)
else:
    print("\n✅ 所有 12 项验收通过 (Stage 2 根因分析)")
    sys.exit(0)
