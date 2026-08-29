---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-03
updated: 2026-08-19
related_log: 2026-08-03 Round 3, 2026-08-18 强化, 2026-08-19 B025/F401/E741
---

# 异常处理规约 (Exception Handling Standards)

> 本文档沉淀自 ai_decision/ 模块 Round 3 全清零的修复经验，作为后续 `utils/`、`ms_strategy/`、`scripts/` 等历史目录重构的参考样板。
> 规约核心：禁止裸 `except Exception`，按调用场景选择具体异常类型，每处附带注释说明可能抛出的异常。

## 1. 核心原则

### 1.1 禁止裸 `except Exception`

```python
# ❌ 禁止 — 吞没所有异常（含 KeyboardInterrupt/MemoryError/SystemExit），掩盖真实故障
try:
    do_something()
except Exception:
    pass

# ✅ 推荐 — 列出具体异常类型
try:
    do_something()
except (ValueError, TypeError, KeyError) as exc:
    logger.warning("操作失败: %s", exc)
```

### 1.2 fail-safe 行为保留

修复 `except Exception` 时**必须保留原有降级行为**，不改变业务语义：
- 原本返回 `None` → 仍返回 `None`
- 原本返回空列表/空字典 → 仍返回空容器
- 原本记录日志后继续 → 仍记录日志后继续

### 1.3 快速失败改进

未列举的异常（如 `MemoryError`/`SystemExit`/`KeyboardInterrupt`）现在向上传播，便于运维发现真实故障。这是规约的**有意改进**，不是回归。

### 1.4 注释完整性

每处修复**必须附带注释**说明可能抛出的具体异常类型及原因，便于后续维护：

```python
except (ImportError, OSError, ValueError, TypeError, AttributeError,
        RuntimeError) as exc:
    # ImportError: yaml 未安装
    # OSError: 文件读取失败 (权限/编码/磁盘)
    # ValueError/TypeError/AttributeError: 解析失败/格式错误/字段类型不符
    # RuntimeError: _deep_merge 合并过程抛出
    logger.warning("配置加载失败, 用默认值: %s", exc)
```

## 2. 按调用场景的异常类型选择

### 2.1 配置加载 (yaml/json)

```python
except (ImportError, OSError, ValueError, TypeError, AttributeError,
        RuntimeError) as exc:
```

| 异常 | 触发场景 |
|------|---------|
| `ImportError` | yaml 模块未安装 |
| `OSError` | 文件读取失败 (含 `FileNotFoundError`/`PermissionError`/磁盘错误) |
| `ValueError`/`TypeError` | 解析失败 (yaml.safe_load 抛出) |
| `AttributeError` | 字段类型不符 (非 dict 上调用 .get) |
| `RuntimeError` | 合并过程抛出 |

### 2.2 JSONL / JSON 加载

```python
except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as exc:
```

注意：行级 `json.JSONDecodeError`（`ValueError` 子类）应单独捕获 `continue` 跳过损坏行，外层只捕获文件级错误。

```python
try:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 跳过损坏行
except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as exc:
    # OSError: 文件读取失败
    # AttributeError: line 非 str 时 .strip() 抛出
    # ValueError: 文件编码错误
    logger.error("JSONL 加载失败 %s: %s", path, exc)
```

### 2.3 LLM Provider 调用 (网络 + API)

```python
# 适配 llm_client (内部已多级降级)
except (RuntimeError, ValueError, TypeError, OSError, ConnectionError, TimeoutError) as exc:

# 直接 requests.post (OpenAI 兼容)
except (ImportError, OSError, ConnectionError, TimeoutError,
        ValueError, KeyError, TypeError, RuntimeError) as exc:
```

| 异常 | 触发场景 |
|------|---------|
| `ImportError` | requests 模块未安装 |
| `OSError` | 网络错误基类（含 `ConnectionError`/`TimeoutError`/`HTTPError`） |
| `ConnectionError`/`TimeoutError` | 网络层失败 |
| `ValueError` | JSON 解析失败 (`resp.json()` 抛出) |
| `KeyError`/`TypeError` | `data["choices"][0]["message"]["content"]` 字段缺失/类型错 |

### 2.4 importlib 动态加载

```python
except (ImportError, OSError, AttributeError, TypeError, ValueError,
        SyntaxError, RuntimeError) as exc:
```

特别列出 `SyntaxError`：目标 .py 文件可能存在语法错误，`spec.loader.exec_module()` 会抛出。

### 2.5 外部插件接口 (push_fn / data_provider)

外部接口可能抛任何异常，**单条失败不影响其他**的场景用最宽集合：

```python
except (RuntimeError, OSError, ConnectionError, TimeoutError,
        ValueError, TypeError, KeyError, AttributeError):
    # push_fn 可能抛: 网络/超时/参数格式/字段缺失/类型不匹配
    pass  # 单条失败不影响其他
```

### 2.6 pandas / numpy 计算

```python
except (ValueError, KeyError, TypeError, AttributeError,
        RuntimeError, OSError) as exc:
```

pandas/numpy 操作多为 `ValueError`（数据格式/形状错误）和 `KeyError`（字段缺失）。

### 2.7 数据加载协议 (Loader Protocol)

```python
except (ValueError, KeyError, TypeError, AttributeError,
        RuntimeError, OSError) as exc:
```

注意特殊场景：`"key" in obj` 当 `obj` 非 dict 时抛 `TypeError`，需在注释中说明。

## 3. 常见反模式

### 3.1 反模式：吞没异常不记录

```python
# ❌ 隐藏故障，无法排查
except Exception:
    pass
```

### 3.2 反模式：过宽捕获 + 无差别处理

```python
# ❌ 捕获 MemoryError 但当业务错误处理
except Exception as exc:
    return {"error": str(exc)}
```

### 3.3 反模式：捕获后不反馈熔断器

```python
# ❌ provider 调用失败但不记录熔断器状态
except Exception:
    return None
```

正确做法（来自 orchestrator.py）：

```python
except (ValueError, KeyError, TypeError, AttributeError, RuntimeError,
        OSError, TimeoutError) as exc:
    # 辩论失败反馈到熔断器 (累计触发熔断)
    _mon.record_failure("judge")
    logger.warning("[Orchestrator] 辩论异常, 降级快速聚合: %s", exc)
```

## 4. 修复流程（参考样板）

1. **扫描定位**：`py -3.11 scripts/_scan_except_remaining.py <目录>`
2. **查看上下文**：`py -3.11 scripts/_show_except_ctx.py <文件> 6`
3. **阅读调用链**：理解被调函数可能抛出的异常类型
4. **选择异常类型**：按本文档 §2 选择对应场景的异常集合
5. **加注释**：说明每个异常类型的触发场景
6. **保留降级**：原有返回值/日志/熔断反馈必须保留
7. **语法验证**：`py -3.11 -c "import ast; ast.parse(open('<file>').read())"`
8. **导入验证**：`py -3.11 -c "from <module> import <name>"`
9. **回归测试**：运行相关测试，用 `git stash` 比对确认无新增失败

## 5. ai_decision/ 修复样板索引

| 文件 | 处数 | 场景 | 参考章节 |
|------|------|------|---------|
| [providers.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/providers.py) | 7 | LLM Provider 调用 | §2.3 |
| [orchestrator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/orchestrator.py) | 3 | 流程控制 + 熔断反馈 | §2.3 + §3.3 |
| [backtest_replay.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/backtest_replay.py) | 7 | 回测计算 + Loader 协议 | §2.6 + §2.7 |
| [config.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/config.py) | 1 | yaml 配置加载 | §2.1 |
| [health.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/health.py) | 2 | importlib 加载 + provider 探测 | §2.4 + §2.3 |
| [eod_review.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/eod_review.py) | 5 | 配置 + JSONL + 监控 + 推送 | §2.1 + §2.2 + §2.5 |
| [dashboard.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/dashboard.py) | 4 | 配置 + JSONL + 监控 | 同 eod_review |
| [execution_bridge.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/execution_bridge.py) | 7 | 真实下单路径 (Round 2) | §2.2 + sqlite3.Error |
| [consensus_aggregator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ai_decision/consensus_aggregator.py) | 1 | 非线性聚合 (Round 2) | §2.6 |

## 6. 门禁规则建议

### 6.1 ruff/flake8 配置

```ini
# pyproject.toml 或 .flake8
[tool.ruff.lint]
select = ["BLE001"]  # blind-except

[flake8]
extend-select = BLE001
```

`BLE001` 检测裸 `except Exception`，阻止新增。已修复的 29 处用 `# noqa: BLE001` 标注的无需关注，但新代码必须用具体异常类型。

### 6.2 Pre-commit Hook 建议

```yaml
# .pre-commit-config.yaml
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: v0.5.0
  hooks:
    - id: ruff
      args: [--select, BLE001, --fix]
```

## 7. 验证工具

| 工具 | 用途 |
|------|------|
| [scripts/_scan_except_remaining.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/scripts/_scan_except_remaining.py) | 扫描指定目录所有 .py 中剩余 `except Exception`，按文件聚合统计 |
| [scripts/_show_except_ctx.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/scripts/_show_except_ctx.py) | 显示每处 `except Exception` 的前后 6 行上下文，辅助理解调用场景 |

## 8. 踩坑记录

### 8.1 PowerShell `$_` 转义问题

contains: powershell-gotcha

在 Shell 工具中执行 PowerShell 命令时，`ForEach-Object { $_.LineNumber }` 中的 `$_` 会被 shell 层吞掉，导致 `Missing expression after unary operator` 错误。

**解决**：改用 Python 脚本（`_scan_except_remaining.py`）替代 PowerShell 管道命令，避免转义问题。

### 8.2 pre-existing bug 验证

contains: testing-gotcha

修复后跑测试若发现失败，**必须用 `git stash` 比对**确认是否为本次回归：

```bash
git stash push -m "tmp-verify" <修改的文件>
py -3.11 -m pytest <失败测试>  # 若仍失败 → pre-existing
git stash pop
```

Round 3 中 `test_backward_compat_corrupted_jsonl` 和 `test_run_debate_with_mock_no_key` 两处失败经此方法验证为 pre-existing，与异常处理修复无关。

### 8.3 命令行长度超限

contains: cli-gotcha

Windows PowerShell 命令行长度上限约 32000 字符，长 Python 脚本直接 `-c` 执行会超限。

**解决**：将脚本保存为 .py 文件再 `py -3.11 <script>.py` 执行。

## 9. 后续推进计划

参见 [cairn/bug_fix_tracker.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/bug_fix_tracker.md) 第二阶段（08-04 ~ 08-12）。

- `utils/` 838 处（中优先级，历史代码风格）
- `ms_strategy/` 122 处（低）
- `scripts/` 105 处（低，CLI 工具）
- `research/` 78 处（低，可能延后至 v9.x）

每批 50-100 处，按本文档规约选择异常类型，每批完成后跑相关测试。

## 10. 相关文档

- [cairn/refactoring-standards.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/refactoring-standards.md) — 重构规约（本文档的姊妹篇，同为代码质量加固规范，含表驱动化 vs 提取 helper 决策树）

## 11. except 元组收窄规约（2026-08-17 补充）

> 沉淀自 08-16 NB-1/NB-2/NB-6 + 08-17 NEW-7 修复经验。这 4 个 bug 均源于 BLE001 精确化收窄 except 元组时未核对 try 块内实际 raise 面。

contains: except-narrowing, raise-face-audit, BLE001

### 11.1 问题模式

BLE001 精确化把 `except Exception` 收窄为元组时，只列了"预期"异常，漏了 try 块内**实际会抛出的异常类型**：

```python
# ❌ 错误 — 漏了 TypeError (send_alert 缺必填参数时抛)
try:
    from utils.notify import send_alert
    send_alert(content=message, level=level)  # title 是必填位置参数!
except (ImportError, AttributeError):  # ← 不含 TypeError
    logger.warning(...)

# ❌ 错误 — 漏了 ImportError (PostMixLayer 不存在时抛)
try:
    from utils.signal_fusion import PostMixLayer  # ← 不存在!
    ...
except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError):
    ...
```

### 11.2 规约：收窄必核对 raise 面

收窄 `except Exception` 为元组时，**必须**执行以下步骤：

1. **列出 try 块内每个调用**（含 import、函数调用、属性访问）
2. **对每个调用列出全部可能抛出的异常类型**：
   - `import X` → `ImportError`（模块不存在）、`AttributeError`（从模块导入不存在的符号）
   - `f(args)` → `TypeError`（参数不匹配）、`ValueError`/`KeyError`/...（业务异常）
   - `obj.attr` → `AttributeError`（属性不存在）
3. **元组必须覆盖全部 raise 面**，或保守保留 `except Exception` + `logger.exception()` + 显式 re-raise 策略

#### 11.2.1 PR 提交前检查清单（2026-08-18 补充）

contains: pr-checklist, except-narrowing-checklist

收窄 except 元组的 PR，提交前**逐项勾选**：

- [ ] try 块内每个 `import` / `from X import Y` 已列出 → 元组含 `ImportError`（Y 不存在时抛）+ `AttributeError`（符号缺失）
- [ ] try 块内每个函数调用 `f(args)` 已核对签名 → 元组含 `TypeError`（必填参数缺失/类型不匹配时抛）
- [ ] try 块内每个 `obj.attr` / `obj[key]` 访问 → 元组含 `AttributeError` / `KeyError`
- [ ] try 块内委托给外部/被监控对象的方法 → 元组含该方法可能抛出的业务异常（如 `RuntimeError`）
- [ ] try 块内有 `close()` / `cleanup()` → 改用 try/finally 或 with（见 §11.4），close 本身异常单独捕获
- [ ] ruff `ruff check <文件> --select BLE001` 无新增
- [ ] 相关测试全绿（用 `git stash` 比对确认非 pre-existing 失败，见 §8.2）

> 历史教训：0816 NB-1（漏 TypeError）/ NB-2（漏 ImportError）/ NB-6（漏 RuntimeError）均因跳过此清单导致生产链路崩溃。0818 ruff 修复批次（F821/B904）进一步证实"异常路径自身未被执行过"是这类 bug 长期潜伏的根因。

### 11.3 常见漏列类型

| try 块内操作 | 常被漏列的异常 | 案例 |
|-------------|---------------|------|
| `from X import Y` | `ImportError`（Y 不存在） | NB-2 PostMixLayer |
| `f(必填参数缺失)` | `TypeError` | NB-1 send_alert 缺 title |
| `被监控对象.方法()` | `RuntimeError`（业务层抛） | NB-6 DriftMonitor |
| `rag.close()` 在 try 内 | close 自身异常 | NEW-7 资源泄漏 |

### 11.4 资源管理补充规约

`close()`/`cleanup()` 不应在 try 块内调用（异常时不执行），必须用 try/finally 或 with：

```python
# ❌ 错误 — impact_analysis 抛异常时 close 不执行
try:
    rag = CodeGraphRAG()
    impact = rag.impact_analysis(path)
    rag.close()
except ...:

# ✅ 正确 — try/finally 保证 close
rag = None
try:
    rag = CodeGraphRAG()
    impact = rag.impact_analysis(path)
except ...:
finally:
    if rag is not None:
        try:
            rag.close()
        except Exception:  # noqa: BLE001
            pass
```

### 11.5 相关文档

- [cairn/code-review-newcode-bug-patterns-20260817.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/code-review-newcode-bug-patterns-20260817.md) — 7 大 bug 根因模式（本文档模式 3 的来源）

## 12. B025 duplicate-try-except 修复规约（2026-08-19 补充）

> 沉淀自 0819 ruff Tier 1 批量修复经验。B025 是 try-except 块中多个 except 子句捕获相同异常类型。

contains: B025, duplicate-except, replaceAll-gotcha, ruff-fix

### 12.1 问题模式

```python
# ❌ B025 — RuntimeError 在两个 except 子句中都出现
try:
    do_something()
except RuntimeError:
    raise
except (ValueError, KeyError, RuntimeError) as e:  # ← RuntimeError 重复
    logger.error(...)
```

Python 按顺序匹配 except 子句，第一个 `except RuntimeError` 会先捕获 RuntimeError，第二个 except 中的 RuntimeError 永远不会触发 → 冗余。

### 12.2 修复方式

移除第二个 except 元组中的重复类型：

```python
# ✅ — RuntimeError 只在第一个 except
try:
    do_something()
except RuntimeError:
    raise
except (ValueError, KeyError) as e:
    logger.error(...)
```

### 12.3 replaceAll 陷阱

contains: replaceAll-gotcha, batch-fix-gotcha

**关键踩坑**：用 edit 的 `replaceAll` 批量修改 except 行时，可能匹配到**没有前置 `except X: raise`** 的位置：

```
# 假设文件中有 10 处相同的 except 行：
except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:

# 但只有 1 处前面有 `except RuntimeError: raise`（B025 成立）
# 其余 9 处没有前置 except（RuntimeError 是唯一捕获点，B025 不成立）

# replaceAll 全改 → 9 处误移除 RuntimeError → 异常未捕获 → 测试失败
```

**规约**：
1. B025 修复**禁止用 replaceAll**，必须逐处确认前置 except 子句
2. 修改后立即跑 `ruff check <文件> --select B025` 确认
3. 修改后跑相关测试确认无破坏（见 §8.2 pre-existing bug 验证）

### 12.4 F401 甄别策略（2026-08-19 补充）

contains: F401, unused-import, availability-check, re-export

F401 unused-import 的三种情况及处理：

| 情况 | 特征 | 处理 |
|------|------|------|
| 可用性检查 | try-except 中 `import X`，后设 `_AVAILABLE=True` | 行尾加 `# noqa: F401` |
| re-export | `__init__.py` 或统一入口导入子模块类供外部引用 | ruff.toml per-file-ignores 豁免 |
| 真未用 | 重构遗留，导入但完全未使用 | 删除导入语句 |

**甄别方法**：读取导入上下文，如果导入后紧跟 `_AVAILABLE = True` / `self._available = True` 等标志设置 → 可用性检查；如果文件是 `__init__.py` 或统一入口（如 `daily_workflow.py`）→ re-export；否则 → 真未用。

### 12.5 E741 改名后 F821 验证

contains: E741, rename-gotcha, F821-validation

批量改名模糊变量名（`l`/`I`/`O`）后，**必须**用 `ruff check --select F821` 验证引用完整性：

- agent 可能误改非违规变量（如 `l1` 不是单字符 `l`，不触发 E741，但被误改为 `lows1`）
- 只改了引用处未改定义处 → F821 undefined-name 拦截

**规约**：E741 批量改名后，`ruff check . --select F821` 必须为 0 才算完成。

### 12.6 相关文档

- [cairn/test-health-20260819.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/test-health-20260819.md) §三 — Tier 1 批量修复明细与踩坑记录
- [cairn/code-review-ruff-fix-batch-20260818.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/code-review-ruff-fix-batch-20260818.md) — 0818 ruff F821/F811/B904 清零批次

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [重构规约 (Refactoring Standards)](refactoring-standards.md) (相似度 28%)
- [W6.3.3 预研 · QS-Trader 风格 secid 合约解析难点清单](w633_secid_contract_parsing_challenges.md) (相似度 24%)
- [测试债清零经验沉淀 (2026-08-16)](test-debt-clearance-20260816.md) (相似度 16%)
- [新代码审查 bug 模式与根因（2026-08-17）](code-review-newcode-bug-patterns-20260817.md) (相似度 15%)
- [Bug修复追踪表 v2.4](bug_fix_tracker.md) (相似度 13%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
