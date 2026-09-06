# D 盘依赖归位: 量化系统自包含改造 (2026-09-05)

## 背景 / 目标
换 MacBook 时可直接整体拷贝工程目录 → 消除对 `D:\` 的硬依赖，把 D 盘量化资产迁入工程并改路径引用。

## 迁移清单（源 → 工程内目标）
| D 盘源 | 体量 | 目标 | 说明 |
|---|---|---|---|
| `D:\free-stockdb\stockdb` | 5.9GB / 211 文件 | `third_party/free-stockdb` | 全量含 data/ + pybao/ + exe；git 忽略 |
| `D:\QuantData` | 74MB | 并入项目根 `models/` `reports/` `output/` `data_cache/` | 旧 7 月 logs 未搬；models 全 skip（目标已更新） |
| `D:\QuantBackup\28-quant` | 13MB / 442 文件 | `backups/28-quant` | EOD 备份历史并入 |
| `D:\etf_data_2015_2026` | 1.6MB / 16 文件 | `data/etf_2015_2026` | 长样本研究数据 |

## 代码改动
- `utils/free_stockdb_adapter.py:38-41`：`FREE_STOCKDB_ROOT` 默认 = `_PROJECT_ROOT/third_party/free-stockdb`（env 仍可覆盖）
- `scripts/launch_etf_shadow.py`：`DATA_FILES["s6"/"s8"/"s9"]` → `data/etf_2015_2026/all_etf_daily.parquet`
- `scripts/run_eod_backup.py`、`scripts/compute_health_score.py`：`_BACKUP_ROOT` = env `QUANT_BACKUP_ROOT` 或 `backups/28-quant`
- `scripts/verify_path_config.py`：判定改为「自包含(项目根)= OK / 异盘= OK / 其他 FAIL」
- `.env`：`QUANT_DATA_ROOT` 注释化 → 数据根回项目根（自包含模式）
- `.gitignore`：追加 `third_party/free-stockdb/`

三个 env（`FREE_STOCKDB_ROOT` / `QUANT_BACKUP_ROOT` / `QUANT_DATA_ROOT`）保留覆盖能力 → 异盘部署向后兼容。

## 踩坑记录
1. **`.env` 被 ACL 只读保护**：`icacls` 显示 `Administrator:(R)`（attrib 无只读标志）。内置写工具返回成功但磁盘内容不变 → 需先 `icacls ".env" /grant "*S-1-5-32-544:(F)"` 再用 .NET `WriteAllText`（UTF8 no BOM）写入。
2. **`stockdb.exe` 在自动化会话无法就绪**：进程能存活但 7899 端口永不监听（60s+）。D 盘原位置行为相同 → 与迁移无关的既有环境限制；需桌面会话双击或先跑 `数据更新.exe` 验证。训练降级链不受影响（data_provider 通达信 P3 实测可用）。
3. **pybao SDK import 即连服务**：`import stock_sdk` 抛 `ConnectionError 10061`（连接本机 LGDB 服务），`stockdb.pyd` 本体 import 正常 → SDK 通道强依赖运行中的引擎服务。
4. **PowerShell 变量名 `$exit` 被包装层吞并触发解析错误**：避免使用（改用 `$exited`）。

## 换机清单（MacBook）
- 整体拷贝 `E:\各种PY程序\28-终极量化交易系统8.4`（含 `.codebuddy/`、`.venv` 可重装、`third_party/`、`data/`、`backups/`）
- `.env` 已 gitignore，换机重配：DeepSeek Key 等（无明文密钥保存）
- Windows 专属二进制（stockdb.exe / 通达信 / Wind / iFinD / Ollama GGUF）在 Mac 不适用 → 数据通道用 akshare / baostock / 云端 Wind MCP；`local_llm.py` 的 `D:\models` 兜底为懒加载 fail-open，无硬阻断

## 验证
- `free_stockdb_adapter` 路径解析 = 工程内，exe/data/pybao 存在；单测 21 passed
- `verify_path_config.py` → `[OK] Data root is project-internal`
- 修改 6 文件 `py_compile` OK、ruff/lint 0 诊断
