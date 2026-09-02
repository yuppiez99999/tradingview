# T5 生产运营中心 Dashboard 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增"生产运营中心" Streamlit 只读页，消费 Health Score 聚合 JSON + shadow 状态 + 降级日志，兑现《v8.7 Production Edition 架构升级方案》§4.3 与运营件四件套最后一块。

**Architecture:** 两层分离——`ui/components/health_center.py` 承载纯数据函数（无 streamlit 依赖，单测覆盖），`ui/pages/17_🏭_生产运营中心.py` 只做渲染（复用现有 inject_global_style 组件体系）。页面为聚合产物的**只读消费者**，不接触生产链路（方案 §4.1 两层解耦决策）。

**Tech Stack:** streamlit（需安装到 .venv）、plotly 6.9.0（已装，Scatterpolar 雷达 + Scatter 趋势线）、pytest。

**上游设计:** 方案 §4.3 版面五件套：①总评分卡（当日成立+近 30 日趋势+状态色）②五维雷达图+明细 ③关键指标集 ④异常时间线 ⑤灰度进度条。

**v1 范围边界（已按方案"只读数据源：聚合 JSON + 每日状态报告"约束）:**
- 数据源三件：`reports/health_score/*.json` + `output/shadow_account/s12_shadow_state.json` + `reports/degradation_log.jsonl`
- 关键指标集展示三源中**可得项**（NAV/交易日数/降级条目/五维分项）；Sharpe/Alpha/VaR 等未接入项显示 `—（未接入）`占位，后续版本接入归因与风险产物——不做数据源伪装
- 灰度进度：v9_200w_preset 路线（20 万测试→100 万灰度→200 万正式）静态阶段表 + shadow state 动态天数（当前 = Phase 3 shadow 200 万虚拟阶段）

**已核验的环境事实（2026-09-02）:**
- `.venv` **无 streamlit**（现有 17 个 ui/pages 实际无法在当前环境运行）；plotly 6.9.0 已装
- `requirements.txt` 含 `plotly>=5.15.0`，无 streamlit 条目
- 页面命名模式：`ui/pages/NN_emoji_名称.py`（现有编号 01~16，存在两个 13/15 重复编号不影响新增 17）
- 页面标准骨架：`_BASE_DIR` sys.path 注入 → `from ui.components.common import inject_global_style`
- T2 评分 JSON 结构：`{date, generated_at, total_score, status, dimensions{model|data|trading|risk|capital{score,weight,degraded,detail}}, degraded_dimensions}`
- shadow state 结构：`{account_id, strategy_id, initial_capital, start_date, ...}`
- degradation_log 行结构：`{ts, scope, key, ...}`（ts 为 ISO 日期前缀）

---

### Task 1: 安装 streamlit + requirements 补条目

**Files:**
- Modify: `requirements.txt`（plotly 行后追加 streamlit 行）

- [ ] **Step 1: 安装 streamlit 到 .venv**

Run（cwd 项目根）: `.venv\Scripts\python.exe -m pip install streamlit`
Expected: `Successfully installed streamlit-x.x.x ...`（连同 altair/pyarrow 等依赖）

- [ ] **Step 2: 验证 import + 现有 UI 可加载**

Run: `.venv\Scripts\python.exe -c "import streamlit; print('streamlit', streamlit.__version__)"`
Expected: `streamlit 1.x.x`

Run: `.venv\Scripts\python.exe -c "import ui.pages"`（验证包结构无 import 错误）
Expected: 无输出（成功）

- [ ] **Step 3: requirements.txt 补条目**

在 `requirements.txt` 中 `plotly>=5.15.0` 行之后追加一行：

```
streamlit>=1.30.0
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build(deps): 补 streamlit 依赖 — ui/pages 体系运行所需 (T5 前置)"
```

（pip install 不产生仓库变更，仅 requirements.txt 入库。）

---

### Task 2: 数据加载层 health_center.py（纯函数，TDD）

**Files:**
- Create: `ui/components/health_center.py`
- Test: `tests/unit/test_health_center.py`

- [ ] **Step 1: 写失败测试**

```python
"""生产运营中心数据加载层单测 (Production Edition T5, 2026-09-02)."""
from __future__ import annotations

import json
from pathlib import Path

from ui.components.health_center import (
    derive_status_color,
    load_anomaly_timeline,
    load_health_history,
    load_latest_health,
    load_shadow_progress,
)


def _write_health(root: Path, date: str, total: float, status: str) -> None:
    d = root / "reports" / "health_score"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"health_score_{date}.json").write_text(
        json.dumps({
            "date": date,
            "generated_at": f"{date}T17:05:00",
            "total_score": total,
            "status": status,
            "dimensions": {
                "model": {"score": 90.0, "weight": 0.25, "degraded": False, "detail": {}},
                "data": {"score": 80.0, "weight": 0.2, "degraded": False, "detail": {}},
                "trading": {"score": 70.0, "weight": 0.15, "degraded": False, "detail": {}},
                "risk": {"score": 100.0, "weight": 0.2, "degraded": False, "detail": {}},
                "capital": {"score": 100.0, "weight": 0.2, "degraded": False, "detail": {}},
            },
            "degraded_dimensions": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_shadow_state(root: Path, start_date: str, days: int) -> None:
    d = root / "output" / "shadow_account"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s12_shadow_state.json").write_text(
        json.dumps({
            "account_id": "S12_SHADOW_P3",
            "strategy_id": "S12_DEFENSIVE_RP",
            "initial_capital": 2000000.0,
            "start_date": start_date,
            "trading_day_count": days,
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_degradation(root: Path, lines: list[dict]) -> None:
    d = root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "degradation_log.jsonl").open("w", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class TestLoadHealthHistory:
    def test_sorted ascending by date(self, tmp_path):
        _write_health(tmp_path, "2026-09-01", 73.5, "YELLOW")
        _write_health(tmp_path, "2026-08-31", 90.0, "GREEN")
        _write_health(tmp_path, "2026-09-02", 68.0, "RED")
        hist = load_health_history(tmp_path)
        assert [h["date"] for h in hist] == ["2026-08-31", "2026-09-01", "2026-09-02"]

    def test_days_limit(self, tmp_path):
        for i in range(1, 41):
            _write_health(tmp_path, f"2026-07-{i:02d}" if i <= 31 else f"2026-08-{i - 31:02d}", 80.0, "GREEN")
        hist = load_health_history(tmp_path, days=30)
        assert len(hist) == 30
        assert hist[-1]["date"] == "2026-08-09"

    def test_empty_dir(self, tmp_path):
        assert load_health_history(tmp_path) == []
        assert load_latest_health(tmp_path) is None

    def test_corrupt_json_skipped(self, tmp_path):
        _write_health(tmp_path, "2026-09-01", 90.0, "GREEN")
        d = tmp_path / "reports" / "health_score"
        (d / "health_score_2026-09-02.json").write_text("{not json", encoding="utf-8")
        hist = load_health_history(tmp_path)
        assert [h["date"] for h in hist] == ["2026-09-01"]

    def test_load_latest(self, tmp_path):
        _write_health(tmp_path, "2026-09-01", 73.5, "YELLOW")
        _write_health(tmp_path, "2026-09-02", 96.0, "GREEN")
        latest = load_latest_health(tmp_path)
        assert latest is not None
        assert latest["date"] == "2026-09-02"
        assert latest["total_score"] == 96.0


class TestAnomalyTimeline:
    def test_recent_entries_only(self, tmp_path):
        _write_degradation(tmp_path, [
            {"ts": "2026-09-02T16:31:00", "scope": "daily_trade_executor", "key": "k1"},
            {"ts": "2026-08-01T09:00:00", "scope": "old_scope", "key": "k0"},
            {"ts": "2026-09-02T17:00:00", "scope": "pretrade_guard", "key": "k2"},
        ])
        tl = load_anomaly_timeline(tmp_path, days=7, today="2026-09-02")
        assert len(tl) == 2
        assert all(e["ts"].startswith("2026-09-02") for e in tl)

    def test_malformed_lines_skipped(self, tmp_path):
        d = tmp_path / "reports"
        d.mkdir(parents=True)
        (d / "degradation_log.jsonl").write_text(
            '{"ts": "2026-09-02T10:00:00", "scope": "a"}\nnot-json\n\n', encoding="utf-8"
        )
        tl = load_anomaly_timeline(tmp_path, days=7, today="2026-09-02")
        assert len(tl) == 1
        assert tl[0]["scope"] == "a"

    def test_missing_file(self, tmp_path):
        assert load_anomaly_timeline(tmp_path, days=7, today="2026-09-02") == []


class TestShadowProgress:
    def test_phase3_shadow(self, tmp_path):
        _write_shadow_state(tmp_path, "2026-09-02", 0)
        p = load_shadow_progress(tmp_path)
        assert p["current_stage"] == "Phase 3 影子验证 (200 万虚拟)"
        assert p["stages"][2]["status"] == "进行中"
        assert p["stages"][0]["status"] == "已完成"
        assert p["trading_day_count"] == 0

    def test_missing_state_all_pending(self, tmp_path):
        p = load_shadow_progress(tmp_path)
        assert p["current_stage"] == "未启动"
        assert all(s["status"] == "未开始" for s in p["stages"])
        assert p["trading_day_count"] == 0


class TestStatusColor:
    def test_mapping(self):
        assert derive_status_color("GREEN") == "green"
        assert derive_status_color("YELLOW") == "orange"
        assert derive_status_color("RED") == "red"
        assert derive_status_color("unknown") == "gray"
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_center.py -v`
Expected: FAIL（ModuleNotFoundError / ImportError: health_center）

- [ ] **Step 3: 写实现**

`ui/components/health_center.py`:

```python
"""生产运营中心数据加载层 (Production Edition T5, 2026-09-02).

纯数据函数, 不 import streamlit — 页面渲染层只消费这里的返回值.
数据源三件 (全部只读):
  reports/health_score/health_score_{date}.json   T2 聚合引擎产物
  output/shadow_account/s12_shadow_state.json     S12 shadow 账户状态
  reports/degradation_log.jsonl                   降级审计日志
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

_STATUS_COLORS = {"GREEN": "green", "YELLOW": "orange", "RED": "red"}


def derive_status_color(status: str) -> str:
    """评分状态 → 显示色."""
    return _STATUS_COLORS.get(str(status).upper(), "gray")


def load_health_history(project_root: Path, days: int = 30) -> list[dict]:
    """按日期升序加载近 N 日评分 JSON (损坏文件跳过)."""
    d = Path(project_root) / "reports" / "health_score"
    if not d.is_dir():
        return []
    records: list[dict] = []
    for f in d.glob("health_score_*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and "date" in rec and "total_score" in rec:
            records.append(rec)
    records.sort(key=lambda r: str(r["date"]))
    return records[-days:] if days > 0 else records


def load_latest_health(project_root: Path) -> dict | None:
    """最新一日评分 (无历史返回 None)."""
    hist = load_health_history(project_root, days=1)
    return hist[-1] if hist else None


def load_anomaly_timeline(project_root: Path, days: int = 7, today: str | None = None) -> list[dict]:
    """近 N 日降级日志条目 (升序, 损坏行跳过)."""
    path = Path(project_root) / "reports" / "degradation_log.jsonl"
    if not path.is_file():
        return []
    today_str = today or datetime.now().strftime("%Y-%m-%d")
    try:
        cutoff = (
            datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=days)
        ).strftime("%Y-%m-%d")
    except ValueError:
        cutoff = "0000-00-00"
    out: list[dict] = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return out
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
        ts = str(rec.get("ts", ""))
        if ts >= cutoff:
            out.append(rec)
    return out


_GRADIENT_STAGES = [
    {"name": "Sprint3-1 · 20 万测试", "target": "小资金链路验证"},
    {"name": "Sprint3-2 · 100 万灰度 shadow", "target": "30 天影子对照"},
    {"name": "Sprint3-3 · 200 万正式", "target": "实盘切换"},
]


def load_shadow_progress(project_root: Path) -> dict:
    """v9_200w_preset 灰度阶段进度 (静态阶段表 + shadow state 动态天数)."""
    state_path = Path(project_root) / "output" / "shadow_account" / "s12_shadow_state.json"
    state: dict = {}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, ValueError):
            state = {}
    stages = [
        {**s, "status": "未开始"} for s in _GRADIENT_STAGES
    ]
    trading_days = int(state.get("trading_day_count", 0) or 0)
    current = "未启动"
    if state:
        # shadow state 存在 = 已越过 20 万测试, 处于 Phase 3 影子验证 (200 万虚拟)
        stages[0]["status"] = "已完成"
        stages[1]["status"] = "已完成" if trading_days >= 30 else "进行中"
        stages[2]["status"] = "待切换" if trading_days >= 30 else "未开始"
        current = stages[1]["name"] if trading_days < 30 else stages[2]["name"]
    return {
        "current_stage": current,
        "stages": stages,
        "trading_day_count": trading_days,
        "start_date": state.get("start_date"),
        "account_id": state.get("account_id"),
    }
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv\Scripts\python.exe -m pytest tests\unit\test_health_center.py -v`
Expected: 12 passed

- [ ] **Step 5: ruff + Commit**

Run: `.venv\Scripts\python.exe -m ruff check ui\components\health_center.py tests\unit\test_health_center.py`
Expected: All checks passed

```bash
git add ui/components/health_center.py tests/unit/test_health_center.py
git commit -m "feat(ui): 生产运营中心数据加载层 — 评分历史/异常时间线/灰度进度纯函数"
```

---

### Task 3: 页面渲染（五版面 + 冒烟验证）

**Files:**
- Create: `ui/pages/17_🏭_生产运营中心.py`

- [ ] **Step 1: 写页面**

```python
"""生产运营中心 — Production Edition 运营件四件套之 Dashboard (T5, 2026-09-02).

只读消费者: Health Score 聚合 JSON + shadow 状态 + 降级日志.
版面五件套 (方案 §4.3): 评分卡 / 五维雷达 / 关键指标 / 异常时间线 / 灰度进度.
"""

import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from ui.components.common import inject_global_style
from ui.components.health_center import (
    derive_status_color,
    load_anomaly_timeline,
    load_health_history,
    load_latest_health,
    load_shadow_progress,
)

st.set_page_config(page_title="生产运营中心", page_icon="🏭", layout="wide")
inject_global_style()

st.title("🏭 生产运营中心")
st.caption("v8.7 Production Edition — 系统健康评分 · 异常时间线 · 灰度进度（只读视图）")

_ROOT = Path(_BASE_DIR)
DIM_ORDER = ["model", "data", "trading", "risk", "capital"]
DIM_CN = {"model": "模型", "data": "数据", "trading": "交易", "risk": "风险", "capital": "资金"}


def _score_trend_fig(history: list[dict]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[h["date"] for h in history],
        y=[h["total_score"] for h in history],
        mode="lines+markers",
        name="总分",
        line=dict(color="#0068c9"),
    ))
    fig.add_hline(y=85, line_dash="dot", line_color="green",
                  annotation_text="GREEN ≥85")
    fig.add_hline(y=70, line_dash="dot", line_color="orange",
                  annotation_text="RED <70")
    fig.update_layout(
        height=260, margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(range=[0, 105]), xaxis_title=None,
    )
    return fig


def _radar_fig(latest: dict) -> go.Figure:
    dims = latest.get("dimensions", {})
    labels = [DIM_CN.get(k, k) for k in DIM_ORDER]
    values = [float(dims.get(k, {}).get("score", 0.0)) for k in DIM_ORDER]
    fig = go.Figure(go.Scatterpolar(
        r=values + [values[0]],
        theta=labels + [labels[0]],
        fill="toself",
        line=dict(color="#0068c9"),
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(range=[0, 100], showticklabels=True)),
        height=320, margin=dict(l=60, r=60, t=30, b=30),
        showlegend=False,
    )
    return fig


def _render_score_card(latest: dict) -> None:
    color = derive_status_color(latest.get("status", ""))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("系统健康总分", f"{latest.get('total_score', 0):.1f} / 100")
    c2.metric("状态", str(latest.get("status", "—")))
    c3.metric("数据日期", str(latest.get("date", "—")))
    c4.metric("降级维度", f"{len(latest.get('degraded_dimensions', []))} / 5")
    st.markdown(
        f'<div style="height:6px;border-radius:3px;background:{color};"></div>',
        unsafe_allow_html=True,
    )


def _render_dimensions(latest: dict) -> None:
    dims = latest.get("dimensions", {})
    cols = st.columns(5)
    for i, key in enumerate(DIM_ORDER):
        d = dims.get(key, {})
        degraded = "⚠️ " if d.get("degraded") else ""
        with cols[i]:
            st.markdown(
                f"**{degraded}{DIM_CN.get(key, key)}** · "
                f"{float(d.get('score', 0)):.0f} 分"
                f"（权重 {d.get('weight', 0):.0%}）"
            )
            detail = d.get("detail", {})
            reason = detail.get("reason")
            if reason:
                st.caption(f"原因: {reason}")
            elif key == "data":
                st.caption(f"当日降级条目: {detail.get('entries', '—')}")
            elif key == "capital":
                st.caption(f"交易日数: {detail.get('trading_day_count', '—')}")
            else:
                st.caption("正常")


def _render_metrics(latest: dict, progress: dict) -> None:
    capital_detail = latest.get("dimensions", {}).get("capital", {}).get("detail", {})
    data_detail = latest.get("dimensions", {}).get("data", {}).get("detail", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NAV (shadow)", f"{capital_detail.get('nav', float('nan')):.4f}"
              if isinstance(capital_detail.get("nav"), (int, float)) else "—")
    c2.metric("shadow 交易日数", progress.get("trading_day_count", "—"))
    c3.metric("当日降级条目", data_detail.get("entries", "—"))
    c4.metric("Sharpe / Alpha / VaR", "—（未接入）")
    st.caption("指标集 v1 范围: 评分 JSON + shadow 状态可得项; 归因/风险产物接入后扩展。")


def _render_timeline(events: list[dict]) -> None:
    if not events:
        st.success("近 7 日无降级/异常记录")
        return
    rows = [
        {
            "时间": str(e.get("ts", ""))[:19],
            "scope": str(e.get("scope", "")),
            "key": str(e.get("key", "")),
        }
        for e in events
    ]
    st.dataframe(rows, use_container_width=True, height=240)


def _render_progress(progress: dict) -> None:
    st.markdown(f"**当前阶段**: {progress.get('current_stage', '—')}"
                f"（已运行 {progress.get('trading_day_count', 0)} 个交易日）")
    cols = st.columns(len(progress["stages"]))
    for col, s in zip(cols, progress["stages"]):
        with col:
            icon = {"已完成": "✅", "进行中": "🔵", "待切换": "🟣", "未开始": "⬜"}.get(
                s["status"], "⬜"
            )
            st.markdown(f"{icon} **{s['name']}**")
            st.caption(f"{s['target']} · {s['status']}")


# === 渲染主流程 ===
history = load_health_history(_ROOT, days=30)
latest = load_latest_health(_ROOT)

if latest is None:
    st.warning("尚无 Health Score 数据——请先运行 scripts/compute_health_score.py"
               "（计划任务 System_HealthScore 每交易日 17:05 自动产出）。")
    st.stop()

with st.container(border=True):
    st.markdown("### 一、系统健康总评")
    _render_score_card(latest)

left, right = st.columns([3, 2])
with left:
    st.markdown("### 总分趋势（近 30 日）")
    st.plotly_chart(_score_trend_fig(history), use_container_width=True)
with right:
    st.markdown("### 五维雷达")
    st.plotly_chart(_radar_fig(latest), use_container_width=True)

with st.container(border=True):
    st.markdown("### 二、五维明细")
    _render_dimensions(latest)

with st.container(border=True):
    st.markdown("### 三、关键指标")
    _render_metrics(latest, load_shadow_progress(_ROOT))

with st.container(border=True):
    st.markdown("### 四、异常时间线（近 7 日降级记录）")
    _render_timeline(load_anomaly_timeline(_ROOT, days=7))

with st.container(border=True):
    st.markdown("### 五、灰度进度（v9_200w_preset）")
    _render_progress(load_shadow_progress(_ROOT))
```

- [ ] **Step 2: 语法/导入静态验证**

Run: `.venv\Scripts\python.exe -m py_compile "ui\pages\17_🏭_生产运营中心.py" && .venv\Scripts\python.exe -c "import ast; ast.parse(open(r'ui\pages\17_🏭_生产运营中心.py', encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

（不直接 import 页面模块——顶层 st.* 调用需要 streamlit runtime。）

- [ ] **Step 3: ruff 检查**

Run: `.venv\Scripts\python.exe -m ruff check "ui\pages\17_🏭_生产运营中心.py"`
Expected: All checks passed（若报未用 import，删除之）

- [ ] **Step 4: 冒烟启动验证（headless）**

Run（非阻塞，起在 8765 端口避免冲突）:
`.venv\Scripts\python.exe -m streamlit run ui\app.py --server.port 8765 --server.headless true`
等待 ~10 秒后 CheckCommandStatus，期望输出含 `You can now view your Streamlit app` 与 `URL: http://localhost:8765`；随后 StopCommand。

（页面本身通过 app.py 多页面导航加载；如需直验单页可 `streamlit run "ui\pages\17_🏭_生产运营中心.py"` 同理。）

- [ ] **Step 5: 浏览器人工检视（用户参与）**

主会话向用户报告预览 URL，用户浏览器打开确认五版面渲染正常（真实数据：09-01/09-02 两日评分 + 135 条备份 + shadow 状态）。发现问题回子代理修。

- [ ] **Step 6: Commit**

```bash
git add "ui/pages/17_🏭_生产运营中心.py"
git commit -m "feat(ui): 生产运营中心页 — 评分卡/雷达/趋势/异常时间线/灰度进度五版面"
```

---

### Task 4: Runbook 互链 + LOG 登记

**Files:**
- Modify: `docs/runbooks/PRODUCTION_OPERATIONS_RUNBOOK.md`（§1.3 检视清单加 Dashboard 入口行）
- Modify: `cairn/LOG.md`（顶部追加条目）

- [ ] **Step 1: runbook §1.3 人工检视清单头部加一行**

在"**人工检视清单**:"列表第 1 条之前插入：

```markdown
0. 入口: 浏览器打开生产运营中心 Dashboard（`streamlit run ui\app.py` 后进入"17_🏭_生产运营中心"页）——本清单 1-5 项均可视化完成.
```

- [ ] **Step 2: LOG.md 顶部追加条目**

```markdown
## 2026-09-02 · T5 生产运营中心 Dashboard 落地（运营件四件套收官，提前于排期 12-10 冻结后）

- **背景**: Production Edition 方案 §4.3（两层解耦：聚合引擎 T2 已就绪，本页为只读消费者）；用户决策记录①完整 UI Dashboard
- **交付**: ①`ui/components/health_center.py` 数据加载层（评分历史/异常时间线/灰度进度纯函数，无 streamlit 依赖，12 单测）②`ui/pages/17_🏭_生产运营中心.py`（五版面：总评分卡+30 日趋势+状态色 / 五维雷达 / 关键指标 / 异常时间线 / 灰度进度条）③requirements.txt 补 streamlit（发现 .venv 原本无 streamlit——既有 17 页面在当前环境实际不可运行，本次一并修复）
- **v1 范围边界**: 数据源 = 评分 JSON + shadow state + degradation_log 三件（方案"只读消费聚合产物"约束）；Sharpe/Alpha/VaR 等显示"—（未接入）"占位，归因/风险产物接入后扩展，不做数据源伪装
- **验证**: 12 单测 + py_compile + ruff + streamlit headless 冒烟启动 + 浏览器人工检视
- **意义**: 运营件四件套（Health Score 引擎 + Dashboard + SOP 手册 + 备份恢复链）全部就位，Production Edition Q4 任务 T1-T5 提前完成
- **指针**: `ui/pages/17_🏭_生产运营中心.py`；`ui/components/health_center.py`；`docs/superpowers/plans/2026-09-02-t5-dashboard.md`
```

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/PRODUCTION_OPERATIONS_RUNBOOK.md cairn/LOG.md docs/superpowers/plans/2026-09-02-t5-dashboard.md
git commit -m "docs(cairn): T5 Dashboard 落地 LOG 登记 + runbook Dashboard 入口互链"
```

---

## Self-Review 记录

- **Spec 覆盖**: 方案 §4.3 五版面逐一对应 Task 3 渲染函数（评分卡+趋势+状态色→_render_score_card/_score_trend_fig；雷达+明细→_radar_fig/_render_dimensions；关键指标集→_render_metrics；异常时间线→_render_timeline；灰度进度→_render_progress）✓；只读约束（不直连生产库、不接触生产链路）✓；运营件四件套收官判据 ✓。方案排期"12-10 冻结后"→用户指示提前执行，页面为纯新增只读不碰冻结四核心，LOG 已注明。
- **占位符扫描**: 无 TBD/TODO；"—（未接入）"是产品语义占位（明确标注数据源未接入），非计划占位符 ✓
- **类型一致性**: load_health_history(root, days) -> list[dict] / load_latest_health(root) -> dict|None / load_anomaly_timeline(root, days, today) -> list[dict] / load_shadow_progress(root) -> dict{current_stage, stages, trading_day_count} 在 Task 2 定义与 Task 3 消费一致 ✓；DIM_ORDER/DIM_CN 页面局部常量仅 Task 3 内用 ✓
- **环境事实核对**: streamlit 缺失已核验（Task 1 装）；plotly 6.9.0 已装（雷达/趋势可用）；页面编号 17 顺延（现有 01-16）✓
- **回归风险**: 纯新增文件（1 组件 + 1 页面 + 1 测试）+ requirements 一行，零生产链路接触；pre-commit 门禁照常 ✓
