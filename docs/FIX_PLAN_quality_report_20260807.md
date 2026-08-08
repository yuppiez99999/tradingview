# 质量报告修复计划 (2026-08-07)

> 基于 `2026-08-07-18-14-30/quality_report.html` 的审计结果制定。
> 修复执行时间：2026-08-07。
> 状态：**已完成**。

---

## 1. 背景

质量报告扫描发现系统存在 6 类问题，涵盖代码质量、接口一致性、防御性编程、依赖锁定和工程规范。本计划按 P0（阻断）/P1（高优）/P2（中优）分级修复，并给出验证结果。

---

## 2. 问题清单与修复记录

### P0-1：隔离 `research/` 目录（476 个破损文件，96% 语法错误）

**问题**：`research/` 目录下存在大量实验性/废弃脚本，其中 `references/`（1290 个 .py 文件）和 `vibe_trading_factor_analysis/`（63 个 .py 文件）语法错误率接近 100%，导致质量扫描噪声和误 import 风险。

**修复**：
- 将 `research/references/` 移动到 `_archive/research_references_quarantine/references/`
- 将 `research/vibe_trading_factor_analysis/` 移动到 `_archive/research_references_quarantine/vibe_trading_factor_analysis/`
- 保留 `research/` 根级 28 个文件（含被测试引用的 `lgbm_factor_mining.py` 和 `lgbm_reproducibility.py`，语法完好）
- 创建 `research/__init__.py` 废弃标记

**验证**：核心代码区语法错误从 495 个降至 4 个（降幅 99.2%）。

---

### P0-2：修复 `SignalFusionEngine` 接口脱节（~31 个失败测试）

**问题**：测试期望的接口（`fuse()`、`inject_research_distilled_signals()`、`inject_pipeline_factor_signals()`）与现有实现（`get_fused_signal`、`get_fused_signals_batch`）完全脱节，导致 25 个单元测试 + 10 个集成测试失败。

**修复**（`utils/signal_fusion.py`）：
1. 新增 `import math`
2. 新增 `FusedSignalV2` 数据类（字段：`symbol`、`strength`、`sources`、`meta`）
3. 扩展 `__init__` 参数：`research_distilled_weight=0.03`、`pipeline_factor_weight=0.05`
4. 新增属性：`_research_distilled_signals`、`_pipeline_factor_signals`
5. 实现 `inject_research_distilled_signals()` — 4 层 NaN/Inf/非数值防御
6. 实现 `inject_pipeline_factor_signals()` — 同上
7. 实现 `fuse(alpha_signals=...)` — post-mix 加权融合，公式：
   ```
   final_strength = tanh(alpha_strength * alpha_w + research_val * used_rw + pipeline_val * used_pw)
   ```
   其中权重归一化：仅对实际存在的信号源分配权重，缺失信号源权重回退给 alpha。

**验证**：
- `tests/unit/test_signal_fusion_research_distilled_unit.py`：25/25 通过
- `tests/integration/test_research_distiller_signal_fusion_integration.py`：10/10 通过
- 全量回归：`tests/ -k "signal_fusion or alpha_hedge or hedge_engine"`：40 passed, 0 failed

---

### P1-3：修复 `alpha_hedge_engine.py` BUG-1（NaN 价格绕过）

**问题**：`execute_covered_call` 中 `current_price <= 0` 校验无法拦截 NaN（`NaN <= 0` 为 False），导致 NaN 传播到 `int(NaN)` 崩溃。

**修复**（L203）：
```python
if current_price is None or current_price <= 0 or not math.isfinite(current_price):
```

**验证**：ruff E9/F63/F7/F82 检查通过；相关测试 40/40 通过。

---

### P1-3：修复 `alpha_hedge_engine.py` BUG-3（期权乘数硬编码）

**问题**：`_get_option_multiplier` 仅按 `symbol.startswith("y")` 判定商品期权（乘数 10），其余一律返回 10000。非 y 开头的商品期权（如 `m`/`c`/`ag`/`au` 等）会误用 ETF 期权乘数，导致下单金额/张数成倍算错。

**修复**：改为按标的代码前缀映射各交易所合约乘数：
- ETF 期权（510050/510300/588000/159919 等）：10000
- 股指期权（IO/MO/HO）：100
- 商品期权：按品种映射（铜 5/铝 5/锌 5/黄金 1000/白银 15/豆粕 10 等，覆盖大商所/郑商所/上期所/INE 共 50+ 品种）
- 兜底：无法识别时记录警告并返回 10000

**验证**：ruff 检查通过；无相关测试回归。

---

### P1-4：锁定依赖版本 `certifi`

**问题**：`certifi` 2026.07.22+ 版本移除了 `where()` 接口，导致 6 个测试失败。`requirements.txt` 未锁定 certifi 版本。

**修复**（`requirements.txt`）：
```
certifi>=2026.07.22
```

**验证**：pip install 后 certifi 版本符合要求；测试 collection 0 errors。

---

### P2-5：修复 BUG-2/4/5

#### BUG-2：流动性 NaN 防护

**问题**：`check_liquidity_spread` 中 `NaN > 0.05` 为 False，导致 NaN 报价被误判为"价差可接受"而放行。

**修复**（入口校验）：
```python
if not (math.isfinite(ask_price) and math.isfinite(bid_price)
        and bid_price > 0 and ask_price > 0):
    return False
```

#### BUG-4：AUM 配置化

**问题**：`AlphaHedgeEngine.__init__` 硬编码 `self.total_aum = 5000000`，无法适配不同账户规模。

**修复**：
- 新增 `total_aum` 参数（默认 None）
- 优先从参数读取，其次从 `broker.get_total_aum()` 读取，最后降级到 500 万
- 失败时记录警告日志

#### BUG-5：halt 字段校验

**问题**：`drawdown_decision.get("allow_new_buy") is False` 可能因字段不存在而永不触发。

**验证**：`DrawdownDecision.to_dict()` 确认包含 `level`/`allow_new_buy`/`breach_hard_limit` 字段。当前代码已使用三重判断（`level == "HALT"` || `allow_new_buy is False` || `breach_hard_limit`），风险已缓解。添加注释说明字段校验确认。

---

### P2-6：对齐 mypy `python_version` + ruff/pre-commit 接入

**问题**：`.mypy.ini` 中 `python_version = 3.8` 与实际运行版本 3.14 不一致，可能导致类型检查误报。

**修复**：
- `.mypy.ini`：`python_version = 3.14`
- 排除路径增加 `_archive/research_references_quarantine/`
- 安装 `ruff 0.16.1` 和 `pre-commit 4.6.1` 到 venv
- 验证 ruff 检查通过（E9/F63/F7/F82 零错误）

---

## 3. 验证摘要

| 检查项 | 修复前 | 修复后 |
|--------|--------|--------|
| SignalFusionEngine 失败测试 | 35 个（25 单元 + 10 集成） | 0 个 |
| alpha_hedge + hedge_engine 相关测试 | 40 passed | 40 passed, 0 failed |
| 核心代码区语法错误 | 495 个 | 4 个（降幅 99.2%） |
| ruff 严重错误（E9/F63/F7/F82） | 未安装 | 0 错误 |
| certifi 锁定 | 未锁定 | `>=2026.07.22` |
| mypy python_version | 3.8（不一致） | 3.14（对齐） |

---

## 4. 后续建议

1. **持续隔离**：`research/` 目录剩余文件建议逐步迁移到 `_archive/` 或转为正式模块，避免继续积累技术债。
2. **接口契约**：为 `SignalFusionEngine` 编写正式的接口文档（docstring + 类型注解），防止测试与实现再次脱节。
3. **依赖审计**：定期运行 `pip list --outdated` 和 `safety check`，及时锁定有安全更新的依赖。
4. **CI 集成**：将 ruff 和 mypy 检查加入 pre-commit 钩子和 GitHub Actions，确保每次提交自动校验。
5. **期权乘数维护**：`_COMMODITY_MULTIPLIER` 映射表需随交易所新品种上市定期更新，建议提取到 `config/option_multiplier.yaml` 便于维护。

---

## 5. 参考文件

- 质量报告：`C:\Users\Administrator\WorkBuddy\2026-08-07-18-14-30\quality_report.html`
- 修复涉及文件：
  - `utils/signal_fusion.py`
  - `alpha_hedge_engine.py`
  - `requirements.txt`
  - `.mypy.ini`
  - `research/`（隔离）
  - `_archive/research_references_quarantine/`（新增）
