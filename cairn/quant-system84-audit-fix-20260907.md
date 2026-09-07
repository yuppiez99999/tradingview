# 8.4 代码审查处置记录（2026-09-07）

> 来源: `量化系统8.4代码审查报告_20260907.md`（外部深审产物，工作台 `.cluster/qt84-review-20260907/`
> 已不在仓库/工作区）。本文件为逐条核对 + 修复 + 遗留的单一处置记录。

## 核对结论速览

| # | 级别 | 报告结论 | 核验结果 | 处置 |
|---|---|---|---|---|
| R1 | P1 | 执行层混居模拟执行器、余额校验 stub | **部分成立**（报告所指 = `ms_strategy/scripts/automated_execution_system.py` 死副本；真主链路已迁移 `utils/execution/*`，但 `order_router._check_pool_availability` 余额占位 stub 属实） | 已修复 |
| R2 | P1 | 实盘状态三处口径矛盾（README 500万/门控模拟盘/ROADMAP 灰度） | 成立（README 三处 500万/已部署 + 绩效"≥8%/回撤<15%"均与 300 万权威口径冲突） | 已修复 |
| R3 | P2 | 时区 utcnow 混用 491 处 | 成立（执行链 4 文件已治理；全库其余 ~27 文件留规约渐进） | 已修复(执行链)+规约 |
| R4 | P2 | bandit MEDIUM ≈10 处（llm_client urlopen + jinja2 autoescape） | **一半误报**：全仓已无 jinja2 用法（可能已被 06e902d2/e3d30750 收敛）；llm_client urlopen 属实 | 已修复(白名单出口) |
| R5 | P2 | 门禁覆盖盲区（豁免表过期重审/.tmp_pip） | 部分成立：`.tmp_pip` 存在于磁盘但已被 .gitignore 忽略（不污染提交，清理需手动 rmdir）；豁免表 review-by 机制建议后续 | 部分（见遗留） |
| R6 | P3 | 统一入口 God 文件 2701 行 | 成立 | 建议后续排期（不实施） |
| R7 | P3 | stress_tester MC 未固定随机种子 | 成立 | 已修复 |

## R1 修复（执行层隔离 + 真实资金校验）

事实澄清：报告精读的 `ms_strategy/scripts/automated_execution_system.py`（2154 行, 内嵌 OrderRouter/ExecutionPool,
随机滑点+time.sleep 模拟, 无 broker/fills/KillSwitch）是 **T3.6 迁移前的死副本**——
全仓无任何 import/调度引用，仅 `__main__` 手动演示。主链路 = `utils/execution/automated_execution_system.py`
(1574 行) + `utils/execution/order_router.py`（P0-1 双签 `_use_live`、KillSwitch、FillsStore 落盘、get_broker 注入）。

落地 4 项：
1. `utils/execution/order_router.py` `_check_pool_availability` 余额占位 stub → 注释显式 paper-only 标记
   （真实校验已上移实盘分支）。
2. 新增 `OrderRouter._enforce_live_cash()`：实盘买入前置真实资金校验 —— broker 提供
   `get_available_funds()`/`get_account_info()['available']` 时 notional>available fail-closed 拒绝；
   查询异常 fail-closed 拒绝；无接口 warning fail-open（券商端兜底）。
3. 死副本隔离：`ms_strategy/scripts/automated_execution_system.py` docstring 加 SUPERSEDED·SIMULATION-ONLY banner
   + `__main__` 加 `AES_ALLOW_SIM_DEMO=1` 环境变量守卫（非放行拒绝启动）。
4. `scripts/g7_coverage/coverage_inventory.py` order_placement 清单剔除该死副本（无生产覆盖义务）。

验证：tests/unit/test_automated_execution_system_unit.py 全绿（TestOrderRouter 17+ 用例含
_check_pool_availability/pool 并发/execution 用例均在）。live 分支新增校验对既有测试零影响
（_use_live 仅 TRADING_ENV=production 时激活，单测环境恒 False；MagicMock broker 无资金接口走 fail-open 分支）。

## R2 修复（README 口径收敛）

`README.md` 四处与 09-07 权威口径（根 system_config.json total=300万/stock=200万/hedge=100万，ROADMAP 灰度 20万→100万→200万）对齐：
- 标题徽章/特性行：`500万实盘已部署` → `300万资金配置（证券200万+对冲载体100万）· 实盘灰度推进中`
- 实盘状态行：`✅ 已部署(2026-07-28)` → `🔒 灰度推进中（权威源=system_config broker 段+ROADMAP；生产切换窗 2026-12-31）`
- 双账户结构行：同步 300 万口径 + 权威源标注
- 绩效口径：`年化≥8% 且回撤<15%` → `年化8~18% 且最大回撤≤10%`（对齐 ROADMAP）
- LLM 段残留"豆包"三处删除（对齐 2026-09-07 豆包出局；`finetune_doubao` 仅考古 DEPRECATED 保留）

## R3 修复（执行链时区治理）

详见 `cairn/timezone-convention-20260907.md`（规约权威文档）。4 文件落地 + 剩余治理面声明。
核心：`datetime.utcnow()`（3.12+ 弃用）执行链清零；ntp_sync 语义不变保类型；
smart_order_router ts 显式 UTC 标注；algo_engine 切片建议时间改北京时间；hedge_rebalancer 审计戳转 aware UTC 保 `Z` 同形。

## R4 修复（HTTP 出口白名单）

`15_每日工作流/llm_client.py` 6 处 `urllib.request.urlopen`/`opener.open` → 统一出口
`_open_llm_request()`：scheme(http/https) + host 前缀白名单（`_ALLOWED_LLM_BASES` 由
DEEPSEEK/GLM/HY3/QIANFAN/DOUBAO_SPEED/OLLAMA 6 个 BASE_URL 常量派生）+ 无代理 opener（MC5）。
校验失败抛 ValueError（调用方捕获降级），bandit B404/B310 nosec 只留在受白名单保护的出口处。
jinja2 项 = 报告误报（全仓无 jinja2 使用，bandit 扫描范围可能含已删除文件）。

## R7 修复

`ms_strategy/src/risk/stress_tester.py`：`run_monte_carlo(..., seed: int|None=42)` 默认固定种子 + 局部
`np.random.default_rng(seed)`（3 处 np.random.* → rng.*）；`run_all(..., mc_seed=42)` 透传。
默认 42 保路径可复现，传 None 退化随机。既有测试仅断 key 不断数值 → 零破坏。

## 遗留（建议后续排期）

1. `.tmp_pip`（pip/pytest 临时目录，gitignore 已忽略）：磁盘清理命令触发 IDE 审批被拦，
   请手动 `rmdir /s /q .tmp_pip`（28 仓根），安全无密钥。
2. R5 豁免表 review-by 重审机制、每周全量 lint 快照：建议排入工程债务板，非阻断。
3. R6 God 文件拆包（统一入口 2701 行）：建议 Sprint 级任务，与 T3.6 收敛节奏同排。
4. 全库其余 ~480 处 utcnow（非执行链）：按 timezone-convention 渐进替换（模块 touched 时先消弃用）。
