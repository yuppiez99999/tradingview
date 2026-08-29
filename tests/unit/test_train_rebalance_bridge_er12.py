"""ER-1.2: 训练→进化→再平衡串联桥接测试 (Wave 7-ERL Sprint 1)

验证目标:
    1. make_train_evolution_rebalance_callback: 返回可调用回调
    2. 双 flag 关闭 → evolution/rebalance 均返回 disabled
    3. 进化启用、再平衡关闭 → 仅运行进化循环
    4. 进化关闭、再平衡启用 → 仅尝试再平衡
    5. 双 flag 启用 + providers 提供 → 全链路运行
    6. 进化异常 → fail-safe 降级不阻断
    7. 再平衡异常 → fail-safe 降级不阻断
    8. positions_provider 返回 None → 再平衡降级跳过
    9. weight_adjustments 乘子约束 [0.5, 2.0]
    10. 审计日志写入 JSONL
    11. 与 invoke_post_train_callback 集成 (端到端)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.evolution.train_rebalance_bridge import (  # noqa: E402
    WEIGHT_MULTIPLIER_MAX,
    WEIGHT_MULTIPLIER_MIN,
    _clamp_weight_adjustments,
    make_train_evolution_rebalance_callback,
)


# ============================================================
# 回调工厂测试
# ============================================================
class TestMakeCallback:
    """make_train_evolution_rebalance_callback 工厂函数测试."""

    def test_returns_callable(self):
        """工厂返回可调用对象."""
        callback = make_train_evolution_rebalance_callback()
        assert callable(callback)

    def test_callback_accepts_train_result(self):
        """回调接收训练结果字典不报错 (flag 关闭时)."""
        callback = make_train_evolution_rebalance_callback(enable_audit=False)
        train_result = {"status": "OK", "trained": 5, "failed": 0}
        callback(train_result)  # 不应抛异常


# ============================================================
# Feature Flag 控制测试
# ============================================================
class TestFeatureFlagControl:
    """Feature Flag 开关行为测试."""

    def test_both_flags_disabled(self):
        """双 flag 关闭 → evolution/rebalance 均返回 disabled."""
        callback = make_train_evolution_rebalance_callback(enable_audit=False)
        with patch(
            "utils.evolution.train_rebalance_bridge._is_flag_enabled",
            return_value=False,
        ):
            callback({"status": "OK"})
        # 不抛异常即通过 (内部 evolution/rebalance 均为 disabled)

    def test_evolution_enabled_rebalance_disabled(self):
        """进化启用、再平衡关闭 → 仅运行进化循环."""
        callback = make_train_evolution_rebalance_callback(enable_audit=False)

        def flag_check(name: str) -> bool:
            return name == "USE_EVOLUTION_ORCHESTRATOR"

        mock_cycle_result = MagicMock()
        mock_cycle_result.to_dict.return_value = {
            "status": "ok",
            "weight_adjustments": {"600519": 1.2},
        }
        with (
            patch(
                "utils.evolution.train_rebalance_bridge._is_flag_enabled",
                side_effect=flag_check,
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_evolution_cycle",
                return_value={"status": "ok", "weight_adjustments": {"600519": 1.2}},
            ) as mock_evolution,
            patch(
                "utils.evolution.train_rebalance_bridge._run_rebalance",
            ) as mock_rebalance,
        ):
            callback({"status": "OK"})

        mock_evolution.assert_called_once()
        mock_rebalance.assert_not_called()

    def test_evolution_disabled_rebalance_enabled(self):
        """进化关闭、再平衡启用 → 仅尝试再平衡."""
        positions_provider = MagicMock(return_value={"600519": {"weight": 0.3}})
        prices_provider = MagicMock(return_value={"600519": 1500.0})
        callback = make_train_evolution_rebalance_callback(
            positions_provider=positions_provider,
            prices_provider=prices_provider,
            enable_audit=False,
        )

        def flag_check(name: str) -> bool:
            return name == "USE_EOD_REBALANCE"

        with (
            patch(
                "utils.evolution.train_rebalance_bridge._is_flag_enabled",
                side_effect=flag_check,
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_evolution_cycle",
            ) as mock_evolution,
            patch(
                "utils.evolution.train_rebalance_bridge._run_rebalance",
                return_value={"status": "ok"},
            ) as mock_rebalance,
        ):
            callback({"status": "OK", "trade_date": "2026-08-27"})

        mock_evolution.assert_not_called()
        mock_rebalance.assert_called_once()
        positions_provider.assert_called_once()
        prices_provider.assert_called_once()

    def test_both_flags_enabled_full_chain(self):
        """双 flag 启用 + providers 提供 → 全链路运行."""
        positions_provider = MagicMock(return_value={"600519": {"weight": 0.3}})
        prices_provider = MagicMock(return_value={"600519": 1500.0})
        callback = make_train_evolution_rebalance_callback(
            positions_provider=positions_provider,
            prices_provider=prices_provider,
            enable_audit=False,
        )

        with (
            patch(
                "utils.evolution.train_rebalance_bridge._is_flag_enabled",
                return_value=True,
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_evolution_cycle",
                return_value={"status": "ok", "weight_adjustments": {"600519": 1.1}},
            ) as mock_evolution,
            patch(
                "utils.evolution.train_rebalance_bridge._run_rebalance",
                return_value={"status": "ok"},
            ) as mock_rebalance,
        ):
            callback(
                {"status": "OK", "trade_date": "2026-08-27", "current_drawdown": 0.05}
            )

        mock_evolution.assert_called_once()
        mock_rebalance.assert_called_once()


# ============================================================
# fail-safe 降级测试
# ============================================================
class TestFailSafe:
    """异常 fail-safe 降级测试."""

    def test_evolution_exception_does_not_propagate(self):
        """进化循环异常 → fail-safe 降级, 不阻断回调."""
        callback = make_train_evolution_rebalance_callback(enable_audit=False)
        with (
            patch(
                "utils.evolution.train_rebalance_bridge._is_flag_enabled",
                return_value=True,
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_evolution_cycle",
                side_effect=RuntimeError("进化引擎崩溃"),
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_rebalance",
                return_value={"status": "ok"},
            ),
        ):
            # 不应抛异常
            callback({"status": "OK"})

    def test_rebalance_exception_does_not_propagate(self):
        """再平衡异常 → fail-safe 降级, 不阻断回调."""
        positions_provider = MagicMock(return_value={"600519": {"weight": 0.3}})
        prices_provider = MagicMock(return_value={"600519": 1500.0})
        callback = make_train_evolution_rebalance_callback(
            positions_provider=positions_provider,
            prices_provider=prices_provider,
            enable_audit=False,
        )
        with (
            patch(
                "utils.evolution.train_rebalance_bridge._is_flag_enabled",
                return_value=True,
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_evolution_cycle",
                return_value={"status": "ok"},
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_rebalance",
                side_effect=RuntimeError("再平衡引擎崩溃"),
            ),
        ):
            # 不应抛异常
            callback({"status": "OK", "trade_date": "2026-08-27"})

    def test_positions_provider_returns_none_skips_rebalance(self):
        """positions_provider 返回 None → 再平衡降级跳过."""
        positions_provider = MagicMock(return_value=None)
        prices_provider = MagicMock(return_value={"600519": 1500.0})
        callback = make_train_evolution_rebalance_callback(
            positions_provider=positions_provider,
            prices_provider=prices_provider,
            enable_audit=False,
        )
        with (
            patch(
                "utils.evolution.train_rebalance_bridge._is_flag_enabled",
                return_value=True,
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_evolution_cycle",
                return_value={"status": "ok"},
            ),
            patch(
                "utils.evolution.train_rebalance_bridge._run_rebalance",
                return_value={"status": "skipped"},
            ) as mock_rebalance,
        ):
            callback({"status": "OK", "trade_date": "2026-08-27"})

        # _run_rebalance 被调用 (传入 None positions), 由其内部跳过
        mock_rebalance.assert_called_once()
        call_args = mock_rebalance.call_args
        # 位置参数调用: _run_rebalance(positions, prices, trade_date, drawdown)
        assert call_args[0][0] is None


# ============================================================
# 乘子约束测试
# ============================================================
class TestWeightMultiplierClamp:
    """weight_adjustments 乘子约束 [0.5, 2.0] 测试."""

    def test_clamp_below_min(self):
        """低于 0.5 的乘子被截断到 0.5."""
        result = _clamp_weight_adjustments({"600519": 0.1, "000858": 0.3})
        assert result["600519"] == WEIGHT_MULTIPLIER_MIN
        assert result["000858"] == WEIGHT_MULTIPLIER_MIN

    def test_clamp_above_max(self):
        """高于 2.0 的乘子被截断到 2.0."""
        result = _clamp_weight_adjustments({"600519": 5.0, "000858": 3.5})
        assert result["600519"] == WEIGHT_MULTIPLIER_MAX
        assert result["000858"] == WEIGHT_MULTIPLIER_MAX

    def test_clamp_in_range_unchanged(self):
        """[0.5, 2.0] 范围内的乘子保持不变."""
        result = _clamp_weight_adjustments(
            {"600519": 1.0, "000858": 1.5, "601318": 0.8}
        )
        assert result["600519"] == 1.0
        assert result["000858"] == 1.5
        assert result["601318"] == 0.8

    def test_clamp_empty(self):
        """空字典或 None 返回空字典."""
        assert _clamp_weight_adjustments({}) == {}
        assert _clamp_weight_adjustments(None) == {}

    def test_clamp_non_numeric_skipped(self):
        """非数值乘子被跳过."""
        result = _clamp_weight_adjustments({"600519": "abc", "000858": 1.2})
        assert "600519" not in result
        assert result["000858"] == 1.2


# ============================================================
# 审计日志测试
# ============================================================
class TestAuditLog:
    """审计日志 JSONL 写入测试."""

    def test_audit_log_written(self, tmp_path, monkeypatch):
        """启用审计时写入 JSONL 记录."""
        audit_file = tmp_path / "bridge_audit.jsonl"
        monkeypatch.setattr(
            "utils.evolution.train_rebalance_bridge.BRIDGE_AUDIT_LOG",
            audit_file,
        )
        monkeypatch.setattr(
            "utils.evolution.train_rebalance_bridge.EVOLUTION_REPORT_DIR",
            tmp_path,
        )

        callback = make_train_evolution_rebalance_callback(enable_audit=True)
        with patch(
            "utils.evolution.train_rebalance_bridge._is_flag_enabled",
            return_value=False,
        ):
            callback({"status": "OK"})

        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["bridge"] == "train_evolution_rebalance"
        assert record["evolution"]["status"] == "disabled"
        assert record["rebalance"]["status"] == "disabled"

    def test_audit_disabled_no_write(self, tmp_path, monkeypatch):
        """禁用审计时不写入文件."""
        audit_file = tmp_path / "bridge_audit.jsonl"
        monkeypatch.setattr(
            "utils.evolution.train_rebalance_bridge.BRIDGE_AUDIT_LOG",
            audit_file,
        )
        monkeypatch.setattr(
            "utils.evolution.train_rebalance_bridge.EVOLUTION_REPORT_DIR",
            tmp_path,
        )

        callback = make_train_evolution_rebalance_callback(enable_audit=False)
        with patch(
            "utils.evolution.train_rebalance_bridge._is_flag_enabled",
            return_value=False,
        ):
            callback({"status": "OK"})

        assert not audit_file.exists()


# ============================================================
# 端到端集成测试 (与 invoke_post_train_callback)
# ============================================================
class TestEndToEndIntegration:
    """与 invoke_post_train_callback 集成测试."""

    def test_invoke_post_train_callback_invokes_bridge(self):
        """invoke_post_train_callback 调用桥接回调 (flag 关闭时不报错)."""
        from autolearn_trainer import invoke_post_train_callback

        callback = make_train_evolution_rebalance_callback(enable_audit=False)
        train_result = {"status": "OK", "trained": 5, "failed": 0}
        with patch(
            "utils.evolution.train_rebalance_bridge._is_flag_enabled",
            return_value=False,
        ):
            # 通过 invoke_post_train_callback 调用 (二级 fail-safe)
            invoke_post_train_callback(callback, train_result)

    def test_bridge_exception_fail_safe_via_invoke(self):
        """桥接内部异常经 invoke_post_train_callback 二级 fail-safe 保护."""
        from autolearn_trainer import invoke_post_train_callback

        callback = make_train_evolution_rebalance_callback(enable_audit=False)
        train_result = {"status": "OK"}
        # mock _is_flag_enabled 抛异常 (会被 _run_train_evolution_rebalance 捕获)
        with patch(
            "utils.evolution.train_rebalance_bridge._is_flag_enabled",
            side_effect=RuntimeError("flag 检查崩溃"),
        ):
            # 即使桥接内部有未捕获异常, invoke_post_train_callback 也兜底
            invoke_post_train_callback(callback, train_result)
