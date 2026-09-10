"""D1 压力测试非零断言 单元测试 (2026-09-10 修复"假 PASS"缺陷回归).

缺陷背景:
    `check_d1_stress_test_nonzero` 原实现用
        `sorted(reports_dir.glob("stress_test_*.json"), reverse=True)[0]`
    取"最新"报告。这是**字典序**排序, 而 `stress_test_SIMULATED_*.json` 的
    'S' (0x53) > '2' (0x32), 使模拟持仓报告**恒排首位**; 代码读到
    `is_simulated=true` 即跳过校验 → D1 恒 PASS (假阳性), 真实报告
    (即使 actual_pnl 全 0) 永远不被检查。

覆盖场景:
    1. SIMULATED 报告不得遮蔽真实报告 (回归主用例: 修复前 PASS / 修复后 FAIL)
    2. 最新真实报告 actual_pnl 非零 → PASS
    3. 仅有模拟报告 → 跳过 (不阻断)
    4. reports 目录缺失 → 跳过 (不阻断)
    5. 文件名未标记但内容 is_simulated=true → 跳过 (兜底分支)
    6. 多份真实报告按"文件名日期"取最新, 而非字典序
    7. 最新真实报告无法解析 → FAIL
    8. scenarios 为空 → 不误判为"全零"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.assert_data_validity as mod  # noqa: E402


def _write_report(
    reports_dir: Path,
    name: str,
    scenarios: dict | None = None,
    is_simulated: bool = False,
) -> Path:
    """落盘一份 stress_test 报告."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "is_simulated": is_simulated,
        "scenarios": scenarios if scenarios is not None else {},
    }
    path = reports_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _zero_scenarios() -> dict:
    return {
        "market_crash_2008": {"actual_pnl": 0},
        "liquidity_crisis_2015": {"actual_pnl": 0},
    }


def _nonzero_scenarios() -> dict:
    return {
        "market_crash_2008": {"actual_pnl": -779780.0},
        "liquidity_crisis_2015": {"actual_pnl": -258441.0},
    }


def test_simulated_name_does_not_shadow_real_report(tmp_path, monkeypatch):
    """回归主用例: 修复前因 'S' > '2' 取到 SIMULATED 报告而假 PASS."""
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    reports_dir = tmp_path / "reports"
    # 字典序下 "stress_test_SIMULATED_..." 排在 "stress_test_2026..." 之前
    _write_report(
        reports_dir, "stress_test_SIMULATED_20260824.json", _zero_scenarios(), True
    )
    _write_report(reports_dir, "stress_test_20260909.json", _zero_scenarios(), False)

    result = mod.check_d1_stress_test_nonzero("2026-09-10")

    assert result.passed is False, "真实报告全零时必须 FAIL, 不能被模拟报告遮蔽"
    assert result.evidence == "stress_test_20260909.json"


def test_real_report_nonzero_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    reports_dir = tmp_path / "reports"
    _write_report(
        reports_dir, "stress_test_SIMULATED_20260824.json", _zero_scenarios(), True
    )
    _write_report(reports_dir, "stress_test_20260909.json", _nonzero_scenarios(), False)

    result = mod.check_d1_stress_test_nonzero("2026-09-10")

    assert result.passed is True
    assert "stress_test_20260909.json" in result.detail
    assert "2 个场景, 0 个为零" in result.detail


def test_only_simulated_reports_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    reports_dir = tmp_path / "reports"
    _write_report(
        reports_dir, "stress_test_SIMULATED_20260824.json", _zero_scenarios(), True
    )

    result = mod.check_d1_stress_test_nonzero("2026-09-10")

    assert result.passed is True
    assert "均为模拟持仓" in result.detail


def test_missing_reports_dir_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)

    result = mod.check_d1_stress_test_nonzero("2026-09-10")

    assert result.passed is True
    assert "无 reports 目录" in result.detail


def test_is_simulated_flag_fallback_skips(tmp_path, monkeypatch):
    """文件名未带 SIMULATED, 但内容标记为模拟 → 兜底跳过 (不阻断)."""
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    reports_dir = tmp_path / "reports"
    _write_report(
        reports_dir, "stress_test_20260910.json", _zero_scenarios(), is_simulated=True
    )

    result = mod.check_d1_stress_test_nonzero("2026-09-10")

    assert result.passed is True
    assert "is_simulated=true" in result.detail


def test_latest_selected_by_date_not_lexicographic(tmp_path, monkeypatch):
    """两份真实报告: 取文件名日期更晚者 (20260909 > 20260820)."""
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    reports_dir = tmp_path / "reports"
    _write_report(reports_dir, "stress_test_20260820.json", _nonzero_scenarios(), False)
    _write_report(reports_dir, "stress_test_20260909.json", _zero_scenarios(), False)

    result = mod.check_d1_stress_test_nonzero("2026-09-10")

    assert result.passed is False
    assert result.evidence == "stress_test_20260909.json"


def test_unparsable_latest_real_report_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "stress_test_20260909.json").write_text("{broken", encoding="utf-8")

    result = mod.check_d1_stress_test_nonzero("2026-09-10")

    assert result.passed is False
    assert "无法解析" in result.detail


def test_empty_scenarios_not_treated_as_all_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_PROJECT_ROOT", tmp_path)
    reports_dir = tmp_path / "reports"
    _write_report(reports_dir, "stress_test_20260909.json", {}, False)

    result = mod.check_d1_stress_test_nonzero("2026-09-10")

    assert result.passed is True
