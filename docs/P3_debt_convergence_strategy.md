# P3 工具链债务渐进式收敛策略

生成日期：2026-08-07
关联：代码质量修复计划_20260807.md P3-1~P3-4

## 现状基线

| 项目 | 当前数量 | 目标 | 工具 |
|------|---------|------|------|
| ruff F821 未定义名称 | 9 (非核心路径) | 0 | ruff baseline 已建立 |
| ruff invalid-syntax | 5 (临时脚本) | 0 | ruff baseline 已建立 |
| ruff F541 f-string 误用 | 257 | 0 | 分批清零 |
| ruff F401 未用导入 | 445 | <50 | 分批清零 |
| ruff BLE001 宽泛 except | 667 | <100 | 分批清零 |
| ruff T201 print | 5266 | <500 | 分批清零 |
| mypy # type: ignore (无错误码) | 483 | 0 (全精确化) | 自动化脚本 |
| mypy unused-ignore | ~224 (daily_trade_executor 单文件) | 0 | mypy --warn-unused-ignores |
| print 调试语句 | 1541 | <200 | logging 替换 |

## P3-2 # type: ignore 精确化方案

### 问题
全项目 483 处 `# type: ignore` 无错误码，mypy 无法区分有效忽略与无效忽略。核心执行文件 `daily_trade_executor.py` 单文件 224 处 unused-ignore。

### 渐进式收敛步骤

1. **启用 warn-unused-ignores（已完成）**：mypy.ini 已配置，CI 中 mypy 会报告 unused-ignore
2. **移除 unused-ignore（第一批）**：运行 `mypy --warn-unused-ignores` 导出所有 unused-ignore 行号，批量删除这些 `# type: ignore` 注释
3. **精确化剩余 ignore（第二批）**：对每个有效的 `# type: ignore`，用 `mypy --show-error-codes` 获取具体错误码，替换为 `# type: ignore[具体错误码]`
4. **CI 门禁**：PR 新增的 `# type: ignore` 必须带错误码，否则 CI 拒绝

### 自动化脚本（建议）
```bash
# 步骤2: 批量移除 unused-ignore
.venv\Scripts\python.exe -m mypy --config-file mypy.ini --warn-unused-ignores <file>.py 2>&1 | grep "unused" | \
  awk -F: '{print $1 ":" $2}' | while read loc; do
    sed -i "${loc}d # type: ignore"  # 需精确匹配行内容
  done

# 步骤3: 精确化有效 ignore
.venv\Scripts\python.exe -m mypy --config-file mypy.ini --show-error-codes <file>.py
# 逐行对照错误码, 替换 # type: ignore → # type: ignore[错误码]
```

### 优先级
- **P3-2a（本周）**：核心执行文件 `daily_trade_executor.py`、`automated_execution_system.py`、`hedge_order_executor.py` 的 unused-ignore 清零
- **P3-2b（下周）**：全项目 unused-ignore 清零
- **P3-2c（月底）**：剩余有效 ignore 精确化为带错误码

## P3-3 print 收敛方案

### 问题
1541 处 print，Windows GBK 控制台下含 ¥ 等非 ASCII 符号时抛 UnicodeEncodeError。

### 渐进式收敛步骤

1. **立即修复（已完成）**：含 ¥ 符号的 print 已在历史修复中改为 RMB（参见 hedge_order_executor.py）
2. **高风险文件优先**：执行链文件（daily_trade_executor / automated_execution_system / hedge_order_executor）的 print 改 logging
3. **批量替换**：非执行链文件的 print 用脚本批量替换为 logger.info/debug
4. **CI 规则**：新增 print 在 ruff T201 规则下报警，PR 审查时要求改为 logging

### 优先级
- **P3-3a（本周）**：执行链三文件的 print → logging（约 80 处）
- **P3-3b（下周）**：utils/ 目录文件的 print → logging（约 400 处）
- **P3-3c（月底）**：剩余文件的 print → logging（约 1000 处）

## CI 门禁恢复

### ruff baseline 已建立
- 文件：`docs/ruff_baseline_20260807.json`（607KB，含 F821/F541/F401 错误快照）
- CI 规则：新错误（不在 baseline 中的）阻断 CI，baseline 中的已有错误渐进清零
- 清零顺序：F821(9) → invalid-syntax(5) → F541(257) → F401(445) → BLE001(667) → T201(5266)

### mypy baseline
- 文件：`docs/mypy_baseline_v9.2.txt`（已存在，1967 行）
- CI 规则：新增 mypy 错误阻断 CI，已有错误渐进清零
- 启用 `warn-unused-ignores` 后，unused-ignore 会自动报告

### bandit
- 3 处 High 误报已标注 `# nosec`（P3-4 完成）
- CI 中 bandit `-lll` 可正常通过
