---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-03
updated: 2026-08-03
related_log: 2026-08-03 Round 3
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
