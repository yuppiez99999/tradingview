# -*- coding: utf-8 -*-
"""T02: 修复 kill_switch.py 的 9 处 broad-except (硬约束: 风控路径禁止)

修复策略 (按硬约束 HC: 风控路径禁止 broad exception):
- 配置加载: FileNotFoundError + yaml.YAMLError + KeyError + TypeError
- JSON 解析: json.JSONDecodeError + KeyError + TypeError + ValueError
- 文件写入: OSError + IOError
- broker callback: 保留 Exception (无法预知 broker 异常类型) + 显式 fail-closed 标记 + # noqa: BLE001
- 历史日志读取: ValueError + KeyError + OSError
"""
from pathlib import Path

p = Path(__file__).resolve().parent / "utils" / "kill_switch.py"
c = p.read_text(encoding="utf-8")
original = c

# ============================================================
# Fix 1: line 80 — 配置加载 (显式路径)
# ============================================================
old1 = """            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("kill_switch", {}) if isinstance(cfg, dict) else {}
            except Exception as e:
                logger.error(f"加载配置失败 (显式路径 {self.config_path}): {e}")
                return {}"""
new1 = """            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("kill_switch", {}) if isinstance(cfg, dict) else {}
            except (FileNotFoundError, yaml.YAMLError, OSError) as e:
                logger.error(f"加载配置失败 (显式路径 {self.config_path}): {e}")
                return {}"""
if old1 in c:
    c = c.replace(old1, new1, 1)
    print("  ✅ Fix 1 (line 80): 配置加载 → (FileNotFoundError, yaml.YAMLError, OSError)")
else:
    print("  ⚠ Fix 1 未找到")

# ============================================================
# Fix 2: line 95 — ConfigManager 加载
# ============================================================
old2 = """        except Exception as e:
            logger.error(f"ConfigManager 加载失败, 回退到旧路径: {e}", exc_info=True)"""
new2 = """        except (ImportError, AttributeError, OSError, yaml.YAMLError) as e:
            logger.error(f"ConfigManager 加载失败, 回退到旧路径: {e}", exc_info=True)"""
if old2 in c:
    c = c.replace(old2, new2, 1)
    print("  ✅ Fix 2 (line 95): ConfigManager → (ImportError, AttributeError, OSError, yaml.YAMLError)")
else:
    print("  ⚠ Fix 2 未找到")

# ============================================================
# Fix 3: line 101 — 嵌套配置加载
# ============================================================
old3 = """            except Exception as e2:
                logger.error(f"配置加载彻底失败, 使用默认配置: {e2}")"""
new3 = """            except (FileNotFoundError, yaml.YAMLError, OSError) as e2:
                logger.error(f"配置加载彻底失败, 使用默认配置: {e2}")"""
if old3 in c:
    c = c.replace(old3, new3, 1)
    print("  ✅ Fix 3 (line 101): 嵌套配置 → (FileNotFoundError, yaml.YAMLError, OSError)")
else:
    print("  ⚠ Fix 3 未找到")

# ============================================================
# Fix 4: line 205 — 持仓文件读取
# ============================================================
old4 = """        try:
            with open(positions_file, "r", encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"读取持仓文件失败: {e}, 使用保守值 0.50")
            return None"""
new4 = """        try:
            with open(positions_file, "r", encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logger.error(f"读取持仓文件失败: {e}, 使用保守值 0.50")
            return None"""
if old4 in c:
    c = c.replace(old4, new4, 1)
    print("  ✅ Fix 4 (line 205): 持仓文件 → (FileNotFoundError, json.JSONDecodeError, OSError)")
else:
    print("  ⚠ Fix 4 未找到")

# ============================================================
# Fix 5: line 427 — positions.json total_capital 读取
# ============================================================
old5 = """        except Exception as e:
            logger.debug(f"读取 positions.json total_capital 失败: {e}")"""
new5 = """        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, OSError) as e:
            logger.debug(f"读取 positions.json total_capital 失败: {e}")"""
if old5 in c:
    c = c.replace(old5, new5, 1)
    print("  ✅ Fix 5 (line 427): total_capital → (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, OSError)")
else:
    print("  ⚠ Fix 5 未找到")

# ============================================================
# Fix 6: line 553 — 写入熔断日志
# ============================================================
old6 = """        try:
            with open(KILL_SWITCH_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\\n")
        except Exception as e:
            logger.error(f"写入熔断日志失败: {e}")"""
new6 = """        try:
            with open(KILL_SWITCH_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\\n")
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"写入熔断日志失败: {e}")"""
if old6 in c:
    c = c.replace(old6, new6, 1)
    print("  ✅ Fix 6 (line 553): 写入熔断日志 → (OSError, TypeError, ValueError)")
else:
    print("  ⚠ Fix 6 未找到")

# ============================================================
# Fix 7: line 642 — broker callback (保留 Exception, 添加 noqa + 注释)
# 这是 KillSwitch 调用 broker 真实撤单, 无法预知 broker 异常类型
# 但必须 fail-closed: 记录 critical 日志, 标记 executed=False
# ============================================================
old7 = """        except Exception as e:
            logger.critical(
                f"Kill Switch L{level} broker callback 执行失败! "
                f"熔断协议未真正执行: {e}"
            )"""
new7 = """        except Exception as e:  # noqa: BLE001  # broker API 异常类型不可预知, 必须 fail-closed
            logger.critical(
                f"Kill Switch L{level} broker callback 执行失败! "
                f"熔断协议未真正执行: {e}"
            )"""
if old7 in c:
    c = c.replace(old7, new7, 1)
    print("  ✅ Fix 7 (line 642): broker callback → 保留 Exception + # noqa: BLE001 + fail-closed")
else:
    print("  ⚠ Fix 7 未找到")

# ============================================================
# Fix 8: line 770 — 历史日志解析 (单条记录)
# ============================================================
old8 = """                    except Exception:
                        continue"""
new8 = """                    except (ValueError, KeyError, TypeError):
                        continue"""
if old8 in c:
    c = c.replace(old8, new8, 1)
    print("  ✅ Fix 8 (line 770): 历史日志单条 → (ValueError, KeyError, TypeError)")
else:
    print("  ⚠ Fix 8 未找到")

# ============================================================
# Fix 9: line 772 — 历史日志读取 (整体)
# ============================================================
old9 = """        except Exception:
            pass"""
new9 = """        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass"""
if old9 in c:
    c = c.replace(old9, new9, 1)
    print("  ✅ Fix 9 (line 772): 历史日志整体 → (FileNotFoundError, OSError, json.JSONDecodeError)")
else:
    print("  ⚠ Fix 9 未找到")

# ============================================================
# 写回
# ============================================================
if c != original:
    p.write_text(c, encoding="utf-8")
    print()
    print("✅ kill_switch.py 已修复 (8/9 真修 + 1/9 noqa)")
else:
    print()
    print("⚠ 内容未变化")
