---
type: review_methodology
status: completed
authoring_mode: ai_generated
created: 2026-08-10
updated: 2026-08-10
contains: agent-direct-review, fallback-when-llm-quota-exhausted, batch-B-C, zero-quota-dependency
related:
  - cairn/code-review-glm45-llm-scan.md
  - cairn/code-review-sop.md
  - cairn/LOG.md
---

# Agent 直接审查兜底方法论（外部 LLM 额度耗尽时）

> 2026-08-10 实战：ocr + GLM 4.5-air 批 A 跑通后，批次 B/C 改用 stepfun / GLM / DeepSeek 全部失败——根因是**所有外部 LLM 凭证额度归零**（GLM 429 余额不足、DeepSeek 402 Insufficient Balance）。此时选择「方案3」：由本 Agent 在会话内直接读取 13 个文件做静态分析，零额度依赖地产出缺陷清单。

## 一、触发条件与决策

| 信号 | 表现 |
|------|------|
| ocr 批量失败 | 批次 B（stepfun）、批次 B+C（deepseek）全部 `check your LLM configuration and API key` |
| 根因 | GLM z-ai/glm45air → 429；DeepSeek deepseek-chat → 402 Insufficient Balance；**全部凭证额度耗尽** |
| 排查要点 | `ocr llm test` 仍打 GLM URL 说明 active_provider 切换未生效；直接 Python 请求 DeepSeek `https://api.deepseek.com/v1` 拿到真实 HTTP 402，确认是额度非配置问题 |
| 决策 | 选方案3：Agent 直接审查（不依赖 ocr/外部 LLM），立即产出可用缺陷清单 |

**关键认知**：当所有外部 LLM 凭证额度归零时，**不要反复重试 ocr**——它永远因额度失败。直接切到 Agent 会话内审查，缺陷发现率虽不如 LLM 全面撒网（无跨文件图推理），但对**已明确的执行/风险/对冲模块**足够覆盖单文件静态缺陷，且**零额度、零延迟、可立即行动**。

## 二、Agent 直接审查的执行流程

1. **定位文件清单**：从 `docs/CODE_REVIEW_PLAN_GLM52_20260810.md` 取批次 B（6 文件 P0/P1 未扫）+ 批次 C（7 文件 P0/P1 续扫）精确路径。
2. **分批读取**：大文件（`automated_execution_system.py` 60000+ token、`daily_trade_executor.py` 1175 行）用 `read_file` 分块 + `search_content` 定位执行核心区，避免一次性超长上下文。
3. **静态审查维度**：
   - 执行闭环（是否"只生成不撮合"）、幂等去重、fail-open/fail-closed 分层
   - 异常保护（文件 IO / JSON 解析 / 除零 / 空值）
   - 废弃 API（如 `datetime.utcnow()` Python 3.12+ 移除）
   - 原子写、`full_code` vs 6位 code 键匹配、持仓同步有效性
4. **交叉验证**：对可疑点（如 `daily_trade_executor` 的 `inst["code"]` vs positions 键）读 `config/positions.json` 确认键格式，避免误报。
5. **产出报告**：写入 `docs/CODE_REVIEW_BATCH_BC_AGENT_20260810.md`，含缺陷汇总表 + 优先修复顺序 + 与先前 OCR 结论对照。

## 三、本次发现（13 文件, 批次 B 6 + 批次 C 7）

### 真实缺陷（已修复）
| ID | 文件 | 严重度 | 问题 | 修复 |
|----|------|--------|------|------|
| C6-1 | daily_build_and_hedge.py | P2 bug | 重复 `lines = []` 清空 serialize 结果，持仓快照只输出单条 | 删冗余赋值 |
| C7-1 | rebalance_execution_orders.py | P1 | `load_positions()` 无 try/except，文件损坏/缺失直接崩溃 | 加异常保护 + 空持仓降级 |
| C3-2 | broker_failover.py | P2 | `datetime.utcnow()` 废弃 | `datetime.now(timezone.utc)` |
| C5-1 | broker_adapters.py | P2 | 同上 | 同上 |

### 降级/暂不修（误判复核）
- **C3-1（原 P1 → 降级 INFO）**：`_do_failover` 锁外 connect 成功后锁内 active 被改会漏记——重读确认为**故意保守设计**（`return True` 代表切换目标已达成，调用方继续用新 active broker，不裸实盘），强行改动会引入回归。
- **未发现新执行断链**：G1/G2/G4 闭环、H14 崩溃缺陷均确认已修复（与 OCR 批次 A 结论一致）。

### 剩余 P2（未修，非阻断）
B5-1 加权均价 / B6-1 坏行容错 / B4-1 配置缓存 / C2-1 import os / C4-1 幂等去重 / C7-2 Path API。

## 四、方法对比：LLM 扫描 vs Agent 直接审查

| 维度 | ocr + GLM 4.5-air | Agent 直接审查 |
|------|-------------------|----------------|
| 触发条件 | 有额度凭证 | 零额度依赖 |
| 覆盖度 | 全量撒网 305 comments，跨文件图推理 | 单文件静态，需人工定位核心区 |
| 误报率 | ~92%（需二次过滤） | 低（直接读源码，但需交叉验证键格式防误报） |
| 速度 | 慢（1.39M token/批） | 快（直接读） |
| 交付物 | JSON + 二次过滤脚本 | Markdown 缺陷表 |
| 适用 | 全量未知缺陷探索 | 已知模块范围、额度耗尽兜底 |

**核心教训**：
1. **额度是 LLM 审查的硬阻塞**——先 `ocr llm test` 验证再扫，否则整批白跑。
2. **Agent 兜底不丢人**：当所有凭证额度归零，直接会话内审查 > 反复重试 ocr。
3. **缺陷严重度要复核**：LLM 报告 critical≠真 bug（批次 A 6 critical 仅 1 真）；Agent 报告也需读源码确认行号 + 交叉验证（如 positions 键格式）。
4. **单点修复后必须重扫/复看**：同文件易残留同类缺陷（批次 A 的 L134 即是上一轮 H19/H20 漏掉的同类）。
5. **废弃 API 是跨文件通病**：`datetime.utcnow()` 在 broker_failover/broker_adapters 多处出现，应一次性全量替换并加 ruff 规则防回归。

## 五、指针
- 审查报告：`docs/CODE_REVIEW_BATCH_BC_AGENT_20260810.md`
- 批次计划：`docs/CODE_REVIEW_PLAN_GLM52_20260810.md`
- 关联方法论：`cairn/code-review-glm45-llm-scan.md`（有额度时的 LLM 扫描法）
- 修复 commit：`d9dd7beb`（fix(exec): 批次B/C代码审查修复）

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [GLM 4.5-air LLM 驱动代码审查方法论（open-code-review + GLM 4.5-air）](code-review-glm45-llm-scan.md) (相似度 23%)
- [MMR 评审规约 (multi-model-review 集成协议)](mmr-review-protocol.md) (相似度 20%)
- [open-code-review 代码审查报告（核心模块）](code-quality-review-open-code-review.md) (相似度 15%)
- [代码审查 + 修复批次 标准作业流程 (SOP)](code-review-sop.md) (相似度 13%)
- [代码质量审查 Wave6（2026-08-06）— 对冲/执行/管道/数据模块 + 待复核项复核](code-quality-review-wave6-20260806.md) (相似度 12%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
