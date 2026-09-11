# item 12 第一增量 — `config/` vs `configs/` 双目录分歧实测 (2026-09-11)

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

## B. `configs/portfolio.yaml` 的 6 个生产消费方（实测行号）

| 文件:行 | 读法 |
|---|---|
| `utils/config_manager.py:107,533` | **已修复**：`get_portfolio_config()` 优先 `config/`（此前缺失单数目录导致落到 configs/） |
| `utils/kill_switch.py:62-69` | **已修复**（P1-Q8）：ConfigManager 优先级链「v8.3 唯一事实源 > configs/ 历史回退」 |
| `15_每日工作流/morning_info_runner.py:230` | **直读硬编码** `PROJECT_ROOT/"configs"/"portfolio.yaml"`（主读）|
| `utils/alpha/evolution_orchestrator.py:728` | **直读** `Path("configs/portfolio.yaml")`（主读）|
| `utils/alpha/vol_regime_weighter.py:1024` | **直读** `Path("configs/portfolio.yaml")`（主读；且其 8 类风格大类注释声明与 configs 版 `style` 字段对齐）|
| `utils/auto_trading_system.py:446` | **直读** `Path("configs/portfolio.yaml")`（主读）|

即：**4 个模块仍把 `configs/portfolio.yaml` 当主读**，与声明口径相悖。

## C. 内容分歧的业务含义（为什么不能机械合并）

`config/portfolio.yaml`（13685B）比 `configs/portfolio.yaml`（12285B）多 ~1.4KB。
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
