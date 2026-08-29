# 量化交易系统 · 统一代码审查标准与流程 v2.0

> ## ⚠️ 已被取代（2026-08-29）
> **本文档不再作为执行依据**，仅作历史追溯保留。
> 当前唯一事实源：**`docs/代码审查体系_v4_20260829.md`**。
> 取代原因：v1.0/v2/v3 三份标准并存且已分叉，v4 将其统一，并把标准逐条绑定到可执行命令与退出码
> （新增 G0 分支纪律 / G2 三维度 / G5 债务熔断三道卡点，落地脚本 `scripts/review_gate.py`）。

> 整合自：`CODE_REVIEW_STANDARD.md`(v1.0)、`CODE_REVIEW_PROCESS.md`、`CODE_REVIEW_GAP_AUDIT_2026-08-08.md`、各 audit / BACKLOG / RETRO，以及 2026-08-11 三轮定向审查（F-1~F-9）的实测结论。
> 核心理念：**静态工具告警是线索，不是结论；流水线要"拦截缺陷"，不是"生产审查报告"。**

---

## 0. 为什么要有这个标准（三轮审查的血泪教训）

本项目已有 20+ 份审查文档、ruff / bandit / mypy / pytest / CI 全套基建，但 bug 仍反复进入实盘路径。根因不是"审查次数不够"，而是**审查没有接进"出错会拦人"的链路**——制品（文档 / 报告）再多，只要不挡在代码进仓库 / 进实盘的路上，bug 照过不误。

| 泄漏点 | 现象 | 对应的真实 bug |
|---|---|---|
| A. 钩子死 | Windows 只执行无扩展名钩子；旧 `.git/hooks/` 只有 `.bat/.cmd` → 本地门禁全静默失效 | F-1 |
| B. 报告被吞 | 自检用 `logger.info()` 输出但无 handler，INFO 被丢 → 跑了也看不到 | F-2（已修） |
| C. 漏掉语义 bug | ruff / bandit / mypy 全绿也抓不到"读缓存不校验 / 算指标不 guard" | F-3 / F-4 / F-5 / F-6 / F-7 |

**关键认知**：F-3（261 天假日历把国庆判为交易日）、F-7（Black-Litterman δ=0 → NaN 权重）这类 bug，JSON 合法、Python 合法、lint 全绿，**只能被"针对函数的行为契约测试"抓住**。而恰恰是这些致命路径，当时零测试覆盖。

---

## 1. 严重度模型

| 级别 | 含义 | 处理时效 | 能否带过门禁 |
|---|---|---|---|
| 🔴 **P0 阻断** | 资金安全 / 实盘正确性 / 数据正确性缺陷；或门禁 / CI 配置失效 | 立即，禁止合并 | 否（门禁 exit 1） |
| 🟡 **P1 建议** | 静默数值污染、可观测性缺失、明显逻辑隐患 | 本周内 | 否（门禁应拦） |
| 💭 **P2 / P3 挑刺** | 风格、可读性、非资金路径瑕疵 | 周期清理 | 是 |

**资金安全升格规则**：任何触及「交易日期 / 行情数据 / 资金计算 / 风控阈值 / 下单指令」的缺陷，一律升格为 P0。典型：缓存读取不校验、相关系数未 NaN 守卫、分母参数未校验。

---

## 2. 量化系统专项审查清单（5 条硬规则，必须编入门禁）

这 5 条是三轮审查沉淀出的、静态工具抓不到但会静默腐蚀决策的高频失效模式。审查每一笔涉及数据的代码，逐条核对：

### 规则 Q1 · 相关系数 / IC 调用必须 `np.isfinite` 守卫
`np.corrcoef` / `scipy.stats.spearmanr` / `pearsonr` 对**常数序列**（停牌、恒定价、未更新数据）返回 `NaN` 且不抛异常 → NaN 沿计算图静默传播。

```python
# ❌ 坏：常数列返回 NaN 且不报错，下游被污染（F-4/F-5/F-6）
ic = float(np.corrcoef(factor_arr, return_arr)[0, 1])
# ✅ 好：先判方差再算；结果非有限按 0
if len(factor_arr) > 1 and np.std(factor_arr) > 0 and np.std(return_arr) > 0:
    ic = float(np.corrcoef(factor_arr, return_arr)[0, 1])
else:
    ic = 0.0
ic = ic if np.isfinite(ic) else 0.0
```

> 自相关恒为 `1.0`；非对角未知相关按 `0.0`。相关矩阵用 `np.nan_to_num(corr, nan=0.0)` 后**对角线强制 `1.0`**。

### 规则 Q2 · 除零路径必须守卫
`a / b`、`np.divide`、对任何 `mean/std/var` 分母，当分母可能为 0（常数序列、空序列、配置误填）时必须 `if denom != 0` 或 `np.where(b != 0, a/b, 0)`。

```python
# ❌ 坏：常数列 std=0 → 0/0=NaN
z = (x - x.mean()) / x.std()
# ✅ 好
std = x.std()
z = (x - x.mean()) / std if std > 1e-10 else 0.0
```

### 规则 Q3 · 分母类参数构造期必须校验 > 0
优化器 / 模型构造时，显式校验风险厌恶、收缩强度、置信度等分母参数 `> 0`，把 numpy 的 `inf/NaN 不抛异常` 转成明确报错。

```python
# ❌ 坏：delta=0 → 权重 inf/inf=NaN，全程不报错（F-7）
self.delta = float(risk_aversion)
# ✅ 好
if risk_aversion <= 0:
    raise ValueError("risk_aversion 必须 > 0")
self.delta = float(risk_aversion)
```

### 规则 Q4 · 陈旧 / 缓存数据必须显式标记 stale 并降级质量分
任何兜底数据源（P5/P6 缓存、陈旧历史）命中时，必须在 `quality_score` / 结果元信息上标记陈旧（**不得满分 100**），并打 warning 日志，禁止把陈旧数据当实时数据流通。

```python
# ❌ 坏：陈旧缓存被误报 quality_score=100（F-8）
quality_score = 100.0
# ✅ 好
quality_score = STALE_QUALITY_SCORE  # 0.0
logger.warning("使用 P6 陈旧缓存兜底 … 请勿作为实时数据用于交易决策")
```

### 规则 Q5 · 禁止裸 `except:`
裸 `except:` / `except Exception:` 会吞掉所有异常，掩盖真实错误，且会捕获 `KeyboardInterrupt` / `SystemExit`。仅捕获具体异常类型。

```python
# ❌ 坏（F-9）
try:
    detail = resp.json().get('error', '')
except:
    pass
# ✅ 好
try:
    detail = resp.json().get('error', '')
except (ValueError, AttributeError):
    pass  # 仅跳过错误响应体解析
```

---

## 3. 通用审查核查点（lint 之外的逻辑层）

- **缓存读写**：读缓存必须校验有效性（范围 / 格式 / 跨年 / 过期）+ 隔离坏缓存；写缓存前必须校验上游数据合理。→ 参考 `utils/trade_calendar.py` 三层防御（L1 校验、L1 隔离、L2 降级、L3 可观测）。
- **边界**：空序列 `max/min/argmax`、单元素、长度不匹配分支。
- **浮点金额比较**：用 `math.isclose` 而非 `==`。
- **可变默认参数**：禁止 `def f(x=[])`。
- **未定义名 / 重定义**：`F811/F821`（注：本轮扫描活跃代码零命中，旧 backlog D-1 实际指向存档文件，非活跃缺陷）。
- **密钥 / 凭证**：禁止硬编码；缺失视为 P0 ERROR，但 pre-commit 用 `--skip-datasource` 不强制连源，不阻断日常提交。

---

## 4. 五步审查工作流

1. **静态初筛**：ruff（真实缺陷规则集）+ bandit + mypy，仅作线索。
2. **语义复核**：按 Q1~Q5 与通用核查点逐条过关键路径；静态工具看不出的，用行为测试验证。
3. **关键路径契约测试**：每个 P0 函数配至少一个"正确行为"断言（如 `is_trading_day('2026-10-01') == False`）。
4. **门禁卡点**：本地 pre-commit + CI 双重拦截；P0 / P1 不得带过。
5. **闭环归档**：缺陷入 Backlog，修复必带回归测试，批量修复入 `CODE_REVIEW_FIX_ROUNDx.md`。

---

## 5. 门禁与流水线配置

### 5.1 本地 pre-commit（激活状态见 §7）
```bash
git config core.hooksPath githooks   # 团队共享，钩子脚本入 git
# 或个人立即生效：cp githooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```
钩子 `githooks/pre-commit` → `scripts/pre_commit_check.py`，依次执行：
硬编码路径扫描 → 悬挂引用检查 → P0 文件裸 print 检查 → P0 启动自检（`--skip-datasource`）→ NaN 污染守卫。

### 5.2 跳过方式（紧急情况）
- `SKIP_P0_CHECK=1 git commit`：跳过整轮自检
- `SKIP_P0_PRINT=1 git commit`：仅跳过 P0 print 检查
- 仅 `.md/.txt/.gitignore/.editorconfig` 变更自动跳过

### 5.3 CI
`.github/workflows/ci.yml`：pytest + ruff + bandit + mypy；门禁失败阻断 PR。

---

## 6. 完成定义（DoD）

一笔变更满足以下全部方可合并：
- [ ] ruff / bandit / mypy 无新增 P0 / P1 告警（存量噪声可后续清理）
- [ ] Q1~Q5 专项核对通过
- [ ] 触及资金 / 数据 / 风控路径的，新增契约测试
- [ ] 修复类变更附带**回归测试**（负向验证：先构造触发条件，断言已修复）
- [ ] 本地 pre-commit 与 CI 均绿
- [ ] 缺陷已记入 Backlog 或对应 FIX 批次文档

---

## 7. 当前激活状态与激活步骤

> **实测（2026-08-11）**：`git config --get core.hooksPath` 已返回 `githooks`，`githooks/pre-commit` 存在且可执行（`-rwxr-xr-x`）。即钩子**当前已生效**。若此前因 `.git/hooks/` 仅含 `.bat/.cmd` 而失效，现已通过 `core.hooksPath` 指向正确的 `githooks/` 修复（Windows 下 Git 只执行无扩展名钩子，`core.hooksPath` 方案规避了该限制）。

确认 / 激活命令（幂等）：
```bash
cd 28-终极量化交易系统8.4
git config core.hooksPath githooks
git hook run pre-commit   # 干跑验证钩子确实触发
```

⚠️ 注意：pre-commit 用 `--skip-datasource`，**不强制连数据源 / 凭证**，故日常提交不会被 `WIND_API_KEY` 缺失阻断；只有真实自检失败（硬编码路径、悬挂引用、P0 print、NaN 污染、漏守卫）才会拦。

---

## 8. Backlog（整合 D / G 系列 + 本轮 F-1~F-9）

| 编号 | 级别 | 描述 | 状态 |
|---|---|---|---|
| F-1 | 🔴 | 本地 pre-commit 在 Windows 上失效（钩子未接） | ✅ 已激活（core.hooksPath=githooks） |
| F-2 | 🔴 | P0 自检报告被 logger 吞掉不可见 | ✅ 已修（改 print） |
| F-3 | 🔴 | 交易日历缓存投毒 → 国庆判为交易日 | ✅ 已修 + 回归锁定 |
| F-4 | 🟡 | `utils/infra/core.py` 策略 IC 无 NaN 守卫 | ✅ 已修 |
| F-5 | 🟡 | `utils/risk_metrics.py` 相关矩阵无 NaN 守卫 | ✅ 已修 |
| F-6 | 🟡 | `utils/ledoit_wolf_covariance.py` 收缩 avg_corr 无守卫 | ✅ 已修 |
| F-7 | 🔴 | Black-Litterman δ 未校验 > 0 → NaN 权重 | ✅ 已修 |
| F-8 | 🟡 | P6 陈旧缓存误报 quality_score=100 | ✅ 已修 |
| F-9 | 💭 | `cli/modes/gemma_analyze.py` 裸 except 吞异常 | ✅ 已修 |
| D-1 | — | 未定义名（活跃代码已无，原审计指向存档文件） | ✅ 复核无 |
| D-2 / D-3 | — | logger 先用后定义 / 硬编码行情（活跃关键路径已无） | ✅ 复核无 |
| G-1~G-3 | 🟡 | 风格 / 噪声类（F841 / B007 / PLW2901 / E741） | ⏳ 周期清理 |

---

## 9. 度量指标（建议纳入周报）

- 门禁拦截率（pre-commit / CI 阻断次数）
- P0 / P1 存量与新增趋势
- 关键路径契约测试覆盖率
- 回归测试通过率（目标 100%）

---

## 10. 落地检查表（Reviewer / Author 通用）

- [ ] 这笔改动触及"交易日期 / 行情 / 资金 / 风控"吗？→ 是则按 P0 审查
- [ ] 有读缓存 / 读外部数据吗？→ 校验 + 降级 + 可观测（Q + §3 缓存项）
- [ ] 有相关系数 / IC / 除零 / 优化器分母吗？→ Q1~Q3 守卫齐备
- [ ] 有兜底 / 陈旧数据吗？→ Q4 标记 stale
- [ ] 有 try / except 吗？→ 无裸 except（Q5）
- [ ] 改了关键路径吗？→ 带契约 / 回归测试
- [ ] 本地 `git hook run pre-commit` 通过？
