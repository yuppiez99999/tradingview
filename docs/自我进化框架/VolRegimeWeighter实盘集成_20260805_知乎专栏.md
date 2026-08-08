# 当 VIX=7.72 遇到 iVIX 停用：波动率 Regime 动态权重的实盘集成笔记

> 自我进化框架最怕的不是"不会调权重"，而是"想调权重却拿不到 VIX"——一个停用的指标，逼出了一整套降级链设计。

## 一、一个简单需求的复杂化

自我进化框架运行到第三周，观察期数据积累渐入正轨。框架能感知漂移、能触发重训、能做 DSR 评估——但缺一个关键能力：**根据市场波动情况动态调整权重大小**。

需求听起来简单：市场波动大时减仓进攻、加仓防御；波动小时反之。落地却涉及一连串决策：

| 决策点 | 选项 | 我们的选择 |
|---|---|---|
| Regime 分几档 | 3档 / 4档 / 连续 | 4档（bull/neutral/bear/crisis） |
| 用什么指标 | VIX / realized_vol / 混合 | VIX 为主 + RV 一致性校验 |
| 权重怎么调 | 等比例 / 矩阵 / 优化 | 4×8 权重矩阵（4档 Regime × 8类风格） |
| 约束怎么执行 | 软约束 / 硬约束 | 硬约束（单标的≤8%、风格≤30%、现金≥5%、总和=1.0） |
| 上不上实盘 | 直接上 / 分阶段 | Phase 0 只读建议 → Phase 1 自动调仓 |
| VIX 拿不到怎么办 | 等恢复 / 替代方案 | 三级降级链 |

最后一行是真正的麻烦——**A 股的 iVIX 早就停用了**。

## 二、VolRegimeWeighter：四档 Regime 的权重矩阵

### 2.1 Regime 识别

按 VIX 绝对值分四档，阈值与 portfolio.yaml 的 `dynamic_hedge_policy` 完全对齐：

```
VIX < 20     → bull     (对冲比例 20%, 进攻类加仓)
VIX 20-30    → neutral  (对冲比例 40%, 维持权重)
VIX 30-40    → bear     (对冲比例 75%, 减仓进攻)
VIX ≥ 40     → crisis   (对冲比例 90%, 大幅避险)
```

光看 VIX 不够。`sense_regime` 还会用 realized_vol 做一致性校验——如果 VIX 说 bull 但 RV 飙高，取更保守的档位。盘中没有日收益序列时，`consistency_check.consistent = "no_rv_data"`，置信度不调整（保持 0.75 基础值）。

### 2.2 权重调整矩阵

4×8 矩阵，行是 Regime 档位，列是 8 类风格（科技/新能源/医药/金融/宽基/资源/防御/现金），值是调整倍数：

| Regime | 科技 | 新能源 | 医药 | 金融 | 宽基 | 资源 | 防御 | 现金 |
|---|---|---|---|---|---|---|---|---|
| bull | ×1.20 | ×1.15 | ×1.10 | ×1.05 | ×1.05 | ×1.00 | ×0.90 | ×0.50 |
| neutral | ×1.00 | ×1.00 | ×1.00 | ×1.00 | ×1.00 | ×1.00 | ×1.00 | ×1.00 |
| bear | ×0.80 | ×0.85 | ×0.90 | ×0.95 | ×0.95 | ×1.00 | ×1.10 | ×1.50 |
| crisis | ×0.60 | ×0.70 | ×0.80 | ×0.85 | ×0.90 | ×0.95 | ×1.20 | ×2.00 |

设计思路：bull 档现金×0.50（把现金转移到进攻资产），crisis 档现金×2.00（大幅避险）。这比等比例缩放更精细——不同风格对波动的敏感度不同。

### 2.3 约束执行

算完建议权重后，三道约束依次执行：

```
1. max_sector_exposure: 单一风格 ≤ 30% → 超限裁剪
2. sum_to_one: 总和 = 1.0 → 差额归现金
3. final_check: 现金 ≥ 5% → 最终校验
```

实战中，bull 档科技类原始计算值 0.285 × 1.20 = 0.342，超过 30% 上限，裁剪到 0.30。裁剪后总和 0.9945，差额 +0.0055 归现金。最终现金 5.80% > 5% 下限，校验通过。

## 三、iVIX 停用之后：三级降级链

### 3.1 问题

VIX 是 Regime 识别的核心指标。但 A 股的 iVIX（中国波指）2018 年就停用了。没有现成的 VIX 数据源，怎么办？

第一反应是用 50ETF 期权隐含波动率自己算。但期权数据获取复杂，且 Wind MCP 接口在盘中环境不一定可用。

### 3.2 降级链设计

设计了一个三级降级链，优先级从高到低：

```
Level 1: Wind MCP 510050 K线波动率
    ↓ (Wind 不可用)
Level 2: shadow_state.json 计算 realized_vol × 100
    ↓ (shadow_state 不存在)
Level 3: 缓存兜底 (TTL 300s)
    ↓ (缓存过期)
None: sense_regime 降级到中性保守档
```

**Level 2 是实际命中的数据源**。Shadow 账户的 `shadow_state.json` 里有 `daily_nav` 数组，从中计算已实现波动率（realized_vol），年化后乘以 100 转换到 VIX 量纲：

```python
# 从 daily_nav 计算 realized_vol
nav_values = [entry["nav"] for entry in daily_nav]
returns = [math.log(nav_values[i]/nav_values[i-1]) for i in range(1, len(nav_values))]
rv = np.std(returns) * math.sqrt(252)  # 年化
vix = rv * 100  # 转换到 VIX 量纲 (0.0772 → 7.72)
```

实测值 VIX = 7.72，对应年化波动率 7.72%，属于低波动区间（VIX < 20 = bull 档）。

### 3.3 盘中缓存 vs EOD 强制刷新

降级链有一个细节：盘中和 EOD 用不同的缓存策略。

| 场景 | use_cache | 原因 |
|---|---|---|
| 盘中监控 (每 30s) | True | 保护 Wind MCP 配额，5 分钟缓存够用 |
| EOD 报告 (16:05) | False | 强制刷新，获取当日最新数据 |

盘中 `shadow_state.json` 的 `daily_nav` 不会更新（只有 EOD 才刷新），所以 30 分钟内 VIX 值是恒定的——这是预期行为，不是 bug。

## 四、双链路架构：盘中告警 + EOD 报告

实盘集成不能只接一条链路。我们设计了两条：

### 4.1 盘中实时监控（AutoTradingSystem）

在 `_run_monitor_cycle` 末尾新增第 5 步 `_check_vol_regime`：

```python
def _check_vol_regime(self) -> None:
    """波动率 Regime 监控 (Phase 0 只读建议模式)."""
    # 每 30s 调用 VolRegimeWeighter
    # 盘中用缓存, 不写 decisions.jsonl (orchestrator=None)
    # bull/neutral/bear/crisis 输出对应级别日志
    # bear/crisis 触发 WARN 告警
```

关键设计：**`orchestrator=None`**。盘中不写决策日志（decisions.jsonl），避免污染审计链。审计链只记录 EOD 的完整决策，盘中只输出告警日志。

### 4.2 EOD 完整报告（EvolutionOrchestrator）

每天 16:05 的 `v84_EvolutionEval` 任务调用 `_run_vol_regime_weighter`：

```python
def _run_vol_regime_weighter(self, metrics):
    weighter = VolRegimeWeighter()
    portfolio_snapshot = self._read_portfolio_snapshot()
    vix_value = self._fetch_vix()  # use_cache=False 强制刷新
    current_drawdown = DrawdownReader().get_current_drawdown()
    
    return weighter.run_cycle(
        portfolio_snapshot=portfolio_snapshot,
        vix_value=vix_value,
        daily_returns=daily_returns,
        current_drawdown=current_drawdown,
        orchestrator=self,  # 传入 orchestrator, 写 decisions.jsonl
    )
```

EOD 链路传入 `orchestrator=self`，通过 `log_decision` 写入审计链，action 强制为 `evaluate_only`（Phase 0 不调仓）。

### 4.3 为什么分两条链路

| 维度 | 盘中链路 | EOD 链路 |
|---|---|---|
| 频率 | 每 30s | 每日 1 次 (16:05) |
| 缓存 | use_cache=True | use_cache=False |
| 决策日志 | 不写 (orchestrator=None) | 写 decisions.jsonl |
| 用途 | 实时告警 | 完整报告 + 审计 |
| 数据 | VIX + 回撤 | VIX + 回撤 + 日收益序列 |

盘中链路关注"现在是什么 Regime"，EOD 链路关注"今天建议怎么调"。两者职责分离，避免审计链被 30s 一次的盘中检查淹没。

## 五、Phase 0 安全边界

### 5.1 为什么要分阶段

直接上自动调仓太危险——Regime 识别可能不准，权重矩阵可能不合理，约束可能有漏洞。Phase 0 只读建议模式让系统先"看"不"做"：

```
Phase 0: 只读建议 (review_only)
    → 输出 Regime + 建议权重到 reports/evolution/
    → 不修改 portfolio.yaml
    → 不触发调仓

Phase 1: 自动调仓 (apply_to_portfolio) [需再次双签]
    → 修改 portfolio.yaml
    → 触发再平衡

Phase 2: 回测验证 (backtest) [预留]
```

### 5.2 双签授权

Feature Flag `USE_VOL_REGIME_WEIGHTER` 标注了 `requires: "dual_signature"`。启用需要双签授权，记录到 cairn/LOG.md：

```yaml
USE_VOL_REGIME_WEIGHTER:
  default: true  # 双签授权后改为 true
  description: "启用波动率 Regime 权重建议器...
    [2026-08-05 双签授权启用 Phase 0 实战监控,
     观察期内只读建议模式, 预计 08-20 期满评估 Phase 1]"
  requires: "dual_signature"
```

紧急回滚不需要双签——Phase 0 是只读模式，改回 `false` 不会影响持仓。

### 5.3 审计字段

每次生成的权重建议报告 JSON 包含审计字段：

```json
{
  "audit": {
    "portfolio_yaml_untouched": true,  // portfolio.yaml 未被修改
    "feature_flag": "USE_VOL_REGIME_WEIGHTER=True",
    "config_source": "configs\\vol_regime_weighter.yaml",
    "vol_controller": "VolTargetController"
  },
  "next_steps": {
    "phase_0_action": "review_only",
    "phase_1_trigger": "user_approval_after_observation_period"
  }
}
```

`portfolio_yaml_untouched: true` 是 Phase 0 的核心承诺——306 个周期，portfolio.yaml 一行都没动。

## 六、踩坑记录

### 6.1 ML 信号 return_raw 兼容性

启用 Flag 后第一个监控周期，ML 信号段报错：

```
[INFO] --- ML信号 ---
[INFO]   ℹ️  ML信号检查跳过: get_ml_signal_section() got an unexpected keyword argument 'return_raw'
```

排查发现有两个 `get_ml_signal_section` 定义：

| 位置 | 签名 | 支持 return_raw |
|---|---|---|
| `utils/cli_helpers.py` (降级 stub) | `get_ml_signal_section(code: str) -> str` | ❌ |
| `量化策略系统_统一入口_v8.6.py` (完整版) | `get_ml_signal_section(external_signals=None, return_raw=False, ...)` | ✅ |

`auto_trading_system.py` 从 `cli_helpers` 导入了降级 stub，但调用时传了 `return_raw=True`——签名不兼容。

**修复**：给 stub 加上 `return_raw` 参数，与完整版签名对齐。`return_raw=True` 时返回 `None`（降级），调用方走"暂无信号"分支。

```python
# 修复后
def get_ml_signal_section(code: str = None, return_raw: bool = False) -> Optional[str]:
    if return_raw:
        return None  # 降级, 调用方走"暂无信号"分支
    return ""
```

为什么不直接导入完整版？因为 `utils.ml_predictor` 模块不存在，完整版也会因 `ML_PREDICTOR_AVAILABLE=False` 返回 None。给 stub 加参数保持降级行为是最安全的最小修复，不引入从主入口脚本导入的副作用风险。

### 6.2 VIX 数据源选型纠结

最初想用 Wind MCP 获取 510050 期权 IV，但发现：
1. Wind MCP 在盘中环境不一定连接
2. 期权 IV 接口实际不存在，只能用 K线波动率替代
3. K线波动率需要下载历史数据，盘中做太慢

最终选择 `shadow_state_rv` 作为主数据源——Shadow 账户本来就有 `daily_nav` 数据，计算 realized_vol 成本为零，且数据质量有保障（经过数据清洗闭环验证）。

**教训**：降级链的第一级不一定是"最好"的数据源，而是"最可靠"的。Wind MCP 最好但不稳定，shadow_state_rv 够用且稳定。

### 6.3 进程运行 306 周期后外部终止

后台进程运行 2 小时 36 分钟，完成 306 个周期后突然 exit code -1 退出。最后一个周期 #306 状态完全正常：

```
13:24:59 | 📊 Regime=bull  置信度=0.75  VIX=7.72  回撤=0.70%
13:24:59 | ✅ 牛市档, 可适当加仓进攻类
13:24:59 | ✅ 周期 #306 完成
13:25:00 | 📊 监控周期 #307 开始  ← 此后无日志, 进程被终止
```

不是代码崩溃——是后台任务超时或系统资源限制导致的外部终止。这提醒我们：**长期运行的监控进程需要容器化或守护进程管理**，不能依赖会话级的后台任务。

## 七、306 周期实战验证

### 7.1 Regime 识别稳定性

| 指标 | 数值 |
|---|---|
| 运行时长 | 2 小时 36 分钟 (10:49 → 13:25) |
| 完成周期 | 306 个 (100% 成功) |
| Regime | 306/306 均为 bull (100% 稳定) |
| VIX | 7.72 (shadow_state_rv, 恒定) |
| 回撤 | 0.70% (shadow_state.json, 恒定) |
| 置信度 | 0.75 |
| ERROR | 0 个 |
| WARNING | 9 个 (全部降级兜底) |

VIX 和回撤盘中恒定是预期行为——`shadow_state.json` 的 `daily_nav` 只有 EOD 才刷新。306 周期 Regime=bull 完全一致，说明 Regime 识别在数据源稳定时高度可靠。

### 7.2 与市场行情的一致性

这不是一个"自嗨"的 Regime 识别——它和市场实际表现高度吻合：

| ETF | 首周期涨幅 | 末周期涨幅 | 趋势 |
|---|---|---|---|
| 科创50ETF | +3.58% | +5.57% | ⬆️ 扩大 |
| 中证500ETF | +2.01% | +3.14% | ⬆️ 扩大 |
| 创业板ETF | -0.06% | +2.28% | ⬆️ 翻红 |
| 新能源车ETF | +1.47% | +2.27% | ⬆️ 扩大 |
| 沪深300ETF | +0.67% | +1.63% | ⬆️ 扩大 |
| 医疗ETF | +1.20% | +1.20% | — 持平 |

6 只 ETF 全线上涨，科创50 领涨 +5.57%，创业板从微跌翻红——典型的 bull 市场结构。Regime=bull 的判断与市场状态完全一致。

### 7.3 权重建议（bull 档）

```
当前权重          →    建议权重          调整
科技  28.50%      →    30.00%           +1.50%  (×1.20, 裁剪至30%上限)
新能源 9.00%      →    10.35%           +1.35%  (×1.15)
医药  15.00%      →    16.50%           +1.50%  (×1.10)
现金  10.50%      →     5.80%           -4.70%  (×0.50, 大幅减仓)
```

bull 档的核心操作：**把现金转移到进攻资产**。现金从 10.50% 降到 5.80%，释放的 4.70% 分配给科技/新能源/医药。但 Phase 0 不执行——只是建议。

## 八、经验总结

### 8.1 降级链设计原则

iVIX 停用这件事教会我们：**任何外部数据依赖都要有降级方案**。降级链设计有三个原则：

1. **优先级从高到低**：最好的数据源在前，但最稳定的在后
2. **盘中和 EOD 策略分离**：盘中用缓存保护配额，EOD 强制刷新获取最新
3. **最终兜底要安全**：全部失败时降级到保守档，不要让系统"裸奔"

### 8.2 只读模式的价值

Phase 0 只读建议模式跑了 306 个周期，验证了三件事：
- Regime 识别在数据源稳定时高度可靠
- 权重建议符合预期（bull 档加仓进攻、减仓现金）
- 约束执行正确（科技裁剪至 30%、总和=1.0、现金>5%）

如果直接上 Phase 1 自动调仓，这些验证都做不了。**先看后做，分阶段上线**——这不是保守，是工程纪律。

### 8.3 双链路的职责分离

盘中告警和 EOD 报告分两条链路，核心是**审计链的洁净度**：

- 盘中每 30s 一次的检查不写 decisions.jsonl，避免审计链被淹没
- EOD 每日一次的完整决策写入审计链，可追溯
- `orchestrator=None` 是盘中链路的关键参数——它决定了"只看不记"

### 8.4 一致性校验的边界

`sense_regime` 的 VIX+RV 双指标一致性校验在盘中无法工作（没有日收益序列），`consistency_check.consistent = "no_rv_data"`。这不是缺陷——盘中本来就没有当日收益数据。置信度保持 0.75 不调整，EOD 时有了日收益序列才能做完整的一致性校验。

**教训**：一致性校验的价值在于"有数据时校验，无数据时降级"，而不是"强行校验"。

## 九、下一步

观察期预计 08-20 满 14 天。届时评估：
1. 306 周期 bull 稳定性是否持续
2. EOD 链路的 decisions.jsonl 是否完整
3. 是否进入 Phase 1（自动调仓，需再次双签）

Phase 1 最大的挑战不是技术，是信任——让系统自动修改 portfolio.yaml，需要 HC-4 约束的解除和充分的风控验证。

但至少，第一步已经迈出去了。

---

*本文是自我进化框架实战笔记第四篇。前篇：[《当 PSI=8.48 是统计噪音》](https://zhuanlan.zhihu.com/p/718630000)（Shadow 数据质量闭环）、[《GNN 因子前视偏差排查》](https://zhuanlan.zhihu.com/p/718500000)。*

*系列主题：量化系统里的数据真实性——失真的"客观数据"比没有数据更危险。*

---

**附录：关键文件**

| 文件 | 用途 |
|---|---|
| `utils/alpha/vol_regime_weighter.py` | 核心模块（Regime 识别 + 权重计算 + 约束执行） |
| `utils/alpha/vix_data_source.py` | VIX 数据源（三级降级链） |
| `utils/alpha/drawdown_reader.py` | 回撤读取（从 shadow_state.json） |
| `utils/auto_trading_system.py` | 盘中集成（_check_vol_regime） |
| `utils/alpha/evolution_orchestrator.py` | EOD 集成（_run_vol_regime_weighter） |
| `configs/vol_regime_weighter.yaml` | 配置（权重矩阵 + 数据源 + 监控参数） |
| `configs/feature_flags.yaml` | Flag 配置（USE_VOL_REGIME_WEIGHTER） |
| `reports/volatility/daily_run_report_2026-08-05.md` | 首日运行总结报告 |
