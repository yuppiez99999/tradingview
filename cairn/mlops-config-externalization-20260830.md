# MLOps 配置显式化（2026-08-30）

创建: 2026-08-30 | 关联: `config/lgb_training.yaml`, `config/mlops.yaml`
LOG 指针: `cairn/LOG.md` 2026-08-30 · MLOps 配置显式化

---

## 背景

LightGBM 增强训练超参 `LGB_ENHANCED_CONFIG` 硬编码在 `lgb_enhanced_trainer.py:69`，是权威配置源；`auto_retrain_scheduler.py` 已对接 `get_config("mlops")` 但 `config/mlops.yaml` 缺失，走空默认。本批次把硬编码配置显式化为 yaml，走 `ConfigManager` 4 级优先级加载。

---

## 设计

### 配置加载链路

```
QUANT_CONFIG_DIR（环境变量，最高优先级）
  > v8.3_institutional/config/
  > configs/
  > ms_strategy/config/
  > 硬编码 _FALLBACK_CONFIG（fail-safe 回退）
```

- `ConfigManager.get_config(key)` 按 4 级优先级查找 yaml
- 找到则深度合并 yaml 覆盖 `_FALLBACK_CONFIG`（保留 fallback 未覆盖字段）
- 全部找不到则回退硬编码，不崩溃

### 新增配置文件

#### `config/lgb_training.yaml`（18 字段）

从 `LGB_ENHANCED_CONFIG` 迁移，含：
- 训练参数：objective/num_leaves/learning_rate/num_iterations/early_stopping_rounds 等
- 特征参数：feature_fraction/bagging_fraction/bagging_freq
- 正则化：lambda_l1/lambda_l2/min_gain_to_split
- 类别特征：categorical_feature
- 路径：model_dir/feature_store_dir

#### `config/mlops.yaml`（4 块）

| 块 | 职责 | 消费方 |
|---|---|---|
| `auto_retrain` | 自动重训触发条件（drift_threshold/min_samples/cooldown） | `utils/alpha/auto_retrain_scheduler.py` |
| `retrain_workflow` | 重训工作流参数（retrain_window/features/symbols） | `15_每日工作流/run_auto_retrain.py` |
| `drift_monitor` | 漂移监控（monitor_interval/alert_threshold） | drift monitor |
| `ab_testing` | A/B 测试（traffic_split/evaluation_window） | ab testing |

---

## 实现

### `lgb_enhanced_trainer.py`

```python
_FALLBACK_CONFIG = { ... }  # 原硬编码移为 fallback

def _load_lgb_config() -> dict:
    """从 config/lgb_training.yaml 加载，深度合并覆盖 fallback。"""
    cfg = copy.deepcopy(_FALLBACK_CONFIG)
    try:
        yaml_cfg = get_config("lgb_training") or {}
        cfg = _deep_merge(cfg, yaml_cfg)
    except Exception:
        pass  # fail-safe 回退 fallback
    return cfg

LGB_ENHANCED_CONFIG = _load_lgb_config()
```

### `15_每日工作流/run_auto_retrain.py`

```python
RETRAIN_CONFIG = _load_retrain_config()  # 从 mlops.yaml retrain_workflow 块加载
```

---

## 验证

- ruff 全绿
- 配置加载正确（yaml 字段覆盖 fallback）
- `QUANT_CONFIG_DIR` 环境变量覆盖生效（测试验证）
- 135 测试通过
- 子模块通过 `configure_paths` 注入配置，未破坏现有接口

---

## 使用

### 默认

直接用 `config/lgb_training.yaml` 和 `config/mlops.yaml`。

### 环境覆盖

```powershell
$env:QUANT_CONFIG_DIR = "D:\my_config"
# 放 D:\my_config\lgb_training.yaml 和 D:\my_config\mlops.yaml
```

### Windows 计划任务

每月 1 日 06:00 触发 ML 重训（`setup_retrain_scheduled_task.ps1`），读取 `mlops.yaml` 配置。

---

## 踩坑标注 — contains(配置显式化深度合并保留 fallback)

**坑**: 若直接用 yaml 替换整个配置，yaml 漏写字段会变成 None/缺失，破坏训练。
**解**: 深度合并 — 以 `_FALLBACK_CONFIG` 为底，yaml 字段逐层覆盖，未覆盖的字段保留 fallback 值。
**教训**: 配置显式化必须保留硬编码 fallback 作为安全底，yaml 是"覆盖"不是"替换"。