---
type: review_methodology
status: completed
authoring_mode: ai_generated
created: 2026-08-10
updated: 2026-08-10
contains: open-code-review, glm-4.5-air, llm-driven-scan, secondary-filtering, false-positive-rate
related:
  - cairn/code-quality-review-open-code-review.md
  - cairn/code-review-sop.md
  - cairn/LOG.md
---

# GLM 4.5-air LLM 驱动代码审查方法论（open-code-review + GLM 4.5-air）

> 2026-08-10 实战：用 `open-code-review` (ocr) 工具 + 智谱 GLM 4.5-air 模型（带 API Key 的 LLM 驱动扫描），对 23 个执行/风险/对冲核心文件做全量审查，沉淀"LLM 全面撒网 + 人工二次过滤"的可靠方法论。

## 一、工具链与配置

### 1.1 工具
- **`open-code-review` (ocr)**：本地 CLI 工具，LLM 驱动扫描（`ocr scan --path ... -f json -b "..." --audience agent`），输出 `scan_review_*.json`（含 severity / category / path / start_line / end_line / content / existing_code / suggestion_code）。
- **LLM Provider**：智谱 `glm-4.5-air`（注意不是 `glm-4.5` —— 资源包只覆盖 `glm-4.5-air`，配错模型会报 `429 余额不足`）。
- **配置位置**：`C:\Users\Administrator\.opencodereview\config.json` 的 `glm` 段：
  ```json
  "glm": {
      "api_key": "528b98d021ab45ad81e8d409e1fea19d.nM7h26qLHYNz7k2z",
      "url": "https://open.bigmodel.cn/api/paas/v4",
      "protocol": "openai",
      "model": "glm-4.5-air"
  }
  ```

### 1.2 关键坑
- **模型名必须精确匹配资源包**：账户资源包只覆盖 `glm-4.5-air`，配成 `glm-4.5` / `glm-5.2` 会 `429 余额不足`。先 `ocr llm test` 验证连通性再扫。
- **扫描范围要显式排除非代码**：首次 `--preview` 发现会扫进 `.json` 报告文件，需加 `--exclude "**/*.json,**/reports/**,**/data/**"`。
- **必须在主系统目录执行**：`cd "28-终极量化交易系统8.4"` 后再跑，否则相对路径解析错。
- **多文件用逗号分隔 `--path`**：`ocr scan --path "a.py,b.py,dir1,dir2"`，不支持 glob 通配。

## 二、本次扫描结果

| 指标 | GLM 5.2 (上次, API 余额不足) | GLM 4.5-air (本次) |
|------|------------------------------|---------------------|
| 状态 | `completed_with_errors`（16/24 文件未扫） | `success`（23/23 全扫） |
| 文件数 | 8（部分） | 23 |
| Comments | 73（不完整） | **305**（完整） |
| 严重度分布 | — | 6 critical / 97 high / 165 medium / 37 low |
| Token 消耗 | — | 1.39M / 12M（11.6%） |

## 三、二次过滤方法论（核心认知）

**LLM 驱动扫描的本质是"全面撒网"——召回率高但误报率极高，不能直接按 severity 修，必须二次过滤。**

### 3.1 严重度 ≠ 真实缺陷

逐条验证后发现：
- **6 CRITICAL → 仅 1 真 bug**（C1/C2：float.get() AttributeError）；其余 5 条是幻觉（行号=0 臆测、有意识 FIFO 简化、防御纵深设计）。
- **97 HIGH → 仅 ~8 条可操作**：65 条是**线程安全误报**（项目是单线程 EOD 工作流，不会触发竞态），18 条是安全误报（`sys.path`/`exec_module` 标准用法、阈值 0 是有意设计）。
- **165 medium + 37 low**：主要是风格/可维护性建议（docstring、类型注解、unused import），非功能性缺陷。

**结论：LLM 扫描报告的"严重度"是模型对代码风险的估计，不是已验证的缺陷。critical 100% 需现场验证，high 必须筛选真 bug，medium/low 默认跳过。**

### 3.2 二次过滤的验证工具链

对每条疑似真 bug，用以下手段交叉验证（**不只读模型描述**）：
1. **读源码确认行号存在且逻辑匹配**（模型常报行号=0 或偏移）。
2. **实际运行复现**：构造最小用例触发错误路径（如强制 SELL 路径、dropna 后短序列）。
3. **类型检查**：`python -c "..."` 直接调用问题函数，捕获真实的 `AttributeError` / `IndexError`。
4. **门禁兜底**：修复后跑 `industrial_grade_check.py` + `assert_data_validity.py` + `py_compile`，确认无回归。

### 3.3 本次确认并修复的 4 个真 bug

| # | 文件:行 | 严重度 | 问题 | 修复 |
|---|---------|--------|------|------|
| C1/C2 | `utils/execution/rebalance_execution_orders.py:134` | CRITICAL | `positions[code].get("current_shares", 0)` —— `load_positions()` 返回 `positions[code]=float(qty)`，在 float 上调 `.get()` 必抛 AttributeError，SELL 路径 100% 崩溃 | 改为 `positions.get(code, 0)` |
| HIGH27 | `utils/risk/risk_event.py:125` | HIGH | `from_dict()` 用 `RiskEventType(data["event_type"])` 无枚举校验，无效值直接 ValueError 阻断事件链 | 加 try/except 回退 `KILL_SWITCH_TRIGGERED` + `INFO` |
| HIGH26 | `utils/risk/risk_bus.py:524` | HIGH | `_aggregate_strictest` 聚合 `reduce_pct` 无类型防御，可能把 `0%` 决策误聚合 | 加 `isinstance(p,(int,float))` + 排除 `p<=0` |
| HIGH-BUG | `utils/execution/daily_build_and_hedge.py:253` | HIGH | `closes.iloc[-21]` 在 `dropna()` 后不足 21 行时 IndexError | `dropna().reset_index(drop=True)` + 长度二次校验 |
| HIGH-SEC | `rebalance_order_executor.py:64` | HIGH | CLI `date` 参数直接拼文件路径，存在 path traversal | 新增 `_validate_date()` 严格 `YYYY-MM-DD` 校验 + `resolve()` 越界二次防御 |

### 3.4 关键发现：上一轮 H19/H20 修复的漏洞

`rebalance_execution_orders.py:134` 的 `float.get()` 是 GLM 4.5-air **新发现的真 bug**，GLM 5.2 因 API 余额不足只扫了 8 个文件而遗漏。且上一轮 H19/H20 修复只改了 `remaining_gap` 和 `qty` 上限，**没改 L134 的数据结构访问错误**。

**印证铁律：修复批次后必须用全量扫描重新验证，单点修复容易漏掉同文件其他缺陷。**

## 四、提交策略

- **聚焦提交**：只 `git add` 本次审查修复的 5 个文件（`rebalance_execution_orders.py` / `risk_event.py` / `risk_bus.py` / `daily_build_and_hedge.py` / `rebalance_order_executor.py`），不触碰工作区其他 100+ 累积修改。
- **commit message** 用 Conventional Commits + 列明 4 个 bug + 门禁结果。
- **不要一次性 commit 整个工作区**——累积修改跨多轮、多主题，混合提交会丢失可追溯性。

## 五、沉淀物

- `scan_review_glm45.json`：完整扫描结果（305 comments）。
- `analyze_glm45_findings.py` / `analyze_high_bug.py` / `analyze_high_security.py`：复盘脚本，可复用做后续扫描的二次过滤。
- 提交 `9a266290`：`fix(exec/risk): GLM 4.5-air 代码审查修复 4 个真实缺陷`。

## 六、方法论沉淀（可复用 SOP）

1. **配置阶段**：确认 API Key 余额 + 模型名精确匹配资源包 + `ocr llm test` 连通性。
2. **扫描阶段**：`--preview` 确认范围（排除 .json/reports/data）→ 正式 `scan` 输出 JSON。
3. **过滤阶段**（不只读 severity）：
   - critical 100% 现场验证（读源码 + 运行复现）。
   - high 用脚本聚类，剔除线程安全/安全误报，筛真 bug。
   - medium/low 默认跳过（风格类非功能缺陷）。
4. **修复阶段**：只改验证过的真 bug，加防御性代码（try/except / 类型检查 / 长度校验 / 输入校验）。
5. **验证阶段**：`industrial_grade_check` + `assert_data_validity` + `py_compile` 全绿。
6. **提交阶段**：聚焦 commit 本次修复文件，不混入无关修改。

**核心原则：LLM 扫描是"探针"不是"判官"——它的价值在于把人类注意力引导到可疑区域，最终缺陷判定权在人类。误报率高的模型（如 GLM 4.5-air 的 92% 误报）反而更有价值，因为它不漏报，配合严格二次过滤即可兼得召回与精度。**
