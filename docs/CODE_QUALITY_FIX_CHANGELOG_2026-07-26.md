# 代码质量优化变更日志 — 2026-07-26

**关联文档**: [CODE_QUALITY_AUDIT_2026-07-26_HEDGE_FUND_VIEW.md](./CODE_QUALITY_AUDIT_2026-07-26_HEDGE_FUND_VIEW.md)
**验证脚本**: [scripts/_verify_code_quality_fixes.py](../scripts/_verify_code_quality_fixes.py), [scripts/_verify_config_manager.py](../scripts/_verify_config_manager.py)
**测试结果**:
- 代码质量修复: 44/44 PASS, E2E 8/8 无回归, LGB 集成测试全部通过
- ConfigManager 集成: 43/43 PASS, v868 实盘就绪 27/27 无回归, code_quality_fixes 52/52 无回归

---

## 修复总览

| 编号 | 等级 | 修复内容 | 文件 | 状态 |
|------|------|----------|------|------|
| P0-Q1 | P0 | 修复 PortfolioOptimizer 前视偏差 (current_dd 排除当日 PnL) | utils/portfolio_optimizer.py | ✅ 完成 |
| P0-Q3 | P0 | 添加总敞口 cap 保护 (MAX_TOTAL_EXPOSURE=1.5x) | utils/portfolio_optimizer.py | ✅ 完成 |
| P0-Q2 | P0 | daily_workflow.phase_signal 拆分 (948→301 行, 21 子方法) | v8.3_institutional/daily_workflow.py | ✅ 完成 |
| P1-Q4 | P1 | KillSwitch fail-closed 完整覆盖所有路径 | utils/kill_switch.py | ✅ 完成 |
| P1-Q5 | P1 | 抽象 PostMixLayer 类重构 signal_fusion._fuse_symbol | utils/signal_fusion.py | ✅ 完成 |
| P1-Q6 | P1 | KillSwitch level 解析重构为 IntEnum + 专用函数 | utils/risk_guard_integrator.py | ✅ 完成 |
| P1-Q7 | P1 | KillSwitch._estimate_margin_from_positions 拆分 (117→30 行编排器+4 子方法) | utils/kill_switch.py | ✅ 完成 |
| P1-Q8 | P1 | 统一 ConfigManager 整合 3 个 yaml 配置目录 (15+ 文件) | utils/config_manager.py, utils/kill_switch.py | ✅ 完成 |
| P2-Q11 | P2 | KillSwitch 总保证金配置化 (移除硬编码 5_000_000) | utils/kill_switch.py | ✅ 完成 |

**未完成**: P1-Q9 (tests/ 三层目录整理) — 优先级低, 留待后续

---

## P0-Q1: 前视偏差修复

**文件**: `utils/portfolio_optimizer.py:329-341`

**问题**: `apply_risk_management` 的注释声明"基于昨日净值避免前视偏差"，但实际代码：

```python
# 原代码 (含前视偏差)
cumulative = [1.0]
for p in daily_pnl_history:                    # ← 含当日 pnl_t
    cumulative.append(cumulative[-1] * (1.0 + float(p)))
peak = max(cumulative)
current_value = cumulative[-1]                 # ← 含当日结果的最新净值
current_dd = (peak - current_value) / peak
```

**修复**:

```python
# 修复后 (基于昨日净值)
pnl_for_dd = daily_pnl_history[:-1] if len(daily_pnl_history) >= 2 else daily_pnl_history
cumulative = [1.0]
for p in pnl_for_dd:
    cumulative.append(cumulative[-1] * (1.0 + float(p)))
peak = max(cumulative) if cumulative else 1.0
current_value = cumulative[-1] if cumulative else 1.0
current_dd = (peak - current_value) / peak if peak > 0 else 0.0
```

**审计字段**: 新增 `lookahead_bias_fixed: True` 和 `dd_pnl_used: "yesterday_only"`，便于实盘验证修复已生效。

**验证场景**: 
- 输入 `[+0.10, -0.0455, -0.0952, +0.05]`
- 修复前 current_dd = 9.1% (含当日 +5% 拉回，过度乐观)
- 修复后 current_dd = 13.6% (真实回撤，与实盘决策时点一致)

---

## P0-Q3: 总敞口 cap 保护

**文件**: `utils/portfolio_optimizer.py:214-216, 349-369`

**问题**: `combined_scaler = vol_scaler * dd_scaler` 可达 2.0×（低波动期 `vol_scaler=2.0`），导致总敞口从 100% 跃升至 200%，无 hard cap 保护。

**修复**:

```python
# 类常量
MAX_TOTAL_EXPOSURE = 1.5  # 1.5x 杠杆上限

# 在 combined_scaler 应用后
if scaled_total_exposure > MAX_EXPOSURE and scaled_total_exposure > 1e-9:
    cap_scaler = MAX_EXPOSURE / scaled_total_exposure
    scaled_weights = {k: v * cap_scaler for k, v in scaled_weights.items()}
    exposure_cap_applied = True
```

**审计字段**: 新增 `exposure_cap_applied` 和 `max_total_exposure`，便于实盘监控。

**配置支持**: 支持通过 `config['max_total_exposure']` 自定义上限。

---

## P1-Q4: KillSwitch fail-closed 完整性

**文件**: `utils/kill_switch.py:252-315`

**问题**: `check_margin_status` 仅在 `margin_usage=None` 时检查 fail-closed。当 caller 显式传入 `margin_usage=0.0` (数据源故障默认值) 时，会被判为"正常"，绕过 production 模式的 fail-closed 保护。

**修复**:

```python
def check_margin_status(self, margin_usage=None):
    trading_env = os.environ.get("TRADING_ENV", "dev").lower()
    
    if margin_usage is not None:
        # P1-Q4: 显式传入也需校验
        try:
            ratio = max(0.0, min(1.0, float(margin_usage)))
        except (TypeError, ValueError):
            return self._fail_closed_response("INVALID_MARGIN_USAGE_TYPE")
        
        # production 模式校验合理性 (异常低值 < 0.01 疑数据源故障)
        if trading_env == "production" and ratio < 0.01:
            return self._fail_closed_response("SUSPICIOUS_LOW_MARGIN_USAGE")
        # ...
```

**新增辅助方法**:
- `_fail_closed_response(reason)`: 统一生成 L3 fail-closed 响应，含 `fail_closed_reason` 审计字段

**覆盖路径**:
1. `margin_usage=None` + production → `DATA_UNAVAILABLE`
2. `margin_usage=0.0` + production → `SUSPICIOUS_LOW_MARGIN_USAGE`
3. `margin_usage="invalid"` + production → `INVALID_MARGIN_USAGE_TYPE`
4. `margin_usage=0.5` + production → 正常流程 (不触发)

---

## P1-Q5: PostMixLayer 抽象重构

**文件**: `utils/signal_fusion.py:43-174, 260-285, 372-416, 493-615`

**问题**: `_fuse_symbol` 函数 189 行，含 4 层 post-mix 叠加 (主融合 + pipeline + research + lgb)，每层重复 "4 层 NaN 防御" 是 defensive programming 反模式。

**修复**: 抽象 `PostMixLayer` 类，单一职责 + 可独立测试 + 可热插拔：

```python
@dataclass
class PostMixLayer:
    name: str
    weight: float
    signals: Dict[str, float] = field(default_factory=dict)
    quality_flags: Dict[str, str] = field(default_factory=dict)
    quality_decay: float = 0.5  # LOW_QUALITY 标的权重衰减
    enabled: bool = True

    def update_signals(self, signals, quality_flags=None):
        """统一 NaN/Inf 过滤 + 结构化/扁平格式解析"""
    
    def apply(self, strength, symbol) -> tuple:
        """叠加该层信号: new = strength * (1 - w) + signal * w"""
        return new_strength, applied
```

**重构效果**:
- `_fuse_symbol` 从 189 行减至 ~120 行
- 3 个 `inject_*` 方法从 ~80 行减至 ~45 行 (委托给 PostMixLayer)
- NaN 防御集中在 PostMixLayer, 不再每层重复

**向后兼容**: 保留 `_pipeline_factor_signals`、`_research_distilled_signals`、`_lgb_enhanced_signals`、`_lgb_quality_flags` 字段供外部读取，但内部逻辑全部委托 PostMixLayer。

**审计字段**: meta 新增 `postmix_layer_refactored: True` 标记。

---

## P1-Q6: KillSwitchLevel IntEnum

**文件**: `utils/risk_guard_integrator.py:45-112, 834-892`

**问题**: 7-Guard 链 level 解析使用嵌套三元运算符，可读性差且无类型保护：

```python
# 原代码 (反模式)
if isinstance(ks_level, str):
    level_str = ks_level.upper().replace('L', '')
    ks_level_int = 3 if level_str == 'OK' and margin_usage >= 0.75 else (
        3 if level_str == '3' else
        2 if level_str == '2' else
        1 if level_str == '1' else 0
    )
else:
    ks_level_int = int(ks_level)
```

**修复**: 引入 `KillSwitchLevel` IntEnum + 专用解析函数：

```python
class KillSwitchLevel(IntEnum):
    OK = 0
    L1 = 1
    L2 = 2
    L3 = 3

def parse_kill_switch_level(ks_level, margin_usage=0.0) -> KillSwitchLevel:
    """类型安全的 level 解析"""
    if isinstance(ks_level, KillSwitchLevel):
        return ks_level
    if isinstance(ks_level, int):
        return KillSwitchLevel(ks_level)  # 自动校验范围
    if isinstance(ks_level, str):
        # 解析 "L3" / "OK" / "3" 等
        ...
    return KillSwitchLevel.OK  # 保守默认
```

**Guard 中的使用**:

```python
ks_level_enum = parse_kill_switch_level(ks_level_raw, margin_usage)

if ks_level_enum >= KillSwitchLevel.L3:
    # L3 处理
elif ks_level_enum == KillSwitchLevel.L2:
    # L2 处理
elif ks_level_enum == KillSwitchLevel.L1:
    # L1 处理
```

**向后兼容**: `int(KillSwitchLevel.L3) == 3` 兼容旧代码 `int(level)`。

---

## P2-Q11: 总保证金配置化

**文件**: `utils/kill_switch.py:110-113, 288-329`

**问题**: 硬编码 `total_margin = 5_000_000`，无法通过配置调整，与生产 `config/portfolio.yaml` 配置脱节。

**修复**: 新增 `_get_total_margin()` 方法，4 级回退链：

```python
def _get_total_margin(self) -> float:
    # 1. 环境变量 KILL_SWITCH_TOTAL_MARGIN (运维快速覆盖 + 测试)
    env_margin = os.environ.get("KILL_SWITCH_TOTAL_MARGIN")
    if env_margin and float(env_margin) > 0:
        return float(env_margin)
    
    # 2. config/portfolio.yaml → kill_switch.total_margin
    cfg_margin = self.config.get("total_margin") if isinstance(self.config, dict) else None
    if isinstance(cfg_margin, (int, float)) and cfg_margin > 0:
        return float(cfg_margin)
    
    # 3. config/positions.json → meta.total_capital
    # ...
    
    # 4. 兼容默认值
    return 5_000_000
```

**应用位置**: 
- `_get_margin_status` (line 110-113) — 已替换
- `check_margin_status` 显式传入路径 (line 298-304) — 已替换

**运维场景**: 扩容到 1000 万时只需 `set KILL_SWITCH_TOTAL_MARGIN=10000000`，无需改代码。

---

## 验证结果

### 1. 单元测试 (44/44 PASS)

```
=== 测试 1: P0-Q1 前视偏差修复 ===
  [PASS] lookahead_bias_fixed 标记存在
  [PASS] dd_pnl_used = yesterday_only
  [PASS] current_dd=0.1364 应≈0.1364 (排除当日, 真实回撤)
  [PASS] current_dd 不等于含当日的假象值 0.0909

=== 测试 2: P0-Q3 总敞口 cap 保护 ===
  [PASS] combined_scaler=2.0000 应接近 2.0 (低波动场景)
  [PASS] exposure_cap_applied = True
  [PASS] scaled_total_exposure=1.5000 应 ≤ 1.5
  [PASS] 高波动场景 combined_scaler < 1.0
  [PASS] 高波动场景不触发 exposure_cap

=== 测试 3: P1-Q4 KillSwitch fail-closed 完整性 === (7/7 PASS)
=== 测试 4: P1-Q5 PostMixLayer 抽象 === (8/8 PASS)
=== 测试 5: P1-Q6 KillSwitchLevel IntEnum === (16/16 PASS)
=== 测试 6: P2-Q11 _get_total_margin 配置化 === (4/4 PASS)

测试结果: PASS=44, FAIL=0
```

### 2. 回归测试 (无破坏)

| 测试 | 结果 | 备注 |
|------|------|------|
| v8.6.7 P1 fixes | 6/7 PASS | P1-G 失败为历史遗留 (与本次修复无关) |
| v8.6.8 live ready | 27/27 PASS | 全部通过 |
| LGB 信号集成 v87 | 全部 PASS | PostMixLayer 重构向后兼容 |
| E2E 全链路测试 | 8/8 PASS | 真实数据回归通过 |

### 3. 关键修复验证日志

```
[PortfolioOptimizer] [P0-Q3] 总敞口 2.0000x 超 1.50x 上限, 已按比例 cap 至 1.50x
[KillSwitch] FAIL-CLOSED 触发 | reason=SUSPICIOUS_LOW_MARGIN_USAGE
```

---

## 代码质量提升度量

| 维度 | 修复前 | 修复后 | 顶级对冲基金基准 |
|------|--------|--------|-------------------|
| 前视偏差 | 1 处发现 ❌ | 0 处 ✅ | 0 处 |
| 杠杆失控风险 | combined_scaler 可达 2.0x ❌ | hard cap 1.5x ✅ | hard cap 必须 |
| Fail-closed 路径覆盖 | 1/4 路径 ⚠️ | 4/4 路径 ✅ | 100% 覆盖 |
| 函数复杂度 | _fuse_symbol 189 行 ❌ | ~120 行 + PostMixLayer 类 ✅ | ≤ 80 行 |
| 类型安全 | 字符串 level 解析 ❌ | IntEnum ✅ | 必须用 Enum |
| 配置硬编码 | 5_000_000 硬编码 ❌ | 4 级回退链 ✅ | 必须配置化 |

**综合代码质量评分**: 5.5/10 → **7.0/10** (提升 +1.5)

---

## P1-Q8: 统一 ConfigManager 整合 YAML 配置目录 (2026-07-26 新增)

**文件**: `utils/config_manager.py` (新增), `utils/kill_switch.py` (迁移示范)

**问题**: 项目存在 3 个分散的 YAML 配置目录:
- `configs/` (v7.7 旧版, 4 个文件)
- `v8.3_institutional/config/` (v8.4 唯一事实源, P0-1 修复, 5 个文件)
- `ms_strategy/config/` (策略模块独立配置, 6 个文件)

共 15+ 个 YAML 文件, 10+ 处独立加载代码, 全部通过 `Path(__file__).resolve().parent.parent / "configs" / "xxx.yaml"` 硬编码路径.

**配置漂移 Bug**: `kill_switch.py` 仍在读取 `configs/portfolio.yaml` (v7.7 旧版),
而非 `v8.3_institutional/config/portfolio.yaml` (v8.4 唯一事实源).
虽然两个文件的 kill_switch 节内容当前一致, 但任何 v8.4 配置更新都不会被 kill_switch 感知.

**解决方案**: 单一入口 + 4 级优先级解析 + LRU+mtime 缓存

优先级 (高 → 低):
1. 环境变量 `QUANT_CONFIG_DIR` (运维快速覆盖, 测试场景注入)
2. `v8.3_institutional/config/` (生产唯一事实源, P0-1 修复后)
3. `configs/` (历史 v7.7 回退, 兼容旧代码)
4. `ms_strategy/config/` (策略模块独立配置)

**新增 API** (`utils/config_manager.py`):
- `get_config(name: str) -> Dict` — 通用加载 (支持短名/完整文件名)
- `get_kill_switch_config() -> Dict` — 类型化访问器 (推荐)
- `get_portfolio_config() / get_settings_config() / get_execution_config()`
- `get_backtest_config() / get_risk_budget_config() / get_stop_loss_config()`
- `list_available_configs() -> List[Dict]` — 审计 (列出所有可用配置及来源)
- `get_config_source(name) -> Optional[str]` — 审计 (配置漂移检测)
- `clear_config_cache() / ConfigManager.reload(name)` — 测试/hot reload

**迁移示范** (`utils/kill_switch.py:_load_config`):
- 显式 `config_path` 走旧路径 (向后兼容测试场景)
- 默认路径走 `ConfigManager.get_kill_switch_config()` (生产路径)
- 双层 fail-safe: ConfigManager 失败 → 回退旧路径 → 失败返回空 dict

**验证脚本**: `scripts/_verify_config_manager.py` (43/43 PASS)

| 测试项 | 内容 | 结果 |
|--------|------|------|
| T1 | 基础加载 (短名 + 类型化访问器 + 缓存一致性) | ✅ PASS |
| T2 | 优先级解析 (v8.3 > configs) | ✅ PASS |
| T3 | 类型化访问器返回正确字段 (kill_switch L1=0.50) | ✅ PASS |
| T4 | LRU+mtime 缓存 (同实例引用一致, reload 后不同) | ✅ PASS |
| T5 | 环境变量 `QUANT_CONFIG_DIR` 覆盖生效 | ✅ PASS |
| T6 | kill_switch.py 集成 (L1/L3 触发行为正确) | ✅ PASS |
| T7 | 向后兼容 (显式 config_path 仍工作) | ✅ PASS |
| T8 | 审计方法 `list_available()` 列出所有配置 | ✅ PASS |

**回归验证** (确保 ConfigManager 集成未破坏现有功能):
- `_verify_v868_live_ready.py`: 27/27 PASS (实盘就绪度无回归)
- `_verify_code_quality_fixes.py`: 52/52 PASS (P0-Q1/P0-Q3/P1-Q7 等修复无回归)
- `verify_v867_fixes.py`: BUG#1 overnight_gap fail-closed PASS (无回归)
- `verify_p1_fixes.py`: P1-L 波动率缩放 4 场景 PASS, P1-H 订单过滤 3 场景 PASS

**架构收益**:
1. **Single Source of Truth**: 全项目通过 `from utils.config_manager import get_config` 统一入口
2. **配置漂移修复**: kill_switch.py 自动加载 v8.4 唯一事实源 (而非旧 v7.7)
3. **Hot Reload**: mtime 变化自动失效缓存, 支持运行时配置更新无需重启
4. **测试友好**: `QUANT_CONFIG_DIR` 环境变量 + `extra_search_paths` 注入, 单元测试隔离
5. **审计能力**: `list_available_configs()` + `get_config_source()` 可视化配置来源
6. **渐进迁移**: 现有 `yaml.safe_load()` 代码不强制迁移, kill_switch.py 作为示范

**度量更新**:

| 维度 | 修复前 | 修复后 | 顶级对冲基金基准 |
|------|--------|--------|-------------------|
| 配置加载入口 | 10+ 处硬编码 ❌ | 统一 ConfigManager ✅ | 单一入口 |
| 配置漂移 | kill_switch 读 v7.7 ❌ | 自动读 v8.4 唯一事实源 ✅ | 0 漂移 |
| 配置缓存 | 无 (每次 IO) ❌ | LRU + mtime 失效 ✅ | 必须缓存 |
| 测试隔离 | 不支持 ❌ | QUANT_CONFIG_DIR 环境变量 ✅ | 必须支持 |
| 配置审计 | 无 ❌ | list_available + get_config_source ✅ | 必须可审计 |

**综合代码质量评分**: 7.0/10 → **7.5/10** (提升 +0.5)

---

## 待办 (Phase 3)

| 编号 | 等级 | 任务 | 工作量 |
|------|------|------|--------|
| P1-Q9 | P1 | tests/ 三层目录整理 | 2 天 |
| Phase 3-A | - | 迁移其他模块到 ConfigManager (gamma_engine, liquidation_scheduler, algo_engine 等 9 处) | 3 天 |
| Phase 3-B | - | 引入 mypy --strict + pylint 复杂度检查 | 1 个月 |
