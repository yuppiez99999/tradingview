# W6.3.3 预研 · QS-Trader 风格 secid 合约解析难点清单

> 输出日期: 2026-08-11 · 所属排期: [高价值项目集成排期计划_20260811.md §4 Sprint 3](file:///E:/各种PY程序/28-终极量化交易系统8.4/docs/高价值项目集成排期计划_20260811.md)
> 预研目标: 在引入 QS-Trader 的 NewType 类型安全 + secid 感知合约解析设计前, 识别当前系统已有的代码格式 / 解析逻辑 / 数据结构层面的隐性分裂, 作为 W6.3.3 落地的输入。

## 0. W6.3.3 任务拆解（与 QS-Trader 对应关系）

| W6.3.3 子项 | 借鉴 QS-Trader 要素 | 当前系统痛点 |
|---|---|---|
| A. `typing.NewType` 合约代码分层 | `SymbolCode` / `ContractId` / `SecId` / `ExchangeCode` / `ProductCode` 区分不同语义, mypy 静态防错 | 全系统裸 `str`, 传参易混淆, 392 处 `# type: ignore` 与合约代码强相关 |
| B. 统一代码解析入口 `parse_symbol()` | 一个函数按资产类型 dispatch, 严格校验 (非匹配抛异常) | 至少 3+ 处本地手写 `_secid` 规则 + 1 处期货正则, 互不调用, 分支不完整 |
| C. 合约注册表 `ContractSpec/ContractUniverse` | 按合约 ID 查询乘数/保证金/到期日/最小变动价位, 不依赖模块内硬编码 | 商品期货元数据 2 处各自硬编码表; 股指期货到期日仅单模块支持; 未建注册表单例 |

## 1. 代码格式分裂（8+ 种同时存在, 100+ 文件引用）

当前系统至少并行 **8 种"代码/交易所"的表达格式**, 各种格式在不同模块中被手写转换, 且没有单一入口:

| 编号 | 格式示例 | 使用位置 | 类型语义 | 备注 |
|---|---|---|---|---|
| 1 | 6 位裸码 `600519` / `510300` / `300750` | 回测测试辅助 `test_event_driven_engine.make_bar()` / 上游外部调用方 | AShareCode6 (6位纯数字裸码) | ✅ 直接用 code 字段, 不含任何交易所信息 |
| 2 | Wind 风格后缀 `600519.SH` / `300750.SZ` / `IF2507.CFFEX` | wt_structs 官方约定 (`TickData.code: str` 注释示例); conftest / test_constraints; ETF flow wc | WindCode (code + "." + 交易所后缀) | ✅ wt_structs 里 code 和 exchange **两个字段同时存在**, 存在冗余不一致风险 |
| 3 | 前缀式 `sh510300` / `sz300750` | `utils.astock_realtime._tx_prefix()` 局部 | TencentPrefix (腾讯/新浪行情用) | 仅 astock_realtime 内部使用 |
| 4 | 东财 secid `1.510300` / `0.300750` | `utils.astock_realtime._secid()` 手写; `utils.etf_flow_monitor._fetch_eastmoney_flow()` **行内重写一遍** | EastMoneySecId | 🔴 **分裂: 两处 secid 规则不一致** (etf_flow_monitor 只区分 "SH"/"SZ", 没有 astock 里 `58/68/113/123/127/128` 可转债/ETF 前缀判断) |
| 5 | 期货 `FUTURES_CODE_PATTERN` `IF2507.CFFEX` / `CU2508.SHF` | `utils.futures_rollover_manager` 单独正则 | FuturesContractCode (正则匹配) | 🔴 **分裂: SHF vs SHFE, ZCE vs CZCE, 缺 INE** (见 §2.3) |
| 6 | 期货字典元数据 `{"code":"CU", "name":"铜", "exchange":"SHFE"}` | `utils.attribution.managers.SUPPORTED_COMMODITIES`; 多处 `directional_futures_trader.get_spec()` 中 dict | ProductCode (品种代码 + 交易所 dict) | 🔴 与 #5 正则的 "SHF" 不兼容 |
| 7 | 字符串拼接式 `underlying_code + {exchange_suffix}` | `execution.automated_execution_system:2182`; `broker_adapters` | 裸拼接无验证 | 🔴 无法静态校验是否合法 |
| 8 | QMT/实盘 Plain `IF2507` + 外部字段 `exchange=CFFEX` | `daily_build_and_hedge` / `executor` | PlainCode + Exchange | 与 wt_structs 的 Wind 风格 code 字段存在冲突 (code 里没有 .CFFEX 后缀) |

### 分裂直接后果 (已发现)
- 同一标的跨模块流转时, 若没有统一 "Normalize → Canonical WindCode" 步骤, **订单会因交易所后缀匹配不上而被撮合引擎/风控引擎静默忽略** (因撮合匹配条件是 `order.code == event.code` 裸串比较)
- secid 生成: ETF/可转债/退市板块容易被分配错误市场前缀 (1 vs 0), 导致东财 API 返回空数据 / 错数据

## 2. 期货解析具体隐患

### 2.1 `FUTURES_CODE_PATTERN` 与 `managers` 元数据不兼容

- `futures_rollover_manager.py:66` 正则:  
  `re.compile(r"^([A-Za-z]+)(\d{2})(\d{2})\.(CFFEX|SHF|DCE|ZCE|GFEX|CZCE)$")`
- `managers.py:51-58` 商品期货表:
  - 上期所 `SHFE` (3 字符) → 正则只接受 `SHF` (2 字符) ❌
  - 上海国际能源交易中心 `INE` (原油 SC) → 正则未包含 ❌
  - 郑商所同时写了 `ZCE` 和 `CZCE` (两套命名, 正则虽都包含但 managers 未统一) ❌
- **实际影响**: `FuturesRolloverManager.is_tradable("SC2509.INE")` → 返回 False, 原油等 INE 品种被误判为不可交易

### 2.2 股指期货/商品期货/期权支持严重不均
- 已实现到期日计算: 仅 `IF/IC/IM/IH` 四个股指期货品种 (`_get_futures_expiry`), 用 "第三个周五" 规则
- 商品期货: 到期日 `_get_futures_expiry` 返回 `None` (所有商品无法计算距到期天数 → `detect_rollover_need` 退化为 "到期前 3 天" 比较为 False 分支, 商品永不自动换月 ❓)
- 期权完全没有专用代码解析 (期权命名 `510300购2512月3500` 类结构没有解析函数)

### 2.3 多资产格式统一困难
| 资产类型 | 代码规则样例 | 需解析出的结构化字段 |
|---|---|---|
| 股票 | 600519.SH / 000001.SZ / 688981.SH / 900957.SH (B股) | code_numeric, market (SH/SZ/BSE/STAR) |
| ETF/LOF | 510300.SH / 159915.SZ / 501050.SH / 161725.SZ | product_class = ETF / LOF, 跟踪指数, T+0 / T+1 |
| 可转债 | 113050.SH / 127015.SZ / 110044.SH | 转股价 / 正股代码 / 强赎触发价 / 到期收益率 |
| 股指期货 | IF2507.CFFEX / IH2507.CFFEX | product, year, month, exchange, 第三个周五到期日, 乘数 300 |
| 商品期货 | CU2508.SHFE / SC2509.INE / M2509.DCE | product, year, month, exchange, 品种元数据 (乘数/保证金/最小变动) |
| 期权 | IO2507-C-4000.CFFEX / MO2507-P-2700.CFFEX / 510300购2512月3500 | underlying, expiry, put/call, strike, 美式/欧式 |
| 债券/国债期货 | TL2509.CFFEX | 需另外建模 |

**没有统一注册表**会导致: 每种资产在 3-5 个模块里重复实现相同的"判断→提取→元数据查询"流程, 且改一处规则需要同步 N 处 (当前已有 §2.1 正则 vs dict 的分裂案例)。

## 3. type: ignore 分布与合约代码关联

全局 `# type: ignore` 共 **392 处 / 100 文件**。按模块 Top 15 (均 ≥5 处):

| 文件 | 处数 | 合约代码强相关? | 典型场景 |
|---|---|---|---|
| `utils.ifind_client` | 34 | ✅ 强相关 | 同花顺 iFinD 返回 code 格式差异 + 动态属性透传, 大量加 ignore 绕过 |
| `utils.data_provider` | 29 | ✅ 强相关 | 多源 (Wind/iFinD/akshare/本地) 返回 code 格式不统一, DataFrame 转 dict 丢类型 |
| `utils.execution.automated_execution_system` | 15 | ✅ 强相关 | 执行层合约代码组装 + broker 适配, 动态字段透传 |
| `utils.directional_futures_trader` | 10 | ✅ 强相关 | futures spec dict 取 multiplier/margin_rate, 用 `[key]  # type: ignore` 绕过 Missing key |
| `utils.execution_algo_engine` / `greek_hedge_manager` / `hedge_execution_engine` / `gamma_engine` (合计 28) | 各 5-9 | ✅ 强相关 | 期权/期货代码解析 + 合约乘数运算 |
| `utils.akshare_data_source` | 12 | ⚠️ 中等 | AkShare 函数参数 str 动态拼接 code |
| `utils.etf_flow_decision` / `attribution/managers` / `daily_panel` 等 | 合计 20 | ⚠️ 中等 | code 格式转换 + DataFrame 列操作 |

**判断**: W6.3.3 引入 NewType + 统一 `parse_symbol()` 后, 上述 **120+ 处 ignore 可消除约 30~40 处** (尤其是 directional_futures_trader / execution / managers 这类直接依赖 spec dict 取数的位置), 验证标准中 "mypy 类型错误下降 ≥30%" 需结合 Wave 3 基线, 但仅 backtest + 合约相关模块有把握达到。

## 4. wt_structs 冗余字段: code + exchange 并存, 但无校验

`wt_structs.py` 每个数据类 **都有 `code: str` 和 `exchange: str` 两个独立字段**, 但:
- 没有运行时或静态类型保证两者一致 (例如可能写出 `code="600519.SZ"` 但 `exchange="SSE"` 的矛盾组合)
- 约定不明: code 是 "600519" 还是 "600519.SH"? 注释写的是后者 (`code: str  # 标的代码,如 "510300.SH"`), 但 backtest 引擎 / 撮合引擎 / 持仓字典都用 `order.code == event.code` 裸串比较 — 如果构造事件时一方写裸码一方写 Wind 码, 会永远不匹配 (**已在测试中踩坑**: `test_event_driven_engine.make_bar()` 写 `code="600519.SH"` vs 旧辅助函数写 `"600519"` 不一致时, 订单会被撮合引擎跳过)
- 没有构造时规范化函数, 只能靠调用方自觉

## 5. 现有 3 处 secid/code 本地实现 (互不调用)

### 实现 1: `utils.astock_realtime._secid(code)` → 东财 secid
```
沪前缀集合: ("51", "58", "60", "68", "9", "11", "113", "110")  → 1.xxx
深前缀集合: ("15", "16", "00", "30", "12", "123", "127", "128")  → 0.xxx
fallback: 1.xxx
```
✅ 覆盖了 ETF(51/58/15/16) / 股票(60/68/00/30/9=B股) / 债券可转债(11/113/12/123/127/128) / 缺省沪

### 实现 2: `utils.etf_flow_monitor._fetch_eastmoney_flow` 行内拼接
```python
wc = self._to_wind_code(etf_code)  # '510300.SH' / '510300.SZ'
num, mkt = wc.split(".")
secid = f"1.{num}" if mkt == "SH" else f"0.{num}"
```
🔴 缺陷: 只看交易所后缀 "SH"/"SZ" 来判断 1/0, 若遇到 B 股 `900957.SH` (其实应 `1.900957` 没问题), 若遇到跨市场前缀未对齐时无法判断 — 如 SH 科创板 688 前缀 / SZ 创业板 30 前缀, 由于 SH=1, SZ=0, 结果恰好正确; 但它没有像 #1 那样走 "前缀优先" 规则, 如果未来有新板块 (如 BE 北交所 8/4 开头), 会错分。

### 实现 3: `utils.futures_rollover_manager._parse_contract`
期货专用正则 (非函数式 dispatch, 仅单模块内部调用), §2.1 已列分裂隐患。

### 缺失: 北交所
北交所 8 开头 (83xxxx) / 4 开头 (43xxxx) 的 code → Wind 后缀应 `.BJ` / `.BSE`, 东财 secid 的 0/1 归属? 现有两处 secid 规则都没覆盖, **存在前视偏差数据源隐患 (secid 错 → 请求错市场 → 返回空, 若未校验则退化为价格 0 → 组合权益失真)**。

## 6. 落地优先级建议 (为 W6.3.3 实施做准备)

| 阶段 | 工作 | 预计消除 type ignore | 兼容回退策略 |
|---|---|---|---|
| Step 0 | 建立 `utils/contracts/` 包 + `utils.contracts.symbols` 模块: 定义 NewType (`AShareCode6`, `WindCode`, `EastMoneySecId`, `FuturesContractCode`, `ExchangeCode`, `ProductCode`) + 1 个公开函数 `parse_symbol(s: str, *, hint_asset: Literal["auto","stock","etf","future","option","bond"]="auto") -> SymbolInfo` dataclass | 准备阶段 0, 后续替换才下降 | NewType 在运行时退化为普通 str, 不影响生产行为; parse_symbol 默认宽松 warn 模式 strict=False, 不抛异常先打日志 |
| Step 1 | 迁移 3 处 secid / contract 本地实现到统一入口: `_secid()` + ETF 行内拼接 → `to_eastmoney_secid(parse_symbol(x))`; `_parse_contract` → `parse_symbol(x, hint_asset="future")` | 下降 ~10 处 (减少 futures 相关 ignore) | 保留旧函数 1 个版本, 内部调统一入口, 做 100% 行为对齐回归 |
| Step 2 | 修复 `FUTURES_CODE_PATTERN` → `SHFE` + `INE`; 郑商所统一为 `CZCE`; 新增 `ContractRegistry` 单例, 商品期货 SUPPORTED_COMMODITIES / IF 乘数 / 保证金率 1 处查询 | 下降 ~20 处 (directional_futures_trader.spec[key] 不再 # type: ignore) | 注册表可回退到 managers.py 的硬编码, 运行期 lazy load |
| Step 3 | 在 wt_structs 数据类上增加 `__post_init__` 规范化: 若 code 有 `.SH/.SZ/.*` 后缀, 自动校验 exchange 字段一致, 不一致则 RuntimeWarning 或异常 (strict 开关) | 下降 ~5 处 | 默认 strict=False 仅 warning, 不阻断 |
| Step 4 | 与 Wave 3 TYPE_IGNORE 清零对齐: 用 mypy 跑 Wave 3 基线 → 跑当前 → 报告 ≥30% 下降点 | 达标需 Wave 3 配合 | 仅统计, 不强制 |

## 7. 与排期计划协调点

1. **严格单调校验门禁**可借鉴 W6.3.2 的 NonMonotonicTimestampError 设计: 新 `parse_symbol` 的 strict 模式下, 非法代码直接抛 `SymbolParseError`, 不降级 (与 backtest-standards 的"前视偏差防门禁"一致)
2. **Rust 加速 POC (W6.3.4)** 若启动, 合约注册表的热点查询 `registry.lookup(code)` 可放到 Rust 侧做 BTreeMap O(1) 查询, 但当前 Python dict 已足够, 暂不触发 ROI≥3 阈值
3. **Wave 5 GNN 因子数据层** 依赖统一的 secid → 归一化行业/供应链节点映射, Step 0 NewType 定义可提前被 GNN 引用

## 8. 指针 (相关代码)

- 回测数据结构: [wt_structs.py](file:///E:/各种PY程序/28-终极量化交易系统8.4/utils/wt_structs.py)
- Secid 实现 1 (astock): [astock_realtime.py _secid](file:///E:/各种PY程序/28-终极量化交易系统8.4/utils/astock_realtime.py#L30-L37)
- Secid 实现 2 (ETF): [etf_flow_monitor.py 行内拼接](file:///E:/各种PY程序/28-终极量化交易系统8.4/utils/etf_flow_monitor.py#L196-L198)
- 期货解析 (隐患): [futures_rollover_manager.py FUTURES_CODE_PATTERN](file:///E:/各种PY程序/28-终极量化交易系统8.4/utils/futures_rollover_manager.py#L65-L66) + [_parse_contract](file:///E:/各种PY程序/28-终极量化交易系统8.4/utils/futures_rollover_manager.py#L233-L245)
- 期货元数据 (分裂表): [attribution/managers.py SUPPORTED_COMMODITIES](file:///E:/各种PY程序/28-终极量化交易系统8.4/utils/attribution/managers.py#L50-L59)
- 排期任务: [高价值项目集成排期计划_20260811.md W6.3.3](file:///E:/各种PY程序/28-终极量化交易系统8.4/docs/高价值项目集成排期计划_20260811.md#L222)
- 前视偏差门禁标准: [backtest-standards.md §二](file:///E:/各种PY程序/28-终极量化交易系统8.4/cairn/backtest-standards.md)
- 类型安全进度基线: [code-quality-wave3.md](file:///E:/各种PY程序/28-终极量化交易系统8.4/cairn/code-quality-wave3.md)

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [异常处理规约 (Exception Handling Standards)](exception-handling-standards.md) (相似度 24%)
- [重构规约 (Refactoring Standards)](refactoring-standards.md) (相似度 17%)
- [nautilus_trader 架构研究报告 — Wave 6 Sprint 3 W6.3.1](nautilus-trader-study.md) (相似度 12%)
- [daily_workflow.py 拆分计划（门禁 ≤3000 行 · 长期架构重构）](daily-workflow-split-plan.md) (相似度 10%)
- [因子发现 Loop Engineering 升级方案](factor-discovery-loop-engineering.md) (相似度 9%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
