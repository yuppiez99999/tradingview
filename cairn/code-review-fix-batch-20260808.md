# 2026-08-08 代码审查修复批次经验沉淀

## 一句话结论
7 个缺陷 + 3 项门禁加固全部落地, 关键指标全部清零 (F821=0, bandit B501=0, 静默吞咽=0), 工业级判据维持 7/2/0。

## 修复批次明细

### P0 (CRITICAL/HIGH) 真实缺陷
1. **B1 hedge_quantity_calculator.py 硬编码期货行情** (L88-91)
   - 风险: IF=3800/IC=5500/IM=5800 与实际偏差 3-5%, 误用为实盘前测算会导致对冲比例系统性偏差
   - 修复: 强化 OFFLINE_ONLY 标注 + 添加运行时 logger.warning + 标注 B1 锚点
   - 教训: 离线脚本必须有显式护栏 (logger.warning + 文档锚点), 防止误用为实盘
   - 深层修复 (未做): quant_review_lint.py 增加 "P0 文件硬编码行情" 规则自动检测 (见 G-6 建议)

2. **B2/B3 realtime_monitor/watch_my_{positions,universe}.py SSL verify=False**
   - 风险: B501 High/High confidence, MITM 可伪造行情数据 → 持仓监控误判
   - 修复: verify=False -> verify=certifi.where() (带 ImportError 回退到 True)
   - 验证: bandit -r realtime_monitor/ 全 0 issue
   - 教训: bandit 扫描口径必须排除 qlib_env/ 噪声, 纳入 realtime_monitor/ 真实业务代码

3. **B4 automated_execution_system.py except 静默吞咽** (L1704, L1720)
   - 风险: 对冲/再平衡失败仅 logger.warning, "对冲实际没生效"与"普通告警"长得一样
   - 修复: 加 hedge_failure/rebalance_failure 状态字段 + 连续失败计数 + send_alert 告警
   - 教训: fail-safe 设计必须区分"告警"与"失败", 用独立通道 (send_alert) 而非 logger.warning
   - 原则: 决策路径 fail-close, 观测路径 fail-open (08-08 fills-driven-pnl-lessons 已沉淀)

### P1 门禁加固 (结构性盲区)
4. **G-2 P0_FILES 未同步重构**
   - 问题: P0 名单按 basename 匹配, automated_execution_system.py 从根目录薄包装迁移到 utils/execution/ (2500 行真实现) 后, P0 门禁未同步, 真正下单引擎在门禁外
   - 修复: P0_FILES 增加 automated_execution_system.py + wt_backtest_engine.py (1 行改动)
   - 教训: 重构必须同步更新 P0 名单, basename 匹配机制要主动利用

5. **G-3 pre-commit 缺 py_compile 检查**
   - 问题: D-4 类语法错误 (f-string 反斜杠, docstring 重复粘贴) 可入库, 提交前连一次 py_compile 都没跑
   - 修复: .pre-commit-config.yaml 加 py_compile_check 钩子 (10 行, ~1s/文件)
   - 教训: 最廉价的防线往往收益最大, py_compile 是 Python 静态检查的最低门槛

6. **G-4 CI 增量门禁缺 F821 规则**
   - 问题: 全仓 8 个 F821 (含 v8.3_institutional/daily_workflow.py:4525 真实 bug), 增量门禁扫不到存量, 永久豁免
   - 修复: ci.yml 加 F821 步骤 (阻断式, 仅 PR 改文件) + daily_workflow.py 补模块级 pd import + ruff.toml 排除 external/airllm_src 噪声
   - 验证: ruff check --select F821 . = All checks passed!
   - 教训: 增量门禁防新增, 全量扫描清存量, 二者必须配合 (建议 nightly 全量 + 基线)

### P2 存量债
7. **B7 F821 排查** — 08-07 报告说 cli/modes 8 个 F821, 实测 cli/modes + scripts/ 0 个, 真实 F821 在 v8.3_institutional/daily_workflow.py (1 个) + external/airllm_src/*.ipynb (4 个噪声)
   - 教训: 审查报告行号会失效, 必须用 `ruff check` 重新核验当前状态而非引用旧报告

## 工具链经验

### 验证脚本的字面匹配陷阱
- _verify_fixes_20260808.py 用 `verify=False not in c` 检测 B2/B3 修复, 但修复用的是 `verify=_verify` 变量赋值, 字面 "verify=False" 只在注释里作为历史说明
- 解决: 用 bandit 实际扫描验证 B501 真实清零, 而非字面匹配
- 教训: 验证脚本不能假设修复方式, 要用权威工具 (bandit/ruff) 验证实际安全状态

### PYTHONUSERBASE=C:\NUL 绕过 site.py GBK 坑
- Python 3.8 site.py 加载 user site-packages 时, .pth 文件含非 ASCII 字符触发 GBK UnicodeDecodeError 阻塞所有 python 命令
- 绕过: `$env:PYTHONUSERBASE="C:\NUL"` 强制 user site-packages 指向 NUL 设备
- 08-06 已沉淀, 本批次再次验证有效

### ruff 排除配置的位置敏感
- ruff.toml 的 extend-exclude 必须在 [lint] 段之前, 否则不生效
- external/airllm_src 排除后, F821 从 4 -> 0 (全为第三方 ipynb 噪声)

## 下一步建议

### 立即可做 (1-2h)
- G-6 quant_review_lint.py 增加 "P0 文件硬编码行情" 规则 (5 行 Python, 自动检测 B1 类问题)
- G-7 bandit 配置固化: 排除 qlib_env/, 纳入 realtime_monitor/ (1 行 yaml)
- B5 hedge_quantity_calculator vs hedge_rebalance_backtest 双份去重 (1-2h)

### Sprint 排期 (4-8h)
- Nightly 全量 ruff + 基线文件 (关闭 G-1, 存量开始收敛)
- mypy 基线验证 + CI 接入 (存量不阻断, 新增阻断)
- 覆盖率 65% -> 80% (持续)

### Phase 4 (架构升级)
- G1 QMT 真实下单接线 (dry_run -> 灰度 -> 全量, 3-5 天)
- G9 FeatureStore 物理部署 (3 天)
- G11 CVaR Monte Carlo 接入主风险链路 (2 天)

## 验证快照 (2026-08-08 16:00 CST)

```
industrial_grade_check: 7 PASS / 2 WARN (C1+C8 QMT有意后置) / 0 FAIL
assert_data_validity: 7 PASS / 0 FAIL
py_compile 6 modified files: ALL OK
ruff --select F821 . --no-cache: All checks passed!
bandit -r realtime_monitor/: 0 Total issues
P0_FILES: 17 文件 (原 15 + 新增 2)
pre-commit hooks: 5 (原 4 + 新增 py_compile_check)
CI incremental-static steps: 5 (原 3 + 新增 py_compile + F821)
```

## 关键教训

1. **审查报告行号会失效** — 文件重构后行号全变, 必须用工具 (ruff/bandit/py_compile) 实际核验当前状态, 而非引用旧报告
2. **增量门禁不扫存量** — 全仓 7342 个 ruff 问题、8 个 F821 永远不进审查视野, 必须加 nightly 全量 + 基线
3. **P0 名单必须随重构同步** — basename 匹配机制要主动利用, 重构后立即更新 P0_FILES
4. **fail-safe 必须区分告警与失败** — 静默吞咽 except 是头号坑, 必须用独立通道 (send_alert) 而非 logger.warning
5. **最廉价防线收益最大** — py_compile 1s/文件, 阻断所有 SyntaxError 入库, 性价比最高
6. **验证脚本不能假设修复方式** — 用权威工具 (bandit/ruff) 验证实际安全状态, 而非字面匹配
7. **OFFLINE 脚本必须有显式护栏** — logger.warning + 文档锚点 + lint 规则, 三重防护防止误用为实盘
