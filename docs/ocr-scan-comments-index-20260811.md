# OCR 扫描代码评论索引（2026-08-11）

> 本索引汇总所有通过 OCR 扫描代码中文注释/文档字符串后生成的审查评论，便于快速定位和跟踪修复。

## 扫描记录

| 扫描日期 | 目标文件 | 分块标识 | 评论数 | 状态 |
|---------|---------|---------|--------|------|
| 2026-07-28 | `daily_trading_workflow.py` | `chunk2_dailyworkflow_core.py` | 19 | 已落地 |

## 严重程度分布

- **critical**: 1 条（5.3%）
- **high**: 6 条（31.6%）
- **medium**: 11 条（57.9%）
- **performance**: 1 条（5.3%）

## 热点文件

- `daily_trading_workflow.py`：单文件 19 条评论，每个主要方法至少包含一个 high 级别问题。

## 跨领域关注

1. **不安全内部属性访问**：多处直接访问其他类的私有属性
2. **可变数据未拷贝修改**：`order.pop("_meta")` 修改共享引用
3. **日志性能反模式**：高频循环中 f-string 日志
4. **导入验证缺失**：局部 import 导致运行时才暴露缺失依赖
5. **路径解析脆弱**：`__file__` + `parents[1]` 在特殊加载场景下失效
6. **方向映射信息丢失**：`side` 映射为 BUY/SELL 丢失原始交易意图

## 详细评论

详见：`cairn/ocr-scan-comments-20260811.md`

## 修复优先级建议

1. **P0（立即修复）**：
   - critical: `phase_hedge_fund` 返回类型违约
   - high: `_run_single_agent_shadow` 信号缓存读取错误
   - high: 环境检测字符串与枚举不一致
   - high: 期货行情缺失时使用确定性模拟数据
   - high: 风控状态加载后未持久化

2. **P1（本周修复）**：
   - high: 持仓策略归属字段名不匹配
   - high: `wait_fill` 返回 None 时空指针访问
   - high: BL 优化使用简化对角协方差矩阵
   - high: 量化中性策略回撤状态未持久化
   - high: 对冲计划键名不匹配

3. **P2（下个迭代）**：
   - medium: 错误冲突日志循环体为空
   - medium: 交易方向映射丢失原始意图
   - medium: 熔断强制加对冲可能重复记录
   - medium: `order.pop("_meta")` 破坏性修改共享状态
   - medium: 期权预算计算导致 LIMIT 价格极小
   - medium: `numpy` 局部导入导致静默降级
   - medium: `__file__` 路径解析脆弱
   - medium: 紧耦合访问私有属性
   - performance: 高频循环中 f-string 日志性能损耗
