# item 12 第一增量 — `config/` vs `configs/` 双目录分歧实测 (2026-09-11)

> **⚠️ 2026-09-11 第二增量重大反转**：本文 §A–§C 的"版本冲突"定性**不成立**。
> 逐字段 diff 实测：两份 `portfolio.yaml` **重叠叶子 = 0**（config/ 411 叶 vs configs/ 116 叶，
> identical=0 / value_diff=0），是**同名但 schema 完全不同的两个文件**，不是新旧版本关系。
> 详见文末「§F 第二增量反转」。

> 结论先行：`configs/` **0 个文件被 git 跟踪**（`.gitignore:137` 整目录忽略 = 机器本地），
> `config/` 55 个文件被跟踪。二者仅 **3 个文件名重叠** 且 **全部内容分歧**（sha1 零相同）。
> 声明口径（kill_switch P1-Q8 + `utils/config_manager.py` L1/L3）：**`config/` = 唯一事实源，
> `configs/` = 历史回退**。本轮只测不动；物理合并拆成两个决策点（见 §D）。

## A. 重叠文件分歧明细

| 文件 | `config/`（权威） | `configs/`（回退/历史） | 生产 .py 消费方 |
|---|---|---|---|
| `feature_flags.yaml` | 12104B #f515c395e4，**git 跟踪** | 10898B #0ccecb850d，未跟踪 | `configs/` 版 **0 个**；`config/` 版 5 个（`utils/infra/feature_flags.py`、`engineering_debt_gate.py`、`phase_b_progressive_enabler.py`、`ui_original/{app,auth}.py`）|
| `settings.yaml` | 2987B #764e725dc6 | 1133B #925435ee1b | `configs/` 版 **0 个**；`config/` 版 5 个（`cli/modes/quick_check.py`、`ui/pages/01_系统概览.py`、`utils/local_model_selector.py`、`量化策略系统_统一入口_v8.6.py`、`scripts/fix_settings_yaml.py`）|
| `portfolio.yaml` | 13685B #634d2ec612 | 12285B #c90a58006d | **两侧都有生产消费方**（见 §B/§C）——唯一真实双源冲突 |

**判定**：`configs/feature_flags.yaml` 与 `configs/settings.yaml` 是**死副本**（零生产消费），
可与本增量一起移除（机器本地文件，无 git 记录，删前自行备份）；`portfolio.yaml` 是待决策项（§D）。

## B. `configs/account_structure.yaml` 的 6 个生产消费方（实测行号）

| 文件:行 | 读法 |
|---|---|
| `utils/config_manager.py:107,533` | **已修复**：`get_portfolio_config()` 优先 `config/`（此前缺失单数目录导致落到 configs/） |
| `utils/kill_switch.py:62-69` | **已修复**（P1-Q8）：ConfigManager 优先级链「v8.3 唯一事实源 > configs/ 历史回退」 |
| `15_每日工作流/morning_info_runner.py:230` | **直读硬编码** `PROJECT_ROOT/"configs"/"portfolio.yaml"`（主读）|
| `utils/alpha/evolution_orchestrator.py:728` | **直读** `Path("configs/account_structure.yaml")`（主读）|
| `utils/alpha/vol_regime_weighter.py:1024` | **直读** `Path("configs/account_structure.yaml")`（主读；且其 8 类风格大类注释声明与 configs 版 `style` 字段对齐）|
| `utils/auto_trading_system.py:446` | **直读** `Path("configs/account_structure.yaml")`（主读）|

即：**4 个模块仍把 `configs/account_structure.yaml` 当主读**，与声明口径相悖。

## C. 内容分歧的业务含义（为什么不能机械合并）

`config/portfolio.yaml`（13685B）比 `configs/account_structure.yaml`（12285B）多 ~1.4KB。
`vol_regime_weighter` 的注释声明其**风格大类与 configs 版 `style` 字段对齐** —— 若把主读改到
`config/` 版，风格字段必须逐项核对，否则风格权重/约束静默错位。**合并 = 业务配置取舍，不是代码重构。**

## D. 决策点（须用户拍板后进入下一增量）

1. **D1（可立即做，低风险）**：移除两个死副本 `configs/feature_flags.yaml`、`configs/settings.yaml`
   （零生产消费；若担心回滚，先拷到 `_archive/`）。
2. **D2（须业务拍板）**：`portfolio.yaml` 以哪个版本为准？
   - 若以 `config/` 版为准：改 4 处硬编码主读 → `config/portfolio.yaml`，并核对 `vol_regime_weighter`
     的 8 类风格字段是否在 `config/` 版中齐全；
   - 若以 `configs/` 版为准：则声明口径（config/ 唯一事实源）需修订，且 kill_switch / config_manager
     的优先级链要反向调整。
   - 折衷（推荐）：先 diff 两版的 `style`/`risk_parameters`/`kill_switch` 三段，逐字段对齐后再切主读。
3. **D3（结构项）**：`config_manager.py` 是唯一合适的统一读取口 —— 下一步把 4 处硬编码改为
   `ConfigManager.get_portfolio_config()`，让优先级链只有一个实现点。

## E. 复现命令

```
# 重叠 + 分歧
python -c "import os,hashlib;a=set(os.listdir('config'));b=set(os.listdir('configs'));print(sorted(a&b))"
# 消费方（生产 py）
grep -rn "configs/portfolio" --include=*.py 15_每日工作流 utils | grep -v test
# git 跟踪状态
git ls-files configs/ | wc -l   # 0
git ls-files config/ | wc -l    # 55
```

## F. 第二增量反转与真缺陷修复（2026-09-11）

### F.1 逐字段 diff 反转了 D2 的定性

用 `yaml.safe_load` 展平两文件全部叶子：

| | `config/portfolio.yaml`（git 跟踪） | `configs/account_structure.yaml`（gitignored） |
|---|---|---|
| 叶子数 | 411 | 116 |
| 顶层键 | `fallback_prices` / `hedge(allocation,budget)` / `options` / `positions` / `v9_200w_preset` | `account_structure` / `assets` / `execution` / `hedge` / `kill_switch`(已迁出) / `liquidation_protocol` / `optimization` / `risk_guard` / `risk_parameters` |
| 重叠叶子 | **0**（identical=0 / value_diff=0） | |

⇒ **不存在"以哪个版本为准"**：`config/` 版是**持仓与对冲预算**事实源，
`configs/` 版是**账户结构与风控参数**事实源。**§B 的 4 处硬编码主读 configs/ 版是正确的**
（它们要的 `assets`/`account_structure`/`risk_parameters` 段只在 configs/ 版存在），
原 D2/D3 的"改主读"方案**作废**。真正要做的是消除"同名异义"陷阱（护栏
`test_two_schemas_are_not_versions_of_each_other` 已固化）。

### F.2 真缺陷：kill_switch 段被同名遮蔽 → total_margin 落到过时口径（已修）

**链路实证**（非读报告）：
1. `ConfigManager.get("portfolio")` 按优先级链解析到 **`config/portfolio.yaml`**（实跑确认 resolved path），
   而该文件**没有 `kill_switch` 段**；
2. ⇒ `get_kill_switch_config()` = `portfolio_cfg.get("kill_switch", {})` = **`{}`**（实跑确认）；
3. 回退链 `self.get("kill_switch")` 也为空 —— `config/kill_switch.yaml`、`configs/kill_switch.yaml` 均不存在（实跑确认）；
4. `kill_switch._load_config()` 路径 2 拿到 `{}` 后**靠"回退旧路径"硬编码 `CONFIG_PATH=configs/account_structure.yaml` 碰巧**读到了阈值段 —— P1-Q8 的统一加载**实际从未生效**；
5. 且该段**没有 `total_margin`** ⇒ `_get_total_margin()` 链落到 **positions.json `meta.total_capital = 5000000`**
   （v8.0 历史头，2026-07-12），而权威口径 = **3000000**（证券 200w + 期货 100w）⇒
   **保证金熔断线被放大 1.67 倍（更晚触发）**。

这正是两条既有铁律的复合案例：「缺数据/空集合 = 通过」的假 PASS 形态
（`dict.get` 恒 None → 落 non-blocking 分支）+「口径未知按严格侧 fail-closed」被违反。

**修复（本增量，采用 D5 独立文件方案）**：
- `kill_switch` 段（level_1/2/3 阈值与动作**原样**）自 `configs/` 迁入独立 **`config/kill_switch.yaml`**
  （非敏感治理配置，经 `.gitignore` 的 `!config/kill_switch.yaml` 例外入版本库 → git 可审计；
  注：`config/portfolio.yaml` 整体被 `config/*` 忽略，故 F.2「内联进 portfolio.yaml」实际不可审计，改采 D5），
  并钉死 `total_margin: 3000000`（权威口径，附来源注释）；
- `configs/account_structure.yaml` **移除** `kill_switch` 段（不留双口径；迁移前整文件备份至 `_archive/dead_code/2026-09-11/configs/`）；
- 读取口唯一：`utils/config_manager.get_kill_switch_config()` 优先 `portfolio.kill_switch`(缺失→{}) 再回退
  `self.get("kill_switch")` → `config/kill_switch.yaml`；
- 实跑复验：`get_kill_switch_config()` → `level_1/2/3 + total_margin=3000000` ✓。

**测试**（修复前必失败）：
- `tests/unit/test_kill_switch_unit.py::TestGetTotalMargin::test_production_config_provides_total_margin`
  （新增；修复前 `ks.config.get("total_margin")` 为 None）；
- 原 `test_env_invalid`/`test_default` 断言 5000000 默认 —— 已改为**隔离配置**（`ks.config = {}`）
  以保留"测默认分支"的原意（配置在场时 3M 优先于默认，属预期行为变化）。

### F.3 D1 已执行

`configs/feature_flags.yaml`、`configs/settings.yaml`（零生产消费死副本）已删除，
备份于 `_archive/dead_code/2026-09-11/configs/`（`configs/` 为 gitignored，只能靠 _archive 兜底）。
注：`configs/account_structure.yaml` 移除段时经 `yaml.safe_dump` 重写，原文件（含全部注释）备份在 _archive。

### F.4 剩余决策点（更新）

| 编号 | 内容 | 状态 |
|---|---|---|
| D1 | 删死副本 ×2 | ✅ 本增量 |
| D2 | ~~portfolio 以哪个版本为准~~ → **改定性**：两文件是不同 schema，非版本冲突；4 处硬编码主读合法 | ✅ 结论反转，无需动作 |
| D3 | ~~4 处主读改走 ConfigManager~~ → **作废**（schema 不同，改走会拿不到字段） | ✅ 作废 |
| D4 | `configs/portfolio.yaml` → `configs/account_structure.yaml` 改名消除同名异义（陷阱源头）—— 5 生产消费方 CONFIG_PATH(morning_info_runner/auto_trading_system/vol_regime_weighter/evolution_orchestrator/theta_engine + liquidation_scheduler/gamma_engine/kill_switch) + ConfigManager "portfolio" 回退名重指向 account_structure.yaml + test_t13/test_config_manager_unit 夹具同步；护栏 `test_two_schemas_are_not_versions_of_each_other` 同步比 config/portfolio.yaml vs configs/account_structure.yaml | ✅ 本增量 |
| D5 | kill_switch 阈值段迁到独立 `config/kill_switch.yaml`（`get_kill_switch_config` 的回退名；非敏感治理配置，`!config/kill_switch.yaml` 入版本库） | ✅ 本增量（即实际采用方案；F.2 因 `config/` 被忽略不可审计而作废） |

## G. GLM-5 路径归一（2026-09-11, item 12 收尾 task 7&8）

### G.1 问题定性（非读报告，实测代码）

`utils/glm5_client.py` 的 `GLM5Client` 命名"GLM-5"却实际委托 `LiteLLMRouter`
（`utils/llm_gateway/litellm_router.py`），而 `LiteLLMRouter.chat()` →
`LLMRouter.chat()` 跑 **5-provider fallback 链 `deepseek → glm → siliconflow → ds4 → ollama`**，
**deepseek 优先** ⇒ "GLM-5" 客户端在生产中绝大多数情况命中 deepseek，命名与行为不一致。
真正的 GLM-5 实现是 `utils/alpha/llm/providers/glm.call_glm`（OpenAI 兼容，
读 `GLM_BASE_URL`/`GLM_API_KEY`/`GLM_MODEL` env），它同时是 `LLMRouter.glm` provider 的调用体。

⇒ 两条 GLM-5 路径（`GLM5Client` 品牌 facade vs `call_glm` 真实现）应归一为**单一实现**。

### G.2 修复（最小、低风险）

- `GLM5Client` 不再依赖 `LiteLLMRouter`/`ChatRequest`/`ChatResponse`，内部 `_call_glm()`
  延迟 `from utils.alpha.llm.providers.glm import call_glm` 并委托之（`provider_cfg={}` 走 env 默认）；
- 公开 API 完全保留：`GLM5Client` / `quick_chat` / `get_glm5_client` 签名不变
  ⇒ 20+ 生产消费方（`glm5_decision_engine`/`value_discipline_layer`/`news_intelligence`/
  `llm_client`/`experience_rag`/`auto_research`/`engineering_debt_gate`/`switchyard_adapter`/
  `mobius_addon` 等）**零改动**；
- `is_ready()` 语义改为 `bool(os.environ.get("GLM_API_KEY"))`（与 `call_glm` 前置条件一致，
  原先 `is_ready` 只看 LiteLLMRouter 是否可 import，与"GLM-5 是否真能用"脱节）；
- `get_stats()` 改为本地累计 `total_calls/success_calls/success_rate`（去掉对 LiteLLMRouter 统计的耦合）；
- `GLM5Client.chat()` 返回 `{"role","content","model"(=GLM_MODEL 或 glm-5.2),"provider":"glm"}`。
- 顺带消除一层间接依赖：`glm5_client.py` 不再 import `utils.llm_gateway`（去耦合）。

### G.3 测试（修复前会失败 / 固化契约）

- `tests/unit/test_glm5_client_unit.py`：`TestRouterUnavailable`/`TestRouterAvailable`
  （mock `_get_router`/`utils.llm_gateway.ChatRequest`）改为 `TestApiKeyMissing` /
  `TestCallGlmDelegation`，其中 `test_chat_success_delegates_to_call_glm` 用 monkeypatch
  钉死 `chat()` 必走 `call_glm` 且透传 prompt/temperature/max_tokens/provider_cfg
  （**路径归一契约**，防未来又被改回 deepseek fallback）；`test_chat_none_from_call_glm_returns_error` /
  `test_chat_exception_returns_error` 固化 fail-open。
- 验证：`ruff check` clean；`test_glm5_client_unit.py` 23 passed；
  `test_item12_llm_entry_consolidation_20260911.py` 12 passed（`utils/glm5_client.py`
  仍作为 LIVE LLM 入口，未被误删）；`test_llm_client_unit.py` 23 passed；
  对 `value_discipline_layer`/`news_intelligence`/`glm5_decision_engine`/`llm_client`
  导入冒烟 OK；mypy 对 `glm5_client.py` 自身 0 新增错误（基线 317 不受影响）。

### G.4 设计铁律呼应

- 「完成声明≠完成」：ruff + 23 单测同轮验证；
- 「审计报告口径不可直接采信」：本增量直接读 `LiteLLMRouter.chat` 源码确认 deepseek 优先，
  而非仅信 `glm5_client.py` 模块 docstring 的"GLM-5 客户端"命名；
- 单一事实源：`call_glm` 现是全系统唯一 GLM-5 调用点，`LLMRouter.glm` provider 与
  `GLM5Client` 复用同一实现，消除双口径。

