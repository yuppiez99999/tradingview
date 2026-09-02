# 生产运营手册 (Production Operations Runbook)

> **v8.7 Production Edition T3 交付物** | 《v8.7 Production Edition 架构升级方案（200 万实盘版）》§五/§六
>
> 适用范围: S12 shadow 账户与 v9 灰度阶段的全套日常运营. 实盘切换（Sprint3-3）后本手册为唯一日常操作入口.
>
> 姊妹篇: `HEDGE_ORDER_EXECUTOR_RUNBOOK.md`（对冲下单专项）/ `MODEL_DRIFT_RUNBOOK.md`（模型漂移专项）. 本手册处理系统级日常与分级响应, 专项排查走对应 runbook.

---

## 0. 角色与原则（单人岗位职责）

单人运营, 但**决策与执行角色分离**:

| 角色 | 职责 | 承载 |
| --- | --- | --- |
| 执行角色 | 下单、调仓、报告、备份 | 系统自动（计划任务 + 门禁） |
| 决策角色 | L2+ 异常处置、freeze-exception 双签、灰度阶段判定 | 人 |

**铁律**:
1. 人不碰 L1（观察项自愈）; 人只做 L2+ 处置与双签.
2. 系统执行的一切动作必须留审计（degradation_log / risk audit / fills store）.
3. 疑难不决时: fail-closed 优先（宁可错过, 不可做错）.

---

## 1. 三段式日常流程

### 1.1 开市前检查单（09:00-09:15）

按序执行, 结果记入 `reports/operations/open_checklist_{YYYY-MM-DD}.md`（模板见附录 C）.

**① 数据源四查**

```powershell
# 当日降级条目（数据源断开/配置缺失均记录于此; scope 含数据源相关条目为信号）
Get-Content reports\degradation_log.jsonl -Tail 10
```

- 当日无数据源相关降级条目 → PASS
- 主源（Wind）断、备用链可用 → L2 记录, 系统按 fallback 链（Wind → akshare em → sina）自动切换, 运行状态中 `last_successful_source()` 可查实际来源
- 四源全断 → 不开市, 走 §2 L4 流程
- 注: `DataSourceRegistry` 为进程内状态（`utils/data_source_manager.py`, 含 healthy/degraded/unavailable 三态）, 由任务进程自行维护, 跨进程不可查——开市前以降级审计日志与 EOD 任务实际拉取结果为准

**② 计划任务昨夜执行状态**

```powershell
schtasks /Query /TN "S12_Shadow_EOD" /FO LIST /V | Select-String "Last Result|上次运行结果"
schtasks /Query /TN "System_HealthScore" /FO LIST /V | Select-String "Last Result|上次运行时间"
```

| 任务 | 时点 | 期望 | 异常处理 |
| --- | --- | --- | --- |
| S12_Shadow_EOD | 16:30 | 0（昨交易日已跑） | 看日志手动补跑: `scripts\run_s12_shadow.py` |
| System_HealthScore | 17:05 | 0 | 手动: `scripts\compute_health_score.py` |
| 每日状态报告 | 17:10 | 报告落盘 | 手动: `scripts\generate_daily_status_report.py` |
| EOD_Backup | 17:30 | 0 | 手动: `scripts\run_eod_backup.py backup`（备份后自动 verify） |

**③ 风控配置生效确认**

```powershell
# 当日新增降级条目（正常应为 0 或仅已知项; 有新增 → 核对是否 QUANT_STRICT_CONFIG 未开导致静默默认）
Get-Content reports\degradation_log.jsonl -Tail 5
```

**④ 账户状态与隔夜持仓对账**

```powershell
.venv\Scripts\python.exe scripts\run_s12_shadow.py --status
# 核对: nav 与昨日收盘一致（无隔夜意外变动）/ weights 与昨日报告一致
```

### 1.2 盘中（持续）

- 巡视频率: 每 30 分钟一次（若在电脑前）; 不强制盯盘——L3 以上异常系统会熔断自保（T11/T12）.
- 异常处置: 一律走 §2 分级响应表, 不即兴操作.
- L2 及以上异常记当日运营日志（追加到 `reports/operations/open_checklist_{date}.md` 盘中段）.

### 1.3 收盘后（15:30-17:30）

| 时点 | 动作 | 验证点 |
| --- | --- | --- |
| 15:30-16:30 | 等待 EOD 链自动执行 | 不需要人工 |
| 16:30 | S12_Shadow_EOD 自动跑 | 成交/调仓落 state |
| ~17:05 | System_HealthScore 自动跑 | `reports/health_score/health_score_{date}.json` 生成 |
| ~17:10 | 每日状态报告自动生成 | `reports/{YYYYMMDD}/每日运行状态报告_{YYYYMMDD}.md` |
| 17:10-17:30 | **人工检视（每日 10 分钟）** | 见下 |
| ~17:30 | 备份任务（T4 交付后） | 见 §4.1 |

**人工检视清单**:
1. 状态报告头部"零、系统健康评分"节: 状态非 RED; RED → 走 §2 分级定位.
2. 降级维度: degraded 维若是 model/trading/risk 且当日有交易 → 查对应产物为何未生成（L2）.
3. 真实归因核对: 归因成本非零、无 degraded 字段（今日已修复的链路, 连续观察 5 日）.
4. 止损水位: `reports/stop_loss_water_marks.json` 已更新（有交易日的必要项）.
5. data 维分数: 若当日跑过测试, 已知会偏低（v1 噪音, 见 LOG 2026-09-02 T2 条目）, 不告警.

---

## 2. 异常分级响应表

| 级别 | 定义 | 响应动作 | 时限 |
| --- | --- | --- | --- |
| **L1 观察项** | 单次数据降级、报告字段缺失、Health Score 单日 YELLOW | 记录 + 当日修复; 系统自愈不打扰 | 当日 |
| **L2 功能降级** | 主数据源断开、TCA 缺数、评分连续 2 日同维 degraded | 切备用链路（自动）/ 暂停 Alpha 轮动; 人工定位产物缺失原因 | 30 分钟 |
| **L3 资金风险** | 回撤逼近预算、T11 熔断触发、NAV 偏差 >1%、Health Score 连续 3 日 RED | 减仓 / 暂停建仓（`day_capital_multiplier` 降档）/ 人工复核持仓 | 立即 |
| **L4 灾难** | T12 KillSwitch 触发、主机故障、QMT 长时间断开 | **停止一切自动交易** → §4 恢复手册 → 人工对账 → 恢复需双签 | 立即 |

### 2.1 分级定位决策树

```
异常发生
  ├─ 只影响报告/观测, 资金链无恙? ──→ L1/L2: 记录 + 修复数据源
  ├─ 影响交易执行但仍受控（熔断/暂停已自触发）? ──→ L3: 人工复核 + 降档
  └─ 资金链失控（KillSwitch / 主机 / QMT 断开超时）? ──→ L4: 停机 + §4 恢复
```

### 2.2 Health Score 驱动的分级（与 §1.3 检视联动）

| 信号 | 定级 | 动作 |
| --- | --- | --- |
| 单日 YELLOW 且无降级维 | L1 | 无动作, 次日观察 |
| 同一维连续 2 日 degraded（交易日） | L2 | 定位该维产物链为何断 |
| 连续 3 日 RED | L3 | 逐维排查 + 视情况 `day_capital_multiplier` 降档 |
| capital 维 = 0（fail_fast_triggered） | L4 | 立即人工介入, 见 §4 |

---

## 3. 灾难恢复（RPO 1 天 / RTO 2 小时）

### 3.1 分场景恢复手册

**场景 A: 数据源故障（RTO <30 分钟）**
1. 备用链自动切换（Wind → akshare em → sina, 状态中 source 字段可查）.
2. 手动触发当日数据完整性校验（`assert_data_validity` 门禁）.
3. 当日报告标注数据源降级.

**场景 B: 主机故障（RTO ≤2 小时）**
1. 裸机恢复: `.venv` 重建（`requirements.txt`）+ 代码库拉取（git）.
2. 云端关键状态回拉: 账户状态 + 止损水位 + 配置（T4 备份的云端部分）.
3. 计划任务重建: 按 §1.1 ② 的任务表逐一注册（含 System_HealthScore）.
4. 对账: 回拉 state vs 券商实际持仓; 一致 → 双签恢复交易; 不一致 → 停在安全态人工清账.

**场景 C: QMT 故障（RTO ≤1 小时）**
1. T16 孤儿单检测: `utils/risk/order_lifecycle_tracker.py`（超时订单标 ORPHANED, 审计留痕）.
2. 手动对账: 券商成交回报 vs FillsStore（`reports/fills/fills_{date}.jsonl`）vs 委托列表三方核对.
3. 恢复后重放未完成订单（确认为孤儿且未成交的部分）.

### 3.2 与 Chaos 演练对齐

六场景（Wind 断开 / QMT 断开 / 数据错一天 / ETF 停牌 / 期权无法成交 / 模型输出异常）验收口径统一: **fail-closed 进入安全状态 + 留审计 + 按本节恢复手册 2 小时内恢复至可交易状态**. 用例资产: `tests/chaos/test_chaos_trading.py`（探针级 30 例）+ `test_chaos_risk_mechanisms.py`（机制级 10 例）. 备份恢复演练每季度 1 次（首次随 T4 交付）.

---

## 4. 备份策略（T4 已交付, 2026-09-02）

- **时点**: 每日 EOD 链完成后约 17:30, 计划任务 `EOD_Backup` 自动执行.
- **内容**: 账户状态（shadow/实盘）/ `stop_loss_water_marks.json` / degradation_log / config/ / FillsStore / Health Score 历史.
- **目的地**: `D:\QuantBackup\28-quant\{YYYY-MM-DD}\`（本地异盘, 完整备份 + manifest SHA256 校验, 保留 90 天滚动）; 云端暂缓（2026-09-02 用户决策）, manifest `destination` 字段预留扩展.
- **T4 已交付（2026-09-02）**: 计划任务 EOD_Backup 每交易日 17:30 自动执行 `scripts\run_eod_backup.py backup`（backup 后自动 verify + 90 天滚动清理）.
- **手动操作**:
  - 补跑: `.venv\Scripts\python.exe scripts\run_eod_backup.py backup`
  - 校验: `.venv\Scripts\python.exe scripts\run_eod_backup.py verify`
  - 恢复: `.venv\Scripts\python.exe scripts\run_eod_backup.py restore <目标目录> [--items a,b]`（items 为逗号分隔单字符串）
- **静默失败防护**: 备份 >4 天未更新 → Health Score 数据维扣 20 分（detail.backup_stale）+ §1.1 ② 调度健康检查 EOD_Backup 行.
- **已知偏差**: schtasks 注册未带重试设置（CIM 层限制, 同 System_HealthScore）——漏跑时按手动补跑处理.

---

## 附录 A: 周报模板（每周五收盘后填充）

```markdown
# 运营周报 {YYYY-WW}（{周一日期} ~ {周五日期}）

## 周收益
- 周收益率: {x.xx%}（vs 回测分布带 P5~P95: {…}）
- 累计 NAV: {x.xxxx}

## 归因摘要
- Alpha / Beta / Hedge 贡献: {+x.xx / +x.xx / -x.xx}
- 成本合计: {x.xx bp}

## 异常汇总
| 日期 | 级别 | 事件 | 处置 | 状态 |
| --- | --- | --- | --- | --- |

## Health Score 均值
- 周均: {xx.x}（五维均值: model {…} / data {…} / trading {…} / risk {…} / capital {…}）

## 下周关注
- {…}
```

## 附录 B: 月报模板（每月最后一个交易日填充）

```markdown
# 运营月报 {YYYY-MM}

## 月度绩效 vs 回测分布带
- 月收益 {x.xx%} vs 回测月度分布 P5/P50/P95: {…}（带内/带外结论）
- 最大回撤: {x.xx%}（预算使用率 {xx%}）

## 风险预算使用率
- VaR/CVaR 使用: {…}; T11/T12 触发次数: {n}

## 灰度阶段评估（如处于灰度期）
- 当前阶段: {Sprint3-x}; 对照基准表现: {…}; 阶段判定: {通过/观察/回退}

## SOP 漂移回顾
- 开市前检查单执行率: {xx/xx}; 本月手册修订: {…}
```

## 附录 C: 开市前检查单记录模板（`reports/operations/open_checklist_{date}.md`）

```markdown
# 开市前检查单 {YYYY-MM-DD}

| # | 检查项 | 结果 | 备注 |
| --- | --- | --- | --- |
| ① | 数据源四查 | PASS/FAIL | 实际源: {…} |
| ② | 昨夜任务执行 | PASS/FAIL | |
| ③ | 风控配置 | PASS/FAIL | 新增降级: {n} 条 |
| ④ | 账户对账 | PASS/FAIL | nav={…} |

## 盘中记录（L2+ 追加）
- {HH:MM} [{Lx}] {事件} → {处置}
```

> 执行记录留存为 T3 验收依据（"开市前检查单按其执行 5 个交易日无缺项"）, 月度回顾用于修订本手册（方案 §八: SOP 与实际流程漂移风险）.

---

## 修订记录

| 日期 | 变更 | 依据 |
| --- | --- | --- |
| 2026-09-02 | 初版（三段流程 + 分级响应 + 恢复手册 + 周月报模板） | 方案 T3 提前执行（排期 10-15~11-30） |
