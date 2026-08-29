"""
单元测试: utils/trade_plan_validator.py
覆盖 TradePlanValidator.validate / validate_file / auto_fix + strict/non-strict 模式
"""

from __future__ import annotations

import json

from utils.trade_plan_validator import TradePlanValidator


def _valid_plan() -> dict:
    return {
        "trade_date": "20260818",
        "phase": {"daily_capital": 150000, "day_capital": 150000},
        "execution_plan": {
            "morning_orders": [
                {"symbol": "000001.SZ", "direction": "buy", "shares": 100}
            ],
            "afternoon_orders": [],
        },
        "market_state": {
            "spot_build_allowed": True,
            "build_allowed": True,
            "circuit_level": "NORMAL",
        },
        "risk_guard": {"drawdown_level": 0},
    }


class TestValidateBasic:
    def test_valid_plan(self):
        v = TradePlanValidator()
        result = v.validate(_valid_plan())
        assert result["valid"] is True
        assert result["errors"] == []

    def test_non_dict_input(self):
        v = TradePlanValidator()
        result = v.validate("not a dict")
        assert result["valid"] is False
        assert "dict" in result["errors"][0]

    def test_empty_dict(self):
        v = TradePlanValidator()
        result = v.validate({})
        assert result["valid"] is False
        assert len(result["errors"]) > 0


class TestStrictMode:
    def test_strict_missing_field_is_error(self):
        v = TradePlanValidator(strict=True)
        plan = _valid_plan()
        del plan["trade_date"]
        result = v.validate(plan)
        assert result["valid"] is False
        assert any("trade_date" in e for e in result["errors"])

    def test_non_strict_missing_field_is_warning(self):
        v = TradePlanValidator(strict=False)
        plan = _valid_plan()
        del plan["trade_date"]
        result = v.validate(plan)
        assert any("trade_date" in w for w in result["warnings"])


class TestTypeChecks:
    def test_wrong_type_top_level(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["phase"] = "should be dict"
        result = v.validate(plan)
        assert result["valid"] is False
        assert any("phase" in e and "类型错误" in e for e in result["errors"])

    def test_wrong_type_phase_capital(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["phase"]["daily_capital"] = "should be number"
        result = v.validate(plan)
        assert result["valid"] is False

    def test_negative_capital(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["phase"]["daily_capital"] = -100
        result = v.validate(plan)
        assert result["valid"] is False
        assert any("< 0" in e for e in result["errors"])

    def test_capital_mismatch_warning(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["phase"]["daily_capital"] = 150000
        plan["phase"]["day_capital"] = 140000
        result = v.validate(plan)
        assert any("daily_capital" in w for w in result["warnings"])

    def test_wrong_type_orders(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["execution_plan"]["morning_orders"] = "not a list"
        result = v.validate(plan)
        assert result["valid"] is False

    def test_order_missing_field(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["execution_plan"]["morning_orders"] = [{"symbol": "000001.SZ"}]
        result = v.validate(plan)
        assert result["valid"] is False
        assert any("direction" in e for e in result["errors"])

    def test_order_non_dict(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["execution_plan"]["morning_orders"] = ["not a dict"]
        result = v.validate(plan)
        assert result["valid"] is False

    def test_zero_shares_warning(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["execution_plan"]["morning_orders"] = [
            {"symbol": "000001.SZ", "direction": "buy", "shares": 0}
        ]
        result = v.validate(plan)
        assert any("shares" in w for w in result["warnings"])


class TestMarketState:
    def test_invalid_circuit_level(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["market_state"]["circuit_level"] = "INVALID"
        result = v.validate(plan)
        assert result["valid"] is False
        assert any("circuit_level" in e for e in result["errors"])

    def test_wrong_type_spot_build_allowed(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["market_state"]["spot_build_allowed"] = "yes"
        result = v.validate(plan)
        assert result["valid"] is False


class TestRiskGuard:
    def test_invalid_drawdown_level(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["risk_guard"]["drawdown_level"] = 99
        result = v.validate(plan)
        assert result["valid"] is False

    def test_drawdown_non_int(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["risk_guard"]["drawdown_level"] = 1.5
        result = v.validate(plan)
        assert result["valid"] is False

    def test_kill_switch_not_dict(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["risk_guard"]["kill_switch"] = "not dict"
        result = v.validate(plan)
        assert result["valid"] is False


class TestConsistency:
    def test_critical_with_spot_build_error(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["market_state"]["circuit_level"] = "CRITICAL"
        plan["market_state"]["spot_build_allowed"] = True
        plan["risk_guard"]["drawdown_level"] = 3
        result = v.validate(plan)
        assert result["valid"] is False
        assert any("CRITICAL" in e for e in result["errors"])

    def test_critical_with_spot_build_disabled(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["market_state"]["circuit_level"] = "CRITICAL"
        plan["market_state"]["spot_build_allowed"] = False
        plan["market_state"]["build_allowed"] = False
        plan["risk_guard"]["drawdown_level"] = 3
        result = v.validate(plan)
        assert result["valid"] is True

    def test_warning_build_allowed_warning(self):
        v = TradePlanValidator()
        plan = _valid_plan()
        plan["market_state"]["circuit_level"] = "WARNING"
        plan["market_state"]["build_allowed"] = True
        plan["risk_guard"]["drawdown_level"] = 1
        result = v.validate(plan)
        assert any("build_allowed" in w for w in result["warnings"])


class TestValidateFile:
    def test_nonexistent_file(self, tmp_path):
        v = TradePlanValidator()
        result = v.validate_file(tmp_path / "nonexistent.json")
        assert result["valid"] is False
        assert "不存在" in result["errors"][0]

    def test_invalid_json(self, tmp_path):
        v = TradePlanValidator()
        f = tmp_path / "bad.json"
        f.write_text("{bad json", encoding="utf-8")
        result = v.validate_file(f)
        assert result["valid"] is False
        assert "JSON" in result["errors"][0]

    def test_valid_file(self, tmp_path):
        v = TradePlanValidator()
        f = tmp_path / "plan.json"
        f.write_text(json.dumps(_valid_plan()), encoding="utf-8")
        result = v.validate_file(f)
        assert result["valid"] is True
        assert result["file_path"] == str(f)


class TestAutoFix:
    def test_fix_empty_plan(self):
        v = TradePlanValidator()
        fixed = v.auto_fix({})
        assert "phase" in fixed
        assert "execution_plan" in fixed
        assert "market_state" in fixed
        assert "risk_guard" in fixed
        assert len(fixed["_fixes_applied"]) > 0

    def test_fix_preserves_existing(self):
        v = TradePlanValidator()
        plan = {"phase": {"daily_capital": 200000}}
        fixed = v.auto_fix(plan)
        assert fixed["phase"]["daily_capital"] == 200000

    def test_fix_non_dict(self):
        v = TradePlanValidator()
        fixed = v.auto_fix("not a dict")
        assert fixed == {"_fixes_applied": []}
