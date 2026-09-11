"""test_positions_loader_unit.py — 持仓配置加载器单元测试

覆盖要点:
    - load_positions (文件不存在/正常/解析失败/自定义路径/自定义默认值)
    - get_positions_list (dict 格式/list 格式/空)
    - get_positions_dict (code 提取/无 code 用 key)
"""

from __future__ import annotations

import json

import pytest

from utils.positions_loader import (
    DEFAULT_POSITIONS_PATH,
    get_positions_dict,
    get_positions_list,
    load_positions,
)

# ============================================================
# load_positions
# ============================================================


class TestLoadPositions:
    @pytest.mark.unit
    def test_file_not_exists_returns_default(self, tmp_path):
        p = tmp_path / "nope.json"
        result = load_positions(path=p)
        assert result == {}

    @pytest.mark.unit
    def test_file_not_exists_custom_default(self, tmp_path):
        p = tmp_path / "nope.json"
        result = load_positions(path=p, default={"positions": {}})
        assert result == {"positions": {}}

    @pytest.mark.unit
    def test_normal_load(self, tmp_path):
        p = tmp_path / "positions.json"
        data = {"positions": {"600519": {"code": "600519", "weight": 0.1}}}
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_positions(path=p)
        assert "positions" in result
        assert "600519" in result["positions"]

    @pytest.mark.unit
    def test_malformed_json_returns_default(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        result = load_positions(path=p)
        assert result == {}

    @pytest.mark.unit
    def test_malformed_json_custom_default(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{bad", encoding="utf-8")
        result = load_positions(path=p, default={"fallback": True})
        assert result == {"fallback": True}

    @pytest.mark.unit
    def test_empty_json_file(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("{}", encoding="utf-8")
        result = load_positions(path=p)
        assert result == {}

    @pytest.mark.unit
    def test_default_path_constant(self):
        """DEFAULT_POSITIONS_PATH 指向 config/positions.json"""
        assert DEFAULT_POSITIONS_PATH.name == "positions.json"
        assert "config" in str(DEFAULT_POSITIONS_PATH)


# ============================================================
# get_positions_list
# ============================================================


class TestGetPositionsList:
    @pytest.mark.unit
    def test_dict_format(self, tmp_path):
        p = tmp_path / "positions.json"
        data = {
            "positions": {
                "600519": {"code": "600519", "weight": 0.1},
                "300750": {"code": "300750", "weight": 0.2},
            }
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = get_positions_list(path=p)
        assert len(result) == 2
        codes = {item["code"] for item in result}
        assert "600519" in codes
        assert "300750" in codes

    @pytest.mark.unit
    def test_list_format(self, tmp_path):
        p = tmp_path / "positions.json"
        data = {
            "positions": [
                {"code": "600519", "weight": 0.1},
                {"code": "300750", "weight": 0.2},
            ]
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = get_positions_list(path=p)
        assert len(result) == 2

    @pytest.mark.unit
    def test_file_not_exists(self, tmp_path):
        p = tmp_path / "nope.json"
        result = get_positions_list(path=p)
        assert result == []

    @pytest.mark.unit
    def test_no_positions_key(self, tmp_path):
        p = tmp_path / "positions.json"
        p.write_text(json.dumps({"other": 1}), encoding="utf-8")
        result = get_positions_list(path=p)
        assert result == []


# ============================================================
# get_positions_dict
# ============================================================


class TestGetPositionsDict:
    @pytest.mark.unit
    def test_with_code_field(self, tmp_path):
        p = tmp_path / "positions.json"
        data = {
            "positions": {
                "key1": {"code": "600519", "weight": 0.1},
                "key2": {"code": "300750", "weight": 0.2},
            }
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = get_positions_dict(path=p)
        assert "600519" in result
        assert "300750" in result
        assert result["600519"]["weight"] == 0.1

    @pytest.mark.unit
    def test_without_code_field_uses_key(self, tmp_path):
        p = tmp_path / "positions.json"
        data = {
            "positions": {
                "600519": {"weight": 0.1},  # 无 code, 用 key
            }
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = get_positions_dict(path=p)
        assert "600519" in result

    @pytest.mark.unit
    def test_non_dict_value(self, tmp_path):
        p = tmp_path / "positions.json"
        data = {
            "positions": {
                "600519": 0.1,  # 非 dict 值
            }
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = get_positions_dict(path=p)
        assert result["600519"] == 0.1

    @pytest.mark.unit
    def test_file_not_exists(self, tmp_path):
        p = tmp_path / "nope.json"
        result = get_positions_dict(path=p)
        assert result == {}

    @pytest.mark.unit
    def test_list_positions_returns_empty(self, tmp_path):
        """positions 是 list 而非 dict → 返回空 dict"""
        p = tmp_path / "positions.json"
        data = {"positions": [{"code": "600519"}]}
        p.write_text(json.dumps(data), encoding="utf-8")
        result = get_positions_dict(path=p)
        assert result == {}


class TestP03MissingFileWarning:
    """P0-3: 持仓文件缺失必须以 WARNING 声明, 不得静默 (debug 级) 降级."""

    @pytest.mark.unit
    def test_missing_file_logs_warning(self, tmp_path, caplog):
        import logging

        from utils.positions_loader import load_positions

        p = tmp_path / "nope.json"
        with caplog.at_level(logging.WARNING):
            result = load_positions(path=p)
        assert result == {}
        assert any(
            "持仓文件不存在" in r.message and "数据缺失" in r.message
            for r in caplog.records
        ), "缺失文件必须以 WARNING + 明确语义记录, 而非 debug 静默"


class TestP03RebalanceStrictMode:
    """P0-3: 再平衡 load_positions strict 模式 fail-closed."""

    @pytest.mark.unit
    def test_strict_raises_on_missing_file(self, tmp_path, monkeypatch):
        from utils.execution import rebalance_execution_orders as reb

        monkeypatch.setattr(reb, "_PROJECT_ROOT", tmp_path)
        with pytest.raises(reb.PositionFileError):
            reb.load_positions(strict=True)

    @pytest.mark.unit
    def test_lenient_keeps_old_behavior(self, tmp_path, monkeypatch):
        from utils.execution import rebalance_execution_orders as reb

        monkeypatch.setattr(reb, "_PROJECT_ROOT", tmp_path)
        positions, prices, styles = reb.load_positions(strict=False)
        assert positions == {} and prices == {} and styles == {}

    @pytest.mark.unit
    def test_strict_ok_with_valid_file(self, tmp_path, monkeypatch):
        from utils.execution import rebalance_execution_orders as reb

        cfg = tmp_path / "config"
        cfg.mkdir()
        (cfg / "positions.json").write_text(
            json.dumps(
                {"positions": {"600519.SH": {"code": "600519", "total_shares": 100,
                                             "est_price": 1700.0, "style": "宽基"}}}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(reb, "_PROJECT_ROOT", tmp_path)
        positions, prices, styles = reb.load_positions(strict=True)
        assert positions == {"600519": 100.0}
        assert prices == {"600519": 1700.0}
