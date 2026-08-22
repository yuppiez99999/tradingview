# ECC python rules 与量化系统 CI 对照报告

> 创建: 2026-08-21
> 目的: 以 ECC `rules/python/*.md` 为对照清单，核对量化系统 CI 是否遗漏通用 Python 规则
> 结论: **ECC python rules 是量化系统 CI 的极弱子集，无任何遗漏，不接入**

## 对照明细

| ECC python rule | 量化系统对应 | 覆盖状态 |
|---|---|---|
| **coding-style: PEP 8** | ruff.toml `select=["E","W"]` + N(命名) + UP(现代语法) | ✅ 超集 |
| **coding-style: 类型注解** | ruff.toml `select=["ANN"]` + mypy.ini（渐进式严格） | ✅ 超集 |
| **coding-style: 不可变性(frozen dataclass/NamedTuple)** | AGENTS.md §5.1 不可变性硬约束（用 `.assign` 而非原地改） | ✅ 已有更强约束 |
| **coding-style: black/isort/ruff** | ruff.toml + black + isort（ruff.toml `select=["I"]`） | ✅ 全覆盖 |
| **patterns: Protocol/dataclass/context manager** | 通用 Python 模式，量化系统自然遵循；非可 lint 规则 | ✅ 无需接入 |
| **security: os.environ + dotenv** | AGENTS.md §8 安全要求 + `.env` + `python-dotenv` 依赖 | ✅ 已有 |
| **security: bandit** | pyproject.toml dev 依赖含 `bandit>=1.7.5` | ✅ 已有 |
| **testing: pytest** | pytest.ini + 482 自有测试 + 6 CI job | ✅ 超集 |
| **testing: coverage** | pytest-cov + `coverage-G7-fineng-execution-lessons` + sprint4 80% 门禁 | ✅ 超集 |
| **testing: pytest.mark 分层** | pytest.ini markers + tests/{unit,integration,e2e,regression,smoke,contracts} | ✅ 超集 |

## 量化系统独有（ECC 未覆盖，反向证明量化系统更严格）

- **量化专项 lint**: `scripts/quant_review_lint.py`（Q1/Q2/Q3 阻断项）
- **前视偏差门禁**: `ci_lookahead_guard.py` + `_detect_lookahead_tests.py`
- **P0 文件裸 print 门禁**: 17 个 P0 文件阻断 + `# allow-print` 豁免机制
- **ruff 增量零新增门禁**: `scripts/ruff_incremental_gate.py` + 冻结基线 `ruff_baseline.json`
- **F821 未定义名阻断** + **G-3 py_compile 语法阻断** + **G-4 F821 阻断**
- **pylint --fail-on=broad-exception-caught** 三重门禁（ruff BLE001 增量 + pylint 全量 + engineering_debt_gate T7）
- **金融数学大写变量名豁免**: N806/N803 精细 per-file-ignores（S/K/T/N/Delta/IC/VaR/Sharpe）
- **fail-safe 模块 blind except 豁免**: 风控/执行/编排层降级链
- **V9 基线回归门禁** + **smoke 18 项** + **Phase 3-B 72 断言**

## 结论

ECC `rules/python/*.md` 每条均为 30-42 行通用建议，量化系统 ruff.toml（250 行）+ mypy.ini + .pylintrc + 6 CI job + 量化专项门禁已**全面覆盖且严格得多**。

**决策: 不接入 ECC python rules 到量化系统 CI。** 接入只会引入回退风险（通用规则覆盖不了量化特有需求，且 ECC 无前视偏差/P0 print/增量零新增等关键门禁）。

本对照报告作为"已确认无遗漏"的归档证据，后续不再追踪。