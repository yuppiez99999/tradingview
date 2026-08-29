---
type: review_report
status: completed
authoring_mode: ai_generated
created: 2026-08-17
updated: 2026-08-17
contains: open-code-review, bug-pattern, root-cause, fix-discipline, new-code-audit
related:
  - cairn/LOG.md
  - cairn/exception-handling-standards.md
  - 代码质量修复计划_20260817.md
---

# 新代码审查 bug 模式与根因（2026-08-17）

> 使用 ocr v1.9.0 委托模式审查 54 个未提交文件（+15973 行），发现 17 个新 bug。
> 本文沉淀 **7 大 bug 根因模式**（可跨项目复用）+ **3 条修复规约** + **ocr 委托模式用法**。
> 修复清单见 `代码质量修复计划_20260817.md`，异常处理规约补充见 `exception-handling-standards.md` §11。

## 一、ocr 委托模式用法（绕过 LLM 限流）

当 ocr 的 LLM provider 限流（如智谱 BigModel 429）时，用委托模式让当前 AI agent 执行评审：

```bash
ocr delegate preview                    # 预览可审查的文件列表
ocr delegate rule <files...>            # 获取审查规则（工业级标准）
ocr scan --preview                      # 预览全量扫描文件清单
```

ocr 负责文件筛选 + 规则解析，AI agent 负责逐行精读。规则覆盖：死代码、可变默认参数、边界处理、错误处理、资源管理、性能、并发、安全敏感代码。

## 二、7 大 bug 根因模式

### 模式 1：错误处理路径从未被执行（潜伏型，30%）

**特征**：bug 位于 `except`/`finally`/降级路径，平时不触发、测试不覆盖，潜伏到生产真抛异常时才暴露。错误处理路径自身的 bug 会**放大**原始错误。

**案例**：NEW-7（`rag.close()` 在 try 内，异常时不执行 → 资源泄漏）；08-16 NB-1（告警通道本应降级，却因自身 TypeError 导致下单链路崩溃）。

**对策**：每条 `except` 路径必须有单测（`pytest.raises` + mock 触发异常）；CI 加 mutation testing（`mutmut`）验证错误处理路径被覆盖。

### 模式 2：研究/POC 代码直接上生产（工程缺失型，25%）

**特征**：研究员关注"能跑出结果"，用无状态函数、随机采样、数值微分等便利手段。生产场景的调用频率/数据规模/资源约束与 POC 完全不同。

**案例**：NEW-1（`predict` 每次重新加载 4bit 模型，POC 调 1 次没问题，生产调 200 次就 OOM）；NEW-3（随机标签不均衡，POC 无所谓，生产训练偏差影响实盘）；NEW-5（无解析梯度，POC 小规模 OK，生产 200 资产 × 1000 迭代很慢）。

**对策**：研究代码上生产前必须过"工程化评审"门禁：① 重资源对象（模型/DB 连接）加载次数 ≤ 1；② 训练数据类别均衡；③ 优化器有解析梯度或合理步长；④ 推理有 batch + 内存管理。

### 模式 3：except 元组收窄不核对 raise 面（收窄型，15%）

**特征**：BLE001 精确化把 `except Exception` 收窄为元组时，只列"预期"异常，漏了 try 块内实际会抛的类型。

**案例**：08-16 NB-1（漏 TypeError）、NB-2（漏 ImportError）、NB-6（漏 RuntimeError）。

**对策**：收窄操作必须加一步"列出 try 块内每个调用的全部 raise 面"；详见 `exception-handling-standards.md` §11。

### 模式 4：数值/安全规范缺失（规范缺失型，15%）

**特征**：浮点数零除用 `>0` 而非 `>eps` 导致放大误差；`html.escape` 用在 JS 上下文转义不全。都是**规范缺失**而非逻辑错误。

**案例**：NEW-4（`if m2 > 0` → m2 极小时 `std**3` 放大误差到 1e+10）；NEW-8（`html.escape` 不转义反斜杠，JS 字符串注入）。

**对策**：建立数值规范——除以标准差高次幂必须用 eps 阈值；建立安全规范——禁手拼 JS/SQL/Shell 字符串，统一用 `json.dumps`/参数化查询/`subprocess` 列表参数。

### 模式 5：封装/便利性妥协（便利型，10%）

**特征**：`engine._sources` 比 `engine.has_source()` 短就直接访问私有属性，内部重构后静默失效。

**案例**：NEW-6（4 处 `engine._sources` 访问）；NEW-12（`register` 无幂等保护，重复调用重复注册）。

**对策**：私有属性以 `_` 开头是约定，调用方不应直接访问；公共 API 必须提供查询方法；重复操作必须幂等。

### 模式 6：类型检查被核心文件绕过（债务累积型，5%）

**特征**：`mypy.ini` 中 13 个核心文件 `ignore_errors=True`，类型检查被完全绕过。这些正是 bug 高发区。

**案例**：NB-3（`cast` 未导入）、NB-4（`logging` 未导入）本可在编译期被 mypy 发现。

**对策**：渐进收紧——每周移除 1-2 个文件的 `ignore_errors`，修复暴露的类型错误，直到全部启用 strict。

### 模式 7：测试只覆盖 happy path（覆盖缺失型）

**特征**：108 个失败测试长期被"分批子集运行"掩盖，全量门禁未真正全绿。except 分支从未被执行所以潜伏。

**对策**：门禁改为全量跑而非分批子集；每条 except 路径必须有单测。

## 三、3 条修复规约

### 规约 1：except 路径必有单测

每条 `except`/`finally`/降级路径必须有对应的单测，用 `pytest.raises` + mock 触发异常验证降级行为。

### 规约 2：except 元组收窄必核对 raise 面

收窄 `except Exception` 为元组时，必须列出 try 块内**每个调用**的全部 raise 面。详见 `exception-handling-standards.md` §11。

### 规约 3：研究代码上线必过工程化评审

研究/POC 代码合入生产前必须过门禁：模型加载次数 ≤ 1、类别均衡、解析梯度、batch 推理 + 内存管理。

## 四、17 个 bug 清单

| 编号 | 级别 | 文件 | 问题 | 修复 |
|------|------|------|------|------|
| NEW-1 | High | finetune_sentiment_model.py | predict 每次重新加载模型 | SentimentPredictor + lru_cache |
| NEW-2 | Med | finetune_pipeline.py | evaluate 不校验长度一致 | 加 ValueError + 空集短路 |
| NEW-3 | Med | finetune_pipeline.py | _load_synthetic 标签不均衡 | 分层均衡采样 |
| NEW-4 | Med | risk_budget_optimizer.py | MVSK if m2 > 0 数值不稳定 | _M2_EPS = 1e-12 |
| NEW-5 | Med | risk_budget_optimizer.py | MVSK 无解析梯度 | 标记为债务（后续补） |
| NEW-6 | Med | signal_fusion.py | 访问 _sources 私有属性 | has_source() 公共方法 |
| NEW-7 | Med | ai_coordinator.py | rag.close() 资源泄漏 | try/finally |
| NEW-8 | Med | html_chart_generator.py | JS 字符串注入 | json.dumps |
| NEW-9 | Med | finetune_sentiment_model.py | 无批量推理+GPU管理 | predict_batch + close |
| NEW-10 | Low | finetune_sentiment_model.py | temperature+do_sample 冲突 | 移除冗余 temperature |
| NEW-11 | Low | finetune_pipeline.py | 错别字"验证集B集" | 修正 |
| NEW-12 | Low | signal_fusion.py | register 无幂等保护 | 先检查 has_source |
| NEW-13 | Low | ai_coordinator.py | shadow 比对异常静默 | debug→warning |
| NEW-14 | Low | risk_budget_optimizer.py | MVSK 注释易误读 | 修正注释 |
| NEW-15 | Low | html_chart_generator.py | CDN 离线依赖 | 标记为债务 |
| NEW-16 | Low | strategy_ideation.py | hashlib.md5 无 usedforsecurity | 加参数 |
| NEW-17 | Low | console_encoding.py | subprocess shell=True | 保留 nosec（合理） |

## 五、可复用检查清单

审查新代码时逐项核对：

- [ ] except 元组是否覆盖 try 内全部 raise 面？（模式 3）
- [ ] except/finally 路径是否有单测？（模式 1）
- [ ] 重资源对象（模型/DB/文件句柄）是否加载一次复用？是否用 with/try-finally？（模式 1,2）
- [ ] 训练数据是否类别均衡？（模式 2）
- [ ] 除以标准差高次幂是否用 eps 阈值？（模式 4）
- [ ] JS/SQL/Shell 字符串是否用 json.dumps/参数化/列表参数？（模式 4）
- [ ] 是否访问了 `_` 开头的私有属性？（模式 5）
- [ ] 重复操作是否幂等？（模式 5）
- [ ] 优化器是否有解析梯度或合理步长？（模式 2）
- [ ] 推理是否有 batch + 内存管理？（模式 2）

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [异常处理规约 (Exception Handling Standards)](exception-handling-standards.md) (相似度 15%)
- [测试债清零经验沉淀 (2026-08-16)](test-debt-clearance-20260816.md) (相似度 10%)
- [代码质量审查 Wave6（2026-08-06）— 对冲/执行/管道/数据模块 + 待复核项复核](code-quality-review-wave6-20260806.md) (相似度 9%)
- [GLM 4.5-air LLM 驱动代码审查方法论（open-code-review + GLM 4.5-air）](code-review-glm45-llm-scan.md) (相似度 8%)
- [OCR 扫描代码评论落地（2026-08-11）](ocr-scan-comments-20260811.md) (相似度 8%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
