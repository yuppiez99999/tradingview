"""环境变量加载器 — 从 .env 文件加载配置到 os.environ

简易实现, 不依赖 python-dotenv 第三方库。
支持:
  - KEY=VALUE 基本格式
  - 引号包裹的值 (单引号/双引号)
  - # 注释行
  - 空行跳过
  - 已存在的环境变量不覆盖 (setdefault 语义)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load_dotenv(env_path: str | None = None, override: bool = False) -> bool:
    """加载 .env 文件到 os.environ。

    Args:
        env_path: .env 文件路径, None 时自动查找 (项目根 / 当前目录)
        override: True 时覆盖已存在的环境变量, False 时保留已有值 (setdefault)

    Returns:
        True 表示文件存在并加载成功, False 表示文件不存在
    """
    if env_path is None:
        # 自动查找: 项目根 (本文件上两级) / 当前目录
        project_root = Path(__file__).resolve().parent.parent
        candidates = [
            project_root / ".env",
            Path.cwd() / ".env",
        ]
        for p in candidates:
            if p.is_file():
                env_path = str(p)
                break
        else:
            logger.debug(".env 文件未找到 (查找路径: %s, %s)", *candidates)
            return False

    env_file = Path(env_path)
    if not env_file.is_file():
        logger.debug(".env 文件不存在: %s", env_path)
        return False

    loaded = 0
    try:
        with env_file.open(encoding="utf-8") as f:
            for line_no, raw_line in enumerate(f, 1):
                line = raw_line.strip()
                # 跳过空行和注释
                if not line or line.startswith("#"):
                    continue
                # 解析 KEY=VALUE
                if "=" not in line:
                    logger.warning(
                        ".env L%d: 缺少 = 分隔符, 跳过: %s", line_no, line[:50]
                    )
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # 去除引号包裹
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                # 去除行尾注释 (仅对未加引号的值)
                # 注意: 引号内的 # 不应被当作注释, 上面已去引号, 这里不再处理
                if not key:
                    continue
                # 设置环境变量
                if override or key not in os.environ:
                    os.environ[key] = value
                    loaded += 1
    except OSError as e:
        logger.warning(".env 加载失败: %s", e)
        return False

    logger.debug(".env 加载完成: %d 个变量 (文件: %s)", loaded, env_path)
    return True
