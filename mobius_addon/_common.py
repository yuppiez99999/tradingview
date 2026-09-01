"""Mobius 外挂公共模块。

职责：
- 运行环境变量自设（NO_PROXY / PYTHONIOENCODING / OPENBLAS_NUM_THREADS），不修改系统配置。
- 只读装配 GLM-5 客户端（utils.glm5_client.quick_chat），失败则 fail-open 降级。
- 定位主系统根目录（mobius_addon 的父目录），供只读 import / 只读扫描使用。

设计铁律：本模块及所有子模块绝不写任何现有目录；GLM-5 不可用时降级为规则模板。
"""

import logging
import os
import sys

_GLM5 = None  # type: Optional[object]


def ensure_env() -> None:
    """设置外挂运行所需环境变量（仅在未设置时），不改系统全局配置。"""
    defaults = {
        "NO_PROXY": "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn",
        "PYTHONIOENCODING": "utf-8",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    for key, val in defaults.items():
        if key not in os.environ:
            os.environ[key] = val


def get_sys_root() -> str:
    """返回主系统根目录（mobius_addon 的父目录）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_LOGGER = None


def get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [mobius_addon] %(message)s",
        )
        _LOGGER = logging.getLogger("mobius_addon")
    return _LOGGER


def glm5_quick_chat(message: str, default: str = "") -> str:
    """只读调用系统 GLM-5 客户端。

    fail-open：任何失败（导入失败 / 调用异常 / 返回空）都返回 default，
    保证外挂永不因主系统状态崩溃。
    """
    global _GLM5
    ensure_env()
    if _GLM5 is False:
        return default
    if _GLM5 is None:
        try:
            root = get_sys_root()
            if root not in sys.path:
                sys.path.insert(0, root)
            from utils.glm5_client import quick_chat  # noqa: WPS433

            _GLM5 = quick_chat
        except Exception as exc:  # noqa: BLE001
            get_logger().warning("GLM-5 不可用, 降级规则提取: %s", exc)
            _GLM5 = False
            return default
    try:
        resp = _GLM5(message)
        return resp if isinstance(resp, str) else default
    except Exception as exc:  # noqa: BLE001
        get_logger().warning("GLM-5 调用失败, 降级: %s", exc)
        return default
