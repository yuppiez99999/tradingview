# -*- coding: utf-8 -*-
"""持仓配置加载器 — 统一入口 (B1.7)

所有模块应通过本模块加载 config/positions.json, 避免散落的 json.load
调用导致路径/编码/错误处理不一致。

特性:
    - 默认路径: <project_root>/config/positions.json
    - 失败安全: 文件缺失或解析失败时返回空 dict (或调用方指定的默认值)
    - 原子读取: 内部使用 read_json_locked 防止并发读写损坏
    - 路径自适应: 从本文件位置推导项目根目录, 不依赖 CWD

用法:
    from utils.positions_loader import load_positions

    # 最简: 返回原始 dict (失败返回 {})
    data = load_positions()

    # 指定默认值
    data = load_positions(default={"positions": {}})

    # 指定路径
    data = load_positions(path="/custom/positions.json")
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

# 项目根目录 (本文件位于 <root>/utils/positions_loader.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POSITIONS_PATH = _PROJECT_ROOT / "config" / "positions.json"


def load_positions(
    path: Optional[Union[str, Path]] = None,
    default: Any = None,
) -> Dict[str, Any]:
    """加载 config/positions.json 持仓配置 (统一入口)

    Args:
        path: 自定义路径; None 表示使用默认的 config/positions.json
        default: 文件缺失或解析失败时的返回值; None 表示返回 {}

    Returns:
        持仓配置 dict; 失败时返回 default 或 {}
    """
    target = Path(path) if path else DEFAULT_POSITIONS_PATH
    if default is None:
        default = {}

    if not target.exists():
        logger.debug("positions.json 不存在: %s", target)
        return default

    try:
        # 优先使用并发安全的读取 (与 atomic_write_json 配对)
        try:
            from utils.concurrency import read_json_locked

            result = read_json_locked(target, default=default)
            if result is None:
                return default
            return result
        except ImportError:
            # 并发模块不可用时回退到普通读取
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning("加载 positions.json 失败 (%s): %s", target.name, e)
        return default


def get_positions_list(path: Optional[Union[str, Path]] = None) -> list:
    """便捷方法: 返回持仓列表 (positions dict 的 values)

    Args:
        path: 自定义路径; None 表示默认

    Returns:
        list[dict]: 持仓条目列表; 失败时返回 []
    """
    data = load_positions(path=path, default={"positions": {}})
    positions = data.get("positions", {})
    if isinstance(positions, dict):
        return list(positions.values())
    if isinstance(positions, list):
        return positions
    return []


def get_positions_dict(path: Optional[Union[str, Path]] = None) -> Dict[str, Dict]:
    """便捷方法: 返回 {code: item} 映射

    Args:
        path: 自定义路径; None 表示默认

    Returns:
        dict: {code: 持仓条目}; 失败时返回 {}
    """
    data = load_positions(path=path, default={"positions": {}})
    positions = data.get("positions", {})
    if isinstance(positions, dict):
        # 如果 key 已经是 code, 直接返回; 否则用 item.code 做 key
        result = {}
        for k, v in positions.items():
            if isinstance(v, dict):
                code = v.get("code", k)
                result[code] = v
            else:
                result[k] = v
        return result
    return {}
