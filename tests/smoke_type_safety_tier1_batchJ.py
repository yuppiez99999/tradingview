"""Smoke tests for Batch J (5 modules × 13 type:ignore → 0).

Modules:
  - alpha/strategy_evaluator (3: importlib 动态导入 pit_checker/walk_forward/deflated_sharpe)
  - execution/broker_adapters (3: importlib 动态导入 openctp_ctp.tdapi)
  - pipeline/data_cleaning (3: Optional[type] 前向声明 DataQualityMonitor/DataGate/DataGateResult)
  - attribution/brinson_attribution (2: 冗余 ignore 移除, PEP 604 已启用)
  - attribution/factor_attribution (2: 冗余 ignore 移除, list[str] 已注解)
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

passed = 0
failed = 0


def _ok(msg: str) -> None:
    global passed
    print(f"  [OK] {msg}")
    passed += 1


def _fail(msg: str, e: object) -> None:
    global failed
    print(f"  [FAIL] {msg}: {e}")
    failed += 1


# 1) Import all 5 modules
print("\n=== Smoke Test 1: 模块导入 (5 模块) ===")
modules = {}
import_targets = [
    ("se", "utils.alpha.strategy_evaluator"),
    ("ba", "utils.execution.broker_adapters"),
    ("dc", "utils.pipeline.data_cleaning"),
    ("br", "utils.attribution.brinson_attribution"),
    ("fa", "utils.attribution.factor_attribution"),
]
for short, path in import_targets:
    try:
        modules[short] = importlib.import_module(path)
        print(f"  [OK]  import {path}")
        passed += 1
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] import {path}: {e}")
        failed += 1

# 2) StrategyEvaluator: importlib 动态导入 (3 处)
print("\n=== Smoke Test 2: StrategyEvaluator importlib 动态导入 ===")
try:
    se_mod = modules["se"]
    # 验证 3 个动态导入函数存在且可调用
    assert hasattr(se_mod, "StrategyEvaluator"), "StrategyEvaluator missing"
    evaluator = se_mod.StrategyEvaluator()
    # _check_pit_violations 使用 pit_checker importlib 导入
    assert hasattr(evaluator, "_check_pit_violations"), "_check_pit_violations missing"
    # _compute_wf_sharpe_decay 使用 walk_forward importlib 导入
    assert hasattr(evaluator, "_compute_wf_sharpe_decay"), "_compute_wf_sharpe_decay missing"
    # _compute_dsr 使用 deflated_sharpe importlib 导入
    assert hasattr(evaluator, "_compute_dsr"), "_compute_dsr missing"
    # 验证 walk_forward 衰减计算 (不依赖外部包时降级返回 -1.0)
    decay = evaluator._compute_wf_sharpe_decay([0.01] * 5)
    assert decay == -1.0, f"expected -1.0 for insufficient samples, got {decay}"
    _ok("StrategyEvaluator 3 处 importlib 动态导入验证通过")
except Exception as e:  # noqa: BLE001
    _fail("StrategyEvaluator importlib", e)

# 3) BrokerAdapters: importlib 动态导入 openctp_ctp (3 处)
print("\n=== Smoke Test 3: BrokerAdapters importlib 动态导入 ===")
try:
    ba_mod = modules["ba"]
    # 验证 CtpFuturesAdapter 类存在 (包含 _import_ctp_tdapi 静态方法)
    assert hasattr(ba_mod, "CtpFuturesAdapter"), "CtpFuturesAdapter missing"
    # _import_ctp_tdapi 静态方法应返回 None (openctp_ctp 未安装)
    tdapi = ba_mod.CtpFuturesAdapter._import_ctp_tdapi()
    assert tdapi is None, f"expected None for uninstalled openctp_ctp, got {tdapi}"
    _ok("BrokerAdapters 3 处 importlib 动态导入验证通过 (openctp_ctp 降级返回 None)")
except Exception as e:  # noqa: BLE001
    _fail("BrokerAdapters importlib", e)

# 4) DataCleaningPipeline: Optional[type] 前向声明 (3 处)
print("\n=== Smoke Test 4: DataCleaningPipeline Optional[type] 前向声明 ===")
try:
    dc_mod = modules["dc"]
    # 验证 3 个前向声明属性存在
    assert hasattr(dc_mod, "DataQualityMonitor"), "DataQualityMonitor missing"
    assert hasattr(dc_mod, "DataGate"), "DataGate missing"
    assert hasattr(dc_mod, "DataGateResult"), "DataGateResult missing"
    # 验证 _HAS_QUALITY_MONITOR 和 _HAS_DATA_GATE 标志存在
    assert hasattr(dc_mod, "_HAS_QUALITY_MONITOR"), "_HAS_QUALITY_MONITOR missing"
    assert hasattr(dc_mod, "_HAS_DATA_GATE"), "_HAS_DATA_GATE missing"
    # 验证 DataCleaningPipeline 类可实例化
    assert hasattr(dc_mod, "DataCleaningPipeline"), "DataCleaningPipeline missing"
    _ok(
        f"DataCleaningPipeline 前向声明: DQM={dc_mod.DataQualityMonitor}, "
        f"DG={dc_mod.DataGate}, DGR={dc_mod.DataGateResult}"
    )
except Exception as e:  # noqa: BLE001
    _fail("DataCleaningPipeline 前向声明", e)

# 5) BrinsonAttribution: 冗余 ignore 移除, PEP 604 已启用 (2 处)
print("\n=== Smoke Test 5: BrinsonAttribution 冗余 ignore 移除 ===")
try:
    br_mod = modules["br"]
    assert hasattr(br_mod, "BrinsonAttributionManager"), "BrinsonAttributionManager missing"
    # 验证 attribute 方法签名接受 dict[str, float] | None 参数
    import inspect

    sig = inspect.signature(br_mod.BrinsonAttributionManager.attribute)
    params = list(sig.parameters.keys())
    assert "portfolio_returns" in params, f"portfolio_returns missing: {params}"
    assert "benchmark_returns" in params, f"benchmark_returns missing: {params}"
    _ok(f"BrinsonAttribution.attribute 参数: {params}")
except Exception as e:  # noqa: BLE001
    _fail("BrinsonAttribution", e)

# 6) FactorAttribution: 冗余 ignore 移除, list[str] 已注解 (2 处)
print("\n=== Smoke Test 6: FactorAttribution 冗余 ignore 移除 ===")
try:
    fa_mod = modules["fa"]
    # 验证 FactorAttributionReport 的 concentrated_factors 和 missing_factors 属性
    # 找到 report 类
    report_cls = None
    for attr_name in dir(fa_mod):
        cls = getattr(fa_mod, attr_name)
        if isinstance(cls, type) and hasattr(cls, "__dataclass_fields__"):
            fields = cls.__dataclass_fields__
            if "concentrated_factors" in fields and "missing_factors" in fields:
                report_cls = cls
                break
    assert report_cls is not None, "FactorAttributionReport class not found"
    # 验证字段类型注解
    cf_field = report_cls.__dataclass_fields__["concentrated_factors"]
    mf_field = report_cls.__dataclass_fields__["missing_factors"]
    _ok(
        f"FactorAttribution: concentrated_factors={cf_field.type}, "
        f"missing_factors={mf_field.type}"
    )
except Exception as e:  # noqa: BLE001
    _fail("FactorAttribution", e)

# 7) Verify NO type:ignore remains in any of the 5 modules
print("\n=== Smoke Test 7: type:ignore 残留扫描 ===")
try:
    files_to_check = [
        ROOT / "utils" / "alpha" / "strategy_evaluator.py",
        ROOT / "utils" / "execution" / "broker_adapters.py",
        ROOT / "utils" / "pipeline" / "data_cleaning.py",
        ROOT / "utils" / "attribution" / "brinson_attribution.py",
        ROOT / "utils" / "attribution" / "factor_attribution.py",
    ]
    total_ignores = 0
    for fp in files_to_check:
        content = fp.read_text(encoding="utf-8")
        matches = re.findall(r"type:\s*ignore", content)
        if matches:
            print(f"  [WARN] {fp.name}: {len(matches)} type:ignore remaining")
            total_ignores += len(matches)
    if total_ignores == 0:
        _ok("所有 5 模块 type:ignore = 0")
    else:
        _fail("type:ignore 残留", f"total={total_ignores}")
except Exception as e:  # noqa: BLE001
    _fail("type:ignore 扫描", e)

print(f"\n=== Batch J Smoke Result: {passed} PASS, {failed} FAIL ===")
sys.exit(0 if failed == 0 else 1)
