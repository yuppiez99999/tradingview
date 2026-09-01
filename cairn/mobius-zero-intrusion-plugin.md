# Mobius 零侵入外挂：工程实践与 verify-and-gate 收口（2026-08-29）

> 一句话：把一次性 `mobius_addon/` 外挂经验沉淀为可复用的「零侵入集成」模式，并把本次收口作为「完成声明≠完成」第 E 实例（实证值 + 复现命令）。
> 关联：`cairn/completion-claim-vs-actual-state-20260829.md` §4.1 实例 E；`mobius_addon/README.md` 是用法层，本文是设计/踩坑/收口层，互补不重复。

## 0. 背景与目标

- **mobius_addon 是什么**：借鉴 mobius-system 的 Auto Research 多智能体编排、会话追溯检索、知识沉淀理念，以**纯外挂**方式集成到 v8.4 量化系统，作为论文调研与编码决策的效率工具（论文调研 / 文件导入 / 研报决策 / 追溯检索 / recipe 库）。
- **零侵入铁律**：只新增 `mobius_addon/` 目录；只读 import 现有模块（`utils/glm5_client.py`）、只读扫描现有文件（`config/positions.json` / `reports/` / `utils/alpha_factor/`）；删除目录即完全卸载，主链路零风险。

## 1. 设计决策（ADR 摘要）

### ADR-1 零侵入外挂架构

- **决策**：所有产物写入 `mobius_addon/output/` 与 `index.db`；不注册 `cli/dispatcher`、不改 `config`、不部署 Mobius 本体；只读 import 主系统、只读扫描主系统。
- **理由**：可插拔、可审计、主链路零回归风险。
- **后果**：外挂能力受限于主系统「只读可见」的接口；任何需要写主系统状态的功能（如下单、改 positions.json、调 `automated_execution_system.py`）一律不做，决策卡 `verdict` 仅供人参考。

### ADR-2 fail-open LLM 集成

- **决策**：`llm.py` 后端优先级 = 直接 OpenAI 兼容端点（环境变量配置）→ 主系统 GLM-5（`utils.glm5_client.quick_chat` 只读 import）→ 全部不可用返回 `None`，调用方降级为规则模板。所有异常捕获，绝不抛出到调用方。
- **理由**：本环境 GLM-5 实际走 `LiteLLMRouter` 但底层 `llm_client` 模块缺失导致透传失败（`No module named 'llm_client'`），直接端点常未配置；任何「硬依赖 LLM」都会让外挂崩溃。
- **实证**：本环境跑 `--report` / `--file`，GLM-5 透传失败 → 自动降级规则提取，决策卡/调研卡仍落盘（verdict=neutral，可复现性标注「LLM 不可用, 已降级」），外挂未崩。

### ADR-3 中文检索：全文子串 AND 而非分词倒排

- **决策**：`search/indexer.py` 对中文用「全文子串 AND 匹配」（`执行链` AND `断链`），不用分词倒排。
- **理由**：连续中文被切成一个 token 会导致「执行链」「断链」查不到；子串 AND 简单可靠。
- **实证**：`search_cli.py --kw 执行链 断链 --rebuild` → **命中 6 个文件**。

### ADR-4 SystemJudge 信号校验返回结构化 (status, msg)

- **决策**：`_signal_check` 返回 `(status, message)`，`status ∈ {agree, conflict, skip}`，不依赖中文字串匹配。
- **理由**：曾因「信号一致性校验」含「一致」二字误判 `verdict=agree`；结构化枚举避免中文字串巧合命中。
- **实证**：`tests/test_system_judge_signal.py` 锁定该行为。

### ADR-5 CLI 输出用 logging 而非 print

- **决策**：外挂脚本输出走 `logging`，不用裸 `print()`。
- **理由**：ruff T201 仅对 P0/特定路径豁免，外挂不在豁免列表且不可改 `ruff.toml`；logging 既合规又免 T201 拦截（报告期内核是用 `print` 触发了门禁，反向证明这条）。

### ADR-6 env 自设仅当未设置

- **决策**：`_common.ensure_env()` 仅对未设置的键补默认值（`NO_PROXY`/`PYTHONIOENCODING`/`OPENBLAS_NUM_THREADS` 等），不改系统全局配置。
- **理由**：避免副作用污染主系统运行环境。

## 2. 踩坑教训

- **Py3.8 泛型下标坑**：`cast(list[...])/dict[...]` 在 3.8 运行时抛 `TypeError: 'type' object is not subscriptable`；外挂统一 `from __future__ import annotations` 把注解字符串化规避（type hints 如 `dict[str, Any]` 安全；仅 `cast()` 实参与运行时求值处需 `List/Dict` 或去 cast）。`recipes/py38_generic_subscript.md` 已沉淀。
- **llm_client 透传失败**：`GLM5Client` 可初始化但底层 `llm_client` 模块缺失 → 透传失败；外挂必须在 `glm5_quick_chat` 外包 `try/except` 返回 default，且调用方再判空降级规则模板（**双层 fail-open**）。
- **PDF 解析库缺失**：`pdfplumber`/`PyPDF2` 均未安装时，`--report` 真实 PDF 会 `RuntimeError`；`file_loader` 已做 `pdfplumber → PyPDF2 → 提示转 txt/md` 三级 fail-open。演示用 txt 走同一 `load_text → extract_report → judge` 路径，证明 PDF 缺失也不崩。
- **sys.path namespace 劫持**：外挂只读 import 主系统时 `sys.path.insert(0, root)` 可能把同名顶层包（如 `tests`）劫持到主系统，需谨慎；`recipes/syspath_namespace_hijack.md` 已沉淀。
- **规则提取能力边界**：`_rule_extract` 仅正则抓 6 位代码，不解析中文评级（演示中「买入」被记为 neutral）——这是规则降级的已知限制；决策卡明确标注 `verdict` 来自规则而非 GLM-5，供人复核，**不伪装成模型判断**。

## 3. verify-and-gate 收口（「完成声明≠完成」第 E 实例）

### 3.1 门禁三连（实证值，非模板）

| 维度 | 命令 | 结果 |
|---|---|---|
| 工程 (ruff) | `ruff check mobius_addon` | **All checks passed!** |
| 格式 (black) | `black --check mobius_addon` | 22 files would be left unchanged（exit 0；py314 vs py315 target 警告无害） |
| 语义 (pytest) | `pytest mobius_addon/tests -q` | **26 passed in 2.76s** |

### 3.2 git 零侵入核验

- `git status --short` → 仅 `?? mobius_addon/`（未跟踪的新增外挂目录）。
- 主链路零现有文件改动；既有 ` m external/airllm_src` 是 gitlink（用户决策保留），非本工作引入，外挂未触碰。
- 复现：`cd 28-终极量化交易系统8.4; git status --short mobius_addon` → `?? mobius_addon/`。

### 3.3 三演示实证（真实输出，含 fail-open）

1. **检索命中**（本地确定性）：`python mobius_addon/search_cli.py --kw 执行链 断链 --rebuild` → **命中 6 个文件**（构建 `index.db` 成功）。
2. **研报→决策卡**：`python mobius_addon/research_cli.py --report <txt研报>` → 落盘 `decision_card_600519.md/json`，`verdict=neutral`，四维 judge（position/signal/risk/glm5）全部跑通、GLM-5 缺失项优雅跳过。
   - 注：真实 PDF 需可选 `pdfplumber`/`PyPDF2`（本环境均缺失）；演示用 txt 走同一代码路径，`file_loader` 三级 fail-open 保证 PDF 缺失也不崩。
3. **联网调研卡**：`python mobius_addon/research_cli.py --topic "low volatility anomaly factor" --max-papers 2` → 本沙箱外网受限（arXiv 连接被重置 / Semantic Scholar 429 限流），检索器优雅降级为 **0 篇、未崩溃**——fail-open 设计实证。联网 + GLM-5 可用环境会产出调研卡（代码路径一致，仅数据源不同）。

### 3.4 复现命令（一站式）

```bash
cd 28-终极量化交易系统8.4

# 门禁三连
ruff check mobius_addon && black --check mobius_addon && pytest mobius_addon/tests -q

# 零侵入核验
git status --short mobius_addon   # 期望: ?? mobius_addon/

# 三演示
python mobius_addon/search_cli.py --kw 执行链 断链 --rebuild
python mobius_addon/research_cli.py --report mobius_addon/output/_demo_report_600519.txt
python mobius_addon/research_cli.py --topic "low volatility anomaly factor" --max-papers 2
```

## 4. 防复发要点（落到本工作的 4 条）

1. **完成三连同轮必跑**（ruff + black + pytest），「26 用例全绿」≠ 完成。
2. **git 隔离核验**：声明「零侵入」必须能指到 `git status --short mobius_addon` 的 `??` 单一结果，并排除既有 gitlink/无关改动。
3. **演示要真跑**：三演示均有真实输出（含 fail-open 降级），不写「已验证」而不附命令与结果。
4. **规则降级不伪装**：LLM 缺失时决策卡/调研卡明确标注 `verdict` 来源，供人复核。

## 5. 相关文档

- [完成声明≠完成：三类状态失真与防复发](completion-claim-vs-actual-state-20260829.md) §4.1 实例 E
- [Mobius 外挂 README](../mobius_addon/README.md)（用法层）
- `mobius_addon/recipes/` 下 14 条实战教训：`py38_generic_subscript` / `syspath_namespace_hijack` / `execution_chain_break` / `openblas_oom` / `gbk_console_crash` / `p0_print_gate` / `dry_run_tca_trap` / `data_integrity_eod` 等
