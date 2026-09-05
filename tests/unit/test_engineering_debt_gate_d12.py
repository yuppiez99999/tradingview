"""D12 冻结窗 Change Budget 机械检查 单元测试 (R-4, ROADMAP 2026-09-05).

覆盖场景:
    1. 窗口外 PASS (不产生任何文件, 待激活)
    2. 窗口内首次运行: 冻结基线 PASS
    3. 窗口内重跑无变化 PASS
    4. 新增 enabled flag → FAIL (0 个新增 enable 铁律)
    5. 基线损坏 / 注册表读取失败 → FAIL (fail-closed)
    6. 冻结窗内新增生产模型 pkl → FAIL
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.engineering_debt_gate import (  # noqa: E402
    FREEZE_WINDOW_END,
    FREEZE_WINDOW_START,
    _check_d12_freeze_window_change_budget,
    _freeze_flag_enabled_map,
)

IN_WINDOW = date(2026, 9, 20)  # 窗口内任一日
OUT_OF_WINDOW_BEFORE = date(2026, 9, 5)  # 窗口开始前
OUT_OF_WINDOW_AFTER = date(2026, 12, 31)  # 窗口结束后


def _write_flags_config(tmp_path: Path, flags: dict[str, tuple[bool, bool | None]]) -> Path:
    """写最小 feature_flags.yaml; flags: {NAME: (default, rollout_enabled|None)}."""
    lines = ["flags:"]
    for name, (default, rollout) in flags.items():
        lines.append(f"  {name}:")
        lines.append(f"    default: {str(default).lower()}")
        if rollout is not None:
            lines.append("    rollout:")
            lines.append(f"      enabled: {str(rollout).lower()}")
    p = tmp_path / "feature_flags.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _patch_gate(tmp_path: Path, monkeypatch, flags: dict[str, tuple[bool, bool | None]]):
    """将 D12 全部路径常量重定向到 tmp_path, 返回 flags yaml 路径."""
    cfg = _write_flags_config(tmp_path, flags)
    monkeypatch.setattr("scripts.engineering_debt_gate._FLAGS_CONFIG", cfg)
    monkeypatch.setattr(
        "scripts.engineering_debt_gate._FREEZE_BASELINE_DIR",
        tmp_path / "reports" / "freeze_baseline",
    )
    monkeypatch.setattr("scripts.engineering_debt_gate._PROJECT_ROOT", tmp_path)
    return cfg


class TestFreezeWindowOutside:
    """窗口外: PASS 且不产生任何文件."""

    def test_before_window_pass(self, tmp_path, monkeypatch):
        _patch_gate(tmp_path, monkeypatch, {"USE_X": (False, None)})
        ok, msg = _check_d12_freeze_window_change_budget(today=OUT_OF_WINDOW_BEFORE)
        assert ok is True
        assert "待激活" in msg
        assert not (tmp_path / "reports" / "freeze_baseline").exists()

    def test_after_window_pass(self, tmp_path, monkeypatch):
        _patch_gate(tmp_path, monkeypatch, {"USE_X": (False, None)})
        ok, _ = _check_d12_freeze_window_change_budget(today=OUT_OF_WINDOW_AFTER)
        assert ok is True


class TestFreezeWindowBaseline:
    """窗口内基线生命周期: 创建 → 重跑一致 → 违规检测."""

    def test_first_run_creates_baseline_pass(self, tmp_path, monkeypatch):
        _patch_gate(
            tmp_path,
            monkeypatch,
            {"USE_A": (False, None), "USE_B": (False, True)},
        )
        ok, msg = _check_d12_freeze_window_change_budget(today=IN_WINDOW)
        assert ok is True
        assert "首次创建" in msg
        baseline = (
            tmp_path / "reports" / "freeze_baseline" / f"flags_baseline_{FREEZE_WINDOW_START.replace('-', '')}.json"
        )
        assert baseline.exists()
        data = json.loads(baseline.read_text(encoding="utf-8"))
        assert data == {"USE_A": False, "USE_B": True}

    def test_rerun_no_change_pass(self, tmp_path, monkeypatch):
        cfg = _patch_gate(tmp_path, monkeypatch, {"USE_A": (False, None)})
        _check_d12_freeze_window_change_budget(today=IN_WINDOW)
        ok, msg = _check_d12_freeze_window_change_budget(today=date(2026, 9, 21))
        assert ok is True
        assert "无违规" in msg
        assert cfg.exists()

    def test_new_enabled_flag_fails(self, tmp_path, monkeypatch):
        """基线后某 flag 从 false → enabled → FAIL (修复前逻辑: 无对比, 必然漏检)."""
        _patch_gate(tmp_path, monkeypatch, {"USE_DANGER": (False, None)})
        _check_d12_freeze_window_change_budget(today=IN_WINDOW)
        # 重写注册表: USE_DANGER 开启 rollout
        _write_flags_config(tmp_path, {"USE_DANGER": (False, True)})
        ok, msg = _check_d12_freeze_window_change_budget(today=date(2026, 9, 22))
        assert ok is False
        assert "USE_DANGER" in msg
        assert "新增 enable" in msg


class TestFailClosed:
    """fail-closed: 基线/注册表异常时绝不放行."""

    def test_corrupted_baseline_fails(self, tmp_path, monkeypatch):
        base_dir = tmp_path / "reports" / "freeze_baseline"
        base_dir.mkdir(parents=True)
        _patch_gate(tmp_path, monkeypatch, {"USE_A": (False, None)})
        (base_dir / f"flags_baseline_{FREEZE_WINDOW_START.replace('-', '')}.json").write_text(
            "{broken", encoding="utf-8"
        )
        ok, msg = _check_d12_freeze_window_change_budget(today=IN_WINDOW)
        assert ok is False
        assert "fail-closed" in msg

    def test_missing_registry_fails(self, tmp_path, monkeypatch):
        _patch_gate(tmp_path, monkeypatch, {"USE_A": (False, None)})
        # 删除注册表文件模拟读取失败
        (tmp_path / "feature_flags.yaml").unlink()
        ok, msg = _check_d12_freeze_window_change_budget(today=IN_WINDOW)
        assert ok is False
        assert "fail-closed" in msg


class TestModelDimension:
    """冻结窗内新增生产模型 pkl → FAIL."""

    def test_new_model_file_fails(self, tmp_path, monkeypatch):
        _patch_gate(tmp_path, monkeypatch, {"USE_A": (False, None)})
        _check_d12_freeze_window_change_budget(today=IN_WINDOW)
        # 模拟冻结窗开始后落盘的新模型: mtime 设为 2026-10-01 (> 窗口起点 2026-09-19)
        model = tmp_path / "reports" / "qlib_model_20261001_120000.pkl"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"x")
        ts = datetime(2026, 10, 1, 12, 0, 0).timestamp()
        os.utime(model, (ts, ts))
        ok, msg = _check_d12_freeze_window_change_budget(today=date(2026, 10, 1))
        assert ok is False
        assert "qlib_model" in msg


class TestFreezeFlagEnabledMap:
    """enabled 判定语义: rollout.enabled or default."""

    def test_enabled_semantics(self, tmp_path):
        cfg = _write_flags_config(
            tmp_path,
            {
                "USE_ROLLOUT": (False, True),  # rollout 启用 → True
                "USE_DEFAULT": (True, None),  # default true → True
                "USE_OFF": (False, None),  # 全关 → False
                "USE_DEFAULT_ROLLOUT_OFF": (True, False),  # default true 但 rollout 显式关 → True
            },
        )
        result = _freeze_flag_enabled_map(cfg)
        assert result == {
            "USE_ROLLOUT": True,
            "USE_DEFAULT": True,
            "USE_OFF": False,
            "USE_DEFAULT_ROLLOUT_OFF": True,
        }


class TestWindowConstants:
    """冻结窗常量与 ROADMAP 口径一致 (防漂移)."""

    def test_window_matches_roadmap(self):
        assert FREEZE_WINDOW_START == "2026-09-19"
        assert FREEZE_WINDOW_END == "2026-12-10"
