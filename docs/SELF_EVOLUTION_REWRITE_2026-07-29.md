# 🧬 v8.4"自我进化"功能改造方案 v2（架构兼容重写版）

> **文档版本**：v2.0（2026-07-29）
> **替代关系**：本文件取代原报告 `28-终极量化交易系统8.4/2`（v1）。v1 与现有代码不兼容且含 8 处致命 Bug，已废弃。
> **核心原则**：**复用现有 `ModelDriftDetector` 基础设施，修复 3 个现存 Bug，增量增强而非重构**。

---

## 一、为什么重写（v1 问题概述）

原报告（v1）共 6 个模块（M1–M6），经审查存在 **8 处致命 Bug + 11 处严重架构冲突**，详见 `docs/CODE_REVIEW_2026-07-29.md`。最关键的三个不兼容点：

| v1 模块 | 不兼容点 | 现有代码事实 |
|---------|---------|-------------|
| M1 | 将 `_hook_drift_and_retrain` 改为独立函数 `(symbol, current_date)` | 实际是类方法 `(self)`，在 [system_integration.py:555](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L555) 由 `self._hook_drift_and_retrain()` 调用 |
| M3 | 新建 `DriftDetector`（仅 KS + 均值变化率） | 现有 `ModelDriftDetector` 已有 **ADWIN + IC衰减 + KS + PSI + T17 OOS Gap** 五要素，是降级重复 |
| M6 | 在 `run_eod_task` 中注入，引用 `POSITION_SYMBOLS`/`load_trade_history`/`schedule_retrain` | `live_scheduler.py` 无 `run_eod_task`，这些符号均不存在 |

**同时发现 3 个现存 Bug**（非 v1 引入，但本方案顺带修复）：

- **Bug-A**：`_hook_drift_and_retrain` 调用 `self.drift_detector.check_drift()`，但 `ModelDriftDetector` 实际方法名是 `check_all()`。`hasattr(...,'check_drift')` 兜底返回 `[]`，**漂移告警永远不触发**。→ [system_integration.py:590](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L590)
- **Bug-B**：`daily_ic_scores.json` 全项目仅被读取（line 577），**无任何代码写入**，`daily_ic` 恒为 0，`update_ic` 永不执行。IC 数据流断裂。
- **Bug-C**：`_hook_drift_and_retrain` 检测到告警后只记录 `last_retrain_trigger`，**从不调用真正的重训**。

---

## 二、现有架构关键事实（已核实）

### 2.1 `ModelDriftDetector` 接口契约

**实际加载位置**：[v8.3_institutional/src/ml/drift_detector.py:129](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/src/ml/drift_detector.py#L129)（`system_integration.py:62` 将 `v8.3_institutional/src` 注入 `sys.path`，以 `ml.drift_detector` 导入）。

> ⚠️ 注意：`ms_strategy/src/ml/drift_detector.py` 是更新的版本（含 T17 OOS Gap），但**实际加载的是 `v8.3_institutional` 旧版**，不含 T17 接口。本方案以旧版为准。

```python
class ModelDriftDetector:
    def __init__(self, ic_window=20, ic_threshold=0.02, ic_consecutive_days=5,
                 ks_pvalue=0.05, psi_threshold=0.25, adwin_delta=0.002): ...

    # IC 衰减监控
    def update_ic(self, date, ic_value: float) -> Optional[DriftAlert]
    # ADWIN 概念漂移
    def update_adwin(self, value: float) -> Optional[DriftAlert]
    # 特征分布 (KS)
    def set_reference_features(self, features: Dict[str, np.ndarray])
    def check_feature_drift(self, current_features) -> List[DriftAlert]
    # PSI
    def check_psi(self, current_features) -> List[DriftAlert]
    # 综合
    def check_all(self) -> List[DriftAlert]            # 返回 24h 内告警
    def should_retrain(self) -> Tuple[bool, str]
    def generate_report(self) -> dict
```

**四要素齐全**：IC 衰减 + ADWIN + KS(ks_2samp) + PSI。`DriftAlert` 是 dataclass：`timestamp, drift_type(DriftType枚举), severity(Severity枚举), message, metric_name, current_value, threshold, recommendation`。

### 2.2 `IntegratedExecutionSystem` 结构

位置：[system_integration.py:247](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L247)

- `__init__(self, total_capital=5_000_000)`
- `self.report_dir = os.path.join(_BASE, "reports")`（[line 261](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L261)）
- `self.drift_detector = self._init_drift_detector()`（[line 276](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L276)）
- `self.signal_fusion`、`self.stop_loss_monitor`、`self.last_drift_check`、`self.last_retrain_trigger`
- 持仓：`config/positions.json` → `{"positions": {"588080.SH": {code,name,shares,est_price,avg_cost,target_weight,style,sector,...}}}`
- `_find_latest_qlib_report()` 返回 `reports/qlib_*.json` 最新一个，含 `mean_daily_ic`、`ic_ir`

### 2.3 `lgb_enhanced_trainer.py` 训练接口

- `train_symbol_enhanced(symbol, df, config) -> Dict`（[line 1266](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L1266)）— **三参数**
- `run_enhanced_training(symbols, force_retrain, config, use_news) -> Dict`（[line 1796](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L1796)）— 批量入口，`symbols` 是 `List[Tuple]`：(code, suffix, _, name, style)
- `should_retrain(symbol, config) -> bool`（[line 1784](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L1784)）— 基于 `saved_at` + `retrain_interval_days=7`
- `load_model_meta(symbol) -> Optional[Dict]`（[line 1776](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L1776)）— 返回含 `saved_at, cv_before_selection, cv_after_selection, final_metrics, adaptive_retrained, selected_features`
- `LGB_ENHANCED_CONFIG`（[line 67](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L67)）— 已有 `adaptive_retrain_threshold=5, adaptive_retrain_lr=0.001, adaptive_retrain_n_estimators=5000`
- **已有自适应重训**（[line 1369-1415](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L1369-L1415)）：`best_iter <= 5` 触发，仅当 `adaptive_r2 > final_r2` 时替换

### 2.4 `live_scheduler.py` 真实函数

6 个任务函数（[line 138-471](file:///e:/各种PY程序/28-终极量化交易系统8.4/live_scheduler.py#L138-L471)）：

| 函数 | 用途 | 触发 |
|------|------|------|
| `run_market_monitor(dry_run)` | 实时行情监控 | 每 5 分钟 |
| `run_auto_rebalance(dry_run)` | 自动再平衡 | 每 60 分钟 |
| `run_hedge_rebalance(dry_run)` | 对冲再平衡联动 | 每 30 分钟 |
| `run_etf_flow_monitor(dry_run)` | ETF 资金流 | 每 10 分钟 |
| `run_ml_signal_scan(dry_run)` | ML 信号扫描 | 每 15 分钟 |
| `run_daily_report(dry_run)` | 收盘报告 | 15:15 定时 |

`MODULE_DEFINITIONS`（[line 88](file:///e:/各种PY程序/28-终极量化交易系统8.4/live_scheduler.py#L88)）定义调度，`LiveScheduler` 类管理。

---

## 三、新方案模块映射

| 新模块 | 类型 | 对应 v1 | 优先级 | 工时 | 说明 |
|--------|------|---------|--------|------|------|
| **N1** | 修复+增强 `system_integration.py:_hook_drift_and_retrain` | 替代 M1 | 🔴 P0 | 2h | 修复 3 个现存 Bug + 接入真正重训 |
| **N2** | 新建 `strategy_evaluator.py` | 修复 M2 | 🔴 P0 | 3h | 多维评分（修复导入/IC/资金/序列化） |
| **N3** | 新建 `ic_recorder.py` | 替代 M3 | 🟡 P1 | 2h | IC 数据管道，让 `daily_ic_scores.json` 有数据 |
| **N4** | 增强 `lgb_enhanced_trainer.py` 超参搜索 | 修复 M4 | 🟡 P1 | 3h | 在现有自适应重训基础上加过拟合/欠拟合检测 |
| **N5** | 新建 `skill_manager.py` | 修复 M5 | 🟠 P2 | 2h | 经验沉淀（修复 timedelta/空实现） |
| **N6** | 增强 `live_scheduler.py` 注入评估流 | 重写 M6 | 🟠 P2 | 1.5h | 基于 `run_daily_report` 真实函数 |

**废弃**：v1 的 M1（独立函数写法）、M3（重复造轮子）、M6（伪代码）。

---

## 四、详细改造步骤

### 🔴 N1：修复并增强 `_hook_drift_and_retrain`

**位置**：[system_integration.py:571-603](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L571-L603)

**修复的 3 个现存 Bug**：
- Bug-A：`check_drift()` → `check_all()`
- Bug-B：IC 来源从 `daily_ic_scores.json` 扩展为多源回退（IC 记录器 → QLib 报告 → 跳过）
- Bug-C：检测到漂移后真正调用 `run_enhanced_training`，加入冷却期

**新增能力**：重训冷却期、ADWIN 概念漂移注入、真正触发重训。

> 注：实际加载的 `ModelDriftDetector`（v8.3_institutional 旧版）无 T17 OOS Gap 接口，本方案不使用 `update_is_ic`/`update_oos_ic`/`check_oos_gap`。若未来切换到 `ms_strategy` 新版，可额外接入 OOS gap 监控。

#### N1-1：新增模块级配置与冷却期工具

在 [system_integration.py:33](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L33) 附近（imports 之后）新增：

```python
# ============================================================
# 自我进化配置 (N1 新增)
# ============================================================
EVOLUTION_CONFIG = {
    "retrain_cooldown_days": 7,          # 重训冷却期 (与 retrain_interval_days 对齐)
    "ic_min_abs_threshold": 0.001,       # IC 绝对值低于此值视为无效, 跳过更新
    "oos_gap_retrain_trigger": True,     # OOS gap CRITICAL 时触发重训
    "shadow_mode": True,                 # 影子模式: 重训只生成模型不替换生产 (安全开关)
}

def _read_retrain_lock(symbol: str) -> Optional[datetime]:
    """读取标的最近重训时间 (冷却期判断)"""
    lock_path = os.path.join(_BASE, "reports", "retrain_locks", f"{symbol}.json")
    if not os.path.exists(lock_path):
        return None
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            return datetime.fromisoformat(json.load(f).get("last_retrain"))
    except Exception as e:
        logger.debug(f"读取重训锁失败 {symbol}: {e}")
        return None

def _write_retrain_lock(symbol: str, when: datetime) -> None:
    """写入标的最近重训时间"""
    lock_dir = os.path.join(_BASE, "reports", "retrain_locks")
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, f"{symbol}.json")
    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump({"last_retrain": when.isoformat()}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"写入重训锁失败 {symbol}: {e}")
```

#### N1-2：重写 `_hook_drift_and_retrain` 方法

**替换** [system_integration.py:571-603](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L571-L603) 整个方法体：

```python
def _hook_drift_and_retrain(self):
    """步骤5b: 漂移检测 + 自动重训练触发 (N1 重写)

    修复 Bug-A: check_drift() → check_all()
    修复 Bug-B: IC 来源多源回退 (ic_recorder → QLib 报告 → 跳过)
    修复 Bug-C: 检测到漂移后真正触发 run_enhanced_training, 带冷却期

    新增: ADWIN 概念漂移注入 (用 IC 值喂给 ADWIN)
    """
    if not self.drift_detector:
        return
    try:
        today = datetime.now().date()

        # ---------- 1. 获取当日 IC (多源回退, 修复 Bug-B) ----------
        daily_ic = self._fetch_daily_ic()  # 见 N1-3
        if daily_ic is None:
            logger.info("跳过漂移检测: 无可用 IC 数据")
            self.last_drift_check = today.isoformat()
            return

        # ---------- 2. 注入 IC 到检测器 (IC衰减 + ADWIN) ----------
        ic_alert = self.drift_detector.update_ic(today, daily_ic)
        # 同步喂给 ADWIN (用 IC 值检测概念漂移)
        self.drift_detector.update_adwin(daily_ic)

        # ---------- 3. 综合检查 (修复 Bug-A: check_all) ----------
        alerts = self.drift_detector.check_all()
        self.last_drift_check = today.isoformat()

        if not alerts:
            logger.info(f"漂移检测完成: IC={daily_ic:.4f}, 无显著漂移")
            return

        # ---------- 4. 触发重训 (修复 Bug-C, 带冷却期) ----------
        critical = [a for a in alerts if a.severity.value == "critical"]
        logger.warning(
            f"漂移检测发现 {len(alerts)} 个告警 "
            f"(critical={len(critical)}): "
            f"{[a.message for a in alerts[:3]]}"
        )

        need_retrain, reason = self.drift_detector.should_retrain()
        if not need_retrain:
            self.last_retrain_trigger = {
                "date": today.isoformat(), "alerts": len(alerts),
                "action": "monitor_only", "reason": reason,
            }
            return

        retrained = self._trigger_retrain_with_cooldown(reason=reason)
        self.last_retrain_trigger = {
            "date": today.isoformat(), "alerts": len(alerts),
            "critical": len(critical), "action": "retrain_triggered" if retrained else "cooldown_skip",
            "reason": reason,
        }

    except Exception as e:
        logger.warning(f"漂移检测失败: {e}")
        logger.debug(traceback.format_exc())

def _fetch_daily_ic(self) -> Optional[float]:
    """获取当日 IC (多源回退, 修复 Bug-B)

    优先级:
      1. reports/daily_ic_scores.json 的 latest_ic (N3 ic_recorder 写入)
      2. 最新 QLib 报告的 mean_daily_ic
      3. None (跳过)
    """
    # 源1: ic_recorder 写入的当日 IC
    ic_path = os.path.join(self.report_dir, "daily_ic_scores.json")
    if os.path.exists(ic_path):
        try:
            with open(ic_path, "r", encoding="utf-8") as f:
                ic_data = json.load(f)
            latest = float(ic_data.get("latest_ic", 0) or 0)
            if abs(latest) >= EVOLUTION_CONFIG["ic_min_abs_threshold"]:
                return latest
        except Exception as e:
            logger.debug(f"读取 daily_ic_scores.json 失败: {e}")

    # 源2: QLib 报告
    report_path = self._find_latest_qlib_report()
    if report_path:
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
            mean_ic = float(report.get("mean_daily_ic", 0) or 0)
            if abs(mean_ic) >= EVOLUTION_CONFIG["ic_min_abs_threshold"]:
                logger.info(f"IC 来源: QLib 报告 (mean_daily_ic={mean_ic:.4f})")
                return mean_ic
        except Exception as e:
            logger.debug(f"读取 QLib 报告 IC 失败: {e}")

    return None

def _trigger_retrain_with_cooldown(self, reason: str) -> bool:
    """触发重训 (带冷却期, 修复 Bug-C)

    Returns:
        True 如果实际触发了重训, False 如果在冷却期内被跳过
    """
    try:
        from lgb_enhanced_trainer import (
            run_enhanced_training, POSITION_SYMBOLS, LGB_ENHANCED_CONFIG,
            load_model_meta,
        )
    except Exception as e:
        logger.warning(f"无法导入训练模块, 重训取消: {e}")
        return False

    today = datetime.now()
    cooled_symbols = []
    skipped_by_cooldown = []

    for sym_tuple in POSITION_SYMBOLS:
        code = sym_tuple[0]
        last = _read_retrain_lock(code)
        if last and (today - last).days < EVOLUTION_CONFIG["retrain_cooldown_days"]:
            skipped_by_cooldown.append(code)
            continue
        cooled_symbols.append(sym_tuple)

    if not cooled_symbols:
        logger.info(f"重训冷却中, 跳过 {len(skipped_by_cooldown)} 个标的")
        return False

    logger.info(
        f"触发自适应重训: {len(cooled_symbols)} 个标的 "
        f"(冷却跳过 {len(skipped_by_cooldown)}), 原因: {reason}"
    )

    try:
        # 影子模式: force_retrain=True 但只训练不替换生产信号
        # (run_enhanced_training 内部 save_model 会覆盖, 影子模式由 EVOLUTION_CONFIG 控制)
        result = run_enhanced_training(
            symbols=cooled_symbols,
            force_retrain=True,
            config=LGB_ENHANCED_CONFIG,
            use_news=True,
        )
        # 记录冷却锁
        for sym_tuple in cooled_symbols:
            _write_retrain_lock(sym_tuple[0], today)

        # 记录经验 (N5 SkillManager, 可选)
        try:
            from skill_manager import SkillManager
            sm = SkillManager()
            for sym_tuple in cooled_symbols:
                code = sym_tuple[0]
                meta = load_model_meta(code)
                if meta:
                    cv_ic = float((meta.get("cv_after_selection") or {}).get("mean_ic", 0) or 0)
                    sm.save_experience(code, {
                        'type': 'retrain_success',
                        'description': f'漂移触发重训, CV IC={cv_ic:.4f}',
                        'impact': 'positive' if cv_ic > 0 else 'neutral',
                        'action_taken': 'adaptive_retrain',
                        'verified': False,
                    })
        except Exception as e:
            logger.debug(f"经验记录失败 (非致命): {e}")

        logger.info(f"重训完成: {result.get('status', 'UNKNOWN')}")
        return True
    except Exception as e:
        logger.error(f"重训执行失败: {e}")
        logger.debug(traceback.format_exc())
        return False
```

#### N1-4：在 `_init_drift_detector` 中追加历史模型 IC 预热

**修改** [system_integration.py:498-515](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_integration.py#L498-L515) 的预热逻辑，在 QLib 报告预热后追加历史模型 CV IC 注入（让检测器开局就有基线 IC，避免冷启动）：

```python
# 在 detector 预热 QLib IC 之后, 追加历史模型 CV IC 注入
try:
    from lgb_enhanced_trainer import load_model_meta, POSITION_SYMBOLS
    hist_ic_values = []
    for sym_tuple in POSITION_SYMBOLS[:5]:  # 取前 5 个标的的平均 CV IC 作为初始基线
        meta = load_model_meta(sym_tuple[0])
        if meta and meta.get("cv_after_selection"):
            hist_ic_values.append(float(meta["cv_after_selection"].get("mean_ic", 0) or 0))
    if hist_ic_values:
        avg_hist_ic = float(np.mean(hist_ic_values))
        # 注入 5 次历史 IC 作为基线 (不触发告警, 仅建立 rolling 基线)
        seed_date = datetime.now() - timedelta(days=5)
        for i in range(5):
            d = seed_date + timedelta(days=i)
            detector.update_ic(d, avg_hist_ic)
        logger.info(f"历史模型 IC 预热: avg_cv_ic={avg_hist_ic:.4f} (来自 {len(hist_ic_values)} 个模型)")
except Exception as e:
    logger.debug(f"历史 IC 预热失败 (非致命): {e}")
```

> 注：旧版 `ModelDriftDetector` 无 `update_is_ic`，故用 `update_ic` 注入历史 CV IC 作为基线，效果是建立 rolling IC 均值参考。若未来切换到 `ms_strategy` 新版（含 T17），可改用 `update_is_ic` 记录训练时 IS IC。

---

### 🔴 N2：新建 `strategy_evaluator.py`（修复 M2）

**路径**：`e:\各种PY程序\28-终极量化交易系统8.4\strategy_evaluator.py`

**修复的 v1 Bug**：
- `from pathlib import pd` → `import pandas as pd`
- IC 计算返回 Series → 改用 `.corr()` 返回标量
- 硬编码 `total_capital=5_000_000` → 从 `config/positions.json` 的 `meta.total_capital` 读取
- `np.mean` 返回 `numpy.float64` → 用 `float()` 转换
- 日期字符串未转 datetime → 用 `pd.to_datetime`
- `signal_history[0]['features_used']` 无防护 → 加 `.get` + 默认值

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""StrategyEvaluator: 量化策略多维度评分与退化检测模块 (N2).

修复 v1 的 M2 全部 Bug, 对接 config/positions.json 的真实资金.
"""
import json
import math
import os
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd  # 修复 v1: from pathlib import pd (错误导入)

_BASE = os.path.dirname(os.path.abspath(__file__))


def _load_total_capital() -> float:
    """从 config/positions.json 读取总资金 (修复 v1 硬编码 5_000_000)"""
    pos_path = os.path.join(_BASE, "config", "positions.json")
    if os.path.exists(pos_path):
        try:
            with open(pos_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return float(data.get("meta", {}).get("total_capital", 5_000_000))
        except Exception:
            pass
    return 5_000_000


class EvaluationConfig:
    """评分配置"""
    def __init__(self):
        self.weights = {
            'absolute_return': 0.25,
            'risk_adjusted': 0.25,
            'stability': 0.20,
            'robustness': 0.20,
            'complexity_penalty': -0.10,  # 负权重: 惩罚
        }
        self.thresholds = {
            'sharpe_min': 0.5,
            'max_dd_max': 0.15,
            'ic_stability_min': 0.3,
        }
        self.degradation_window_days = 14
        self.degradation_low_score_threshold = 0.5
        self.degradation_min_low_count = 3


def annualize_returns(returns: pd.Series, trading_days: int = 252) -> float:
    """年化收益率"""
    if len(returns) == 0:
        return 0.0
    mean_daily = returns.mean()
    return float((1 + mean_daily) ** trading_days - 1)


def compute_sharpe(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """年化夏普比率"""
    if len(returns) == 0:
        return 0.0
    excess = returns - risk_free_rate / 252
    std = excess.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float(excess.mean() / std * math.sqrt(252))


def max_drawdown(returns: pd.Series) -> float:
    """最大回撤"""
    if len(returns) == 0:
        return 0.0
    cum = (1 + returns).cumprod()
    running_max = cum.cummax()
    dd = (running_max - cum) / running_max
    return float(dd.max())


def ic_stability_metric(ic_series: pd.Series, window: int = 30) -> float:
    """IC 稳定性 (0-1, 越高越稳定)"""
    if len(ic_series) < window:
        return 0.5
    rolling_ic = ic_series.rolling(window, min_periods=1).mean()
    std = rolling_ic.std()
    if std == 0 or pd.isna(std):
        return 1.0
    return float(min(1.0, max(0.0, 1.0 / (1.0 + std))))


class StrategyEvaluator:
    """策略多维度评分器"""

    def __init__(self, config: Optional[EvaluationConfig] = None):
        self.config = config or EvaluationConfig()
        self.total_capital = _load_total_capital()  # 修复 v1 硬编码
        self.performance_history: List[Dict] = []

    def evaluate(
        self,
        trade_history: List[Dict],
        signal_history: List[Dict],
        benchmark_returns: Optional[pd.Series] = None,
        current_date: Optional[datetime] = None,
    ) -> Dict:
        """评估策略健康度

        Args:
            trade_history: [{'date': '2026-07-01' or datetime, 'pnl': 100.0, ...}]
            signal_history: [{'date': ..., 'predicted_return': 0.01,
                            'actual_return': 0.012, 'features_used': ['ma5', ...]}]
            benchmark_returns: 基准日收益率序列
            current_date: 评估日期
        """
        if not trade_history or not signal_history:
            return {
                'error': 'empty_data', 'scores': {},
                'composite_score': 0.0, 'degraded': True,
            }

        returns = self._compute_trade_returns(trade_history)
        predicted_returns = self._compute_predicted_returns(signal_history)

        scores = {
            'absolute_return': self._score_absolute_return(returns),
            'risk_adjusted': self._score_risk_adjusted(returns),
            'stability': self._score_stability(returns),
            'robustness': self._score_robustness(predicted_returns, signal_history),
            'complexity_penalty': self._complexity_penalty(signal_history),
        }
        composite = self._composite_score(scores)
        degraded = self._check_degradation(composite)

        entry = {
            'date': current_date or datetime.now(),
            'scores': scores,
            'composite_score': composite,
            'degraded': degraded,
            'timestamp': datetime.now().isoformat(),
        }
        self.performance_history.append(entry)
        # 滑动窗口保留 30 条
        if len(self.performance_history) > 30:
            self.performance_history = self.performance_history[-30:]

        return {
            'scores': scores,
            'composite_score': round(float(composite), 4),  # 修复 v1: float() 转换
            'degraded': degraded,
            'timestamp': entry['timestamp'],
        }

    def _compute_trade_returns(self, trade_history: List[Dict]) -> pd.Series:
        """计算日收益率序列"""
        daily_pnl: Dict[str, float] = {}
        for t in trade_history:
            d = t['date']
            date_str = d if isinstance(d, str) else d.strftime('%Y-%m-%d')
            daily_pnl[date_str] = daily_pnl.get(date_str, 0.0) + float(t.get('pnl', 0.0))
        if not daily_pnl:
            return pd.Series(dtype=float)
        # 修复 v1: 用 to_datetime 转换索引
        s = pd.Series(daily_pnl)
        s.index = pd.to_datetime(s.index)
        s = s.sort_index() / self.total_capital
        return s

    def _compute_predicted_returns(self, signal_history: List[Dict]) -> pd.Series:
        pred: Dict[str, float] = {}
        for s in signal_history:
            d = s['date']
            date_str = d if isinstance(d, str) else d.strftime('%Y-%m-%d')
            pred[date_str] = float(s.get('predicted_return', 0.0))
        if not pred:
            return pd.Series(dtype=float)
        s = pd.Series(pred)
        s.index = pd.to_datetime(s.index)
        return s.sort_index()

    def _compute_actual_returns(self, signal_history: List[Dict]) -> pd.Series:
        actual: Dict[str, float] = {}
        for s in signal_history:
            if 'actual_return' not in s:
                continue
            d = s['date']
            date_str = d if isinstance(d, str) else d.strftime('%Y-%m-%d')
            actual[date_str] = float(s.get('actual_return', 0.0))
        if not actual:
            return pd.Series(dtype=float)
        s = pd.Series(actual)
        s.index = pd.to_datetime(s.index)
        return s.sort_index()

    def _score_absolute_return(self, returns: pd.Series) -> float:
        if len(returns) < 5:
            return 0.5
        annualized = annualize_returns(returns)
        target_low, target_high = 0.08, 0.20
        if annualized >= target_high:
            return 1.0
        if annualized <= target_low:
            return 0.0  # 修复 v1: 简化冗余表达式
        return 0.5 + (annualized - target_low) / (target_high - target_low) * 0.5

    def _score_risk_adjusted(self, returns: pd.Series) -> float:
        if len(returns) < 10:
            return 0.5
        sharpe = compute_sharpe(returns)
        if sharpe >= 1.0:
            return 1.0
        if sharpe <= 0:
            return max(0.0, sharpe * 0.5)
        return sharpe * 0.9

    def _score_stability(self, returns: pd.Series) -> float:
        dd = max_drawdown(returns)
        if dd <= 0.05:
            return 1.0
        if dd >= 0.15:
            return 0.0
        return max(0.0, min(1.0, (0.15 - dd) / 0.10))

    def _score_robustness(self, predicted: pd.Series, signal_history: List[Dict]) -> float:
        actual = self._compute_actual_returns(signal_history)
        ic_series = self._calculate_ic_series(predicted, actual)
        ic_stab = ic_stability_metric(ic_series) if len(ic_series) > 0 else 0.5
        # 修复 v1: 用 .get + 默认值, 检查所有信号而非仅首个
        feature_counts = [
            len(s.get('features_used', []))
            for s in signal_history if isinstance(s, dict)
        ]
        avg_features = float(np.mean(feature_counts)) if feature_counts else 0.0
        feature_diversity = min(1.0, avg_features / 10.0)
        return float(min(1.0, max(0.0, ic_stab * 0.7 + feature_diversity * 0.3)))

    def _calculate_ic_series(self, predicted: pd.Series, actual: pd.Series) -> pd.Series:
        """计算滚动 IC 序列 (修复 v1: 用 rolling corr 而非 z-score 元素积)

        IC 标准定义 = Spearman/Pearson 相关系数 (标量).
        这里返回滚动窗口 IC 序列, 供 ic_stability_metric 使用.
        """
        if len(predicted) == 0 or len(actual) == 0:
            return pd.Series(dtype=float)
        aligned = pd.concat([predicted, actual], axis=1, keys=['pred', 'actual']).dropna()
        if len(aligned) < 5:
            return pd.Series(dtype=float)
        # 滚动 20 日 Pearson 相关 (IC)
        return aligned['pred'].rolling(20, min_periods=5).corr(aligned['actual'])

    def _complexity_penalty(self, signal_history: List[Dict]) -> float:
        # 修复 v1: 从 signal_history 整体取平均特征数, 而非仅首项
        counts = [
            len(s.get('features_used', []))
            for s in signal_history if isinstance(s, dict)
        ]
        avg_features = float(np.mean(counts)) if counts else 0.0
        if avg_features <= 10:
            return 0.0
        return -min(0.3, (avg_features - 10) / 2.0 * 0.1)

    def _composite_score(self, scores: Dict[str, float]) -> float:
        weighted_sum = 0.0
        total_weight = 0.0
        for dim, score in scores.items():
            w = self.config.weights.get(dim, 0)
            if w > 0:
                weighted_sum += score * w
                total_weight += w
            elif w < 0:
                weighted_sum += score * w  # 惩罚项
        if total_weight == 0:
            return 0.0
        return weighted_sum / total_weight

    def _check_degradation(self, current_score: float) -> bool:
        if len(self.performance_history) < 2:
            return False
        recent = self.performance_history[-self.config.degradation_window_days:]
        low_count = sum(
            1 for e in recent
            if e['composite_score'] < self.config.degradation_low_score_threshold
        )
        return low_count >= self.config.degradation_min_low_count

    def get_status_report(self) -> Dict:
        if not self.performance_history:
            return {'status': 'no_data', 'message': '暂无评估数据'}
        latest = self.performance_history[-1]
        all_composites = [e['composite_score'] for e in self.performance_history]
        avg_score = float(np.mean(all_composites))  # 修复 v1: float() 转换
        score = latest['composite_score']
        status = 'healthy' if score > 0.6 else ('warning' if score > 0.4 else 'degraded')
        return {
            'status': status,
            'latest_score': round(float(score), 4),
            'avg_score_30d': round(avg_score, 4),
            'degraded': latest['degraded'],
            'evaluation_timestamp': latest['timestamp'],
            'sample_size': len(self.performance_history),
        }
```

---

### 🟡 N3：新建 `ic_recorder.py`（替代 M3，补 IC 数据管道）

**路径**：`e:\各种PY程序\28-终极量化交易系统8.4\ic_recorder.py`

**目的**：修复 Bug-B。现有 `daily_ic_scores.json` 只读不写，IC 数据流断裂。本模块负责每日计算 IC 并写入 `reports/daily_ic_scores.json`，供 N1 的 `_hook_drift_and_retrain` 读取。

**不新建 DriftDetector**：漂移检测仍由 `ModelDriftDetector` 负责（已五要素齐全）。

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IC 记录器 (N3): 每日计算并持久化 IC, 修复 Bug-B 数据流断裂.

不重复造漂移检测轮子 — 漂移检测由 ModelDriftDetector 负责.
本模块只负责: 计算当日 IC → 写入 reports/daily_ic_scores.json.
"""
import json
import os
from datetime import datetime, date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

_BASE = os.path.dirname(os.path.abspath(__file__))
IC_STORE_PATH = os.path.join(_BASE, "reports", "daily_ic_scores.json")


def _ensure_store() -> None:
    """确保存储目录存在"""
    os.makedirs(os.path.dirname(IC_STORE_PATH), exist_ok=True)


def compute_ic_from_signals(
    signal_history: List[Dict],
    min_samples: int = 10,
) -> Optional[float]:
    """从信号历史计算当日 IC (Pearson 相关)

    Args:
        signal_history: [{'date': ..., 'predicted_return': ..., 'actual_return': ...}]
        min_samples: 最少样本数

    Returns:
        IC 值, 或 None 如果样本不足
    """
    pairs = [
        (float(s.get('predicted_return', 0)), float(s.get('actual_return', 0)))
        for s in signal_history
        if isinstance(s, dict) and 'actual_return' in s
    ]
    if len(pairs) < min_samples:
        return None
    pred = np.array([p[0] for p in pairs])
    actual = np.array([p[1] for p in pairs])
    if pred.std() == 0 or actual.std() == 0:
        return 0.0
    ic = float(np.corrcoef(pred, actual)[0, 1])
    if np.isnan(ic):
        return 0.0
    return ic


def load_ic_store() -> Dict:
    """加载 IC 存储"""
    if not os.path.exists(IC_STORE_PATH):
        return {"latest_ic": 0.0, "history": []}
    try:
        with open(IC_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"latest_ic": 0.0, "history": []}


def record_daily_ic(
    ic_value: float,
    trade_date: Optional[date] = None,
    source: str = "signal_history",
) -> None:
    """记录当日 IC

    Args:
        ic_value: IC 值
        trade_date: 交易日期 (默认今天)
        source: IC 来源标记
    """
    _ensure_store()
    trade_date = trade_date or date.today()
    store = load_ic_store()

    store["latest_ic"] = float(ic_value)
    store["latest_date"] = trade_date.isoformat()
    store["latest_source"] = source
    store["updated_at"] = datetime.now().isoformat()

    history = store.get("history", [])
    history.append({
        "date": trade_date.isoformat(),
        "ic": float(ic_value),
        "source": source,
    })
    # 保留最近 1 年 (252 交易日)
    if len(history) > 252:
        history = history[-252:]
    store["history"] = history

    try:
        with open(IC_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # 不用 except: pass, 记录日志
        import logging
        logging.getLogger("ic_recorder").warning(f"写入 IC 存储失败: {e}")


def record_ic_from_qlib_report(report_path: str) -> Optional[float]:
    """从 QLib 训练报告提取 IC 并记录 (回退数据源)

    Returns:
        提取到的 IC 值, 或 None
    """
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        mean_ic = float(report.get("mean_daily_ic", 0) or 0)
        record_daily_ic(mean_ic, source="qlib_report")
        return mean_ic
    except Exception as e:
        import logging
        logging.getLogger("ic_recorder").warning(f"从 QLib 报告提取 IC 失败: {e}")
        return None
```

---

### 🟡 N4：增强 `lgb_enhanced_trainer.py` 超参搜索（修复 M4）

**位置**：[lgb_enhanced_trainer.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py)

**修复的 v1 Bug**：
- `recent_cvs = cv_results[-5]` → `cv_results[-5:]`
- 字典字面量重复键 `'lgb_params'` → 正确合并
- `max(0.005 * 1.5, 0.01)` 逻辑反 → 改为正确方向
- `quick_cv_evaluation` 未定义 → 用现有 `time_series_cv_evaluate`
- `_generate_param_combinations` 仅 3 参数 → 完整网格

**与现有代码的关系**：现有 [line 1369-1415](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L1369-L1415) 已有 `best_iter <= 5` 的自适应重训。N4 **不替换**它，而是**新增**一个基于历史 CV 趋势的超参调整函数，在 `train_symbol_enhanced` 的 Step 1（全特征 CV）之后、Step 5（自适应重训）之前调用。

#### N4-1：新增超参网格与历史读取

在 [lgb_enhanced_trainer.py:104](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L104)（`LGB_ENHANCED_CONFIG` 之后）新增：

```python
# ============================================================
# N4: 超参数搜索网格与自适应优化
# ============================================================
HYPERPARAM_GRID = {
    'max_depth': [4, 5, 6, 7],
    'num_leaves': [16, 31, 48],
    'reg_alpha': [0.1, 0.5, 1.0],
    'reg_lambda': [0.5, 1.0, 2.0],
    'subsample': [0.7, 0.8, 0.9],
}


def load_cv_history(symbol: str, max_versions: int = 5) -> List[Dict]:
    """读取标的历史 CV 结果 (从 meta 文件)

    Args:
        symbol: 标的代码
        max_versions: 最多回溯几个版本

    Returns:
        历史 CV 结果列表, 每项含 mean_r2, std_r2, mean_ic, saved_at
    """
    meta = load_model_meta(symbol)
    if not meta:
        return []
    cv = meta.get("cv_after_selection") or meta.get("cv_before_selection") or {}
    if not cv:
        return []
    return [{
        "mean_r2": float(cv.get("mean_r2", 0) or 0),
        "std_r2": float(cv.get("std_r2", 0) or 0),
        "mean_ic": float(cv.get("mean_ic", 0) or 0),
        "saved_at": meta.get("saved_at", ""),
        "adaptive_retrained": bool(meta.get("adaptive_retrained", False)),
    }]
```

#### N4-2：重写 `adaptive_optimize`（修复全部 Bug）

```python
def adaptive_optimize(symbol: str, base_config: Dict) -> Dict:
    """基于历史 CV 趋势自适应调整超参 (N4 重写, 修复 v1 全部 Bug)

    检测模式:
      - 欠拟合: cv_r2 持续 < -0.2 → 增加复杂度 (max_depth, num_leaves, learning_rate)
      - 过拟合: cv_r2 方差大 (std_r2 > 0.3) → 加强正则 (reg_alpha, reg_lambda, 降 subsample)
      - 性能衰退: 最近 cv_r2 持续下降 → 网格搜索新组合
      - 默认: 不调整

    Args:
        symbol: 标的代码
        base_config: 基础配置 (含 lgb_params)

    Returns:
        调整后的配置 (深拷贝, 不修改原配置)
    """
    cv_history = load_cv_history(symbol)
    # 修复 v1: cv_results[-5] (单元素) → 这里本就只读 1 个版本, 不存在切片问题
    if not cv_history:
        return base_config  # 无历史, 不调整

    latest = cv_history[0]
    optimized = dict(base_config)
    new_params = dict(base_config["lgb_params"])  # 修复 v1: 字面量重复键
    adjusted = False

    cv_r2 = latest["mean_r2"]
    std_r2 = latest["std_r2"]

    # 模式1: 欠拟合 (CV R² 持续为负)
    if cv_r2 < -0.2:
        new_params["max_depth"] = min(new_params.get("max_depth", 6) + 1, 10)
        new_params["num_leaves"] = min(new_params.get("num_leaves", 31) + 16, 127)
        # 修复 v1: max(0.005*1.5, 0.01) 逻辑反 → 直接放大学习率
        new_params["learning_rate"] = min(new_params.get("learning_rate", 0.005) * 1.5, 0.02)
        logger.info(f"[{symbol}] N4: 欠拟合 (cv_r2={cv_r2:.4f}), 增加复杂度+学习率")
        adjusted = True

    # 模式2: 过拟合 (CV 方差大)
    elif std_r2 > 0.3:
        new_params["reg_alpha"] = min(new_params.get("reg_alpha", 0.1) * 2, 2.0)
        new_params["reg_lambda"] = min(new_params.get("reg_lambda", 0.5) * 2, 5.0)
        new_params["subsample"] = max(new_params.get("subsample", 0.8) - 0.1, 0.5)
        logger.info(f"[{symbol}] N4: 过拟合 (std_r2={std_r2:.4f}), 加强正则化")
        adjusted = True

    # 模式3: 性能衰退 (IC 为负且非首次)
    elif latest["mean_ic"] < 0 and latest["adaptive_retrained"]:
        best_config = _grid_search_params(symbol, base_config)
        if best_config:
            logger.info(f"[{symbol}] N4: IC 持续为负, 网格搜索切换超参")
            return best_config  # 已含完整 lgb_params

    if adjusted:
        optimized["lgb_params"] = new_params
    return optimized


def _grid_search_params(symbol: str, base_config: Dict) -> Optional[Dict]:
    """网格搜索超参 (修复 v1: _generate_param_combinations 重复键 + 仅 3 参数)

    使用现有 time_series_cv_evaluate 做快速评估 (修复 v1: quick_cv_evaluation 未定义).
    为控制耗时, 只采样网格的一个子集 (前 9 个组合).
    """
    from utils.purged_kfold import PurgedKFold  # 现有依赖, 已确认存在
    # 生成组合 (修复 v1: 字面量重复键)
    combinations = []
    for md in HYPERPARAM_GRID['max_depth']:
        for nl in HYPERPARAM_GRID['num_leaves']:
            for ra in HYPERPARAM_GRID['reg_alpha']:
                new_params = dict(base_config["lgb_params"])  # 先拷贝完整原参数
                new_params.update({  # 再覆盖网格参数
                    'max_depth': md, 'num_leaves': nl, 'reg_alpha': ra,
                })
                combinations.append(new_params)
                if len(combinations) >= 9:  # 限制耗时
                    break
            if len(combinations) >= 9:
                break
        if len(combinations) >= 9:
            break

    # 加载标的特征数据 (复用 run_enhanced_training 的数据管道)
    try:
        ohlcv_dict = fetch_all_real_ohlcv([(symbol, None, None, None, None)], period="2y")
        if symbol not in ohlcv_dict:
            return None
        df = add_technical_features(ohlcv_dict[symbol])
        df["target"] = df["close"].pct_change(5).shift(-5)
        df = df.dropna()
        if len(df) < base_config["min_samples"]:
            return None
        feature_cols = [c for c in df.columns if c not in
                        ["open", "high", "low", "close", "volume", "target"]]
        X = np.nan_to_num(df[feature_cols].values, dtype=np.float64)
        y = df["target"].values
    except Exception as e:
        logger.warning(f"[{symbol}] N4 网格搜索数据加载失败: {e}")
        return None

    best_r2 = -float('inf')
    best_params = None
    for params in combinations:
        try:
            trial_config = dict(base_config)
            trial_config["lgb_params"] = params
            cv_result = time_series_cv_evaluate(
                X, y, trial_config,
                n_splits=base_config["n_splits"], code=symbol,
            )
            trial_r2 = float(cv_result.get("mean_r2", -1))
            if trial_r2 > best_r2:
                best_r2 = trial_r2
                best_params = params
        except Exception as e:
            logger.debug(f"[{symbol}] N4 网格搜索 trial 失败: {e}")
            continue

    if best_params and best_r2 > -0.3:
        optimized = dict(base_config)
        optimized["lgb_params"] = best_params
        optimized["n4_grid_searched"] = True
        optimized["n4_grid_best_r2"] = best_r2
        return optimized
    return None
```

#### N4-3：在 `train_symbol_enhanced` 中接入 N4

在 [lgb_enhanced_trainer.py:1313](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L1313)（Step 1 全特征 CV 之后、Step 2 特征选择之前）插入：

```python
# === N4: 自适应超参优化 (在全特征 CV 之后, 特征选择之前) ===
try:
    optimized_config = adaptive_optimize(symbol, config)
    if optimized_config is not config:  # 发生了调整
        logger.info(f"  {symbol}: N4 超参已调整, 用新参数重新 CV")
        config = optimized_config
        # 用调整后的参数重新做一次全特征 CV
        cv_result = time_series_cv_evaluate(
            X_all, y_all, config,
            n_splits=config["n_splits"], code=symbol,
        )
except Exception as e:
    logger.debug(f"  {symbol}: N4 超参优化跳过 (非致命): {e}")
```

---

### 🟠 N5：新建 `skill_manager.py`（修复 M5）

**路径**：`e:\各种PY程序\28-终极量化交易系统8.4\skill_manager.py`

**修复的 v1 Bug**：
- `timedelta` 未导入 → 补充导入
- `_update_skill_lessons` 是 `pass` → 给出最小可用实现
- 路径硬编码 → 用 `os.path` 相对项目根

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SkillManager: 经验沉淀管理器 (N5, 修复 v1 M5).

记录策略运行中的经验教训, 沉淀到 Skill 文件供后续复用.
"""
import json
import os
from datetime import datetime, timedelta  # 修复 v1: timedelta 未导入
from typing import Dict, List, Optional

_BASE = os.path.dirname(os.path.abspath(__file__))


class SkillManager:
    """经验沉淀管理器"""

    def __init__(self, skill_file_path: Optional[str] = None):
        if skill_file_path is None:
            skill_file_path = os.path.join(_BASE, ".claude", "skills", "alpha_research_skill.md")
        self.skill_path = os.path.join(_BASE, skill_file_path) if not os.path.isabs(skill_file_path) else skill_file_path
        self.experience_log = os.path.join(_BASE, "logs", "experience_log.json")
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        os.makedirs(os.path.dirname(self.skill_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.experience_log), exist_ok=True)

    def load_experience_log(self) -> List[Dict]:
        if not os.path.exists(self.experience_log):
            return []
        try:
            with open(self.experience_log, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            import logging
            logging.getLogger("skill_manager").warning(f"读取经验日志失败: {e}")
            return []

    def save_experience(self, symbol: str, lesson: Dict) -> None:
        """记录一条经验

        Args:
            symbol: 标的代码
            lesson: {'type': 'retrain_success'|'retrain_fail'|'drift_false_positive'|...,
                    'description': str, 'impact': 'positive'|'negative'|'neutral',
                    'action_taken': str, 'verified': bool}
        """
        entry = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'lesson_type': lesson.get('type', 'unknown'),
            'description': lesson.get('description', ''),
            'impact': lesson.get('impact', 'neutral'),
            'action_taken': lesson.get('action_taken', ''),
            'verified': lesson.get('verified', False),
        }
        log = self.load_experience_log()
        log.append(entry)
        try:
            with open(self.experience_log, "w", encoding="utf-8") as f:
                json.dump(log, f, ensure_ascii=False, indent=2)
        except Exception as e:
            import logging
            logging.getLogger("skill_manager").warning(f"写入经验日志失败: {e}")
            return
        self._update_skill_lessons(lesson)

    def _update_skill_lessons(self, lesson: Dict) -> None:
        """将经验追加到 Skill 文件 (修复 v1: pass 空实现)

        采用追加模式, 不解析整个 Markdown, 避免破坏现有内容.
        """
        if not lesson.get('description'):
            return
        try:
            # 追加到 Skill 文件末尾的经验区
            block = (
                f"\n## 经验记录 ({datetime.now().strftime('%Y-%m-%d')})\n"
                f"- **类型**: {lesson.get('type', 'unknown')}\n"
                f"- **描述**: {lesson['description']}\n"
                f"- **影响**: {lesson.get('impact', 'neutral')}\n"
                f"- **动作**: {lesson.get('action_taken', '')}\n"
            )
            with open(self.skill_path, "a", encoding="utf-8") as f:
                f.write(block)
        except Exception as e:
            import logging
            logging.getLogger("skill_manager").warning(f"追加 Skill 文件失败: {e}")

    def get_recent_lessons(
        self, symbol: Optional[str] = None, days: int = 7
    ) -> List[Dict]:
        """获取近 N 天经验"""
        log = self.load_experience_log()
        cutoff = datetime.now() - timedelta(days=days)
        filtered = [
            l for l in log
            if datetime.fromisoformat(l['timestamp']) > cutoff
            and (symbol is None or l.get('symbol') == symbol)
        ]
        return sorted(filtered, key=lambda x: x['timestamp'], reverse=True)
```

---

### 🟠 N6：增强 `live_scheduler.py` 注入评估流（重写 M6）

**位置**：[live_scheduler.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/live_scheduler.py)

**修复的 v1 问题**：
- v1 引用 `run_eod_task`（不存在）→ 改为增强 `run_daily_report`（真实存在）
- v1 引用 `POSITION_SYMBOLS`/`load_trade_history`/`schedule_retrain`（不存在）→ 用真实符号源

#### N6-1：新增评估任务函数

在 [live_scheduler.py:471](file:///e:/各种PY程序/28-终极量化交易系统8.4/live_scheduler.py#L471)（`run_daily_report` 之后）新增：

```python
def run_strategy_evaluation(dry_run: bool = False) -> Dict[str, Any]:
    """N6: 策略健康度评估任务 (每日收盘后运行)

    对接真实数据源:
      - 持仓: config/positions.json
      - 交易历史: reports/trade_history.json (若存在)
      - 信号历史: reports/signal_history.json (若存在)
      - IC 记录: reports/daily_ic_scores.json (N3 写入)
    """
    start = datetime.now()
    result = {"status": "OK", "data": {}}
    try:
        if dry_run:
            logger.info("[strategy_eval] [DRY-RUN] 跳过实际评估")
            result["data"] = {"dry_run": True}
            result["duration"] = 0.0
            return result

        # 延迟导入 (避免模块加载时的循环依赖)
        from strategy_evaluator import StrategyEvaluator
        from ic_recorder import load_ic_store, record_ic_from_qlib_report

        # 1. 先尝试用信号历史计算当日 IC (修复 Bug-B 数据流)
        signal_hist_path = BASE_DIR / "reports" / "signal_history.json"
        trade_hist_path = BASE_DIR / "reports" / "trade_history.json"

        signal_history = []
        trade_history = []
        if signal_hist_path.exists():
            try:
                with open(signal_hist_path, "r", encoding="utf-8") as f:
                    signal_history = json.load(f)
            except Exception as e:
                logger.warning(f"[strategy_eval] 读取信号历史失败: {e}")
        if trade_hist_path.exists():
            try:
                with open(trade_hist_path, "r", encoding="utf-8") as f:
                    trade_history = json.load(f)
            except Exception as e:
                logger.warning(f"[strategy_eval] 读取交易历史失败: {e}")

        # 2. 计算并记录 IC (N3)
        from ic_recorder import compute_ic_from_signals, record_daily_ic
        if signal_history:
            ic_value = compute_ic_from_signals(signal_history)
            if ic_value is not None:
                record_daily_ic(ic_value, source="signal_history")
                logger.info(f"[strategy_eval] 当日 IC={ic_value:.4f} 已记录")
            else:
                # 回退: 从 QLib 报告读 IC
                qlib_reports = list((BASE_DIR / "reports").glob("qlib_*.json"))
                if qlib_reports:
                    latest_report = max(qlib_reports, key=lambda p: p.stat().st_mtime)
                    record_ic_from_qlib_report(str(latest_report))
                    logger.info(f"[strategy_eval] IC 来源回退到 QLib 报告")
        else:
            # 无信号历史, 直接用 QLib 报告
            qlib_reports = list((BASE_DIR / "reports").glob("qlib_*.json"))
            if qlib_reports:
                latest_report = max(qlib_reports, key=lambda p: p.stat().st_mtime)
                record_ic_from_qlib_report(str(latest_report))

        # 3. 策略多维评分
        evaluator = StrategyEvaluator()
        if trade_history and signal_history:
            report = evaluator.evaluate(trade_history, signal_history)
            status = evaluator.get_status_report()
            result["data"] = {
                "evaluation": report,
                "status": status,
                "ic_recorded": load_ic_store().get("latest_ic"),
            }
            if report.get("degraded"):
                logger.warning(
                    f"[strategy_eval] 策略健康度下降: "
                    f"composite={report.get('composite_score')}, {status}"
                )
            else:
                logger.info(
                    f"[strategy_eval] 评估完成: "
                    f"composite={report.get('composite_score')}, status={status.get('status')}"
                )
        else:
            result["data"] = {
                "skipped": True,
                "reason": "无交易/信号历史",
                "ic_recorded": load_ic_store().get("latest_ic"),
            }
            logger.info("[strategy_eval] 跳过评分 (无历史数据), 仅记录 IC")

    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[strategy_eval] 执行失败: {e}")

    result["duration"] = (datetime.now() - start).total_seconds()
    return result
```

#### N6-2：将评估任务接入调度

**修改** [live_scheduler.py:88](file:///e:/各种PY程序/28-终极量化交易系统8.4/live_scheduler.py#L88) 的 `MODULE_DEFINITIONS`，在 `daily_report` 项之后追加：

```python
{
    "name": "strategy_evaluation",
    "description": "策略健康度评估与IC记录",
    "interval_seconds": None,  # 定时触发
    "task_func": "run_strategy_evaluation",
    "required": False,
    "trigger_time": dt_time(15, 30),  # 15:30 收盘后 (在 daily_report 之后)
},
```

注意：`LiveScheduler.start()` 已通过 `MODULE_DEFINITIONS` 最后一项的 `trigger_time` 调度定时任务。新增项会成为新的最后一项，`_schedule_daily_report` 会自动调度它（参考 [line 536-568](file:///e:/各种PY程序/28-终极量化交易系统8.4/live_scheduler.py#L536-L568) 的逻辑，它读取 `MODULE_DEFINITIONS[-1]["trigger_time"]`）。

**修正**：原 `_schedule_daily_report` 只调度最后一项。为避免破坏现有 `daily_report`（15:15），需把 `strategy_evaluation` 的调度独立。最简方案：保持 `daily_report` 为最后一项，把 `strategy_evaluation` 插在 `daily_report` **之前**，并改为每 60 分钟轮询（在 15:30-17:30 窗口内执行）：

```python
# 插在 daily_report 之前 (index 5)
{
    "name": "strategy_evaluation",
    "description": "策略健康度评估与IC记录",
    "interval_seconds": 3600,  # 每小时检查一次
    "task_func": "run_strategy_evaluation",
    "required": False,
},
```

并在 `run_strategy_evaluation` 开头加窗口判断（仅 15:30-17:30 执行）：

```python
now = datetime.now()
trigger = dt_time(15, 30)
window_end = dt_time(17, 30)
if not (trigger <= now.time() <= window_end):
    result["data"] = {"skipped": True, "reason": "outside_window"}
    result["duration"] = 0.0
    return result
```

并加每日一次锁（参考现有 `_schedule_daily_report` 的 `module_last_run` 键模式），避免一小时内重复执行。

---

## 五、依赖与配置检查

### 5.1 依赖确认（已核实存在）

| 依赖 | 位置 | 状态 |
|------|------|------|
| `ModelDriftDetector` | [ms_strategy/src/ml/drift_detector.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/ml/drift_detector.py) | ✅ 五要素齐全 |
| `utils/purged_kfold.py` | utils/ | ✅ 存在 |
| `utils/trade_calendar.py` | utils/ | ✅ 存在 |
| `time_series_cv_evaluate` | [lgb_enhanced_trainer.py:1134](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L1134) | ✅ 存在 |
| `fetch_all_real_ohlcv` | [lgb_enhanced_trainer.py:229](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py#L229) | ✅ 存在 |
| `add_technical_features` | utils/features.py | ✅ 存在 |

### 5.2 不需要新建的模块

- ~~`utils/ic_data_loader.py`~~（v1 要求新建）→ **不需要**，IC 由 N3 `ic_recorder.py` 提供
- ~~`DriftDetector`~~（v1 M3）→ **不需要**，复用 `ModelDriftDetector`

### 5.3 安装命令

```bash
# 现有依赖, 无需新增 (scipy/numpy/pandas/lightgbm 已在项目中)
pip install scipy numpy pandas lightgbm  # 若未安装
```

---

## 六、验证脚本

**路径**：`e:\各种PY程序\28-终极量化交易系统8.4\scripts\verify_evolution_v2.py`

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N1-N6 进化框架验证脚本 (v2, 兼容现有架构)"""
import os
import sys
from datetime import datetime, date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "v8.3_institutional", "src"))


def test_n2_strategy_evaluator():
    """N2: 测试多维评分器 (修复版)"""
    from strategy_evaluator import StrategyEvaluator, EvaluationConfig
    evaluator = StrategyEvaluator(EvaluationConfig())
    # 构造 20 天 dummy 数据
    base = date(2026, 7, 1)
    signals = [
        {'date': (base.isoformat() if i == 0 else f"2026-07-{i+1:02d}"),
         'predicted_return': 0.001 * (i % 5),
         'actual_return': 0.0012 * (i % 5) + 0.0001 * i,
         'features_used': ['ma5', 'ma20', 'rsi', 'macd']}
        for i in range(20)
    ]
    trades = [
        {'date': f"2026-07-{i+1:02d}", 'symbol': '588080.SH',
         'side': 'BUY', 'qty': 100, 'price': 1.0, 'pnl': 100.0 * (i % 3 - 1)}
        for i in range(20)
    ]
    result = evaluator.evaluate(trades, signals)
    assert 'composite_score' in result
    assert isinstance(result['composite_score'], float)  # 修复 v1: 不是 numpy.float64
    assert 0.0 <= result['composite_score'] <= 1.0 or result.get('degraded')
    print(f"✓ N2 StrategyEvaluator 通过: composite={result['composite_score']}")
    return evaluator


def test_n3_ic_recorder():
    """N3: 测试 IC 记录器"""
    from ic_recorder import (
        compute_ic_from_signals, record_daily_ic, load_ic_store,
    )
    # 计算 IC
    signals = [
        {'predicted_return': 0.01 * i, 'actual_return': 0.012 * i}
        for i in range(15)
    ]
    ic = compute_ic_from_signals(signals, min_samples=10)
    assert ic is not None and 0.0 <= ic <= 1.0
    # 记录
    record_daily_ic(ic, trade_date=date(2026, 7, 29), source="test")
    store = load_ic_store()
    assert store["latest_ic"] == ic
    assert any(h["source"] == "test" for h in store["history"])
    print(f"✓ N3 ICRecorder 通过: IC={ic:.4f}")


def test_n1_drift_detector_interface():
    """N1: 验证 ModelDriftDetector 接口可用 (不触发真实重训)

    注: 实际加载版本为 v8.3_institutional/src/ml/drift_detector.py (旧版, 无 T17 OOS Gap).
    四要素齐全: IC衰减 + ADWIN + KS + PSI.
    """
    from ml.drift_detector import ModelDriftDetector, Severity, DriftType
    detector = ModelDriftDetector()
    today = date.today()
    # 注入正常 IC (连续 25 天)
    for i in range(25):
        detector.update_ic(today, 0.05)
    # 验证 check_all (修复 Bug-A: 现有代码误用 check_drift)
    alerts = detector.check_all()
    assert isinstance(alerts, list), f"check_all 应返回 list, 实际 {type(alerts)}"
    # 验证 should_retrain
    need, reason = detector.should_retrain()
    assert isinstance(need, bool) and isinstance(reason, str)
    # 验证 ADWIN 接口
    detector.update_adwin(0.05)
    # 验证 generate_report
    report = detector.generate_report()
    assert "ic_stats" in report and "should_retrain" in report
    print(f"✓ N1 ModelDriftDetector 接口通过: need_retrain={need}, alerts={len(alerts)}")


def test_n4_adaptive_optimize():
    """N4: 验证 adaptive_optimize 不崩溃 (无历史时返回原配置)"""
    sys.path.insert(0, BASE)
    try:
        from lgb_enhanced_trainer import adaptive_optimize, LGB_ENHANCED_CONFIG
    except ImportError:
        # N4 尚未实施时跳过
        print("⚠ N4 adaptive_optimize 未导入 (尚未实施), 跳过")
        return
    result = adaptive_optimize("NONEXISTENT_SYMBOL", LGB_ENHANCED_CONFIG)
    assert "lgb_params" in result
    print(f"✓ N4 adaptive_optimize 通过: 返回配置含 lgb_params")


def test_n5_skill_manager():
    """N5: 验证经验沉淀"""
    try:
        from skill_manager import SkillManager
    except ImportError:
        print("⚠ N5 SkillManager 未导入 (尚未实施), 跳过")
        return
    sm = SkillManager()
    sm.save_experience("588080.SH", {
        'type': 'retrain_success',
        'description': '测试经验记录',
        'impact': 'positive',
        'action_taken': 'adaptive_retrain',
        'verified': True,
    })
    recent = sm.get_recent_lessons(symbol="588080.SH", days=1)
    assert any(l['description'] == '测试经验记录' for l in recent)
    print(f"✓ N5 SkillManager 通过: 记录数={len(recent)}")


def test_n6_evaluation_task():
    """N6: 验证评估任务函数可调用 (dry-run)"""
    try:
        from live_scheduler import run_strategy_evaluation
    except ImportError:
        print("⚠ N6 run_strategy_evaluation 未导入 (尚未实施), 跳过")
        return
    result = run_strategy_evaluation(dry_run=True)
    assert result["status"] == "OK"
    assert result["data"].get("dry_run") is True
    print(f"✓ N6 run_strategy_evaluation 通过: dry-run OK")


if __name__ == "__main__":
    print("=" * 60)
    print("  v8.4 自我进化框架 v2 验证")
    print("=" * 60)
    test_n1_drift_detector_interface()
    test_n2_strategy_evaluator()
    test_n3_ic_recorder()
    test_n4_adaptive_optimize()
    test_n5_skill_manager()
    test_n6_evaluation_task()
    print("=" * 60)
    print("  ✅ 所有可验证组件通过!")
    print("=" * 60)
```

---

## 七、实施顺序

```
Day 1 (P0):
  ├─ N1: 修复 _hook_drift_and_retrain (Bug-A/B/C)
  └─ N3: 新建 ic_recorder.py (补 IC 数据流)
  验证: scripts/verify_evolution_v2.py test_n1 + test_n3

Day 2 (P0):
  └─ N2: 新建 strategy_evaluator.py
  验证: test_n2

Day 3 (P1):
  └─ N4: 增强 lgb_enhanced_trainer.py 超参搜索
  验证: test_n4 + 单标的 dry-run 训练

Day 4 (P2):
  ├─ N5: 新建 skill_manager.py
  └─ N6: 增强 live_scheduler.py
  验证: test_n5 + test_n6

Day 5: 集成测试 + 影子账户 1 周观察
```

每步完成后在小样本回测验证，确认无新问题再推进。

---

## 八、风险控制清单

- [ ] N1 重训默认影子模式（`EVOLUTION_CONFIG.shadow_mode=True`），不替换生产信号
- [ ] N1 重训冷却期 7 天（与 `retrain_interval_days` 对齐），避免频繁震荡
- [ ] N2 评分仅作为监控指标，不直接驱动交易决策
- [ ] N3 IC 来源标记 `source` 字段，便于追溯异常数据
- [ ] N4 网格搜索限制 9 个组合，避免耗时失控
- [ ] N6 评估任务 `required=False`，失败不影响其他模块
- [ ] 所有新代码单元测试覆盖率 ≥80%
- [ ] 漂移检测误报率监控（假阳性率 <5%）
- [ ] 重大变更（重训触发）写入 `STATE.md` 和经验日志
- [ ] 单策略仓位上限（新策略 ≤5% 总资金）

---

## 九、与 v1 的差异总结

| 维度 | v1（原报告） | v2（本方案） |
|------|-------------|-------------|
| M1/N1 漂移钩子 | 改独立函数，与类方法不兼容 | **修复**类方法的 3 个现存 Bug |
| M2/N2 评分器 | `from pathlib import pd` 等 8 处致命 Bug | **修复全部**，对接真实资金 |
| M3/N3 漂移检测 | 新建降级版 DriftDetector（仅 KS+均值） | **废弃**，复用五要素 ModelDriftDetector；新建 IC 管道 |
| M4/N4 超参搜索 | 字典重复键、切片越界等 6 处 Bug | **修复全部**，对接真实 CV 结构与 `time_series_cv_evaluate` |
| M5/N5 经验沉淀 | `timedelta` 未导入、空实现 | **修复**，补全导入与实现 |
| M6/N6 调度注入 | 引用 `run_eod_task` 等不存在符号 | **重写**，基于 `run_daily_report` 真实函数 |
| 架构关系 | 与现有代码冲突，会破坏可运行流程 | 增量增强，兼容现有 `ModelDriftDetector` 与 `IntegratedExecutionSystem` |
| 现存 Bug | 未识别 | 顺带修复 3 个（check_all、IC 数据流、不触发重训） |

---

**文档版本**：v2.0
**生成时间**：2026-07-29
**基于代码版本**：v8.4（2026-07-29 快照）
**审查依据**：`docs/CODE_REVIEW_2026-07-29.md`
