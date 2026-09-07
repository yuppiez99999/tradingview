"""T1 回归锁: G11 蒙特卡洛 CVaR 生产配置显式固化.

背景 (2026-09-07):
    wt_risk_control.analyze_portfolio 生产路径默认 monte_carlo/student_t/50000 路径,
    但该行为靠 _CVAR_CONFIG_DEFAULT 隐式默认驱动 — system_config.json 缺
    risk_management.cvar 段时才生效. 默认值一旦被修改, 生产行为会静默漂移.

本测试锁住三件事:
    1. system_config.json 的 risk_management.cvar 段显式存在且与
       wt_risk_control._CVAR_CONFIG_DEFAULT 逐字段一致 (漂移检测);
    2. _load_cvar_config() 返回 monte_carlo 口径 (实际生效值);
    3. CVaRConfig.from_system_config() (utils/risk/cvar.py 路径) 可正常加载
       不因新增 risk_management 段崩溃 (双代码路径盲区防御 — EOD 数据断链教训).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.wt_risk_control import _CVAR_CONFIG_DEFAULT, _load_cvar_config  # noqa: E402

SYSTEM_CONFIG = PROJECT_ROOT / "config" / "system_config.json"


def _read_cvar_section() -> dict:
    cfg = json.loads(SYSTEM_CONFIG.read_text(encoding="utf-8"))
    return cfg.get("risk_management", {}).get("cvar", {})


def test_cvar_section_explicit_and_consistent():
    """system_config.json 的 cvar 段必须显式存在且与代码默认值逐字段一致."""
    assert SYSTEM_CONFIG.exists(), f"system_config.json 缺失: {SYSTEM_CONFIG}"
    section = _read_cvar_section()
    assert section, (
        "risk_management.cvar 段缺失 — 生产 CVaR 行为退回隐式默认, 漂移风险. 请恢复该段或同步更新 _CVAR_CONFIG_DEFAULT."
    )
    # 除注释外逐字段对比 (允许代码侧新增字段, 但显式配置的字段值必须一致)
    for key, expected in _CVAR_CONFIG_DEFAULT.items():
        assert key in section, f"system_config cvar 段缺字段 {key}"
        actual = section[key]
        assert type(actual) is type(expected) and actual == expected, (
            f"cvar 段字段 {key} 漂移: config={actual!r} vs 代码默认={expected!r} "
            f"— 若为有意变更, 请同步修改 wt_risk_control._CVAR_CONFIG_DEFAULT 与本测试"
        )


def test_load_cvar_config_returns_monte_carlo():
    """_load_cvar_config() 实际生效口径必须是 monte_carlo + student_t."""
    merged = _load_cvar_config()
    assert merged["method"] == "monte_carlo"
    assert merged["distribution"] == "student_t"
    assert merged["n_paths"] >= 20000, "蒙特卡洛路径数过低, 达不到工业级抽样精度"
    assert merged["seed"] == 42, "蒙特卡洛必须固定种子保证可复现"
    assert 2 <= merged["dof"] <= 30, "student_t 自由度需在合理肥尾区间"


def test_cvar_calculator_path_still_loads():
    """CVaRConfig.from_system_config() 不得因 risk_management 段新增而崩溃.

    双代码路径盲区防御: utils/risk/cvar.py 走 risk_management.cvar.risk_metric
    子段 (与 wt_risk_control 的 cvar 段路径不同), 新增兄弟段不应影响它.
    """
    from utils.risk.cvar import CVaRConfig

    cfg = CVaRConfig.from_system_config()
    # 无 risk_metric 子段时 fail-open 回默认 historical, 不抛异常即通过
    assert cfg is not None
    assert cfg.method in {"historical", "parametric", "evt", "monte_carlo"}
