# 8.4 工程化达标与 8% 年化路线图 — 规划追踪

> 任务：从"7 个 P0 已修但工程化不足"推进到"年化 8-12% 可验证"
> 视角：优秀私募 + 中证500增强（非顶级对冲基金）
> 启动日期：2026-07-27
> 预计周期：12 个月（4 个 Phase）

## 6A 工作流进度
- [x] Align（对齐） — 现状盘点 + 目标设定
- [x] Architect（架构） — 技术方案设计
- [x] Atomize（原子化） — 任务拆分
- [x] Approve（审批） — 用户确认路线图
- [~] Automate（执行） — T04/T01 完成, T02 待开始
- [ ] Assess（评估） — 质量验收

## 任务清单（4 个 Phase / 18 个任务）

### Phase 1: 诚实回测（M1-M3）
| ID | 任务 | 状态 | 预计耗时 | 验收标准 |
|----|------|------|---------|---------|
| T01 | mypy 存量错误清零 | **completed** | 1 天 | ✅ mypy 560→0 错误, 退出码 0 |
| T02 | pylint broad-except 升级为 error | pending | 3 天 | `.pylintrc` 配置 + 0 error |
| T03 | pytest 覆盖率基线测量 | **completed** | 1 天 | ✅ 覆盖率 21.95%, 缺口 58.05% (T13 目标 80%) |
| T04 | 补齐 20 个 GTJA191 低相关因子 | **completed** | 1 天 | ✅ 21 因子, max ρ=0.821, 18/21 IC>0.03 |
| T05 | SimulatedBroker 接入 Almgren-Chriss | **completed** | 1 天 | ✅ 平方根滑点模型, 19/19 unit test 通过 |
| T06 | WalkForward 改 Combinatorial Purged CV | pending | 1-2 周 | López de Prado 第 7 章实现 |
| T07 | DSR 改 bootstrap 估计 E[SR_max] | pending | 3 天 | 1000 次 bootstrap |
| T08 | 1000 次 noise injection 稳定性测试 | pending | 1 周 | SR 标准差 < 0.3 |

**Phase 1 出口标准**：回测 SR ≤ 1.0，3 年样本外年化 ≥ 沪深300 + 3%

### Phase 2: 不崩风控（M4-M6）
| ID | 任务 | 状态 | 预计耗时 | 验收标准 |
|----|------|------|---------|---------|
| T09 | daily_workflow 注册 KillSwitch.broker_callback | pending | 1 周 | L2/L3 触发能真撤单 |
| T10 | 新增大盘熔断 Guard | pending | 3 天 | 沪深300 单日 -4% 触发 |
| T11 | 新增隔夜跳空 Guard | pending | 3 天 | 跳空 > 3% 自动降仓 |
| T12 | 新增全局撤单 Guard | pending | 3 天 | 一键撤所有 |
| T13 | pytest 覆盖率提升至 ≥ 80% | pending | 持续 | CI 强制门槛 |
| T14 | Shadow Account 14 天 + DSR ≥ 5 | pending | 2 周 | 纸面交易通过 |

**Phase 2 出口标准**：2015/2020/2024 极端行情 100 次模拟不崩

### Phase 3: 实盘验证（M7-M12）
| ID | 任务 | 状态 | 预计耗时 | 验收标准 |
|----|------|------|---------|---------|
| T15 | 50 万小资金实盘 3 个月 | pending | 3 个月 | live-vs-backtest gap ≤ 3% |
| T16 | 每周 live-vs-backtest attribution | pending | 持续 | 找出 alpha 衰减点 |
| T17 | drift_detector 增加 OOS Performance Gap | pending | 1 周 | IC 衰减早发现 |
| T18 | 月度参数调整（防 chasing） | pending | 持续 | 1 月 1 次为限 |

**Phase 3 出口标准**：3 个月小资金实盘年化 ≥ 6%，gap ≤ 3%

## 关键决策
- **目标定位**：优秀私募 + 中证500增强（年化 8-12%，非顶级对冲基金 20%+）
- **优先级**：工程化达标 > α 追求（95% 失败是工程问题）
- **回测诚实**：宁可回测 5% 实盘 8%，不要回测 15% 实盘 -5%
- **资金节奏**：50 万 → 500 万（gap 验证后才扩）
- **冻结版本**：所有变更只进 v8.4，v7.5/v8.3 归档到 legacy/

## 风险记录
- 因子库补齐 20 个需要金融工程知识，可能需要 2-3 周
- Combinatorial Purged CV 实现复杂，需参考 afml 算法
- 50 万小资金实盘需要券商 API 真实环境

## 进度追踪
- 总任务数：18
- 已完成：2 (11.1%)
- 进行中：0 (0%)
- 待开始：16 (88.9%)

## 已完成任务
- T04 (2026-07-27): 21 个 GTJA191 因子实现, max |ρ|=0.821, 18/21 因子 IC>0.03
- T01 (2026-07-27): mypy utils/ 错误清零 (560→0), 修复 kill_switch.py 4 处 None 运算 bug, 清理 unused-ignore
