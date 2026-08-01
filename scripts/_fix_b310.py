# -*- coding: utf-8 -*-
"""修复 B310: urllib.request.urlopen 未限制 URL 协议
在 llm_router.py 和 omni_route_client.py 中添加安全封装函数 _safe_urlopen,
校验 URL 必须以 http:// 或 https:// 开头, 拒绝 file:/// 等本地协议。
"""
import re


def fix_llm_router():
    """修复 utils/alpha/llm_router.py 的 3 处 urlopen"""
    filepath = r"utils\alpha\llm_router.py"
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 在文件顶部（import urllib 之后）添加 _safe_urlopen 封装函数
    # 找到第一个 urlopen 调用前的合适位置
    helper = '''

def _safe_urlopen(req, timeout=None):
    """安全封装 urllib.request.urlopen — 拒绝非 http/https 协议 (B310)"""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"拒绝非 HTTP 协议的 URL: {url[:100]}")
    if timeout is not None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req)

'''

    # 在 "import urllib.request" 之后第一次出现的位置插入
    # 找到第一个 urlopen 调用所在行, 在其前面的合适位置插入 helper
    # 更简单: 在 class 定义之前插入
    # 找到 "class " 或第一个 def 之后
    if "_safe_urlopen" not in content:
        # 找到 "class LLMRouter" 或第一个 class/def
        match = re.search(r"\nclass \w+", content)
        if match:
            pos = match.start()
            content = content[:pos] + helper + content[pos:]
        else:
            # 退而求其次: 在文件末尾追加
            content = content + helper

    # 2. 替换 3 处 urllib.request.urlopen(req, timeout=xxx) → _safe_urlopen(req, timeout=xxx)
    content = re.sub(
        r"urllib\.request\.urlopen\((req),\s*timeout=([^)]+)\)",
        r"_safe_urlopen(\1, timeout=\2)",
        content,
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Fixed: {filepath}")


def fix_omni_route_client():
    """修复 utils/alpha/omni_route_client.py 的 3 处 urlopen"""
    filepath = r"utils\alpha\omni_route_client.py"
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    helper = '''

def _safe_urlopen(req, timeout=None):
    """安全封装 urllib.request.urlopen — 拒绝非 http/https 协议 (B310)"""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"拒绝非 HTTP 协议的 URL: {url[:100]}")
    if timeout is not None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req)

'''

    if "_safe_urlopen" not in content:
        match = re.search(r"\nclass \w+", content)
        if match:
            pos = match.start()
            content = content[:pos] + helper + content[pos:]
        else:
            content = content + helper

    # 替换 3 处
    content = re.sub(
        r"urllib\.request\.urlopen\((req),\s*timeout=([^)]+)\)",
        r"_safe_urlopen(\1, timeout=\2)",
        content,
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Fixed: {filepath}")


if __name__ == "__main__":
    fix_llm_router()
    fix_omni_route_client()
