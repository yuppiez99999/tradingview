# VolRegimeWeighter 实盘交易系统集成方案

> **任务**: 把动态权重调整逻辑集成到实盘交易系统中，配置好实时数据源和风控阈值
> **日期**: 2026-08-05
> **方案**: 盘中+EOD 双集成 + 510050 期权 IV + RV 备选数据源
> **约束**: Phase 0 只读建议模式 (USE_VOL_REGIME_WEIGHTER 默认 False，双签启用)

---

## 一、当前状态分析

### 1.1 已完成
- [utils/alpha/vol_regime_weighter.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/vol_regime_weighter.py) 核心模块实现完成（sense_regime / compute_weights / enforce_constraints / emit_suggestion / run_cycle）
- [configs/vol_regime_weighter.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/configs/vol_regime_weighter.yaml) 配置文件完整（regime 阈值 / 约束 / 降级策略）
- [configs/feature_flags.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/configs/feature_flags.yaml) 已注册 `USE_VOL_REGIME_WEIGHTER` 标志
- [utils/alpha/evolution_orchestrator.py:584-601](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/evolution_orchestrator.py#L584-L601) 已集成到 `run_observation_cycle`（Phase 0 只读）
- 单元测试与端到端测试已通过

### 1.2 待修复的缺口（本次任务范围）

| 缺口 | 位置 | 影响 |
|---|---|---|
| **G1: `_fetch_vix` 导入路径错误** | [evolution_orchestrator.py:674](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/evolution_orchestrator.py#L674) `from wind_mcp_fetcher import wind_get_quote` | `wind_mcp_fetcher.py` 在 `tools/` 目录，未加入 sys.path，导入必然失败 |
| **G2: `_fetch_vix` 调用代码错误** | [evolution_orchestrator.py:675](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/evolution_orchestrator.py#L675) `wind_get_quote("VIX")` | Wind MCP 没有 "VIX" 代码，且 iVIX 已停用（见 gamma_engine.py:133） |
| **G3: AKShare 接口不可靠** | [evolution_orchestrator.py:685](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/evolution_orchestrator.py#L685) `ak.stock_zh_index_vix()` | 中国波指 iVIX 早已停用，AKShare 数据为旧数据或空 |
| **G4: `current_drawdown` 参数未传** | [evolution_orchestrator.py:632-637](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/evolution_orchestrator.py#L632-L637) | VolRegimeWeighter.run_cycle 接受 `current_drawdown` 但未传入，回撤 floor 修正失效 |
| **G5: AutoTradingSystem 未集成** | [auto_trading_system.py:292-304](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/auto_trading_system.py#L292-L304) `_check_risk_status` | 实盘 --live 模式没有调用 VolRegimeWeighter，盘中无法感知波动率 Regime |
| **G6: VIX 缓存机制缺失** | 无 | 每次调用都触发网络请求，盘中高频调用会导致 Wind MCP 配额耗尽 |

### 1.3 关键约束（来自 project_memory）
- `USE_VOL_REGIME_WEIGHTER` 默认 False，双签启用（HC-1）
- Phase 0 只读建议模式，不修改 portfolio.yaml（HC-4）
- 实盘环境与 LLM 服务物理隔离，盘中（9:25-15:10）禁用大模型
- V9 Regime-Specific LGB 为生产基线，不可被覆盖
- 交易系统内存预算硬保留 5GB

---

## 二、实施方案总览

```
┌─────────────────────────────────────────────────────────────┐
│                    实盘交易系统集成架构                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  [盘中实时监控] AutoTradingSystem._run_monitor_cycle()       │
│    └─ _check_risk_status()                                  │
│        └─ _check_vol_regime() ← 新增                         │
│            ├─ _fetch_vix_realtime()  ← 新增（带缓存）         │
│            │   ├─ Wind MCP wind_get_option_iv("510050.SH")   │
│            │   ├─ 备选: RV from shadow_state.json            │
│            │   └─ 缓存: reports/volatility/vix_cache.json    │
│            ├─ _fetch_current_drawdown() ← 新增               │
│            │   └─ 从 shadow_state.json 计算                  │
│            └─ VolRegimeWeighter.run_cycle()                  │
│                └─ 输出告警到日志 (不调仓, Phase 0)            │
│                                                              │
│  [EOD 盘后报告] v84_EvolutionEval 16:05                      │
│    └─ EvolutionOrchestrator.run_observation_cycle()          │
│        └─ _run_vol_regime_weighter() ← 修复                  │
│            ├─ _fetch_vix() ← 修复导入+数据源                 │
│            ├─ _fetch_current_drawdown() ← 新增               │
│            └─ VolRegimeWeighter.run_cycle()                  │
│                └─ reports/evolution/vol_regime_weights_*.json│
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、详细变更清单

### 变更 1: 新增 `utils/alpha/vix_data_source.py` 模块（核心数据源）

**文件**: `e:\各种PY程序\28-终极量化交易系统8.4\utils\alpha\vix_data_source.py`（新建）

**原因**: 将 VIX 数据获取逻辑从 orchestrator 中解耦，便于盘中和 EOD 共用，且支持缓存

**接口设计**:
```python
class VixDataSource:
    """VIX 数据源 (510050 期权 IV + RV 备选 + 缓存)."""

    CACHE_PATH = Path("reports/volatility/vix_cache.json")
    CACHE_TTL_SECONDS = 300  # 5 分钟缓存（盘中监控周期 30s，避免重复请求）

    def fetch_vix(self, use_cache: bool = True) -> float | None:
        """获取 VIX 替代值 (降级链: Wind 期权IV → RV → 缓存 → None).

        Args:
            use_cache: 是否使用缓存（盘中 True，EOD False）

        Returns:
            VIX 数值 (如 25.3) 或 None
        """
        # 1. Wind MCP: 510050 期权 IV
        vix = self._fetch_from_wind_option_iv()
        if vix is not None:
            self._save_cache(vix, source="wind_option_iv")
            return vix

        # 2. 备选: 从 shadow_state.json 计算 realized_vol * 缩放因子
        vix = self._fetch_from_realized_vol()
        if vix is not None:
            self._save_cache(vix, source="realized_vol_proxy")
            return vix

        # 3. 缓存兜底
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                logger.warning("VIX 数据源全失败, 使用缓存值: %s", cached)
                return cached

        return None

    def _fetch_from_wind_option_iv(self) -> float | None:
        """从 Wind MCP 获取 510050 期权 IV."""
        # sys.path 添加 tools/ 目录
        # 调用 wind_get_option_iv("510050.SH")
        # 返回 iv_value 或 None

    def _fetch_from_realized_vol(self) -> float | None:
        """从 shadow_state.json 计算 20 日已实现波动率, 转换为 VIX 替代.

        VIX_proxy = realized_vol * sqrt(252) * 100
        """
        # 1. 读取 output/shadow_account/shadow_state.json
        # 2. 提取 daily_nav 中最近 20 日的 daily_return
        # 3. 调用 VolTargetController.calc_realized_vol()
        # 4. 转换: vix_proxy = rv * sqrt(252) * 100
```

**关键实现细节**:
- `tools/` 目录加入 sys.path：`sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))`
- Wind MCP 调用 `wind_get_option_iv("510050.SH")` 获取 IV（复用 gamma_engine.py:127 已验证的接口）
- RV 转换公式：`vix_proxy = realized_vol * sqrt(252) * 100`（年化后乘 100 转 VIX 量纲）
- 缓存格式：`{"vix": 25.3, "source": "wind_option_iv", "timestamp": "2026-08-05T14:30:00", "ttl": 300}`

### 变更 2: 新增 `utils/alpha/drawdown_reader.py` 模块

**文件**: `e:\各种PY程序\28-终极量化交易系统8.4\utils\alpha\drawdown_reader.py`（新建）

**原因**: 从 shadow_state.json 计算当前回撤，避免在 orchestrator 中堆砌 IO 逻辑

**接口设计**:
```python
class DrawdownReader:
    """从 shadow_state.json 读取并计算当前组合回撤."""

    SHADOW_STATE_PATH = Path("output/shadow_account/shadow_state.json")

    def get_current_drawdown(self) -> float | None:
        """返回当前回撤百分比 (正数, 如 0.0352 表示 3.52%).

        Returns:
            drawdown_pct 或 None (数据不可用时)
        """
        # 1. 读取 shadow_state.json
        # 2. 从 daily_nav 提取 nav 序列
        # 3. peak = max(nav_series)
        # 4. current = nav_series[-1]
        # 5. drawdown = (peak - current) / peak
        # 6. 返回 abs(drawdown)

    def get_peak_and_current(self) -> tuple[float, float] | None:
        """返回 (peak_nav, current_nav) 元组, 供 DrawdownController 复用."""
```

### 变更 3: 修复 `evolution_orchestrator.py` 的 `_fetch_vix` 和 `_run_vol_regime_weighter`

**文件**: [utils/alpha/evolution_orchestrator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/evolution_orchestrator.py#L616-L695)

**变更内容**:

1. **`_fetch_vix` 方法（L667-695）整体重写**:
   - 删除直接 `from wind_mcp_fetcher import wind_get_quote` 的错误导入
   - 删除 `ak.stock_zh_index_vix()` 不可靠调用
   - 改为调用 `VixDataSource().fetch_vix(use_cache=False)`（EOD 不用缓存，强制刷新）

2. **`_run_vol_regime_weighter` 方法（L616-637）补充 `current_drawdown`**:
   ```python
   def _run_vol_regime_weighter(self, metrics: Any) -> dict[str, Any]:
       from utils.alpha.vol_regime_weighter import VolRegimeWeighter
       from utils.alpha.drawdown_reader import DrawdownReader  # 新增

       weighter = VolRegimeWeighter()
       portfolio_snapshot = self._read_portfolio_snapshot()
       daily_returns = self._extract_daily_returns(metrics)
       vix_value = self._fetch_vix()
       current_drawdown = DrawdownReader().get_current_drawdown()  # 新增

       return weighter.run_cycle(
           portfolio_snapshot=portfolio_snapshot,
           vix_value=vix_value,
           daily_returns=daily_returns,
           current_drawdown=current_drawdown,  # 新增
           orchestrator=self,
       )
   ```

### 变更 4: 在 `AutoTradingSystem` 集成 VolRegimeWeighter

**文件**: [utils/auto_trading_system.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/auto_trading_system.py)

**变更内容**:

1. **`_run_monitor_cycle` 方法（L183-203）新增第 5 步**:
   ```python
   def _run_monitor_cycle(self) -> None:
       # ... 现有 4 步 ...
       # 5. 波动率 Regime 监控 (新增)
       self._check_vol_regime()
   ```

2. **新增 `_check_vol_regime` 方法**（在 `_check_kill_switch` 之后）:
   ```python
   def _check_vol_regime(self) -> None:
       """波动率 Regime 监控 (Phase 0 只读建议).

       - Feature Flag USE_VOL_REGIME_WEIGHTER=False 时直接跳过
       - 启用时调用 VolRegimeWeighter.run_cycle, 输出告警到日志
       - 不调仓, 不修改 portfolio.yaml (HC-4)
       """
       logger.info("--- 波动率Regime ---")
       try:
           from utils.infra.feature_flags import is_enabled
           if not is_enabled("USE_VOL_REGIME_WEIGHTER"):
               logger.info("  ℹ️  VolRegimeWeighter 未启用 (USE_VOL_REGIME_WEIGHTER=False)")
               return

           from utils.alpha.vol_regime_weighter import VolRegimeWeighter
           from utils.alpha.vix_data_source import VixDataSource
           from utils.alpha.drawdown_reader import DrawdownReader
           from pathlib import Path
           import yaml

           # 读取 portfolio 快照
           portfolio_path = Path("configs/portfolio.yaml")
           with portfolio_path.open("r", encoding="utf-8") as f:
               portfolio_snapshot = yaml.safe_load(f) or {}

           # 获取 VIX 和回撤
           vix_value = VixDataSource().fetch_vix(use_cache=True)  # 盘中用缓存
           current_drawdown = DrawdownReader().get_current_drawdown()

           # 调用 VolRegimeWeighter (不传 orchestrator, 盘中不写 decisions.jsonl)
           weighter = VolRegimeWeighter()
           result = weighter.run_cycle(
               portfolio_snapshot=portfolio_snapshot,
               vix_value=vix_value,
               daily_returns=None,  # 盘中无日收益序列
               current_drawdown=current_drawdown,
               orchestrator=None,  # 不写决策日志
           )

           # 输出告警
           regime = result.get("regime", "unknown")
           confidence = result.get("confidence", 0.0)
           logger.info("  📊 Regime=%s  置信度=%.2f  VIX=%s  回撤=%s",
                       regime, confidence,
                       f"{vix_value:.2f}" if vix_value else "N/A",
                       f"{current_drawdown:.2%}" if current_drawdown else "N/A")

           # 危机档告警
           if regime in ("bear", "crisis"):
               logger.warning("  ⚠️  波动率告警: %s 档, 建议减仓进攻类, 加仓防御类", regime)

       except ImportError as e:
           logger.info("  ℹ️  VolRegimeWeighter 模块未加载: %s", e)
       except Exception as e:  # noqa: BLE001
           logger.warning("  ⚠️  波动率Regime检查异常: %s", e)
   ```

3. **`snapshot` 方法（L348-380）新增 vol_regime 字段**:
   ```python
   def snapshot(self) -> Dict[str, Any]:
       result = {
           "timestamp": datetime.now().isoformat(),
           "quotes": {},
           "etf_flow": {},
           "ml_signals": None,
           "risk_status": "ok",
           "vol_regime": None,  # 新增
       }
       # ... 现有逻辑 ...
       # 新增 vol_regime 快照
       try:
           from utils.infra.feature_flags import is_enabled
           if is_enabled("USE_VOL_REGIME_WEIGHTER"):
               from utils.alpha.vix_data_source import VixDataSource
               vix = VixDataSource().fetch_vix(use_cache=True)
               result["vol_regime"] = {"vix": vix, "enabled": True}
           else:
               result["vol_regime"] = {"enabled": False}
       except Exception:
           result["vol_regime"] = {"error": "fetch_failed"}
       return result
   ```

### 变更 5: 创建 VIX 缓存目录

**目录**: `e:\各种PY程序\28-终极量化交易系统8.4\reports\volatility\`（新建）

**说明**: `VixDataSource.CACHE_PATH` 指向此目录下的 `vix_cache.json`，模块初始化时自动 `mkdir(parents=True, exist_ok=True)`

### 变更 6: 完善 `configs/vol_regime_weighter.yaml` 数据源配置

**文件**: [configs/vol_regime_weighter.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/configs/vol_regime_weighter.yaml)

**新增 section**（追加到文件末尾）:
```yaml
# ============================================================
# 实时数据源配置 (v8.6.14 新增)
# ============================================================
data_source:
  vix:
    primary: "wind_option_iv"        # 主数据源: Wind MCP 510050 期权 IV
    secondary: "realized_vol_proxy"  # 备选: 从 shadow_state 计算 RV
    wind_option_code: "510050.SH"    # 50ETF 期权 Wind 代码
    rv_lookback_days: 20             # RV 计算窗口
    rv_annualization_factor: 252     # 年化因子
    cache_ttl_seconds: 300           # 缓存有效期 (盘中用)
    cache_path: "reports/volatility/vix_cache.json"

  drawdown:
    source: "shadow_state"           # 回撤数据源
    path: "output/shadow_account/shadow_state.json"

  # 盘中监控配置
  live_monitoring:
    enabled_flag: "USE_VOL_REGIME_WEIGHTER"
    check_interval_seconds: 30       # 对齐 monitor_interval
    use_cache: true                  # 盘中启用缓存
    alert_regimes: ["bear", "crisis"] # 触发告警的 Regime

  # EOD 报告配置
  eod_report:
    use_cache: false                 # EOD 强制刷新
    write_decisions_log: true        # 写入 decisions.jsonl
    reports_dir: "reports/evolution"
```

### 变更 7: 单元测试

**文件**: `e:\各种PY程序\28-终极量化交易系统8.4\tests\unit\test_vix_data_source.py`（新建）

**测试用例**:
1. `test_fetch_from_wind_option_iv_success` - Mock wind_get_option_iv 返回 IV 值
2. `test_fetch_from_realized_vol_fallback` - Wind 失败时从 shadow_state.json 计算 RV
3. `test_cache_hit` - 缓存有效时直接返回
4. `test_cache_expired` - 缓存过期时重新获取
5. `test_all_sources_fail` - 全失败返回 None
6. `test_cache_path_creation` - 自动创建缓存目录

**文件**: `e:\各种PY程序\28-终极量化交易系统8.4\tests\unit\test_drawdown_reader.py`（新建）

**测试用例**:
1. `test_get_current_drawdown_normal` - 正常读取 shadow_state.json
2. `test_get_current_drawdown_no_peak` - 数据为空时返回 None
3. `test_get_current_drawdown_file_missing` - 文件不存在时返回 None
4. `test_get_peak_and_current` - 返回 peak/current 元组

**文件**: `e:\各种PY程序\28-终极量化交易系统8.4\tests\unit\test_auto_trading_vol_regime.py`（新建）

**测试用例**:
1. `test_check_vol_regime_disabled` - Flag=False 时跳过
2. `test_check_vol_regime_bear_alert` - bear 档触发告警
3. `test_check_vol_regime_no_exception` - 任何异常都不阻塞主循环

### 变更 8: 端到端集成测试

**文件**: `e:\各种PY程序\28-终极量化交易系统8.4\tests\integration\test_vol_regime_live_e2e.py`（新建）

**测试场景**:
1. 模拟盘中监控周期，验证 VIX 获取 → Regime 识别 → 告警输出
2. 模拟 EOD 调用，验证完整链路：orchestrator → weighter → 报告生成
3. 验证 current_drawdown 参数正确传递并影响 Regime 修正

---

## 四、假设与决策

### 4.1 关键假设
1. **Wind MCP `wind_get_option_iv` 接口可用**：基于 [gamma_engine.py:127](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/gamma_engine.py#L127) 已有调用验证
2. **shadow_state.json 数据有效**：当前已有 7 天 daily_nav 数据（2026-07-27 至 2026-08-04），足够计算 20 日 RV（数据不足时降级到可用天数）
3. **盘中监控间隔 30 秒**：与 [auto_trading_system.py:93](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/auto_trading_system.py#L93) `monitor_interval = 30` 对齐
4. **VIX 缓存 TTL 5 分钟**：覆盖 10 个监控周期，平衡时效性与 Wind MCP 配额

### 4.2 关键决策
| 决策 | 选择 | 理由 |
|---|---|---|
| 集成位置 | 盘中+EOD 双集成 | 用户确认；盘中实时告警 + EOD 完整报告 |
| VIX 数据源 | 510050 期权 IV + RV 备选 | 用户确认；iVIX 已停用，期权 IV 是最佳替代 |
| 回撤数据源 | shadow_state.json | 已有数据，无需重复造轮子；与 ShadowAccount 对齐 |
| 缓存策略 | 盘中启用，EOD 强制刷新 | 盘中高频调用需保护 Wind 配额；EOD 需要当日最新数据 |
| 盘中调用 orchestrator | 不传（None） | 盘中仅告警，不写 decisions.jsonl，避免污染审计链 |
| Phase 0 模式 | 只读建议，不调仓 | 符合 project_memory HC-4 约束 |

### 4.3 风险与缓解
| 风险 | 缓解措施 |
|---|---|
| Wind MCP 配额耗尽 | 5 分钟缓存 + RV 备选 + 失败降级到 None |
| shadow_state.json 数据断层 | DrawdownReader 容错返回 None，VolRegimeWeighter 降级到 neutral |
| 盘中调用阻塞主循环 | 所有异常 catch + `_check_vol_regime` 设 5s 超时 |
| Feature Flag 误启用 | 双签机制 + Phase 0 强制 evaluate_only |

---

## 五、验证步骤

### 5.1 单元测试验证
```bash
py -3.8 -m pytest tests/unit/test_vix_data_source.py -v
py -3.8 -m pytest tests/unit/test_drawdown_reader.py -v
py -3.8 -m pytest tests/unit/test_auto_trading_vol_regime.py -v
```

### 5.2 集成测试验证
```bash
py -3.8 -m pytest tests/integration/test_vol_regime_live_e2e.py -v
```

### 5.3 EOD 链路验证
```bash
# 模拟 v84_EvolutionEval 调用
py -3.8 scripts/run_evolution_eval.py --dry-run --verbose

# 验证报告生成
dir reports\evolution\vol_regime_weights_*.json
```

### 5.4 盘中实盘 dry-run 验证
```python
# 验证脚本 (临时)
from utils.auto_trading_system import AutoTradingSystem
system = AutoTradingSystem()
# 单周期测试
system._run_monitor_cycle()
# 快照测试
snapshot = system.snapshot()
assert "vol_regime" in snapshot
```

### 5.5 Feature Flag 双签启用后验证（需用户授权）
```bash
# 启用后完整测试
py -3.8 量化策略系统_统一入口_v8.6.py --live  # 运行 60 秒后 Ctrl+C
# 检查日志中是否出现 "波动率Regime" section
```

---

## 六、实施顺序

1. **创建 `utils/alpha/vix_data_source.py`** - 核心数据源模块
2. **创建 `utils/alpha/drawdown_reader.py`** - 回撤读取模块
3. **修复 `evolution_orchestrator.py`** - `_fetch_vix` 重写 + `current_drawdown` 传递
4. **集成到 `auto_trading_system.py`** - 新增 `_check_vol_regime` 方法
5. **更新 `configs/vol_regime_weighter.yaml`** - 追加数据源配置
6. **编写单元测试** - 3 个测试文件
7. **编写集成测试** - 端到端验证
8. **运行 EOD dry-run 验证** - 确认报告生成
9. **更新 cairn/LOG.md** - 记录本次集成进展

---

## 七、不在本次范围

- 启用 `USE_VOL_REGIME_WEIGHTER=True`（需双签，由用户单独授权）
- 自动调仓逻辑（Phase 1+，需观察期满 14 天后评估）
- portfolio.yaml 自动写入（违反 HC-4）
- LLM 大模型集成（盘中禁用大模型约束）
- v84 定时任务重新注册（任务已注册 16:05，无需变更）
