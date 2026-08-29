# 28-终极量化交易系统 8.4 → macOS (MacBook Pro) 迁移指南

> 本文件与 `macos/launchd/` 配套，承接代码扫描结论。目标：**同一份代码在 Windows 现在照常跑、迁移到 macOS 后也能跑**。

---

## 0. 先说结论（什么会变、什么不变）

| 维度 | Windows 现状 | macOS 迁移后 |
|------|-------------|--------------|
| 主系统代码 | 相对路径 + `_PROJECT_ROOT` | 不变 ✅ |
| 配置 `settings.yaml` / `system_config.json` | 无盘符硬编码 | 不变 ✅ |
| 主入口 CLI（`量化策略系统_统一入口_v8.6.py` 等） | 动态定位 | 不变 ✅ |
| 定时调度（9 个 `.ps1/.bat`） | 任务计划程序 | 改用 **launchd**（见 `macos/`） |
| QMT 实盘下单 / WindPy 原生客户端 | 可用 | **不可用 ⛔**（见 §5） |
| Python 解释器 | `.venv/Scripts/python.exe` | `.venv/bin/python`（已自动适配） |
| 中文 GBK 文件读写 | gbk | utf-8 优先、gbk 回退（已内置） |

---

## 1. 已完成的代码改造（P0，跨平台安全）

全部为**非破坏性**修改，Windows 当前行为保持不变：

1. `ms_strategy/scripts/live_scheduler.py` — 写死的 `C:\Users\...\Python311\python.exe` → `sys.executable`。
2. `utils/risk_guard_integrator.py` — GBK 字符清洗 hack 改为按日志流实际编码降级（Mac=utf-8 不再丢 emoji/生僻字）。
3. 旧 `28-终极量化交易系统7.1` 失效路径（已指向不存在的 7.1）→ 改为 8.4 相对根 + `QLIB_DATA_DIR` 等环境变量覆盖：
   - `ms_strategy/scripts/signal_monitor.py`
   - `ms_strategy/scripts/rebalance_execution_orders.py`
   - `ms_strategy/scripts/hedge_execution_orders.py`
   - `ms_strategy/training/feature_importance_analysis.py`
   - `ms_strategy/training/qlib_train_test.py`（原为 8.4 盘符路径）
4. 跨项目 Windows 路径（`11_量化策略` + `C:\Users\Administrator\...`）→ 环境变量覆盖：
   - `ms_strategy/scripts/vol_adjusted_stop_loss.py`（`QUANT11_ENV_FILE` / `WIND_MCP_SKILL_DIR` / `QUANT11_STOPLOSS_CONFIG`）
5. venv 解释器探测补上 `.venv/bin/python`：
   - `scripts/gate_check_daily.py`（原写死 `.venv/Scripts/python.exe`）
   - `scripts/install_scipy_and_train.py`（`qlib_env/Scripts/python.exe` → 按平台选 `bin`）
   - `scripts/ci_integrity_check.py` / `scripts/_verify_phase3b_static_analysis.py` / `scripts/_run_v9_regression.py`（venv 探测函数增强）

### 已确认无需改（已是跨平台）
- `ui/components/report_viewer.py`、`utils/value_investing/ashare_data.py`：本地/网络读取已是 **utf-8 优先、gbk 回退**。
- `tools/wind_mcp_fetcher.py`：`python.exe→node.exe` 替换仅在 Windows 触发，Mac 走 `else "node"`，正确。
- `scripts/review_gate.py` 等：本就用 `os.name == "nt"` 或 `PYTHON_EXECUTABLE` 环境变量分支。

---

## 2. macOS 环境准备

```bash
# 1) 安装 Python 3.14（与 Windows venv 同版本，推荐 pyenv 或官方 pkg）
# 2) 在项目根重建 venv（注意是 bin/ 不是 Scripts/）
cd 28-终极量化交易系统8.4
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # 或 pyproject 依赖

# 3) 必需环境变量（写入 ~/.zshrc）
export NO_PROXY="push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export PYTHONIOENCODING=utf-8
# 敏感密钥（勿提交）
export WIND_API_KEY="..."
export TUSHARE_TOKEN="..."
```

> 注意：原 Windows `.venv` 是 Windows 专属，**必须在 Mac 上重新 `python3 -m venv` 创建**，不能直接拷贝。

---

## 3. 定时任务：从 Windows 任务计划程序迁移到 launchd

Windows 的 9 个 `.ps1/.bat` 在 macOS 不可用，已提供等价 launchd 方案：

| 原 Windows 脚本 | macOS 等价（launchd Label） | 调度 |
|----------------|------------------------------|------|
| `run_eod_workflow.bat` / `run_eod_with_env.bat` | `com.yuppie.quant.eod` | 每个交易日 16:15 |
| `run_daily_morning.bat` | `com.yuppie.quant.morning` | 每个交易日 08:30 |
| `run_daily_morning8.bat` → `run_daily_morning.py --phase all` | `com.yuppie.quant.morning8` | 每个交易日 08:00 |
| `run_weekly_report.bat` → `research/generate_weekly_report.py --no-pdf` | `com.yuppie.quant.weekly` | 周五 16:30 |
| `setup_*_scheduled_task.ps1` + `run_auto_retrain.py` | `com.yuppie.quant.retrain` | 每日 02:30 |

**安装：**
```bash
bash macos/setup_launchd.sh
# 或指定路径
bash macos/setup_launchd.sh /Users/you/28-终极量化交易系统8.4
```

**机制：** 每个 plist 调用 `macos/launchd/run_quant_job.sh` 包装器，由它注入上面的环境变量并选用 `.venv/bin/python`。调度时间写在 plist 的 `StartCalendarInterval`，可按需调整（A 股节假日/交易时段）。

**查看 / 卸载：**
```bash
launchctl list | grep yuppie
launchctl unload ~/Library/LaunchAgents/com.yuppie.quant.*.plist
```

---

## 4. 数据目录（环境变量覆盖）

部分训练/信号脚本依赖 QLib 数据目录，迁移后通过环境变量指定真实位置：

```bash
export QLIB_DATA_DIR="/path/to/qlib_data/cn_data"   # signal_monitor / feature_importance / qlib_train_test
export QUANT11_ENV_FILE="/path/to/11_量化策略/.env"
export WIND_MCP_SKILL_DIR="/path/to/wind-mcp-skill"
export QUANT11_STOPLOSS_CONFIG="/path/to/11_量化策略/config/stop_loss_rules_auto.yaml"
```

---

## 5. ⛔ 硬约束：QMT / WindPy 无法在 macOS 实盘

- **QMT / xtquant** 实盘下单：仅 Windows（依赖 Windows 客户端 + 券商插件）。macOS 上 `xtquant` 无法安装，`broker_factory` 会按设计 fail-open 降级到 `SimulatedBroker`（不裸实盘）。
- **WindPy 原生客户端**：仅 Windows。
- **结论**：macOS 笔记本可作为**研究 / 回测 / 盘后报告 / 影子账户追踪**节点；**真实下单必须保留一台 Windows 机器作为远程执行节点**。架构上 `TRADING_ENV=production` 双签 + `broker_factory` 四重门控已支持分离部署，实盘指令从 Windows 节点发出即可。

---

## 6. 验证清单（迁移后在 Mac 上跑一遍）

```bash
cd 28-终极量化交易系统8.4
source .venv/bin/activate

# 1) 配置/数据链路自检
python 量化策略系统_统一入口_v8.6.py --check

# 2) 盘后 EOD 工作流（干跑，不交易）
python 15_每日工作流/run_daily_eod_workflow.py --dry-run

# 3) CI 三件套
python scripts/engineering_debt_gate.py
python scripts/assert_data_validity.py
python scripts/industrial_grade_check.py

# 4) 门禁静态检查
python -m ruff check .
```

若 `assert_data_validity` 的 D11（Phase B shadow 7 天）为 RED，属预期（需真实交易日数据积累），非迁移故障。

---

## 7. 仍忽略的项（纯历史数据，无害）

`v8.3_institutional/trade_plans/*.json`、`reports/daily_pnl_report_*.json` 内嵌的 `E:\各种PY程序\...` 字符串是历史错误信息，纯数据、不参与运行，无需处理。
