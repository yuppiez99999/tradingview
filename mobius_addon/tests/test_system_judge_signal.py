"""回归测试：系统判断 verdict 不应被 '信号一致性' 子串误判为 agree。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.system_judge as sj  # noqa: E402


def _patch_signal(monkeypatch, status, msg):
    monkeypatch.setattr(sj, "_signal_check", lambda code: (status, msg))


def test_skip_signal_does_not_force_agree(monkeypatch):
    _patch_signal(monkeypatch, "skip", "未找到 alpha_signals 产物, 跳过信号一致性校验")
    # 无持仓、无信号，verdict 应为 neutral，而非被 '一致性' 误判为 agree
    res = sj.judge({"stock_code": "999999", "industry": ""})
    assert res["verdict"] == "neutral"


def test_conflict_signal_sets_conflict(monkeypatch):
    _patch_signal(monkeypatch, "conflict", "系统信号为负(-0.5), 与研报看多方向相反")
    res = sj.judge({"stock_code": "999999", "industry": ""})
    assert res["verdict"] == "conflict"


def test_agree_signal_sets_agree(monkeypatch):
    _patch_signal(monkeypatch, "agree", "系统信号为正(0.4), 与研报看多方向一致")
    res = sj.judge({"stock_code": "999999", "industry": ""})
    assert res["verdict"] == "agree"
