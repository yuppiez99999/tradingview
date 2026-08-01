# T02 Pylint broad-except 基线报告

- 生成时间: 2026-07-27
- broad-except 总数: 0
- 受影响文件数: 0

## 按文件统计

| 文件 | broad-except 数 |
|------|----------------|

## 修复策略

### P0 风控路径 (硬约束: 禁止 broad exception)
- utils/risk/* (风控核心)
- utils/execution/* (执行核心)
- utils/kill_switch.py (核心风控)

### P1 重要模块 (优先修复)
- utils/infra/* (基础设施)
- utils/data/* (数据层)

### 修复方式
- 改为具体异常类型 (KeyError/ValueError/ConnectionError 等)
- 风控路径: 必须明确异常类型 + 日志记录 + fail-closed
