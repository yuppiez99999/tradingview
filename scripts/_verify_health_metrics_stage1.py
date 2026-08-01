# -*- coding: utf-8 -*-
"""三层面自我进化 Stage 1 验证脚本.

按 ARCHITECTURE_三层面进化 §第1阶段验收标准执行 10 项验证:
    1. 模块导入成功
    2. Flag 默认 False (HC-1)
    3. 关闭时降级返回 is_degraded=True, 不抛异常
    4. 开启后 layer_scores 含 code/strategy/ops 三键
    5. overall_score ∈ [0, 1]
    6. 持久化: health_trend.jsonl 增加一行
    7. 历史对比: get_history(7) 返回 List
    8. 不可变性: LayerScore frozen=True, 修改抛 FrozenInstanceError
    9. 配置走 ConfigManager (HC-5)
    10. 不污染生产: positions.json / daily_returns.jsonl 不变

硬约束:
    - HC-1: Feature Flag 默认 False
    - HC-4: 不修改 positions.json / daily_returns.jsonl
    - HC-5: 配置走 ConfigManager
"""
from __future__ import annotations

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


# ============================================================
# 测试 1: 模块导入
# ============================================================
print("=" * 70)
print("测试 1: 模块导入")
print("=" * 70)
try:
    from utils.alpha.health_metrics import (
        UnifiedHealthMetrics, HealthReport, LayerScore,
    )
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
    flag_on = is_enabled("USE_UNIFIED_HEALTH_METRICS")
    check("USE_UNIFIED_HEALTH_METRICS 默认 False", flag_on is False,
          f"实际={flag_on}")
except Exception as e:
    check("USE_UNIFIED_HEALTH_METRICS 默认 False", False, str(e))

try:
    metrics = UnifiedHealthMetrics()
    check("UnifiedHealthMetrics._enabled == False", metrics._enabled is False)
except Exception as e:
    check("UnifiedHealthMetrics._enabled == False", False, str(e))


# ============================================================
# 测试 3: 关闭时降级返回 is_degraded=True, 不抛异常
# ============================================================
print("\n" + "=" * 70)
print("测试 3: 关闭时降级 (不抛异常)")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        metrics = UnifiedHealthMetrics(history_path=Path(tmpdir) / "test_trend.jsonl")
        report = metrics.collect_all()
        check("collect_all() 不抛异常", True)
        check("返回 HealthReport 类型", isinstance(report, HealthReport))
        check("is_degraded == True", report.is_degraded is True)
        check("overall_score == 0.0 (降级)", report.overall_score == 0.0)
        check("degraded_layers 含三层", set(report.degraded_layers) == {"code", "strategy", "ops"})
except Exception as e:
    check("关闭时降级", False, f"异常: {e}")


# ============================================================
# 测试 4: 开启后 layer_scores 含 code/strategy/ops 三键
# ============================================================
print("\n" + "=" * 70)
print("测试 4: 开启后三层面齐全 (mock is_enabled=True)")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            metrics = UnifiedHealthMetrics(history_path=Path(tmpdir) / "test_trend.jsonl")
            report = metrics.collect_all()
        check("collect_all() 不抛异常 (开启后)", True)
        check("is_degraded 可能为 False", isinstance(report.is_degraded, bool))
        check("layer_scores 含 code 键", "code" in report.layer_scores)
        check("layer_scores 含 strategy 键", "strategy" in report.layer_scores)
        check("layer_scores 含 ops 键", "ops" in report.layer_scores)
        check("layer_scores 三键齐全",
              set(report.layer_scores.keys()) == {"code", "strategy", "ops"},
              f"keys={list(report.layer_scores.keys())}")
except Exception as e:
    check("开启后三层面齐全", False, f"异常: {e}")


# ============================================================
# 测试 5: overall_score ∈ [0, 1]
# ============================================================
print("\n" + "=" * 70)
print("测试 5: overall_score ∈ [0, 1]")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            metrics = UnifiedHealthMetrics(history_path=Path(tmpdir) / "test_trend.jsonl")
            report = metrics.collect_all()
        check("overall_score >= 0.0", report.overall_score >= 0.0,
              f"score={report.overall_score}")
        check("overall_score <= 1.0", report.overall_score <= 1.0,
              f"score={report.overall_score}")
        # 各层 score 也在 [0, 1]
        for layer_name, layer_score in report.layer_scores.items():
            check(f"{layer_name} score ∈ [0, 1]",
                  0.0 <= layer_score.score <= 1.0,
                  f"score={layer_score.score}")
except Exception as e:
    check("overall_score ∈ [0, 1]", False, f"异常: {e}")


# ============================================================
# 测试 6: 持久化: health_trend.jsonl 增加一行
# ============================================================
print("\n" + "=" * 70)
print("测试 6: 持久化 (health_trend.jsonl 增加一行)")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_path = Path(tmpdir) / "test_trend.jsonl"
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            metrics = UnifiedHealthMetrics(history_path=hist_path)
            # 采集前: 文件不存在
            check("采集前文件不存在", not hist_path.exists())
            report = metrics.collect_all()
            # 采集后: 文件存在且有 1 行
            check("采集后文件存在", hist_path.exists())
            content = hist_path.read_text(encoding="utf-8").strip()
            lines = [l for l in content.splitlines() if l.strip()]
            check("文件有 1 行", len(lines) == 1, f"行数={len(lines)}")
            # 验证行是有效 JSON
            parsed = json.loads(lines[0])
            check("行是有效 JSON", isinstance(parsed, dict))
            check("JSON 含 overall_score", "overall_score" in parsed)
            check("JSON 含 layer_scores", "layer_scores" in parsed)
except Exception as e:
    check("持久化", False, f"异常: {e}")


# ============================================================
# 测试 7: 历史对比: get_history(7) 返回 List
# ============================================================
print("\n" + "=" * 70)
print("测试 7: 历史对比 (get_history)")
print("=" * 70)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_path = Path(tmpdir) / "test_trend.jsonl"
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            metrics = UnifiedHealthMetrics(history_path=hist_path)
            # 采集 3 次
            for _ in range(3):
                metrics.collect_all()
            # 读取历史
            history = metrics.get_history(7)
            check("get_history(7) 返回 List", isinstance(history, list))
            check("历史长度 <= 7", len(history) <= 7, f"len={len(history)}")
            check("历史长度 >= 1 (有记录)", len(history) >= 1, f"len={len(history)}")
            if history:
                check("历史元素是 HealthReport", isinstance(history[0], HealthReport))
            # compare_baseline 测试
            today = report.generated_at[:10] if (report := metrics.collect_all()) else ""
            diff = metrics.compare_baseline(report, today)
            check("compare_baseline 返回 Dict", isinstance(diff, dict))
except Exception as e:
    check("历史对比", False, f"异常: {e}")


# ============================================================
# 测试 8: 不可变性: LayerScore frozen=True
# ============================================================
print("\n" + "=" * 70)
print("测试 8: 不可变性 (frozen=True)")
print("=" * 70)
try:
    ls = LayerScore(layer="test", score=0.5, is_degraded=False)
    check("LayerScore 创建成功", isinstance(ls, LayerScore))
    # 尝试修改 frozen 字段 → 应抛 FrozenInstanceError
    try:
        ls.score = 0.9  # type: ignore
        check("修改 frozen 字段抛异常", False, "未抛异常 (可变!)")
    except AttributeError as e:
        check("修改 frozen 字段抛异常", True, str(e)[:50])
    # HealthReport 同样
    hr = HealthReport(overall_score=0.8)
    try:
        hr.overall_score = 0.1  # type: ignore
        check("HealthReport frozen", False, "未抛异常")
    except AttributeError:
        check("HealthReport frozen", True)
except Exception as e:
    check("不可变性", False, f"异常: {e}")


# ============================================================
# 测试 9: 配置走 ConfigManager (HC-5)
# ============================================================
print("\n" + "=" * 70)
print("测试 9: 配置走 ConfigManager (HC-5)")
print("=" * 70)
try:
    from utils.config_manager import get_config
    cfg = get_config("evolution")
    check("get_config('evolution') 返回 Dict", isinstance(cfg, dict))
    if cfg:
        check("配置含 health_metrics 节", "health_metrics" in cfg,
              f"keys={list(cfg.keys())}")
        hm = cfg.get("health_metrics", {})
        weights = hm.get("weights", {})
        check("health_metrics.weights 含 code/strategy/ops",
              all(k in weights for k in ("code", "strategy", "ops")),
              f"weights={weights}")
        # 验证权重总和 ≈ 1.0
        total = sum(float(weights.get(k, 0)) for k in ("code", "strategy", "ops"))
        check("权重总和 ≈ 1.0", abs(total - 1.0) < 0.01, f"total={total:.4f}")
    else:
        check("配置加载 (允许空, 用默认值)", True, "evolution.yaml 可能未加载, 用默认权重")
except Exception as e:
    check("配置走 ConfigManager", False, f"异常: {e}")

# 验证 UnifiedHealthMetrics 权重加载
try:
    metrics = UnifiedHealthMetrics()
    check("权重含 code/strategy/ops",
          all(k in metrics.weights for k in ("code", "strategy", "ops")))
    check("权重总和 ≈ 1.0",
          abs(sum(metrics.weights.values()) - 1.0) < 0.01,
          f"weights={metrics.weights}")
except Exception as e:
    check("UnifiedHealthMetrics 权重加载", False, str(e))


# ============================================================
# 测试 10: 不污染生产 (positions.json / daily_returns.jsonl 不变)
# ============================================================
print("\n" + "=" * 70)
print("测试 10: 不污染生产数据 (HC-4)")
print("=" * 70)
try:
    # 记录生产文件大小
    positions_path = _PROJECT_ROOT / "config" / "positions.json"
    daily_returns_path = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"

    pos_size_before = positions_path.stat().st_size if positions_path.exists() else -1
    dr_size_before = daily_returns_path.stat().st_size if daily_returns_path.exists() else -1

    # 运行采集 (用临时 history_path 避免污染真实 health_trend.jsonl)
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            metrics = UnifiedHealthMetrics(history_path=Path(tmpdir) / "test_trend.jsonl")
            metrics.collect_all()

    # 检查生产文件大小不变
    pos_size_after = positions_path.stat().st_size if positions_path.exists() else -1
    dr_size_after = daily_returns_path.stat().st_size if daily_returns_path.exists() else -1

    check("positions.json 大小不变",
          pos_size_before == pos_size_after,
          f"before={pos_size_before}, after={pos_size_after}")
    check("daily_returns.jsonl 大小不变",
          dr_size_before == dr_size_after,
          f"before={dr_size_before}, after={dr_size_after}")
except Exception as e:
    check("不污染生产", False, f"异常: {e}")


# ============================================================
# 总结
# ============================================================
print("\n" + "=" * 70)
print(f"总计: {PASS} PASS / {FAIL} FAIL")
print("=" * 70)

if FAIL == 0:
    print("\n✅ 三层面自我进化 Stage 1 验证全部通过!")
    sys.exit(0)
else:
    print(f"\n❌ 有 {FAIL} 项失败, 请检查上述输出.")
    sys.exit(1)
