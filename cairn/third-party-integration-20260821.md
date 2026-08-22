# 第三方项目集成专题 — 2026-08-21

> 将 `10_第三方项目/` 中 5 个高价值项目集成到主系统 v8.6 的完整经验沉淀。
> 包含选型评估、架构模式、可复用经验、踩坑记录。
> LOG 指针: `cairn/LOG.md` 2026-08-21 条目。

## 1. 选型评估

### 评估方法
对照主系统 `28-终极量化交易系统8.4` 现有模块，按"增量价值 × 接入成本"分 S/A/B/C 级。

### 最终选型（S3→S2→A2→A1→S1 顺序）

| 编号 | 项目 | 来源 | 价值 | 集成方式 |
|------|------|------|------|---------|
| S3 | ai-berkshire 决策纪律层 | `10_第三方项目/ai-berkshire-flow/` | 四大师信息分级 + 质量筛选 + 镜像测试 | 新建 `utils/value_discipline_layer.py` + 子包 |
| S2 | Google TimesFM 2.5 | PyPI `timesfm[torch]` | 零样本时序预测 + 分位区间 | 新建 `utils/timesfm_predictor.py` + 集成 `lgb_tscv_trainer.py` |
| A2 | SkillSpector | `10_第三方项目/skillspector/` | Skill 安全门禁 (SARIF 解析) | 新建 `scripts/skill_security_scan.py` + 集成 `pre_commit_check.py` |
| A1 | codebase-memory-mcp | GitHub `DeusData/codebase-memory-mcp` | tree-sitter 知识图谱 (120x 省 token) | 二进制安装 + Codex 配置 |
| S1 | TradingAgents 新闻模块 | GitHub `TauricResearch/TradingAgents` | LLM 驱动新闻深度分析 | 新建 `utils/news_intelligence.py` + 信号源适配器 |

### 未选型（已评估排除）
- **v5.10 已废弃**: UI/AI/对冲/宏观模块已在 v8.6 统一合并，不重复接入
- **FinClaw 1031 Skill**: 数量庞大但单 Skill 质量参差，按需复用而非批量接入
- **ECC 64 Agent**: v8.6.14 已远超其能力（482 自有测试 / 6-job CI），增量价值有限

## 2. 架构模式

### 2.1 信号源适配器模式（S1 核心）

主系统 `SignalFusionEngine` 用 `register_source(name, getter, weight)` 注册信号源。
已有 `sentiment_signal_source.py` (自媒体词典打分) 作为模板。

**S1 新闻智能信号源遵循同一模式**：

```
utils/news_intelligence.py              ← 引擎 (采集 + LLM 分析 + 降级)
utils/signal_sources/news_intelligence_signal_source.py  ← 适配器
utils/signal_fusion.py                  ← 注册函数 register_news_intelligence_signal_source()
```

**适配器接口约定**：
- `get_signal(code) -> SignalResult` (score ∈ [0,1], action, confidence)
- 采集失败/LLM 不可用 → 中性 SignalResult (score=0.5, confidence=0)
- 懒加载依赖 (WebScraper / GLM5Client / NewsSentimentEngine)
- `close()` 资源清理

### 2.2 fail-safe 降级链（S3/S2/S1 共同模式）

```
主流程 → 采集 → LLM 分析 → 词典打分降级 → 中性占位
         ↓        ↓           ↓              ↓
       无新闻   LLM不可用   ImportError    任何异常
```

**关键约定**：
- 每层只捕获 `(ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, ImportError)`
- 不用裸 `except Exception`（ruff BLE001 门禁），用显式异常元组
- ruff.toml per-file-ignores 豁免 BLE001 时必须注释说明"有意设计"

### 2.3 不可变性（S3/S2/S1 共同模式）

所有 dataclass 用 `@dataclass(frozen=True)`：
- `NewsArticle` / `NewsIntelligenceReport` (S1)
- `ValueDisciplineDecision` / `QualityScreenResult` (S3)
- `ForecastResult` (S2)

测试用 `try: obj.field = x; raise AssertionError() except AttributeError: pass` 验证不可变。

## 3. 可复用经验

### 3.1 TimesFM 2.0.2 API 变更适配（踩坑）

**问题**: 代码按 TimesFM 1.x API 写 (`TimesFm(hparams=..., checkpoint=...)` + `load_from_checkpoint()`)，但 PyPI 安装的是 2.0.2，API 完全变了。

**TimesFM 2.0.2 新 API**：
```python
import timesfm
# 工厂函数, 自动下载 checkpoint
model = timesfm.TimesFM_2p5_200M_torch(torch_compile=False)
# forecast 签名变了: (horizon, inputs) 而非 (inputs, horizons)
point, quantile = model.forecast(horizon=5, inputs=[ctx_array])
```

**适配策略**: `hasattr(timesfm, "TimesFM_2p5_200M_torch")` 检测版本，新旧 API 双路径兼容。

**教训**: pip install 前先 `python -c "import timesfm; print(dir(timesfm))"` 检查实际 API，不依赖文档。

### 3.2 ruff.toml per-file-ignores 模式

主系统 ruff.toml 已有大量豁免先例。新增模块的豁免模式：

```toml
# <编号> <模块名> (日期): <豁免理由>
# BLE001: <具体 blind except 的设计理由>
# C901: <复杂度理由>
"utils/<module>.py" = ["BLE001", "C901"]
```

**测试文件豁免**（已有全局规则）：
```toml
"tests/**/*.py" = ["ANN", "T201", "BLE001", "E402", "N806", "N803", "C901", "N801"]
```

### 3.3 codebase-memory-mcp 安装经验

**安装**: `Invoke-WebRequest` 下载 `install.ps1` → 检查脚本 → 执行。

**结果**: 二进制安装成功 (v0.10.8)，Codex CLI 配置成功（MCP + skill + agents + hooks）。

**部分失败**: 3 个 agent (Claude Code/VS Code/OpenClaw) 的 mcp.json 配置失败（格式问题），不影响二进制使用。

**教训**: 安装脚本有 checksum 验证 + HTTPS 强制 + 路径遍历防护，安全可靠；但多 agent 配置可能部分失败，需检查输出日志。

### 3.4 信号源注册幂等性

`register_*_signal_source()` 函数必须幂等：
```python
if engine.has_source('news_intel'):
    logger.info("已注册, 跳过 (幂等)")
    return
```

### 3.5 feature-flag 控制

信号源注册受 feature-flag 控制：
```python
from utils.infra.feature_flags import is_enabled
if not is_enabled("USE_NEWS_INTELLIGENCE_SIGNAL"):
    return None
```

flag 检查失败时降级为不注册（不崩溃主流程）。

## 4. 测试策略

### 4.1 mock 模式（不依赖外部服务）

| 外部依赖 | mock 方式 |
|---------|----------|
| WebScraper | `MagicMock(fetch_news=MagicMock(return_value=articles))` |
| GLM5Client | `MagicMock(chat=MagicMock(return_value={"content": json_str}))` |
| NewsSentimentEngine | `MagicMock(analyze=MagicMock(return_value=result))` |
| timesfm 模型 | `MagicMock(forecast=MagicMock(return_value=(point, quantile)))` |

### 4.2 测试覆盖维度

每个模块测试覆盖：
- 不可变性 (frozen dataclass)
- 主流程成功路径 (LLM/模型返回有效结果)
- 降级链 (空输入/无数据/LLM异常/采集异常 → 中性降级)
- 边界 (score/clamp 到 [0,1], 截断防过长)
- 资源清理 (close 调用)

### 4.3 累计测试数

| 模块 | 单测数 | 状态 |
|------|--------|------|
| S3 value_discipline_layer | 26 | ✅ 全绿 |
| S2 timesfm_predictor | 14 | ✅ 全绿 |
| A2 skill_security_scan | 18 | ✅ 全绿 |
| S1 news_intelligence | 40 | ✅ 全绿 |
| **合计** | **98** | **✅ 全绿** |

## 5. 文件清单

### 新建文件
- `utils/value_discipline_layer.py` + `utils/value_discipline/` 子包 (S3)
- `utils/timesfm_predictor.py` (S2)
- `scripts/skill_security_scan.py` (A2)
- `utils/news_intelligence.py` (S1)
- `utils/signal_sources/news_intelligence_signal_source.py` (S1)
- `config/value_discipline.yaml` / `config/timesfm_predictor.yaml`
- `tests/test_value_discipline_layer.py` / `test_timesfm_predictor.py` / `test_skill_security_scan.py` / `test_news_intelligence.py`
- `docs/第三方项目集成总体计划_20260821.md` + `docs/集成记录/{S3,S2,A2,A1,S1}/`

### 修改文件
- `utils/glm5_decision_engine.py` — S3 集成 (line 591 return 前叠加纪律层)
- `lgb_tscv_trainer.py` — S2 集成 (--predictor 参数)
- `scripts/pre_commit_check.py` — A2 集成 (末尾加 skill 安全扫描)
- `utils/signal_fusion.py` — S1 集成 (register_news_intelligence_signal_source)
- `utils/signal_sources/__init__.py` — S1 re-export
- `ruff.toml` — per-file-ignores 豁免

### 环境变更
- `pip install timesfm[torch]` (timesfm 2.0.2 + huggingface_hub + safetensors)
- codebase-memory-mcp v0.10.8 安装到 `%LOCALAPPDATA%\Programs\codebase-memory-mcp\`
- Codex CLI 配置: `~/.codex/config.toml` + `~/.codex/AGENTS.md` + skill + agents + hooks

## 6. 后续建议

- **S2 checkpoint 下载**: TimesFM 2.0.2 首次 `forecast()` 时自动从 HuggingFace 下载 800MB checkpoint，需确保网络通畅
- **S1 LLM prompt 调优**: 当前中文 prompt 较简，可按实际效果迭代（参考 TradingAgents news_analyst.py 的 prompt 设计）
- **A1 索引**: 重启 Codex 后在主系统目录执行 "Index this project" 建立知识图谱索引
- **A1 配置修复**: Claude Code/VS Code/OpenClaw 的 mcp.json 配置失败，如需使用这些 agent 需手动修复配置格式