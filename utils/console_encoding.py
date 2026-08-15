"""控制台 UTF-8 编码设置 — Windows 环境中文输出兼容

解决 Windows PowerShell/cmd 默认 GBK 编码导致的中文乱码问题。
在主入口文件加载早期调用 setup_utf8_console() 统一设置。
"""
from __future__ import annotations

import io
import logging
import sys

logger = logging.getLogger(__name__)


def setup_utf8_console() -> None:
    """设置控制台为 UTF-8 编码, 确保 print/logger 中文不乱码。

    处理顺序:
      1. sys.stdout/stderr reconfigure 为 utf-8 (Python 3.7+)
      2. Windows 下执行 chcp 65001 切换代码页
      3. 设置环境变量 PYTHONIOENCODING 兜底
    """
    # 1. reconfigure stdout/stderr
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        # Python 3.7+ 支持 reconfigure
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            try:
                reconfigure(encoding='utf-8', errors='replace')
                continue
            except (ValueError, TypeError, OSError):
                pass
        # 兜底: 用 TextIOWrapper 包装
        if hasattr(stream, 'buffer'):
            try:
                setattr(sys, stream_name,
                        io.TextIOWrapper(stream.buffer, encoding='utf-8', errors='replace'))
            except (ValueError, AttributeError):
                pass

    # 2. Windows 切换代码页
    if sys.platform == 'win32':
        try:
            import subprocess
            # chcp 65001 = UTF-8, 不检查返回值 (非 Windows 终端可能失败)
            # nosec B602 — 命令为常量列表 ['chcp','65001'], 无用户输入, 无注入风险
            subprocess.run(['chcp', '65001'], capture_output=True, shell=True, check=False)
        except (OSError, subprocess.SubprocessError):
            pass

    # 3. 环境变量兜底
    import os
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
