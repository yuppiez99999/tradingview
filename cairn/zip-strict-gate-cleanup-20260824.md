# B905 zip(strict=True) 全量治理 — 2026-08-24

> 来源：ruff 报告基线（348 违规）逐批清零工程，第 1~5 批  
> 沉淀日期：2026-08-24  
> 状态：✅ 完成 — B905 53→0 全量清零，B007 12→0，BLE001 2→0，总违规 348→282，硬 bug 门禁 0

---

## 1. 背景与结果

ruff 报告基线共 348 处违规。B905（`zip` 未指定 `strict`）占 53 处，是最集中的代码质量问题。经 5 批处置全部清零：

| 批次 | 范围 | 处置 |
|---|---|---|
| 报告已落 | 报告自带 12 处 | 12 处 strict |
| 第 1 批 | 资金路径 17 处（automated_execution_system / daily_trading_workflow / hedge_order_executor / rebalance_order_executor / order_router / risk 等） | 14 strict + 3 noqa |
| 第 2 批 | B007 12 处 + BLE001 2 处 | 全部清零 |
| 第 3 批 | 复扫验证 + 硬 bug 门禁 + 测试冒烟 | 235 passed |
| 第 4 批 | 资金路径 B905 复核（含 3 处 noqa 判据复核） | 确认 |
| 第 5 批 | 非资金路径 33 处（tests×8 / tools/wind×6 / research×5 / ui×3 / scripts×3 / llm_finetune×4 / utils×3 / second-brain×1 / quant_modules×1） | 32 strict + 1 noqa |

**最终**：全库 65 处 B905 = 61 处 `strict=True` + 4 处 `# noqa: B905`。

---

## 2. 分案方法论 — 加 strict 的判据

`zip(..., strict=True)` 在不等长时抛 `ValueError`。**凡两侧必然等长或等长被显式保证的，必须加 strict**——它能将"静默截断丢数据"变成"立即失败"，是数据完整性最廉价的防线。

本库实证中可安全加 strict 的 6 类等长模式：

1. **同一 DataFrame 的两列**：`dict(zip(df["code"], df.get("name", df["code"])))`、`dict(zip(df["instrument"], df["signal"]))` — pandas 列天然等长。
2. **同一循环 append 的同源列表**：`texts.append(...); labels.append(...)` 后 `zip(texts, labels)` — 长度由循环不变式保证。
3. **前置显式长度检查**：`if len(columns) != len(row): continue` / `if len(a) != len(b): return` 后接 zip — strict 不改变已保证的路径，只捕获"保护逻辑漏网"。
4. **同源推导**：`clean_codes = [f(x) for x in items]` 后 `zip(items, clean_codes)`、`sector_weights` 由 `zip(weights, sectors)` 推导、`cols = st.columns(len(items))` 后 `zip(cols, items)` — 推导保持长度不变式。
5. **自构造测试数据**：测试里 `closes = range(100,130)` + `vols = [1000]*30`，两侧字面量等长 — strict 使断言更严谨。
6. **关键路径硬编码等长**：`ui/06_ETF资金流向.py` 的 `categories`（6 个）与 `colors`（6 个）均硬编码 — 需**读代码确认数量一致**再决定，不能想当然。

## 3. 分案方法论 — noqa 的判据

加 strict 会崩溃且崩溃不正确（不等长是**设计语义**而非 bug）的 4 处，全部 `# noqa: B905` 并附中文说明：

| 位置 | 语义 | 为何不 strict |
|---|---|---|
| `utils/alpha_factor/price_volume.py:603` | 量能列允许长于收盘列，按较短截断 | 截断即设计；L603 前已有长度对齐兜底 |
| `utils/alpha_factor/technical.py:86` | default 近似值生成 | L89 后续显式长度对齐兜底 |
| `utils/lgbm_reproducibility.py:339` | 两个 dict 输入大小可能不同，按较短比较 top_k | 比较为设计语义 |
| `tests/unit/test_t14_risk_audit_logger.py:124` | `zip(recs, recs[1:])` 相邻元素比较 | 末尾天然少一项，截断为设计语义 |

**判据一句话**：等长有保证 → strict；不等长是语义 → noqa（禁止 `zip(list1, list2, strict=False)` 掩盖意图）。

---

## 4. 关键教训

1. **`zip(recs, recs[1:])` 类相邻比较是 B905 高频误判点** — 看似"缺 strict"，实则截断是算法本身，加 strict 必崩。扫描到这类模式直接归 noqa，不要在跑测试时才发现。
2. **"前置 len 检查"是 strict 的安全垫** — `wind_mcp_fetcher` 3 处、`research_rag` 1 处都有 `len==len` 前置保护，加 strict 不改变已保证路径，只强化"保护失效时立即失败"。
3. **动态 vs 硬编码数量必须先读代码** — `ui/06_ETF资金流向.py` 的 categories/colors 均硬编码 6 个才敢加 strict；若 categories 来自动态数据而 colors 固定，必须 noqa 或补齐。**加 strict 前读上下文，不靠猜。**
4. **测试冒烟要隔离预存失败** — 冒烟中 `test_shadow_real_data_feeder.py` 4 个失败为预存（时态 mock），用 `git stash` 对照确认与改动无关，避免把别人家的失败算到自己头上。
5. **相同代码行多位置出现用 replace_all** — `wind_mcp_fetcher` 3 处 `records.append(dict(zip(columns, row)))` 逐字节相同，一次 `replace_all` 处理并人工复核 3 处语义一致。
6. **noqa 必须附中文语义说明** — 4 处 noqa 全部写明"截断是设计语义"或"后续长度对齐兜底"，后续审查能一眼判断 noqa 是否仍成立，而非无脑豁免。

---

## 5. 防复发

- `ruff.toml` 已启用 B905（默认 F 组 + B 组），新代码 `zip()` 不带 strict 会立即被 CI 拦下。
- 新增 zip 的合规模板：`zip(a, b, strict=True)`；仅"截断是语义"处允许 `# noqa: B905` + 说明。
- 与 `cairn/code-review-ruff-fix-batch-20260818.md`（ruff 基线治理方法论）配套，总违规基线 348→282，硬 bug 门禁（E9/F63/F7/F82/F401/F811/F821/F841）保持 0。
