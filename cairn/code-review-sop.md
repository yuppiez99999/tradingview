# 代码审查 + 修复批次 标准作业流程 (SOP)

> 适用场景: 对 `28-终极量化交易系统8.4/` 全量或大模块做质量审查、缺陷核查、门禁加固。
> 首版实践: 2026-08-08 OCR v1.8.10 + DeepSeek-chat + 人工源码分析 + 静态工具链。
> 配套沉淀: `cairn/code-review-fix-batch-20260808.md` (批次明细) / `docs/CODE_REVIEW_COMPREHENSIVE_20260808.md` (综合报告)。

---

## 0. 总览: 两阶段工作法

```
阶段一 审查 ──► 产出: 缺陷清单(B1-Bn) + 门禁盲区(G-2..) + 修复路线图
   │
阶段二 修复 ──► 产出: 代码改动 + 验证快照 + 防复发门禁
   │
收尾 ──────► 工业级检查维持 + 经验沉淀(cairn/) + memory 更新
```

**铁律**: 先审查后修复、每修必验、修复后更新防复发门禁而非只改代码。

---

## 1. 阶段一 — 审查 (Discovery)

### 1.1 工具准备
- OCR 工具 (open-code-review v1.8.10) 配合 OpenAI 兼容接口 (DeepSeek-chat)。
- 注意: OCR 的 config.json 路径兼容性差 (实测 `~/.opencodereview` / `~/.ocr` 多种格式均失败)。
  - **兜底**: 直接调用 DeepSeek API (curl 经测试可用) + 人工源码分析, 不依赖 OCR 配置文件。
- 静态工具链 (必须装): `ruff`, `bandit`, `py_compile` (Python 内置), `pyflakes`。

### 1.2 四步审查法
1. **读文档定基线**: 读 `docs/CODE_REVIEW_COMPREHENSIVE_*.md` / `AGENTS.md` / 工业级工作计划, 明确当前 Phase 状态与验收判据。
2. **跑常驻检查**: `industrial_grade_check.py` (9 判据) + `assert_data_validity.py` (7 断言), 拿到修复前基准快照。
3. **工具扫描缺陷**:
   - `bandit -r <dir>/ --severity high` → 安全缺陷 (B501 SSL / 硬编码密钥)。
   - `ruff check --select F821 .` → 未定义名 (真 bug vs 噪声)。
   - `ruff check --select T201 .` (P0 文件) → 生产路径裸 print。
   - `py_compile` 全量 → SyntaxError 筛查。
4. **源码交叉验证**: 对每条工具告警, 打开源码确认 **真实影响面** (是否进实盘链路 / 是否仅离线脚本 / 是否真 bug)。

### 1.3 缺陷分级与分类
| 等级 | 含义 | 示例 |
|------|------|------|
| CRITICAL | 误用即资金损失 | B1 离线脚本硬编码行情被当实盘 |
| HIGH | 安全/可追溯性断裂 | B2/B3 SSL 绕过, B4 失败静默吞咽 |
| MEDIUM | 结构性盲区/维护债 | B5 双份代码, B6 P0 名单缺口 |
| LOW | 存量噪声 | B7 F821 假阳性 |

**分类标签**:
- `P0 真实缺陷` — 必须修, 有资金/安全/可追溯影响。
- `P1 门禁加固` — 修结构性盲区, 防止同类问题再入库。
- `P2 存量债` — 记录但不阻塞, 排入 nightly。

---

## 2. 阶段二 — 修复 (Remediation)

### 2.1 修复顺序 (高价值低风险优先)
1. SSL / 安全 (B2/B3) — 消除 MITM。
2. P0 名单同步 (G-2) — 1 行改动覆盖核心引擎。
3. pre-commit py_compile (G-3) — 阻断语法错误入库。
4. OFFLINE 护栏 (B1) — 防误用。
5. CI F821 (G-4) — 阻断新未定义名。
6. except 静默吞咽 (B4) — 失败可追溯。
7. F821 清零 (B7) — 存量清理。
8. (可选) 双份去重 (B5) — 风险扩散, 谨慎。

### 2.2 修复模式库

**模式 A — SSL 绕过修复**
```python
try:
    import certifi
    _verify = certifi.where()
except ImportError:
    _verify = True
resp = requests.get(url, timeout=10, headers=headers,
                    verify=_verify, proxies={"http": None, "https": None})
```

**模式 B — except 静默吞咽 → 独立告警通道**
```python
try:
    result = do_risky_call()
except Exception as e:
    logger.error(f"[HEDGE_FAIL] 路由失败: {e}")
    plan["routing_result"] = {"success": False, "error": str(e)}
    _FAIL_COUNT += 1
    if _FAIL_COUNT >= 3:
        from utils.notify import send_alert
        send_alert(f"[CRITICAL] 连续 {_FAIL_COUNT} 次失败", severity="CRITICAL")
```
原则: 决策路径 fail-close, 观测路径 fail-open, 但都必须留日志 (静默 except 是头号坑)。

**模式 C — OFFLINE 脚本护栏**
- 文件头 `# OFFLINE_ONLY` 标注。
- 运行时 `logger.warning("[OFFLINE_ONLY] 使用硬编码价格, 非实盘路径")`。
- lint 规则 (G-6) 自动检测 P0 文件硬编码行情。

**模式 D — P0 名单同步**
- `scripts/check_no_print_p0.py` 的 `P0_FILES` 按 basename 匹配, 重构后立即同步新增文件。

**模式 E — 增量门禁补 F821**
- `.github/workflows/ci.yml` 增 `incremental-static` job 步骤: `ruff check --select F821 <changed>`。
- `ruff.toml` 的 `extend-exclude` 必须在 `[lint]` 段之前, 否则不生效 (排除 `external/airllm_src` 等第三方噪声)。

---

## 3. 收尾 — 验证与防复发

### 3.1 验证快照模板 (必须打真实接入点观察可见变化)
```
industrial_grade_check: 7 PASS / 2 WARN / 0 FAIL   (维持)
assert_data_validity:   7 PASS / 0 FAIL             (维持)
py_compile <modified>:  ALL OK
ruff --select F821 .:   All checks passed!
bandit -r <dir>/:       0 Total issues
P0_FILES:               N 文件 (原 M + 新增 K)
pre-commit hooks:       H (原 h + 新增 1)
CI steps:               S (原 s + 新增 2)
```
**验证铁律**: "跑通没报错" 是最弱验证。必须打真实接入点并观察可见数值变化 (如 PnL 1700→1680.5), 否则不算验证。

### 3.2 防复发门禁 (修一类问题, 加一道门禁)
| 修的问题 | 加的门禁 |
|----------|----------|
| B1 硬编码行情 | G-6 quant_review_lint 加 P0 硬编码行情规则 |
| B2/B3 SSL | G-7 bandit 配置固化 (排除 qlib_env, 纳入 realtime_monitor) |
| B4 静默吞咽 | 审查 checklist 强制检查 except 是否有 send_alert |
| B6 P0 缺口 | 重构 PR 模板强制更新 P0_FILES |
| B7 F821 | CI 增量 F821 + nightly 全量基线 |

### 3.3 经验沉淀三件套
1. **批次明细**: `cairn/code-review-fix-batch-YYYYMMDD.md` (本次改了什么)。
2. **综合报告**: `docs/CODE_REVIEW_COMPREHENSIVE_YYYYMMDD.md` (全景 + 路线图)。
3. **SOP**: 本文件 (方法论, 跨批次复用)。

---

## 4. 七条关键教训 (跨批次通用)

1. **审查报告行号会失效** — 重构后行号全变, 必须用工具 (ruff/bandit/py_compile) 实际核验当前状态, 而非引用旧报告。
2. **增量门禁不扫存量** — 全仓 ruff 存量、F821 永远不进审查视野, 必须加 nightly 全量 + 基线。
3. **P0 名单必须随重构同步** — basename 匹配机制要主动利用, 重构后立即更新 P0_FILES。
4. **fail-safe 必须区分告警与失败** — 静默吞咽 except 是头号坑, 必须用独立通道 (send_alert) 而非 logger.warning。
5. **最廉价防线收益最大** — py_compile 1s/文件, 阻断所有 SyntaxError 入库, 性价比最高。
6. **验证脚本不能假设修复方式** — 用权威工具 (bandit/ruff) 验证实际安全状态, 而非字面匹配 (如 `verify=_verify` 变量赋值, 字面 "verify=False" 只在注释)。
7. **OFFLINE 脚本必须有显式护栏** — logger.warning + 文档锚点 + lint 规则, 三重防护防止误用为实盘。
8. **配置脱节 (双源分裂的隐性形式)** — yaml 已升级 (如观察期 14→21 天) 但代码硬编码旧值且不读 yaml, 导致 PM 决策永不生效。修复: 以 yaml 为单事实源, 代码全部 import 读取, grep 全仓确认 0 处字面量。陷阱: yaml 字段常在嵌套层 (如 `settings.observation_days`), 按顶层键读会静默回退默认。详见 `cairn/observation-period-config-drift-20260809.md`。

---

## 5. 环境坑 (Windows + Python 3.8)

- **GBK 编码**: `PYTHONUSERBASE=C:\NUL` 绕过 site.py 加载 .pth 含非 ASCII 触发 UnicodeDecodeError。
- **venv 损坏**: 用项目 `.venv/Scripts/python.exe` 而非裸 `python`, 避免系统 site-packages 污染。
- **PYTHONIOENCODING**: print 含 `¥` 等非 GBK 字符会崩溃, 改用 `RMB/CNY` 或 `$env:PYTHONIOENCODING="utf-8"`。
- **OpenBLAS 内存**: 低内存环境设 `OPENBLAS_NUM_THREADS=1` + `OMP_NUM_THREADS=1` + `MKL_NUM_THREADS=1`。
- **NO_PROXY**: 导入 akshare/requests 前设 `NO_PROXY=push2his.eastmoney.com,...,sinajs.cn` 避免代理拒绝国内金融 API。

---

*本 SOP 由 2026-08-08 审查修复批次提炼, 后续批次直接复用 §1-§3 流程。*

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [2026-08-08 代码审查修复批次经验沉淀](code-review-fix-batch-20260808.md) (相似度 47%)
- [观察期配置脱节修复 — 2026-08-09](observation-period-config-drift-20260809.md) (相似度 15%)
- [独立代码审查 + 二次核验纠偏（2026-08-08）](code-review-independent-audit-20260808.md) (相似度 14%)
- [Agent 直接审查兜底方法论（外部 LLM 额度耗尽时）](code-review-agent-fallback-20260810.md) (相似度 13%)
- [代码审查质量门禁经验沉淀：为什么多次审查仍有 bug](code-review-quality-gate-lessons-20260811.md) (相似度 12%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
