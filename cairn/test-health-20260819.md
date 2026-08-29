---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-19
updated: 2026-08-19
contains: test-health, scipy-fallback, singleton-pollution, api-refactor-stale-test, dependency-management, ruff-fix, B025-replaceAll-gotcha, E741-rename-gotcha, F401-classification
related:
  - cairn/self-evolution-framework.md
  - cairn/exception-handling-standards.md
  - cairn/code-review-ruff-fix-batch-20260818.md
  - docs/高价值项目集成排期计划_20260811.md
---

# 测试健康度治理经验（2026-08-19）

> 记录从 242 failed / 54 errors → 0 failed / 0 errors（100% 通过）的完整治理路径，沉淀 5 类测试失败模式与修复策略。

## 一、治理基线与结果

| 指标 | 治理前 | 治理后 | 改善 |
|------|--------|--------|------|
| passed | 12,296 | 13,195 | +899 |
| failed | 242 | 0 | -242 |
| errors (collection) | 54 | 0 | -54 |
| 通过率 | 97.0% | 100% | +3.0pp |
| ruff 违规 | 17,602 | 7,060 | -10,542 |

## 二、五类失败模式与修复策略

### 模式 1：依赖缺失（ModuleNotFoundError）— 占 196/242

**现象**：`ModuleNotFoundError: No module named 'scipy'/'pydantic'/'joblib'/'ntplib'/'pyarrow'/'torch'/'sklearn'`

**根因**：开发环境未安装完整依赖，测试中 `try: import scipy; except ImportError: return []` 降级为空结果，导致连锁失败。

**修复**：`pip install scipy pydantic joblib ntplib pyarrow torch scikit-learn`

**教训**：
- 降级逻辑（`except ImportError: return []`）会掩盖依赖缺失，使测试静默失败而非报错
- **建议**：在 `requirements.txt` 或 `pyproject.toml` 固定完整依赖，CI 用 `pip install -r requirements.txt` 确保一致
- **建议**：降级路径应 `logger.warning` 而非静默 return，便于排查

### 模式 2：降级逻辑未实现（注释承诺但代码未做）— evaluator scipy 降级

**现象**：`calc_ic_series_from_history` 在 scipy 缺失时返回 `[]`，但模块 docstring 注释说"降级 Pearson"

**根因**：注释承诺了降级，但代码直接 `return []`，降级逻辑未实现

**修复**：在 `utils/alpha_factor/base.py` 新增 `_spearman_numpy` + `_average_rank` 纯 numpy 实现，scipy 缺失时降级使用

**教训**：
- **注释即契约**：docstring 承诺的降级行为必须实现，否则是 bug
- **建议**：降级实现应与原函数语义等价（Spearman = Pearson of ranks），而非近似降级（如 Pearson 替代 Spearman）

### 模式 3：单例跨测试污染 — limit_pool_provider 10 个

**现象**：单独跑测试文件 26 passed，全量跑 10 failed（`AttributeError: '_cache_ttl' not found`）

**根因**：`LimitPoolProvider` 是单例（`__new__` + `_initialized` 标志），全量测试中某文件先创建实例并污染状态，后续文件的测试拿到不完整实例

**修复**：在测试文件加 `@pytest.fixture(autouse=True)` 重置 `LimitPoolProvider._instance = None`

**教训**：
- 单例模式与 pytest 测试隔离不兼容 — 单例状态跨测试文件泄漏
- **建议**：单例类提供 `reset()` 类方法，测试用 autouse fixture 调用
- **建议**：conftest.py 可全局重置单例，避免每个测试文件重复

### 模式 4：API 重构后测试过时 — etf_flow_monitor 10 + hedge_engine 12

**现象**：`AttributeError: '_fetch_ifind_*' not found` / `ImportError: cannot import name '_exec_ifind'`

**根因**：源码重构移除了 iFinD 相关 API（改用 Wind MCP/东财/新浪回退链），但测试仍引用旧 API

**修复**：
- 旧 API 有意移除 → `@pytest.mark.skip` 跳过并注明重构原因
- 回退链断言 → 更新为匹配新回退链（如 `price_momentum` 替代 `ifind_mcp`）

**教训**：
- 重构移除 API 时应同步删除/更新对应测试，否则测试债累积
- **建议**：重构 PR 必须包含测试更新，CI 门禁拦截"测试引用不存在的符号"
- **建议**：`g7_*_boost` 系列测试是覆盖率提升任务创建的，易与源码脱节，需定期审查

### 模式 5：环境变化导致测试假设失效 — torch/akshare/lightgbm 已安装

**现象**：
- `test_auto_fallback_numpy`：期望 numpy 降级，但 torch 已安装返回 torch 后端
- `test_akshare_not_installed`：期望 akshare 未安装，但环境已安装
- `test_local_lightgbm_signal`：期望 LightGBM 可用，但环境未安装

**修复**：
- 检测依赖可用性，分支断言：`if _TORCH_AVAILABLE: assert torch_backend else: assert numpy_backend`
- mock `builtins.__import__` 模拟依赖未安装
- 依赖未安装时 `pytest.skip`

**教训**：
- 测试不应硬编码"依赖已安装"或"依赖未安装"假设
- **建议**：用 `pytest.importorskip("torch")` 或条件断言，使测试在两种环境都正确

## 三、ruff --fix 经验

- `python -m ruff check . --fix` 自动修复 2826 个（unused-import/unused-variable/简写等）
- `python -m ruff check . --fix --unsafe-fixes` 再修复 1749 个（累计 4575 个）
- 剩余 2719 个需人工审查 → Tier 1 批量修复 375 个 → 剩余 2344 个（Tier 2/3/4）
- **零风险**：`--fix` 仅做语义等价变换（删未用导入/变量），不改行为
- **建议**：pre-commit hook 加 ruff --fix，避免违规累积

### Tier 1 批量修复明细（2719→2344，fix 375 个）

| 规则 | 数量 | 处理方式 |
|------|------|---------|
| B007 unused-loop-var | 16 | 改为 `_` 前缀 |
| B025 dup-try-except | 26 | 移除重复异常类型 |
| E701/E702 multi-stmt | 43 | 拆成多行 |
| E741 ambiguous-name | 108 | `l`→`line`/`low`/`label` 等，同步改引用 |
| F401 unused-import | 35 | 6 删除 + 29 `# noqa`（可用性检查）+ 8 连锁 |
| 零散 (B005/B009/B010/B011/B015/B017/B018/B033/E721/E722/F811/I001/W292) | 15 | 逐个手动修复 |
| N999 invalid-module-name | 32 | ruff.toml 豁免（Streamlit UI 页面命名惯例） |
| daily_workflow.py F401 | 50 | ruff.toml per-file-ignores 豁免（统一入口 re-export） |

**配置变更**（ruff.toml）：
- 新增 `"v8.3_institutional/daily_workflow.py" = ["F401"]`（统一入口 re-export）
- 新增 `"ui/pages/**/*.py" = ["N999"]` + `"ui_original/pages/**/*.py" = ["N999"]`（Streamlit 页面命名）

**踩坑**：
- `replaceAll` 误改：data_provider.py 的 B025 修复用 replaceAll 匹配了 10 处，但只有 1 处有前置 `except RuntimeError: raise`，其余 9 处误移除 RuntimeError 导致测试失败 → 改为精确上下文匹配
- E741 改名漏改引用：agent 把 `l1`→`lows1` 但 `l1` 非单字符不触发 E741，只改了引用处未改定义处 → F821 检测拦截，手动恢复

**验证**：pytest tests/unit/ -x -q → 13237 passed / 0 failed / 89 skipped / 1 xpassed

### 剩余 2344 个违规分布（Tier 2/3/4）

| Tier | 规则 | 数量 | 说明 |
|------|------|------|------|
| 2 | BLE001 blind-except | 360 | 需甄别 fail-safe vs 应细化 |
| 2 | N806/N803 命名 | 393 | 金融数学大写变量，需扩 per-file-ignores |
| 2 | E402 导入位置 | 256 | sys.path/降级导入，需 per-file-ignores |
| 3 | ANN 类型注解 | 1186 | 逐函数添加，机械但量大 |
| 4 | C901 复杂度 | 107 | 需拆函数重构 |
| 4 | N802/N814/N813/N811/N815/N817/N801/N812/N818 | 42 | 改名影响 API |

## 四、依赖管理改进建议

| 缺失依赖 | 影响测试数 | 用途 | 建议 |
|---------|-----------|------|------|
| scipy | ~150 | 统计计算（Spearman/IC） | 必装，核心 |
| pydantic | ~108 | 数据模型校验 | 必装 |
| joblib | ~51 | 并行/缓存 | 必装 |
| ntplib | ~40 | NTP 时间同步 | 生产必装 |
| pyarrow | ~7 | 列式存储 | 可选 |
| torch | ~3 | GAT/Transformer 因子 | 可选（研究用） |
| scikit-learn | ~2 | ML 工具 | 必装 |

**行动项**：将 scipy/pydantic/joblib/ntplib/scikit-learn 加入 `requirements-core.txt`，pyarrow/torch 加入 `requirements-optional.txt`

## 五、对自我进化的影响

- **工程阻塞解除**：测试 100% 通过 → 自我进化 Wave 2 Phase B 启用无测试债阻碍
- **代码质量改善**：ruff 违规 17602→7060 → 可维护性提升
- **仍需关注**：覆盖率 0.43（目标 0.80）、daily_workflow.py 5904 行（目标 ≤3000）、broker_adapters 20+ TODO（实盘未接入）

## 六、修复文件清单

| 文件 | 修复内容 |
|------|---------|
| `utils/alpha_factor/base.py` | 新增 `_spearman_numpy` + `_average_rank`（scipy 降级） |
| `tests/unit/test_transformer_encoder_unit.py` | torch 可用性分支断言 |
| `tests/unit/test_limit_pool_provider_unit.py` | autouse fixture 重置单例 |
| `tests/unit/test_g7_etf_flow_monitor_boost.py` | iFinD API 跳过 + 回退链断言更新 |
| `tests/unit/test_g7_hedge_engine_boost.py` | 移除 iFinD mock |
| `tests/unit/test_g7_hedge_engine_boost_v2.py` | iFinD 函数跳过 + Wind 回退测试 |
| `tests/unit/test_g7_institutional_optimizer_boost.py` | 空持仓断言更新 |
| `tests/unit/test_g7_limit_pool_provider_boost.py` | mock akshare 未安装 |
| `tests/unit/test_g7_system_check_boost.py` | 移除 IFIND_TOKEN 断言 |
| `tests/unit/test_ms_strategy_coverage.py` | LightGBM 未安装 skip |
| `tests/unit/test_tdx_data_source_unit.py` | mock _connect 抛 OSError |
| `tests/unit/test_overnight_gap_monitor_unit.py` | 补 mock tdx 代理层 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [技术债清偿确认记录 — 2026-08-21](tech-debt-cleanup.md) (相似度 15%)
- [异常处理规约 (Exception Handling Standards)](exception-handling-standards.md) (相似度 13%)
- [测试债清零经验沉淀 (2026-08-16)](test-debt-clearance-20260816.md) (相似度 12%)
- [代码质量工业级差距审计 — 2026-08-19](code-quality-industrial-gap-20260819.md) (相似度 10%)
- [代码质量修复批次 2026-08-18：ruff 高危规则清零](code-review-ruff-fix-batch-20260818.md) (相似度 9%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
