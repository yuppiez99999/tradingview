"""OCR 代码质量修复脚本 — 在项目根目录运行: python apply_ocr_fixes.py"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 如果脚本不在项目根目录, 修改为实际路径:
# ROOT = Path(r'E:\各种PY程序\28-终极量化交易系统8.4')

fixes = [
    # ============================================================
    # P1: 静默吞错 — 添加日志, 保留 traceback
    # ============================================================

    # automated_execution_system.py:2007 — except: pass
    (
        "utils/execution/automated_execution_system.py",
        'except Exception:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃\n                    pass',
        'except Exception:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃\n                    logger.warning("读取 market_returns.json 失败, 跳过指数价格推断", exc_info=True)',
    ),
    # automated_execution_system.py:2052 — except: continue
    (
        "utils/execution/automated_execution_system.py",
        'except Exception:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃\n                                continue',
        'except Exception:  # noqa: BLE001  # execution fail-safe, 交易路径不崩溃\n                                logger.warning(f"计算 {col} beta 失败, 跳过", exc_info=True)\n                                continue',
    ),

    # ops_diagnoser.py:421 — except: continue
    (
        "utils/alpha/layers/ops_diagnoser.py",
        "except Exception:\n                    continue",
        'except Exception:\n                    logger.warning("读取告警文件失败, 跳过", exc_info=True)\n                    continue',
    ),
    # ops_diagnoser.py:731 — except: pass
    (
        "utils/alpha/layers/ops_diagnoser.py",
        "except Exception:\n            pass",
        'except Exception:\n            logger.warning("获取 ops layer_score 失败", exc_info=True)\n            pass',
    ),

    # strategy_diagnoser.py:460 — except: continue
    (
        "utils/alpha/layers/strategy_diagnoser.py",
        "except Exception:\n                    continue",
        'except Exception:\n                    logger.warning("解析决策 JSONL 失败, 跳过该行", exc_info=True)\n                    continue',
    ),
    # strategy_diagnoser.py:587 — except: pass
    (
        "utils/alpha/layers/strategy_diagnoser.py",
        "except Exception:\n            pass",
        'except Exception:\n            logger.warning("获取 strategy layer_score 失败", exc_info=True)\n            pass',
    ),

    # ============================================================
    # P1: only-log 宽泛异常 — 添加 exc_info=True 保留堆栈
    # ============================================================

    # decision_theories.py — 4 处
    (
        "utils/alpha/decision_theories.py",
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n        logger.warning(f"索罗斯反身性分析失败: {e}")',
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n        logger.warning("索罗斯反身性分析失败: %s", e, exc_info=True)',
    ),
    (
        "utils/alpha/decision_theories.py",
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n        logger.warning(f"达利奥经济机器分析失败: {e}")',
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n        logger.warning("达利奥经济机器分析失败: %s", e, exc_info=True)',
    ),
    (
        "utils/alpha/decision_theories.py",
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n        logger.warning(f"凯利公式计算失败: {e}")',
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n        logger.warning("凯利公式计算失败: %s", e, exc_info=True)',
    ),
    (
        "utils/alpha/decision_theories.py",
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n        logger.warning(f"综合决策生成失败: {e}")',
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n        logger.warning("综合决策生成失败: %s", e, exc_info=True)',
    ),

    # auto_retrain_scheduler.py — 2 处
    (
        "utils/alpha/auto_retrain_scheduler.py",
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n            logger.warning("加载历史任务失败: %s", e)',
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n            logger.warning("加载历史任务失败: %s", e, exc_info=True)',
    ),
    (
        "utils/alpha/auto_retrain_scheduler.py",
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n                logger.exception("定时检查异常: %s", e)',
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n                logger.exception("定时检查异常: %s", e)',
    ),

    # drift_monitor.py — 2 处
    (
        "utils/alpha/drift_monitor.py",
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n            logger.warning("告警持久化失败: %s", e)',
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n            logger.warning("告警持久化失败: %s", e, exc_info=True)',
    ),
    (
        "utils/alpha/drift_monitor.py",
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n            logger.warning("重训练触发检查失败: %s", e)',
        'except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化\n            logger.warning("重训练触发检查失败: %s", e, exc_info=True)',
    ),

    # static_parser.py — 6 处
    (
        "utils/alpha/layers/static_parser.py",
        'except Exception as e:\n            logger.warning("mypy 输出解析失败 (降级为空): %s", e)',
        'except Exception as e:\n            logger.warning("mypy 输出解析失败 (降级为空): %s", e, exc_info=True)',
    ),
    (
        "utils/alpha/layers/static_parser.py",
        'except Exception as e:\n            logger.warning("pylint 输出解析失败 (降级为空): %s", e)',
        'except Exception as e:\n            logger.warning("pylint 输出解析失败 (降级为空): %s", e, exc_info=True)',
    ),
    (
        "utils/alpha/layers/static_parser.py",
        'except Exception as e:\n                logger.warning("StaticError 转 RootCause 失败 (跳过): %s", e)',
        'except Exception as e:\n                logger.warning("StaticError 转 RootCause 失败 (跳过): %s", e, exc_info=True)',
    ),
    (
        "utils/alpha/layers/static_parser.py",
        'except Exception as e:\n                    logger.warning("解析 mypy 报告失败 %s: %s", f.name, e)',
        'except Exception as e:\n                    logger.warning("解析 mypy 报告失败 %s: %s", f.name, e, exc_info=True)',
    ),
    (
        "utils/alpha/layers/static_parser.py",
        'except Exception as e:\n                    logger.warning("解析 pylint 报告失败 %s: %s", f.name, e)',
        'except Exception as e:\n                    logger.warning("解析 pylint 报告失败 %s: %s", f.name, e, exc_info=True)',
    ),
    (
        "utils/alpha/layers/static_parser.py",
        'except Exception as e:\n            logger.warning("解析报告目录失败 (降级为空): %s", e)',
        'except Exception as e:\n            logger.warning("解析报告目录失败 (降级为空): %s", e, exc_info=True)',
    ),
]

# ============================================================
# 执行修复
# ============================================================
applied = 0
skipped = 0
for rel_path, old, new in fixes:
    fpath = ROOT / rel_path
    if not fpath.exists():
        print(f"[SKIP] 文件不存在: {rel_path}")
        skipped += 1
        continue
    content = fpath.read_text(encoding="utf-8")
    if old not in content:
        print(f"[SKIP] 未匹配: {rel_path}")
        nl_esc = '\\n'
        print(f"       查找: {old[:80].replace(chr(10), nl_esc)}...")
        skipped += 1
        continue
    content = content.replace(old, new)
    fpath.write_text(content, encoding="utf-8")
    print(f"[OK]   {rel_path}")
    applied += 1

print(f"\n修复完成: {applied} 处已应用, {skipped} 处跳过")