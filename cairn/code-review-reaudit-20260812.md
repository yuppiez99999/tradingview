# 代码审查复审（二次 · 2026-08-12）落地与治理

> 专题文档：对 `docs/代码审查复审报告_20260812_二次.md` 的 R10/R11/R12 三项发现执行落地 + 与本次会话动作的校准。
> 对应 LOG 指针：2026-08-12 · 复审 R10/R11/R12 落地。

## 0. 复审报告结论摘要

- 首轮 P0 阻断项 R1（CI 破损）/ R2（工作区未收敛）/ R3（pre-commit 误报）**已全部真实修复**（与本会话 CI 三项修复一致）。
- 工业级判据：`9 PASS / 2 WARN / 1 FAIL` → `11 PASS / 1 WARN / 0 FAIL`。
- 新增发现：R10（fail-safe 宽捕获系统性债，中危）、R11（llm_rate_limiter 无直测，低危）、R12（99 未跟踪文件分流，低危）。

## 1. R12 校准：99 未跟踪非垃圾，已分批提交收敛

报告 R12 假设"99 未跟踪 = 合法新代码(待提交) + 临时副产物"。**校准结论（本会话实测）**：
- 99 个文件中**绝大多数是有效新增代码/测试**（Wave6 因子、contracts、execution 闭环、fineng 测试、AI hedge fund 模块、workflow 拆分），非垃圾。
- 已通过 5 批提交（129 文件）全部收敛，**工作区未跟踪归零**，零有效工作丢失。
- 报告建议的 `.gitignore` 补充（`_*.txt` / `_tmp_*.py` / `~$*.xlsx`）部分已被既有规则覆盖，但 **Excel 锁文件 `~$*.xlsx` 与 CI 转储产物此前缺覆盖** → 已在 `.gitignore` 补齐（A 项）。

## 2. R10 落地：T6 工程债务指标登记（B 项）

**根因**：`except Exception: # noqa: BLE001 # fail-safe` 宽捕获泛滥（signal_fusion 16×、glm5 10+×、debate_layer 6×），真实错误被静默吞掉，注释标"待后续精确化"但无排期。

**治理动作**：
- 在 `scripts/engineering_debt_gate.py` 新增 **T6 fail-safe 宽捕获指标**：扫描 `utils/ scripts/ quant_modules/ ai_decision/` 下 `except Exception` + `# fail-safe`/`# noqa: BLE001` 站点，超阈值（默认 30）判 YELLOW（不 RED 阻断，属可维护性债）。
- 实测首跑：**400 处**（远超 30），债务等级 YELLOW，与报告"已承认未治理"一致。
- 治理排期建议（登记到技术债清单）：
  - 短期（1-2 周）：至少对"数据源获取 / LLM 调用"类失败**记录告警日志**而非静默（已有 `cairn/exception-handling-standards.md` §2.3/§3.3 可参照）。
  - 中期：对 signal_fusion / glm5 / debate_layer 的 32 处精确化（按调用场景列举具体异常类型），每处登记"精确化 TODO + 负责人 + 期限"。
  - 长期：CI 对"无理由宽捕获"加评论门槛（ruff BLE001 + 自定义规则）。
- 参考规约：`cairn/exception-handling-standards.md`（§2.3 LLM Provider 调用异常类型、§3.3 捕获后必须反馈熔断器）。

## 3. R11 落地：llm_rate_limiter 直测（C 项）

新增 `tests/unit/test_llm_rate_limiter.py`（**12 测试全过**），覆盖：
- 令牌桶：容量内获取成功、枯竭超时返回 False、refill 速率数学、阻塞至 refill 时间窗
- TTLCache：miss/hit、过期淘汰、LRU 容量淘汰
- 指数退避：`max_delay` 封顶（序列 1/2/3 不超 3.0）、重试次数正确
- 全局单例：双检锁只初始化一次
- CallStats：`max(1,...)` 除零保护（successful/total=0 时返回 0.0）
- RateLimitedLLMCaller 集成：缓存命中不再调 fn、速率限制超时抛 RuntimeError

代码片段（令牌耗尽超时）：
```python
limiter = TokenBucketRateLimiter(max_tokens=2, refill_rate=1.0)
assert limiter.acquire(timeout=0.1) is True   # 1
assert limiter.acquire(timeout=0.1) is True   # 2
assert limiter.acquire(timeout=0.1) is False  # 枯竭, 0.1s 内 refill_rate=1.0 补不够
```

## 4. 门禁验证快照

| 检查 | 结果 |
|---|---|
| `engineering_debt_gate.py` T6 | ✅ YELLOW（400 处 fail-safe，符合 R10 登记） |
| `pytest tests/unit/test_llm_rate_limiter.py` | ✅ 12 passed |
| `ruff --select F` (新文件) | ✅ All checks passed（顺手清理 2 处预存 F401 + 1 处 F841） |
| `py_compile` (新测试 + 门禁脚本) | ✅ OK |

## 5. 下次复审定点（报告建议 + 本次补充）

报告建议：`memory_reflection.py`（594 行未深读）、`utils/alpha_factor/`、`data_pipeline/` 分层路径。
补充：R10 精确化进度应纳入常规 PR 审查清单（新增 `except Exception` 必须带 `# noqa` 理由或登记技术债期限）。

## 6. 可复用资产

- 新增：`tests/unit/test_llm_rate_limiter.py`、`scripts/engineering_debt_gate.py::T6`
- 修改：`.gitignore`（R12 锁文件/转储补充）、`scripts/engineering_debt_gate.py`（T6 + 清理预存 F 类）
- 关联：`cairn/exception-handling-standards.md`（R10 精确化参照）、`docs/代码审查复审报告_20260812_二次.md`（源报告）
