# Mobius 零侵入外挂

借鉴 [mobius-system/mobius](https://github.com/mobius-system/mobius) 的 Auto Research 多智能体编排、
会话追溯检索、知识沉淀理念，以**纯外挂**方式集成到量化系统，作为论文调研与编码决策的效率工具。

> **零侵入铁律**：本目录 (`mobius_addon/`) 为独立外挂，**不修改任何现有代码/配置**。
> 仅新增本目录；可只读 import 现有模块（`utils/glm5_client.py`）、只读扫描现有文件。
> 删除本目录即可完全卸载，主链路零风险。

## 功能

1. **联网论文调研** `--topic`：arXiv + Semantic Scholar 检索 → GLM-5 提取方法 → 调研卡片 + 因子库对照。
2. **本地文件导入** `--file`：txt/md/pdf 直接读取原文 → 产出分析卡片（纯本地，不联网）。
3. **机构研报决策** `--report`：研报 PDF 导入 → 自动总结 → 系统判断闭环（持仓冲突 / 信号一致性 / 风控规则 / GLM-5 打分）→ 决策卡。
4. **追溯检索** `search_cli.py`：只读扫描 logs/memory/research/cairn/second-brain/recipes 建索引，关键词检索。
5. **编码排错 recipe 库** `recipes/`：实战教训结构化沉淀，纳入检索。

## 用法

```bash
cd 28-终极量化交易系统8.4

# 联网论文调研
python mobius_addon/research_cli.py --topic "low volatility anomaly" --max-papers 10

# 本地论文/笔记导入
python mobius_addon/research_cli.py --file "C:/papers/xxx.pdf"

# 机构研报 -> 系统判断决策卡
python mobius_addon/research_cli.py --report "C:/研报/中信证券_宁德时代.pdf"

# 追溯检索（首次自动建索引）
python mobius_addon/search_cli.py --kw 执行链 断链 --rebuild
```

## 产物位置（全部在外挂目录内）

```
mobius_addon/output/research_cards/   调研卡 (md + json)
mobius_addon/output/decision_cards/   研报决策卡 (md + json)
mobius_addon/index.db                 检索索引 (SQLite)
```

## 决策边界

研报决策卡的 `verdict` ∈ {agree, conflict, neutral, position_conflict} **仅供人决策参考**。
外挂**不自动改代码、不自动下单、不写 positions.json、不调用 automated_execution_system.py**。

## LLM 模块（`llm.py`）

外挂统一 LLM 入口，集中封装 GLM-5 调用与 JSON 解析修复。

**后端优先级（运行时探测，fail-open）：**

1. **直接 OpenAI 兼容端点**（推荐，本机 GLM-5 不可用时仍能工作）— 由环境变量配置：
   ```bash
   export MOBIUS_LLM_BASE_URL="https://api.openai.com/v1"   # 或自建/代理 endpoint
   export MOBIUS_LLM_API_KEY="sk-..."
   export MOBIUS_LLM_MODEL="gpt-4o-mini"                    # 可选，默认 gpt-4o-mini
   ```
2. **主系统 GLM-5**（`utils.glm5_client.quick_chat`，只读 import，失败自动跳过）。
3. **全部不可用** → `chat()` 返回 `None`，调用方降级为规则模板，外挂绝不崩溃。

API：
- `llm.chat(prompt, system=None, temperature=0.2) -> Optional[str]`
- `llm.extract_json(prompt, system=None) -> Optional[dict]`（自动去代码围栏 / 截取 / 去尾随逗号）
- `llm.backend_status() -> str`（`direct:...` / `glm5` / `none`，供 CLI 诊断）

## 依赖

- 仅标准库 + `requests`（项目已具备，仅在配置了直接端点时才用到）。
- 可选：`pdfplumber` 或 `PyPDF2` 用于 PDF 解析，未安装时 fail-open 提示转 txt/md。
- GLM-5 不可用且无直接端点配置时，自动降级为规则模板提取。

## 质量门禁

```bash
# 在外挂目录
ruff --fix mobius_addon
black mobius_addon
pytest mobius_addon/tests
```

## 卸载

直接删除 `mobius_addon/` 目录即可，对主系统无任何影响。
