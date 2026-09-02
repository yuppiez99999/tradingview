# T2 Health Score 聚合引擎 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 System Health Score 聚合引擎（五维遥测 → 加权评分落盘 JSON → 摘要注入每日状态报告），兑现《v8.7 Production Edition 架构升级方案》T2。

**Architecture:** 纯报告侧零侵入。`utils/health/score_engine.py` 承载全部纯函数（五个维度评分器 + 聚合），`scripts/compute_health_score.py` 为 CLI 入口（幂等落盘 `reports/health_score/health_score_{date}.json`），`scripts/generate_daily_status_report.py` 最小修改读取该 JSON 并在报告头部插入"零、系统健康评分"节。降级语义：某维数据不可得 → 60 分中性值 + `degraded=True` 显式标记（fail-open，观测路径不阻断）。

**Tech Stack:** pytest（tmp_path 构造五维 fixture）、json/pathlib、无第三方依赖。

**上游设计:** `docs/v8.7_Production_Edition_架构升级方案_200万实盘版_20260902.md` §4.2

**已核验的真实数据源（2026-09-02 全部实存）:**

| 维度 | 权重 | 数据源 | 关键字段 |
| --- | --- | --- | --- |
| model | 0.25 | `reports/drift/integration_{date}.json` | `delayed_metrics.ic/rank_ic/ic_ir`、`ic_degradation`、`alerts`、`skipped`、`error` |
| data | 0.20 | `reports/degradation_log.jsonl` | 每行 `{ts, scope, key, default, reason}`，按 `ts.startswith(date)` 过滤 |
| trading | 0.15 | `reports/tca/fills_{date}.jsonl` | 每行 `{type:"fill", fill:{...}, estimate:{...}|null}` |
| risk | 0.20 | `reports/evolution/vol_regime_weights_{date}.json` | `regime.label/confidence`、`degraded`、`observation_phase` |
| capital | 0.20 | `output/shadow_account/s12_shadow_state.json` | `nav`、`trading_day_count`、`fail_fast_triggered` |

**验收对照（方案 T2 验收标准）:**
- 引擎可产出 JSON 落盘 ✓（Task 6 真实数据运行验证）
- 五维中 ≥3 维真实数据、不可得维显式 degraded ✓（当前五维全部接真实源）
- 单测覆盖加权与降级语义 ✓（Task 1-5）
- 摘要注入每日状态报告 ✓（Task 7）
- "连续 5 交易日产出" 为运营期验证（Task 8 计划任务注册后自然累积，不在本会话内验收）

**与方案的一处口径调整（记录在案）:** 方案 §4.2 写"计划任务 17:15（衔接 17:10 状态报告）"，但注入要求评分先于报告生成——本计划将调度定为 **17:05**（报告 17:10 读取同日评分），语义更自洽。

**评分规则（v1，确定性、可单测）:**

| 维 | 规则 |
| --- | --- |
| model | 基础 70；`ic_degradation` <0.3 → +30，<0.6 → +15，≥0.6 → +0；每条 alert −10（下限 0）；文件缺失/skipped/error/解析失败 → degraded 60 |
| data | 当日条目 0 → 100；1-2 → 80；3-5 → 60；≥6 → 40；日志文件缺失 → degraded 60 |
| trading | 文件缺失或 0 笔成交 → degraded 60；有成交且 estimate 覆盖率 ≥50% → 100；覆盖不足 → 70 |
| risk | 文件缺失 → degraded 60；`degraded=true` → 50；regime bull → 100 / sideways·neutral → 95 / bear → 80 / 其他 → 90 |
| capital | 状态文件缺失 → degraded 60；`fail_fast_triggered` → 0；nav ∈ [0.5, 2.0] → 100；否则 50 |
| 总分 | 加权和四舍五入 1 位小数；≥85 GREEN / ≥70 YELLOW / <70 RED |

**已知噪音（v1 接受并记录）:** `degradation_log.jsonl` 含测试进程产生的条目（同日多次 pytest 运行会累计），v1 不区分来源，文档注明。

---

### Task 1: 引擎包骨架（DimensionScore / WEIGHTS / status_for）

**Files:**
- Create: `utils/health/__init__.py`
- Create: `utils/health/score_engine.py`
- Test: `tests/unit/test_health_score_engine.py`

- [ ] **Step 1: 写失败测试**

```python
"""Health Score 聚合引擎单测 (Production Edition T2, 2026-09-02)."""
from __future__ import annotations

from pathlib import Path

from utils.health.score_engine import (
    DEGRADED_NEUTRAL,
    WEIGHTS,
    DimensionScore,
    status_for,
)


class TestEngineSkeleton:
    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
        assert set(WEIGHTS) == {"model", "data", "trading", "risk", "capital"}

    def test_status_thresholds(self):
        assert status_for(85.0) == "GREEN"
        assert status_for(100.0) == "GREEN"
        assert status_for(84.9) == "YELLOW"
        assert status_for(70.0) == "YELLOW"
        assert status_for(69.9) == "RED"
        assert status_for(0.0) == "RED"

    def test_degraded_neutral_is_60(self):
        assert DEGRADED_NEUTRAL == 60.0

    def test_dimension_score_fields(self):
        d = DimensionScore(score=80.0, weight=0.2, degraded=True, detail={"k": 1})
        assert d.score == 80.0
        assert d.weight == 0.2
        assert d.degraded is True
        assert d.detail == {"k": 1}
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py -v`
Expected: FAIL（ModuleNotFoundError: utils.health）

- [ ] **Step 3: 写最小实现**

`utils/health/__init__.py`:

```python
"""System Health Score 聚合引擎包 (Production Edition T2)."""
```

`utils/health/score_engine.py`:

```python
"""System Health Score 聚合引擎 (Production Edition T2, 2026-09-02).

五维遥测 → 加权评分 (0-100), 报告侧零侵入:
  model   0.25  reports/drift/integration_{date}.json (IC/ICIR/漂移)
  data    0.20  reports/degradation_log.jsonl (当日降级条目)
  trading 0.15  reports/tca/fills_{date}.jsonl (成交与 TCA 预估覆盖)
  risk    0.20  reports/evolution/vol_regime_weights_{date}.json (regime)
  capital 0.20  output/shadow_account/s12_shadow_state.json (NAV/fail-fast)

降级语义: 某维数据不可得 → 60 分中性值 + degraded=True 显式标记
(fail-open, 观测路径不阻断).

已知噪音 (v1 接受): degradation_log.jsonl 含测试进程产生的条目,
同日多次 pytest 运行会累计, v1 不区分来源.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

DEGRADED_NEUTRAL = 60.0

WEIGHTS: dict[str, float] = {
    "model": 0.25,
    "data": 0.20,
    "trading": 0.15,
    "risk": 0.20,
    "capital": 0.20,
}


@dataclass
class DimensionScore:
    """单维评分: 0-100 分 + 权重 + 降级标记 + 明细."""

    score: float
    weight: float
    degraded: bool
    detail: dict = field(default_factory=dict)


def status_for(total: float) -> str:
    """总分 → 状态色: ≥85 GREEN / ≥70 YELLOW / <70 RED."""
    if total >= 85.0:
        return "GREEN"
    if total >= 70.0:
        return "YELLOW"
    return "RED"


def _degraded(dimension: str, reason: str) -> DimensionScore:
    """数据不可得时的中性降级评分."""
    return DimensionScore(
        score=DEGRADED_NEUTRAL,
        weight=WEIGHTS[dimension],
        degraded=True,
        detail={"reason": reason},
    )
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add utils/health/__init__.py utils/health/score_engine.py tests/unit/test_health_score_engine.py
git commit -m "feat(health): Health Score 引擎骨架 — 维度数据类/权重/状态阈值"
```

---

### Task 2: score_model（drift integration 解析）

**Files:**
- Modify: `utils/health/score_engine.py`（追加 score_model）
- Test: `tests/unit/test_health_score_engine.py`（追加 TestScoreModel）

- [ ] **Step 1: 写失败测试**

```python
import json

from utils.health.score_engine import score_model


def _write_drift(root: Path, date: str, payload: dict) -> None:
    d = root / "reports" / "drift"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"integration_{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class TestScoreModel:
    DATE = "2026-09-01"

    def _good(self, **over):
        p = {
            "date": self.DATE,
            "skipped": False,
            "error": None,
            "ic_degradation": 0.1,
            "alerts": [],
            "delayed_metrics": {"ic": 0.05, "rank_ic": 0.06, "ic_ir": 0.8},
        }
        p.update(over)
        return p

    def test_missing_file_degraded(self, tmp_path):
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.25

    def test_low_degradation_no_alerts_full_score(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good())
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is False
        assert d.score == 100.0
        assert d.detail["ic"] == 0.05

    def test_medium_degradation(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(ic_degradation=0.5))
        assert score_model(tmp_path, self.DATE).score == 85.0

    def test_high_degradation(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(ic_degradation=0.88))
        assert score_model(tmp_path, self.DATE).score == 70.0

    def test_alerts_deduct_with_floor(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(alerts=["a", "b", "c"]))
        assert score_model(tmp_path, self.DATE).score == 70.0  # 100-30

    def test_alerts_floor_zero(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(alerts=[str(i) for i in range(12)]))
        assert score_model(tmp_path, self.DATE).score == 0.0

    def test_skipped_is_degraded(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(skipped=True))
        d = score_model(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0

    def test_error_is_degraded(self, tmp_path):
        _write_drift(tmp_path, self.DATE, self._good(error="boom"))
        assert score_model(tmp_path, self.DATE).degraded is True

    def test_corrupt_json_is_degraded(self, tmp_path):
        d = tmp_path / "reports" / "drift"
        d.mkdir(parents=True)
        (d / f"integration_{self.DATE}.json").write_text("{bad", encoding="utf-8")
        assert score_model(tmp_path, self.DATE).degraded is True
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py::TestScoreModel -v`
Expected: FAIL（ImportError: score_model 不存在）

- [ ] **Step 3: 写实现（追加到 score_engine.py 末尾）**

```python
def score_model(project_root: Path, date: str) -> DimensionScore:
    """模型维: drift integration 的 IC 退化与告警."""
    path = project_root / "reports" / "drift" / f"integration_{date}.json"
    if not path.exists():
        return _degraded("model", "drift integration 文件不存在")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _degraded("model", "drift integration JSON 解析失败")
    if not isinstance(data, dict):
        return _degraded("model", "drift integration 结构异常")
    if data.get("skipped") or data.get("error"):
        return _degraded("model", f"drift integration 未完成: {data.get('error')}")

    try:
        ic_deg = float(data.get("ic_degradation", 1.0))
    except (TypeError, ValueError):
        ic_deg = 1.0
    score = 70.0
    if ic_deg < 0.3:
        score += 30.0
    elif ic_deg < 0.6:
        score += 15.0
    alerts = data.get("alerts") or []
    score = max(0.0, score - 10.0 * len(alerts))
    dm = data.get("delayed_metrics") or {}
    return DimensionScore(
        score=score,
        weight=WEIGHTS["model"],
        degraded=False,
        detail={
            "ic": dm.get("ic"),
            "rank_ic": dm.get("rank_ic"),
            "ic_ir": dm.get("ic_ir"),
            "ic_degradation": ic_deg,
            "alerts": alerts,
        },
    )
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add utils/health/score_engine.py tests/unit/test_health_score_engine.py
git commit -m "feat(health): 模型维评分 — IC 退化分档 + 告警扣分 + 降级语义"
```

---

### Task 3: score_data（降级日志当日条目计数）

**Files:**
- Modify: `utils/health/score_engine.py`（追加 score_data）
- Test: `tests/unit/test_health_score_engine.py`（追加 TestScoreData）

- [ ] **Step 1: 写失败测试**

```python
from utils.health.score_engine import score_data


def _write_degradation_log(root: Path, records: list[dict]) -> None:
    d = root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (d / "degradation_log.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _deg(ts: str, scope: str = "config_manager") -> dict:
    return {"ts": ts, "scope": scope, "key": "k", "default": "d", "reason": "r"}


class TestScoreData:
    DATE = "2026-09-01"

    def test_missing_log_degraded(self, tmp_path):
        d = score_data(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.20

    def test_zero_entries_full_score(self, tmp_path):
        _write_degradation_log(tmp_path, [])
        d = score_data(tmp_path, self.DATE)
        assert d.degraded is False
        assert d.score == 100.0

    def test_other_dates_ignored(self, tmp_path):
        _write_degradation_log(tmp_path, [_deg("2026-08-31T10:00:00")])
        assert score_data(tmp_path, self.DATE).score == 100.0

    def test_one_or_two_entries_80(self, tmp_path):
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:00:00"), _deg(f"{self.DATE}T11:00:00")],
        )
        assert score_data(tmp_path, self.DATE).score == 80.0

    def test_three_to_five_entries_60(self, tmp_path):
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:0{i}:00") for i in range(3)],
        )
        assert score_data(tmp_path, self.DATE).score == 60.0

    def test_six_plus_entries_40(self, tmp_path):
        _write_degradation_log(
            tmp_path,
            [_deg(f"{self.DATE}T10:0{i}:00") for i in range(6)],
        )
        assert score_data(tmp_path, self.DATE).score == 40.0

    def test_detail_has_scopes(self, tmp_path):
        _write_degradation_log(
            tmp_path, [_deg(f"{self.DATE}T10:00:00", scope="z_mod"), _deg(f"{self.DATE}T10:01:00", scope="a_mod")]
        )
        d = score_data(tmp_path, self.DATE)
        assert d.detail["entries"] == 2
        assert d.detail["scopes"] == ["a_mod", "z_mod"]

    def test_corrupt_lines_skipped(self, tmp_path):
        d = tmp_path / "reports"
        d.mkdir(parents=True)
        (d / "degradation_log.jsonl").write_text(
            "{bad\n" + json.dumps(_deg(f"{self.DATE}T10:00:00")) + "\n", encoding="utf-8"
        )
        assert score_data(tmp_path, self.DATE).score == 80.0
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py::TestScoreData -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 写实现（追加到 score_engine.py 末尾）**

```python
def score_data(project_root: Path, date: str) -> DimensionScore:
    """数据维: 当日降级审计条目数 (含测试进程噪音, v1 不区分)."""
    path = project_root / "reports" / "degradation_log.jsonl"
    if not path.exists():
        return _degraded("data", "degradation_log.jsonl 不存在")
    n = 0
    scopes: set[str] = set()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return _degraded("data", "degradation_log.jsonl 读取失败")
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if str(rec.get("ts", "")).startswith(date):
            n += 1
            scopes.add(str(rec.get("scope", "")))
    if n == 0:
        score = 100.0
    elif n <= 2:
        score = 80.0
    elif n <= 5:
        score = 60.0
    else:
        score = 40.0
    return DimensionScore(
        score=score,
        weight=WEIGHTS["data"],
        degraded=False,
        detail={"entries": n, "scopes": sorted(scopes)},
    )
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add utils/health/score_engine.py tests/unit/test_health_score_engine.py
git commit -m "feat(health): 数据维评分 — 当日降级条目分档计数"
```

---

### Task 4: score_trading + score_risk

**Files:**
- Modify: `utils/health/score_engine.py`（追加两个评分器）
- Test: `tests/unit/test_health_score_engine.py`（追加 TestScoreTrading / TestScoreRisk）

- [ ] **Step 1: 写失败测试**

```python
from utils.health.score_engine import score_risk, score_trading


def _write_tca(root: Path, date: str, records: list[dict]) -> None:
    d = root / "reports" / "tca"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (d / f"fills_{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fill(estimate) -> dict:
    return {
        "type": "fill",
        "fill": {"symbol": "510300.SH", "side": "BUY", "shares": 100},
        "estimate": estimate,
    }


class TestScoreTrading:
    DATE = "2026-09-01"

    def test_missing_file_degraded(self, tmp_path):
        d = score_trading(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.15

    def test_no_fills_degraded(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [])
        assert score_trading(tmp_path, self.DATE).degraded is True

    def test_full_estimate_coverage(self, tmp_path):
        _write_tca(
            tmp_path, self.DATE,
            [_fill({"cost_bps": 5.0}), _fill({"cost_bps": 6.0})],
        )
        d = score_trading(tmp_path, self.DATE)
        assert d.score == 100.0
        assert d.detail == {"fills": 2, "estimate_coverage": 1.0}

    def test_zero_estimate_coverage(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [_fill(None), _fill(None)])
        assert score_trading(tmp_path, self.DATE).score == 70.0

    def test_mixed_coverage_below_half(self, tmp_path):
        _write_tca(tmp_path, self.DATE, [_fill({"cost_bps": 5.0}), _fill(None), _fill(None)])
        d = score_trading(tmp_path, self.DATE)
        assert d.score == 70.0
        assert d.detail["estimate_coverage"] == round(1 / 3, 4)


def _write_vol_regime(root: Path, date: str, payload: dict) -> None:
    d = root / "reports" / "evolution"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"vol_regime_weights_{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _vol(label: str, **over) -> dict:
    p = {
        "regime": {"label": label, "confidence": 0.85},
        "degraded": False,
        "observation_phase": False,
    }
    p.update(over)
    return p


class TestScoreRisk:
    DATE = "2026-09-01"

    def test_missing_file_degraded(self, tmp_path):
        d = score_risk(tmp_path, self.DATE)
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.20

    def test_bull_100(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bull"))
        assert score_risk(tmp_path, self.DATE).score == 100.0

    def test_bear_80(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bear"))
        assert score_risk(tmp_path, self.DATE).score == 80.0

    def test_sideways_95(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("sideways"))
        assert score_risk(tmp_path, self.DATE).score == 95.0

    def test_unknown_label_90(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("turbulent"))
        assert score_risk(tmp_path, self.DATE).score == 90.0

    def test_regime_degraded_flag_50(self, tmp_path):
        _write_vol_regime(tmp_path, self.DATE, _vol("bull", degraded=True))
        assert score_risk(tmp_path, self.DATE).score == 50.0

    def test_corrupt_json_degraded(self, tmp_path):
        d = tmp_path / "reports" / "evolution"
        d.mkdir(parents=True)
        (d / f"vol_regime_weights_{self.DATE}.json").write_text("{bad", encoding="utf-8")
        assert score_risk(tmp_path, self.DATE).degraded is True
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py::TestScoreTrading tests\unit\test_health_score_engine.py::TestScoreRisk -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 写实现（追加到 score_engine.py 末尾）**

```python
def score_trading(project_root: Path, date: str) -> DimensionScore:
    """交易维: 当日 TCA 成交记录与预估覆盖率."""
    path = project_root / "reports" / "tca" / f"fills_{date}.jsonl"
    if not path.exists():
        return _degraded("trading", "当日 TCA fills 文件不存在 (无交易或未落盘)")
    fills, estimated = 0, 0
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return _degraded("trading", "TCA fills 文件读取失败")
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == "fill":
            fills += 1
            if rec.get("estimate"):
                estimated += 1
    if fills == 0:
        return _degraded("trading", "当日无成交记录")
    coverage = estimated / fills
    score = 100.0 if coverage >= 0.5 else 70.0
    return DimensionScore(
        score=score,
        weight=WEIGHTS["trading"],
        degraded=False,
        detail={"fills": fills, "estimate_coverage": round(coverage, 4)},
    )


_REGIME_SCORES = {"bull": 100.0, "sideways": 95.0, "neutral": 95.0, "bear": 80.0}


def score_risk(project_root: Path, date: str) -> DimensionScore:
    """风险维: vol regime 状态 (bull 100 / sideways 95 / bear 80)."""
    path = project_root / "reports" / "evolution" / f"vol_regime_weights_{date}.json"
    if not path.exists():
        return _degraded("risk", "vol_regime 权重报告不存在")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _degraded("risk", "vol_regime 报告解析失败")
    if not isinstance(data, dict):
        return _degraded("risk", "vol_regime 报告结构异常")
    regime = data.get("regime") or {}
    label = str(regime.get("label", "")).lower()
    if data.get("degraded"):
        return DimensionScore(
            score=50.0,
            weight=WEIGHTS["risk"],
            degraded=False,
            detail={"regime": label, "regime_engine_degraded": True},
        )
    score = _REGIME_SCORES.get(label, 90.0)
    return DimensionScore(
        score=score,
        weight=WEIGHTS["risk"],
        degraded=False,
        detail={
            "regime": label,
            "confidence": regime.get("confidence"),
            "observation_phase": data.get("observation_phase"),
        },
    )
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py -v`
Expected: 32 passed

- [ ] **Step 5: Commit**

```bash
git add utils/health/score_engine.py tests/unit/test_health_score_engine.py
git commit -m "feat(health): 交易维与风险维评分 — TCA 覆盖率 + vol regime 分档"
```

---

### Task 5: score_capital + compute_health_score 聚合

**Files:**
- Modify: `utils/health/score_engine.py`（追加 score_capital 与聚合入口）
- Test: `tests/unit/test_health_score_engine.py`（追加 TestScoreCapital / TestAggregate）

- [ ] **Step 1: 写失败测试**

```python
from utils.health.score_engine import compute_health_score, score_capital


def _write_shadow_state(root: Path, payload: dict) -> None:
    d = root / "output" / "shadow_account"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s12_shadow_state.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _shadow(**over) -> dict:
    p = {
        "account_id": "S12_SHADOW_P3",
        "nav": 1.0,
        "trading_day_count": 0,
        "fail_fast_triggered": False,
    }
    p.update(over)
    return p


class TestScoreCapital:
    def test_missing_state_degraded(self, tmp_path):
        d = score_capital(tmp_path, "2026-09-01")
        assert d.degraded is True
        assert d.score == 60.0
        assert d.weight == 0.20

    def test_normal_nav_100(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow())
        d = score_capital(tmp_path, "2026-09-01")
        assert d.score == 100.0
        assert d.detail["nav"] == 1.0

    def test_fail_fast_zero(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow(fail_fast_triggered=True))
        assert score_capital(tmp_path, "2026-09-01").score == 0.0

    def test_nav_out_of_sane_range_50(self, tmp_path):
        _write_shadow_state(tmp_path, _shadow(nav=3.0))
        assert score_capital(tmp_path, "2026-09-01").score == 50.0

    def test_corrupt_json_degraded(self, tmp_path):
        d = tmp_path / "output" / "shadow_account"
        d.mkdir(parents=True)
        (d / "s12_shadow_state.json").write_text("{bad", encoding="utf-8")
        assert score_capital(tmp_path, "2026-09-01").degraded is True


class TestAggregate:
    DATE = "2026-09-01"

    def test_all_missing_all_degraded_total_60(self, tmp_path):
        r = compute_health_score(tmp_path, self.DATE)
        assert r["date"] == self.DATE
        assert r["total_score"] == 60.0
        assert r["status"] == "YELLOW"
        assert set(r["degraded_dimensions"]) == {"model", "data", "trading", "risk", "capital"}
        assert set(r["dimensions"]) == {"model", "data", "trading", "risk", "capital"}

    def test_mixed_fixture_exact_weighted_total(self, tmp_path):
        # model 100 (ic_deg 0.1 无告警) / data 100 (0 条) / trading 60 (缺文件)
        # risk 100 (bull) / capital 100 (nav 1.0)
        _write_drift(tmp_path, self.DATE, {
            "date": self.DATE, "skipped": False, "error": None,
            "ic_degradation": 0.1, "alerts": [],
            "delayed_metrics": {"ic": 0.05, "rank_ic": 0.06, "ic_ir": 0.8},
        })
        _write_degradation_log(tmp_path, [])
        _write_vol_regime(tmp_path, self.DATE, _vol("bull"))
        _write_shadow_state(tmp_path, _shadow())
        r = compute_health_score(tmp_path, self.DATE)
        # 100*0.25 + 100*0.20 + 60*0.15 + 100*0.20 + 100*0.20 = 94.0
        assert r["total_score"] == 94.0
        assert r["status"] == "GREEN"
        assert r["degraded_dimensions"] == ["trading"]
        assert r["dimensions"]["trading"]["degraded"] is True

    def test_output_schema_fields(self, tmp_path):
        r = compute_health_score(tmp_path, self.DATE)
        assert "generated_at" in r
        assert r["dimensions"]["model"]["weight"] == 0.25
        assert isinstance(r["dimensions"]["data"]["detail"], dict)
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py::TestScoreCapital tests\unit\test_health_score_engine.py::TestAggregate -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 写实现（追加到 score_engine.py 末尾）**

```python
def score_capital(project_root: Path, date: str) -> DimensionScore:
    """资金维: shadow 账户状态 (NAV 合理性 + fail-fast)."""
    path = project_root / "output" / "shadow_account" / "s12_shadow_state.json"
    if not path.exists():
        return _degraded("capital", "shadow 账户状态文件不存在")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _degraded("capital", "shadow 账户状态解析失败")
    if not isinstance(data, dict):
        return _degraded("capital", "shadow 账户状态结构异常")
    if data.get("fail_fast_triggered"):
        return DimensionScore(
            score=0.0,
            weight=WEIGHTS["capital"],
            degraded=False,
            detail={"fail_fast_triggered": True},
        )
    try:
        nav = float(data.get("nav", 1.0))
    except (TypeError, ValueError):
        nav = 1.0
    score = 100.0 if 0.5 <= nav <= 2.0 else 50.0
    return DimensionScore(
        score=score,
        weight=WEIGHTS["capital"],
        degraded=False,
        detail={"nav": nav, "trading_day_count": data.get("trading_day_count")},
    )


def compute_health_score(project_root: Path, date: str) -> dict:
    """五维聚合 → 评分报告 dict (落盘由 CLI 负责)."""
    scorers = {
        "model": score_model,
        "data": score_data,
        "trading": score_trading,
        "risk": score_risk,
        "capital": score_capital,
    }
    dims = {name: fn(project_root, date) for name, fn in scorers.items()}
    total = round(sum(d.score * d.weight for d in dims.values()), 1)
    return {
        "date": date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_score": total,
        "status": status_for(total),
        "dimensions": {k: asdict(v) for k, v in dims.items()},
        "degraded_dimensions": [k for k, v in dims.items() if v.degraded],
    }
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_score_engine.py -v`
Expected: 40 passed

- [ ] **Step 5: Commit**

```bash
git add utils/health/score_engine.py tests/unit/test_health_score_engine.py
git commit -m "feat(health): 资金维评分 + 五维聚合入口 compute_health_score"
```

---

### Task 6: CLI 入口 + 真实数据运行

**Files:**
- Create: `scripts/compute_health_score.py`

- [ ] **Step 1: 写 CLI 脚本**

```python
"""System Health Score 计算入口 (Production Edition T2, 2026-09-02).

每交易日 17:05 (先于 17:10 状态报告, 使其可读取同日评分) 由计划任务
System_HealthScore 调用, 幂等落盘 (同日重跑覆盖):
  reports/health_score/health_score_{date}.json

用法:
  python scripts/compute_health_score.py                      # 当日
  python scripts/compute_health_score.py --date 2026-09-01    # 指定日期
  python scripts/compute_health_score.py --print              # 打印明细
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.health.score_engine import compute_health_score  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="System Health Score 聚合")
    parser.add_argument("--date", default=None, help="评估日期 YYYY-MM-DD (默认今日)")
    parser.add_argument("--print", action="store_true", help="打印评分明细")
    args = parser.parse_args()

    date = args.date or datetime.now().strftime("%Y-%m-%d")
    result = compute_health_score(_PROJECT_ROOT, date)

    out_dir = _PROJECT_ROOT / "reports" / "health_score"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"health_score_{date}.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] {result['status']} {result['total_score']}/100 -> {out_path}")
    if args.print:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 真实数据运行（用 2026-09-01，五维文件均实存）**

Run: `.venv\Scripts\python.exe scripts\compute_health_score.py --date 2026-09-01 --print`
Expected: 输出 `[OK] <status> <total>/100 -> ...\health_score_2026-09-01.json`；明细中 model/data/trading/risk 四维 detail 非空（真实数据），capital 维 nav=1.0

- [ ] **Step 3: ruff 检查**

Run: `.venv\Scripts\python.exe -m ruff check utils\health\ scripts\compute_health_score.py`
Expected: All checks passed

- [ ] **Step 4: Commit**

```bash
git add scripts/compute_health_score.py
git commit -m "feat(health): Health Score CLI 入口 — 幂等落盘 health_score_{date}.json"
```

---

### Task 7: 注入每日状态报告

**Files:**
- Modify: `scripts/generate_daily_status_report.py:25-27`（追加常量）、`L95`（build_report 签名）、`L123-128`（头部插入）、`L197-220`（main 加载）

- [ ] **Step 1: 修改 generate_daily_status_report.py**

1. 常量区（L27 `SHADOW_TASK = "S12_Shadow_EOD"` 之后）追加：

```python
HEALTH_SCORE_DIR = _PROJECT_ROOT / "reports" / "health_score"
```

2. `build_report` 之前追加渲染函数：

```python
def render_health_section(hs: dict) -> list[str]:
    """渲染系统健康评分节 (health_score JSON → markdown 行)."""
    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(hs.get("status"), "⚪")
    L = ["## 零、系统健康评分", ""]
    L.append(f"**{icon} {hs.get('total_score', 'N/A')} / 100 ({hs.get('status', 'N/A')})**")
    L.append("")
    L.append("| 维度 | 得分 | 权重 | 状态 |")
    L.append("|---|---|---|---|")
    for name, d in (hs.get("dimensions") or {}).items():
        st = "degraded" if d.get("degraded") else "ok"
        L.append(f"| {name} | {d.get('score')} | {d.get('weight')} | {st} |")
    L.append("")
    if hs.get("degraded_dimensions"):
        L.append(f"> 降级维度: {', '.join(hs['degraded_dimensions'])} "
                 "(60 分中性值, 数据不可得)")
        L.append("")
    return L
```

3. `build_report` 签名改为（L95）：

```python
def build_report(state: dict, config: dict, task_info: dict, run_date: str,
                 health_score: dict | None = None) -> str:
```

4. 头部块之后（L128 `L.append("")` 与 L129 `## 一、影子账户` 之间）插入：

```python
    if health_score is not None:
        L.extend(render_health_section(health_score))
```

5. `main()` 中 `report = build_report(state, config, task_info, run_date)`（L211）之前追加加载，并改调用：

```python
    health_score = None
    hs_path = HEALTH_SCORE_DIR / f"health_score_{run_date}.json"
    if hs_path.exists():
        try:
            health_score = json.loads(hs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            health_score = None

    report = build_report(state, config, task_info, run_date, health_score)
```

- [ ] **Step 2: 真实链路运行（当日评分 → 当日报告）**

Run（cwd 项目根，顺序执行）:
`.venv\Scripts\python.exe scripts\compute_health_score.py`
`.venv\Scripts\python.exe scripts\generate_daily_status_report.py --print`
Expected: 报告头部出现"## 零、系统健康评分"节，含总分/状态色/五维表格；表格数值与 `reports/health_score/health_score_2026-09-02.json` 一致

- [ ] **Step 3: ruff + 状态报告无评分时不报错（反向验证）**

Run: `.venv\Scripts\python.exe -m ruff check scripts\generate_daily_status_report.py`
Expected: All checks passed

（health_score 文件不存在时 health_score=None，报告不含零节、不报错——代码路径已由 `if hs_path.exists()` 保证）

- [ ] **Step 4: Commit**

```bash
git add scripts/generate_daily_status_report.py
git commit -m "feat(health): 每日状态报告头部注入系统健康评分节"
```

---

### Task 8: 计划任务注册 + LOG 登记

**Files:**
- Modify: `cairn/LOG.md`（顶部追加条目）

- [ ] **Step 1: 注册计划任务（17:05 交易日，3 次重试/5 分钟，同 S12 模式）**

Run（PowerShell，cwd 项目根）:

```powershell
$action = New-ScheduledTaskAction -Execute "E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe" -Argument "E:\各种PY程序\28-终极量化交易系统8.4\scripts\compute_health_score.py" -WorkingDirectory "E:\各种PY程序\28-终极量化交易系统8.4"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 17:05
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -StartWhenAvailable
Register-ScheduledTask -TaskName "System_HealthScore" -Action $action -Trigger $trigger -Settings $settings -Description "System Health Score 聚合 (Production Edition T2; 17:05 先于 17:10 状态报告)" -Force
```

Expected: 任务注册成功；`Get-ScheduledTask -TaskName System_HealthScore` 返回状态 Ready

- [ ] **Step 2: LOG.md 顶部追加条目（置于说明行下方）**

```markdown
## 2026-09-02 · T2 System Health Score 聚合引擎落地（报告侧零侵入，v8.7.1 提前项）

- **背景**: Production Edition 方案 T2 / v8.7.1 零侵入提前项——S12 日报仅覆盖单账户，真缺口是全系统 Health Score 聚合
- **交付**: ①`utils/health/score_engine.py` 五维评分器（model 0.25=drift integration IC 退化分档+告警扣分 / data 0.20=当日降级条目分档 / trading 0.15=TCA 成交与预估覆盖率 / risk 0.20=vol regime 分档 / capital 0.20=shadow NAV+fail-fast）+ 聚合入口，降级语义=60 中性值+显式 degraded ②`scripts/compute_health_score.py` CLI 幂等落盘 `reports/health_score/health_score_{date}.json` ③`generate_daily_status_report.py` 头部注入"零、系统健康评分"节（缺失时不报错）④计划任务 System_HealthScore 17:05（方案原文 17:15 调整为 17:05：注入要求评分先于 17:10 报告生成）
- **验证**: 单测 40 用例（加权/降级/分档/聚合 schema）；真实数据运行 2026-09-01 与当日两日均产出 JSON；报告注入链路端到端通过；ruff 0 error
- **已知噪音 (v1)**: degradation_log 含测试进程条目，同日多次 pytest 会累计——数据维评分在测试日偏低，v1 接受并记录
- **后续**: "连续 5 交易日产出"由 17:05 计划任务自然累积验收；T5 Dashboard（Streamlit 只读页）排 12-10 冻结后
- **指针**: `utils/health/score_engine.py`；`scripts/compute_health_score.py`；`docs/superpowers/plans/2026-09-02-t2-health-score-engine.md`
```

- [ ] **Step 3: Commit**

```bash
git add cairn/LOG.md docs/superpowers/plans/2026-09-02-t2-health-score-engine.md
git commit -m "docs(cairn): T2 Health Score 引擎落地 LOG 登记 + 实施计划归档"
```

---

## Self-Review 记录

- **Spec 覆盖**: 方案 T2 验收四条——引擎产出（Task 5/6）、≥3 维真实数据+显式 degraded（五维全接真实源，Task 1-5 单测覆盖降级语义）、单测覆盖加权与降级（40 用例）、摘要注入状态报告（Task 7）；"连续 5 交易日产出"为运营期验收（Task 8 计划任务注册，LOG 注明）✓
- **占位符扫描**: 无 TBD/TODO ✓
- **类型一致性**: `DimensionScore(score, weight, degraded, detail)` 四字段在所有评分器与 `asdict` 消费处一致；`compute_health_score(project_root: Path, date: str) -> dict` 与 CLI 调用一致；`render_health_section(hs: dict) -> list[str]` 与 build_report 的 `L.extend` 消费一致 ✓
- **fixture 辅助函数定义顺序**: `_write_drift`/`_write_degradation_log`/`_write_vol_regime` 在 Task 2/3 测试中定义、Task 5 聚合测试复用——同文件模块级函数，pytest 收集无顺序问题 ✓
- **真实字段核对**: `ic_degradation`/`alerts`/`delayed_metrics.ic`（drift 样例 L6-24）、`ts/scope`（degradation_log 样例）、`type:"fill"`/`estimate`（tca fills 样例）、`regime.label`/`degraded`/`observation_phase`（vol_regime 样例）、`nav`/`fail_fast_triggered`（shadow state 样例）均与 2026-09-02 实存文件核对一致 ✓
