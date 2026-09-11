# Quickstart: G1 QMT 真实下单接线（真机操作手册）

**用途**：本文件是**唯一权威的操作指引载体**（FR-007）。其他脚本/报告内的指引若与本文件冲突，以本文件为准。
**依据**：所有路径、端口、环境变量名均**实测自代码**（2026-09-11），非凭记忆书写。

> ⚠️ 本手册只覆盖**模拟盘（paper）**演练。真实资金切换属生产切换窗独立 Go/No-Go，不在本 feature 范围（FR-010）。

---

## 0. 前置清单（缺任一项则 AC-004/AC-005 无法完成）

| # | 前置 | 检查命令 | 期望 |
|---|---|---|---|
| 1 | QMT 客户端已安装且**已登录模拟账户** | 客户端界面确认 | 登录成功 |
| 2 | `xtquant` **可用**（能力级，**非**"能 import"） | `C:\Users\Administrator\xtquant_env\Scripts\python.exe -c "from xtquant import xtdata; from xtquant.xttrader import XtQuantTrader; print('OK')"` | `OK` |
| 3 | 运行 broker 侧代码的解释器 **≤ Python 3.13** | 该解释器 `-c "import sys;print(sys.version)"` | `3.11.9`（本机实测可用） |
| 4 | 模拟账户已开通 A 股交易权限 | 客户端确认 | — |
| 5 | 若跨机部署：云↔实盘机网络打通（Tailscale/WireGuard/云 VPN） | 见 §3 客户端变量 | 可连通 |

> ⚠️ **`xtquant` 的坑（2026-09-11 实测，务必先读）**：其 wheel 标记 `py3-none-any` ⇒ **任意** Python 都能 `pip install` 成功；
> 但二进制只提供 **cp36–cp313**。项目主 venv 是 **Python 3.14** ⇒ `import xtquant` **会成功**，而
> `from xtquant import xtdata` / `from xtquant.xttrader import XtQuantTrader` 全部 `ImportError`
> —— "装上了、能 import" 与 "真的能用" 是两回事，**只测顶层 import 会得到假绿**。
> 因此前置判据必须是**能力级（子模块）**，且 broker 侧须在 **Python ≤ 3.13** 解释器中运行
> （本机可用环境：`C:\Users\Administrator\xtquant_env`，**Py3.11.9**）。
> 防复发回归：`tests/unit/test_qmt_paper_chain_gate.py::test_xtquant_check_rejects_namespace_only_install`。

> `xtquant` 只能从 QMT 官方渠道获取（随终端提供），**不纳入本仓依赖管理**（FR-008）；
> **不要**把它装进项目主 venv（Py3.14 上装得上但功能全废，只会制造假绿）。

## 1. 配置（唯一事实源 = 仓库根 `system_config.json`）

> ❌ **不要**再编辑 `config/system_config.json` —— 该文件已于 2026-09-07 合并到根目录并删除；若在旧文档/旧报告里看到该路径，即为漂移（本 feature 负责修正，见 AC-010）。

`broker` 段实测当前键（模拟盘默认）：

```json
{
  "type": "qmt",
  "enabled": false,          // 切真实通道需 true
  "dry_run": true,           // 切真实通道需 false
  "account_id": "",
  "session_id": 0,
  "account_type": "STOCK",
  "qmt_path": "",
  "connect_timeout": 10
}
```

**四重门控**（缺一即回退 `SimulatedBroker`，防裸实盘，FR-002）：

1. `broker.enabled = true`
2. `broker.dry_run = false`
3. 环境变量 `TRADING_ENV=production`
4. 通道装配成功（装不上 → 抛 `LiveBrokerUnavailableError`，**拒绝启动**，不降级）

## 2. 环境变量（实测自代码，勿臆造）

**服务端**（`utils/execution/qmt_rpc_server.py`，部署在 Windows 实盘机）：

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `QMT_ACCOUNT_ID` | ✅ | — | 资金账号；缺失则拒绝连接 |
| `QMT_PATH` | ✅ | — | QMT 客户端 `userdata_mini` 路径；缺失则拒绝连接 |
| `QMT_SESSION_ID` | — | `0` | 会话号 |
| `QMT_ACCOUNT_TYPE` | — | `STOCK` | 账户类型 |
| `QMT_RPC_TOKEN` | ✅ | — | 鉴权 token；**未配置则网关拒绝服务（503）** |
| `QMT_RPC_HOST` / `QMT_RPC_PORT` | — | `127.0.0.1` / **`8765`** | 绑定地址/端口 |
| `QMT_RPC_ALLOWED_IPS` | 非回环时必需 | — | 白名单；非回环且未配置则**拒绝启动**（fail-closed）。另有 `QMT_RPC_ALLOW_PUBLIC=1` 可覆盖（须确认已前置 TLS/隔离） |
| `QMT_RPC_TRUST_PROXY_HEADERS` | — | 关 | 仅在网关置于**可信反代**之后时设 `1` |

**客户端**（`utils/execution/remote_qmt_broker.py`，云端策略侧）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `QMT_RPC_URL` | — | 网关地址（如 `http://10.0.0.2:8765`） |
| `QMT_RPC_TOKEN` | — | 须与服务端一致；缺失则连接失败 |
| `QMT_RPC_TIMEOUT` | `10` | HTTP 超时（秒） |

> **核对项（待实现阶段确认）**：根 `system_config.json` 的 `broker.comment` 提到登录凭据用 `QMT_ACCOUNT`/`QMT_PASSWORD`，而 RPC 服务端代码读取的是 `QMT_ACCOUNT_ID`/`QMT_PATH`/`QMT_SESSION_ID`。二者可能是**不同关注点**（客户端登录 vs xtquant 订阅），但存在误用风险 → 在 P3 落地时核实并统一注释口径，**不得**凭猜测改代码。

## 3. 启动与验证

```powershell
# 1) 启动 QMT RPC 网关（Windows 实盘机侧）
python utils/execution/qmt_rpc_server.py --port 8765

# 2) 代码链路验证（不依赖 QMT 终端，任何机器可跑 —— 作为回归基线）
.venv/Scripts/python.exe scripts/verify_qmt_sim_chain.py
#    期望: 12/12 PASS，报告落 reports/execution/qmt_sim_chain_verification_<date>.md

# 3) 真实终端段验证（本 feature 新增入口；需 §0 前置就位）
#    ⚠️ 必须用 **Python ≤ 3.13** 的解释器（xtquant 二进制仅 cp36–cp313；主 venv 是 3.14）：
C:\Users\Administrator\xtquant_env\Scripts\python.exe scripts\verify_qmt_paper_chain.py --account <模拟资金账号>
#    期望: 全项 PASS + 报告落 reports/execution/
#    先验: 加 --preflight-only，前置缺失时应 RC=2 并逐条列因（不是静默通过）
#    ✅ T013 已验证（2026-09-11 实测 [OK]）: 该解释器**可导入**本仓 utils.execution.broker_factory

# 4) 成交对账
.venv/Scripts/python.exe scripts/run_trade_reconciliation.py

# 5) C1 判据确认（enabled=true + dry_run=false 时应由 WARN 转 PASS）
.venv/Scripts/python.exe scripts/industrial_grade_check.py
```

> 端口说明：**8765** 是网关默认端口（`QMT_RPC_PORT` 默认值，代码实测）；`verify_qmt_sim_chain.py` 内部使用的 `18765` 是其**沙箱专用端口**，目的是不与真实网关默认端口冲突 —— 二者不矛盾，**无需"统一"**。

## 4. 常见失败与处置

| 现象 | 根因 | 处置 |
|---|---|---|
| `LiveBrokerUnavailableError` 启动即抛 | 门控已开但通道装不上（xtquant 缺失/终端未登录） | 检查 §0 前置；**不要**为了先跑起来而关掉门控（会破坏防裸实盘设计） |
| 网关启动报 503 `QMT_RPC_TOKEN 未配置` | token 未设 | 设置服务端 `QMT_RPC_TOKEN` |
| 网关启动被拒（非回环 + 无白名单） | 安全策略 fail-closed | 设 `QMT_RPC_ALLOWED_IPS`，或确认直连/前置 TLS 后用 `QMT_RPC_ALLOW_PUBLIC=1` |
| 客户端 `QMT_RPC_URL / QMT_RPC_TOKEN 未配置` | 云端侧变量缺失 | 配置并确认与服务端 token 一致 |
| 日志显示走了 `SimulatedBroker` | 四重门控有缺失 | 逐项核对 §1；**这是预期行为**（防裸实盘），非 bug |

## 5. 回滚（退回模拟盘）

```powershell
# 方式一：改回默认（推荐）
#   根 system_config.json → broker.enabled=false（或 dry_run=true）
# 方式二：撤销实盘意图
#   清除 TRADING_ENV=production
```

回滚后确认：`industrial_grade_check.py` 的 C1 恢复为 WARN（符合 ROADMAP 对当前阶段的记录），且全链路 0 笔真实报单（AC-007）。

## 6. 口径引用（不复制数值）

灰度档位、生产切换窗、门禁三件套判据、C1 WARN 的决策截止日 —— 均以 `cairn/ROADMAP.md` §CURRENT STATE 为**唯一事实源**，本文件不复写。
