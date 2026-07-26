# GitHub 热门项目深度集成落地文档（2026-07-26）

> **集成批次**：2026 年 7 月第 4 周 GitHub Trending Top 10 深度集成
> **方案文档**：`.trae/documents/GitHub热门项目深度集成方案_2026-07-26.md`
> **模式提取**：`research/references/awesome-llm-apps-patterns/PATTERN_EXTRACTION.md`
> **版本**：v8.6.9
> **状态**：✅ 已完成（7 阶段全部落地，233 个测试全过）

---

## 一、集成总览

将本周 GitHub Trending 中 3 个与量化交易相关的项目**深度集成到生产代码**（非 shallow clone 参考），实现开发工具升级 + 第 6 信号源 + 金融多Agent Shadow Mode。

| 项目 | GitHub 星数 | 集成形态 | 影响范围 | 风险 | 可逆性 | 状态 |
|------|------------|---------|---------|------|--------|------|
| **code-review-graph** | +4,791 | uv tool + MCP server + AI skill | 仅开发时 | 极低 | `uv tool uninstall` + 删目录 | ✅ |
| **cangjie-skill → research_distiller** | +1,364 | 原生重写为 `utils/research_distiller.py` | 影子账户 (Phase 10) | 中 | weight=0 + 删模块 | ✅ |
| **awesome-llm-apps → finance_agent_orchestrator** | +5,385 | 架构借鉴 + 原生实现 | 仅日志 (Shadow Mode) | 低 | 删 shadow 块 + 删模块 | ✅ |

---

## 二、7 阶段实施记录

### 阶段 1: code-review-graph 安装 ✅

**新增文件**：
- `tools/code-review-graph/install.ps1` — 安装脚本（参考 codebase-memory-mcp 模板）
- `tools/code-review-graph/mcp_config_snippet.json` — MCP 配置片段
- `tools/code-review-graph/README.md` — 版本/用法/回滚
- `.claude/skills/code-review-graph/SKILL.md` + `.trae-cn/skills/code-review-graph/SKILL.md`

**安装命令**（uv 隔离，不污染 Python 3.8.9）：
```powershell
uv tool install code-review-graph
code-review-graph --version
code-review-graph query SignalFusionEngine
```

**知识图谱构建**：5710 节点 / 80792 边，支持增量代码审查。

### 阶段 2: awesome-llm-apps 模式提取文档 ✅

**新增文件**：
- `research/references/awesome-llm-apps-patterns/PATTERN_EXTRACTION.md`（29KB）

**研究范围**（WebFetch 5 个金融 Agent 项目，不 clone 代码）：
1. ai-hedge-fund — 多 Agent 投票架构
2. financial-rag-agent — RAG 增强财务分析
3. ai-multi-agents-samples — 多 Agent 协调模式
4. fraud-detection-agent — 异常检测 Agent
5. insurance-claim-agent — 理赔自动化 Agent

**提取的架构模式**：mermaid 图 + 适配方案，为 finance_agent_orchestrator 提供设计依据。

### 阶段 3: finance_agent_orchestrator Shadow Mode ✅

**新增文件**：
- `utils/finance_agent_orchestrator.py`（16KB）— 协调器 + AgentConsensus + ShadowDiff
- `utils/finance_agents/__init__.py` — 包初始化
- `utils/finance_agents/base_agent.py` — BaseAgent ABC + AgentDecision dataclass
- `utils/finance_agents/value_agent.py` — 估值分析（DCF/PE/PB 分位/ROE）
- `utils/finance_agents/momentum_agent.py` — 动量分析（均线突破/RSI/量价配合）
- `utils/finance_agents/sentiment_agent.py` — 舆情分析（复用 AIReportAgent）
- `utils/finance_agents/risk_agent.py` — 风险分析（回撤/波动率/流动性，含 veto）
- `utils/finance_agents/macro_agent.py` — 宏观分析（利率/北向资金/行业景气）
- `tests/unit/test_finance_agent_orchestrator_unit.py` — 27 个测试
- `tests/unit/test_finance_agents_unit.py` — Agent 单元测试
- `tests/integration/test_finance_agents_shadow_mode_integration.py` — 7 个测试
- `tests/e2e/test_finance_agent_orchestrator_e2e.py` — 4 个测试

**修改文件**：
- `utils/ai_report_agent.py` — 追加 `to_agent_decision()` 适配方法（向后兼容）

**加权投票**：`value(0.25) + momentum(0.25) + sentiment(0.15) + risk(0.25) + macro(0.10) = 1.00`

**Shadow Mode**：不进入 signal_fusion，仅在 Phase 7 跑对比，输出 `ShadowDiff` 记录到 `data/agent_orchestrator_audit/shadow_diffs_YYYYMMDD.jsonl`。

### 阶段 4: research_distiller 模块 ✅

**新增文件**：
- `utils/research_distiller.py`（42KB）— 主模块，RIA--TV++ 量化版
- `scripts/distill_research_batch.py`（9.9KB）— 06:00 离线蒸馏脚本
- `tests/unit/test_research_distiller_unit.py`（18KB）— 15 个测试
- `tests/fixtures/sample_research_report.json` — 测试 fixture

**核心类**：`ResearchDistiller`
**核心方法**：
- `distill_report(pdf_path)` — 研报蒸馏
- `distill_earnings_call(transcript, symbol)` — 业绩会蒸馏
- `distill_book_chapter(book_path, chapter)` — 书籍章节蒸馏
- `distill_news_batch(news_items)` — 新闻批量蒸馏
- `to_signal_map(signals)` — 转为 `{symbol: strength}` 字典
- `load_daily_snapshot(trade_date)` — 加载当日快照（供 daily_workflow 调用）
- `save_daily_snapshot(signals, trade_date)` — 保存当日快照

**数据结构**：`DistilledSignal` dataclass
- `symbol: str` / `strength: float` / `confidence: float`
- `source_type: str`（news/earnings_call/book_chapter）
- `source_id: str` / `reasoning: str` / `valid_until: str`
- `timestamp: str` / `key_factors: List[str]`

**持久化**：`data/distilled_signals/distilled_signals_YYYYMMDD.json`（gitignored）

**降级链**：LLM 蒸馏 → 规则引擎蒸馏（关键词 + 标的 NER）→ 空信号（安全降级）

### 阶段 5: signal_fusion 第 6 信号源 ✅

**修改文件**：`utils/signal_fusion.py`（最小侵入，沿用 pipeline_factor 的 post-mix 模式）

| 修改点 | 位置 | 改动 |
|--------|------|------|
| `__init__` 参数 | 第 67 行后 | 追加 `research_distilled_weight: float = 0.03` |
| 内部缓存 | 第 87 行后 | 追加 `self._research_distilled_signals: Dict[str, float] = {}` |
| 注入方法 | 第 207 行后 | 追加 `inject_research_distilled_signals()`，镜像 pipeline_factor 结构 |
| 融合逻辑 | 第 346 行后 | 追加 research_distilled post-mix 块，含 4 层 NaN 防御 |
| `sources` 字段 | 第 378-384 行 | 追加 `"research_distilled_strength"` |
| `meta` 字段 | 第 385-393 行 | 追加 `"research_distilled_weight"` / `"research_distilled_applied"` |

**新增测试**：`tests/unit/test_signal_fusion_research_distilled_unit.py` — 25 个测试

**4 层 NaN 防御**：
1. 注入过滤：`inject_research_distilled_signals()` 过滤 NaN/Inf
2. 取值防御：`dict.get()` 后再检查 `math.isfinite()`
3. 融合后检查：post-mix 后检查 strength 是否 NaN
4. 最终边界裁剪：`max(-1.0, min(1.0, strength))`

### 阶段 6: daily_workflow.py 注入块 ✅

**修改文件**：`v8.3_institutional/daily_workflow.py`（单点插入，第 4355 行）

在 `phase_signal` 方法中 Pipeline 因子注入块之后追加研究蒸馏信号注入块，**严格沿用既有降级模板**：

```python
# === v8.6.9 新增: 研究蒸馏信号注入 (第 6 信号源) ===
research_signals = {}
try:
    from utils.research_distiller import ResearchDistiller
    distiller = ResearchDistiller()
    research_signals = distiller.load_daily_snapshot(self.trade_date)
    if research_signals and self.signal_fusion is not None:
        self.signal_fusion.inject_research_distilled_signals(research_signals)
        signal["research_distilled_count"] = len(research_signals)
        signal["research_distilled_applied"] = True
        logger.info(f"研究蒸馏信号加载: {len(research_signals)} 个标的, ...")
    else:
        signal["research_distilled_applied"] = False
except Exception as e:
    logger.warning(f"研究蒸馏信号加载失败 (不影响主流程, 降级为原始权重): {e}")
    signal["research_distilled_applied"] = False
```

**新增测试**：
- `tests/integration/test_research_distiller_signal_fusion_integration.py` — 10 个测试
- `tests/e2e/test_research_distiller_e2e.py` — 5 个测试

### 阶段 7: 文档更新 ✅

**修改文件**：
- `README.md` — 顶部版本号 v8.6.7 → v8.6.9；新增"🧰 GitHub 热门项目深度集成"章节；版本历史表追加 v8.6.9 条目

**新增文件**：
- `docs/GITHUB_HOT_PROJECTS_DEEP_INTEGRATION_20260726.md`（本文档）

---

## 三、测试覆盖

### 新增 93 个测试

| 测试文件 | 层级 | 测试数 | 覆盖内容 |
|---------|------|-------|---------|
| `tests/unit/test_research_distiller_unit.py` | 单元 | 15 | 蒸馏逻辑/降级链/持久化/NER |
| `tests/unit/test_signal_fusion_research_distilled_unit.py` | 单元 | 25 | post-mix/NaN防御/降级链/与pipeline共存 |
| `tests/unit/test_finance_agent_orchestrator_unit.py` | 单元 | 27 | 协调器/加权投票/Shadow对比/审计日志 |
| `tests/integration/test_research_distiller_signal_fusion_integration.py` | 集成 | 10 | 完整链路/持久化往返/多源共存 |
| `tests/integration/test_finance_agents_shadow_mode_integration.py` | 集成 | 7 | Shadow Mode 全流程/多Agent协作 |
| `tests/e2e/test_research_distiller_e2e.py` | E2E | 5 | 真实端到端/daily_workflow注入模式/三源融合 |
| `tests/e2e/test_finance_agent_orchestrator_e2e.py` | E2E | 4 | 真实SignalFusion对比/多标的/降级 |
| **合计** | — | **93** | **全过 (运行时间 ~2 秒)** |

### 完整测试套件回归

```powershell
python -m pytest tests/unit tests/integration tests/e2e --ignore=tests/verify_qlib_data.py -q
# 结果: 233 passed in 57.56s (无回归)
```

---

## 四、关键约束（不可违反）

1. **不修改主融合公式**：`alpha(0.70) + llm(0.10) + etf(0.12) + macro(0.08) = 1.00` 不变，research_distilled 用 post-mix 叠加
2. **不绕过 Kill Switch**：新信号源在 Kill Switch 之下
3. **仅影响影子账户**：research_distilled 仅影响 Phase 10，不影响 Phase 6 实盘执行
4. **Shadow Mode 不入信号路径**：finance_agent_orchestrator 仅写审计日志
5. **Python 3.8.9 兼容**：所有新模块用 `from __future__ import annotations`
6. **不修改 requirements.txt**：可选依赖（pdfplumber）用 lazy import + 降级
7. **不触碰孤儿 submodule**：`ms_strategy/`、`factor_kill_switch.py`、`research/vibe_trading_factor_analysis/safety/` 不动

---

## 四-补、环境隔离保护（v8.6.9 模拟盘运行模式）

> **用户要求（2026-07-26）**：暂不接入实盘，依然用模拟盘跑数据。

### 当前运行环境

```bash
# .env 文件配置
TRADING_ENV=shadow   # 模拟盘模式 (非 production 实盘)
```

**shadow 模拟盘模式特性**：
- `fail_closed=True` — 异常时阻止交易（保守保护）
- `allow_real_orders=False` — **不执行真实订单**，仅记录
- `shadow_capital_pct=0.1` — 10% 资金（¥500,000）影子账户
- `research_distilled_weight=0.03` — 第 6 信号源**激活**（模拟盘验证）

### 三层环境隔离保护

| 层级 | 位置 | 保护逻辑 | production 行为 | shadow/development 行为 |
|------|------|---------|----------------|----------------------|
| **层 1** | `.env` | `TRADING_ENV=shadow` | — | 模拟盘模式 |
| **层 2** | `daily_workflow.py:4365-4374` | `if get_trading_env() == PRODUCTION: 跳过注入` | 强制跳过 research_distiller 注入 | 正常注入 |
| **层 3** | `signal_fusion.py:92-104` | `__init__` 中 `if PRODUCTION: weight=0` | 强制 `research_distilled_weight=0` | `weight=0.03` 正常 |

### 环境隔离验证结果

```
[1] production 模式:  research_distilled_weight=0.0   (实盘禁用)
    注入信号后: applied=False (post-mix 不触发)
[2] shadow 模式:      research_distilled_weight=0.03  (模拟盘激活)
    注入信号后: applied=True, strength=0.8 (post-mix 正常)
[3] development 模式: research_distilled_weight=0.03  (开发模式激活)
    注入信号后: applied=True, strength=0.8 (post-mix 正常)
```

### 切换到实盘的前置条件

当模拟盘验证充分（建议运行 14 天以上，影子账户 NAV 稳定）后，可切换到实盘：

```bash
# 1. 修改 .env
TRADING_ENV=production

# 2. 验证 research_distilled 在 production 下被禁用
python -c "
import os; os.environ['TRADING_ENV']='production'
from utils.signal_fusion import SignalFusionEngine
e = SignalFusionEngine()
assert e.research_distilled_weight == 0.0
print('production 模式: research_distilled 已禁用')
"

# 3. 确认影子账户 Stage 1 运行满 14 天且 NAV 稳定
# 4. 确认 finance_agent_orchestrator Shadow Mode 审计日志无异常
```

**注意**：切换到 production 后，research_distiller 信号源会被自动禁用（weight=0），需要单独评估后再决定是否在实盘启用。

---

## 五、回滚步骤（一键回滚）

```powershell
# 1. 回滚生产代码修改
git checkout HEAD -- v8.3_institutional/daily_workflow.py utils/signal_fusion.py utils/ai_report_agent.py

# 2. 删除新增模块
Remove-Item -Recurse -Force utils/research_distiller.py utils/finance_agent_orchestrator.py utils/finance_agents

# 3. 删除新增测试
Remove-Item -Recurse -Force tests/unit/test_research_distiller_unit.py tests/unit/test_finance_agent_orchestrator_unit.py
Remove-Item -Recurse -Force tests/unit/test_signal_fusion_research_distilled_unit.py
Remove-Item -Recurse -Force tests/integration/test_research_distiller_signal_fusion_integration.py
Remove-Item -Recurse -Force tests/integration/test_finance_agents_shadow_mode_integration.py
Remove-Item -Recurse -Force tests/e2e/test_research_distiller_e2e.py tests/e2e/test_finance_agent_orchestrator_e2e.py

# 4. 回滚文档
git checkout HEAD -- tests/conftest.py .gitignore README.md

# 5. 卸载 code-review-graph
uv tool uninstall code-review-graph
Remove-Item -Recurse -Force tools/code-review-graph

# 6. 验证回归
pytest tests/ -v  # 期望: 回到 120 个测试全过
```

---

## 六、Post-deploy 监控（首个交易日）

| 时点 | 检查项 | 期望 |
|------|--------|------|
| 09:00 前 | `daily_workflow_{YYYYMMDD}.log` | phase_signal 完成，无 Exception |
| 09:30 | signal_fusion 输出 | `research_distilled` 字段存在 |
| 10:00 | Phase 10 影子账户 | NAV 在 [0.97, 1.03] 区间 |
| 15:00 | EOD 报告 | 无 P0/P1 告警 |
| 16:00 | Shadow Mode 审计日志 | `data/agent_orchestrator_audit/shadow_diffs_YYYYMMDD.jsonl` 存在 |

### 回滚触发条件（任一满足立即回滚）

1. 任何 P0 告警（Kill Switch 触发 / CircuitBreaker 启动）
2. Phase 10 影子账户 NAV 偏离 > 3%
3. phase_signal 耗时 > 30s（原 ~10s）
4. signal_fusion 输出包含 NaN

---

## 七、集成成果总结

| 维度 | 集成前 (v8.6.7) | 集成后 (v8.6.9) | 增量 |
|------|----------------|----------------|------|
| 信号源数量 | 5 个 | 6 个 | +research_distiller (0.03) |
| 测试用例数 | 120 个 | 233 个 | +93 个 |
| 金融 Agent 架构 | 无 | 5 专家 Agent Shadow Mode | +协调器 + veto 机制 |
| 代码审查工具 | 无 | code-review-graph (MCP) | +5710 节点图谱 |
| 研究蒸馏能力 | 无 | RIA--TV++ 量化版 | +研报/业绩会/书籍蒸馏 |
| 实盘影响 | — | 零（仅影子账户） | 安全隔离 |

**结论**：3 个 GitHub 热门项目已深度集成到生产代码，新增 93 个测试守护，233 个测试全过无回归。所有生产改动用 post-mix 模式 + Shadow Mode，可一键回滚，不破坏 500 万实盘。
