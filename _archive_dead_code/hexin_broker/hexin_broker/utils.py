"""
同花顺客户端自动化工具函数。

提供控件查找、文本反查、树遍历等通用能力。
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def find_window(app, title_keywords: Optional[List[str]] = None):
    """按标题关键字模糊查找顶层窗口"""
    if not title_keywords:
        try:
            return app.top_window()
        except Exception:
            return None
    for keyword in title_keywords:
        try:
            import re
            pattern = f".*{re.escape(keyword)}.*"
            return app.window(title_re=pattern, visible_only=False)
        except Exception:
            continue
    try:
        return app.top_window()
    except Exception:
        return None


def safe_call(func, *args, **kwargs) -> Any:
    """安全调用，失败返回 None"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.debug("safe_call error: %s", e)
        return None


def retry(max_retries: int = 3, interval: float = 1.0):
    """重试装饰器工厂"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            last = None
            for i in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last = e
                    import time
                    time.sleep(interval)
            raise last
        return wrapper
    return decorator


def iter_children(node: Any, max_depth: int = 10, _depth: int = 0):
    """递归遍历子控件"""
    if _depth >= max_depth:
        return
    try:
        children = node.children()
    except Exception:
        return
    for child in children:
        yield child
        yield from iter_children(child, max_depth=max_depth, _depth=_depth + 1)


def find_controls_by_text(node: Any, keyword: str, max_depth: int = 10) -> List[Any]:
    """递归查找包含关键字的控件"""
    results: List[Any] = []
    try:
        title = node.window_text() or ""
    except Exception:
        title = ""
    if keyword in title:
        results.append(node)
    try:
        for child in node.children():
            results.extend(find_controls_by_text(child, keyword, max_depth))
    except Exception:
        pass
    return results


def read_funds_by_text(window: Any) -> Dict[str, Optional[str]]:
    """尝试从资金区域文本中解析关键字段"""
    import re
    text = ""
    try:
        text = window.window_text() or ""
    except Exception:
        pass
    return {
        "available_amount": safe_call(_first_group, text, r"可用金额[^0-9]*([0-9,.]+)"),
        "total_asset": safe_call(_first_group, text, r"总[ 0-9]*资[ 0-9]*产[^0-9]*([0-9,.]+)"),
        "market_value": safe_call(_first_group, text, r"股票市值[^0-9]*([0-9,.]+)"),
    }


def _first_group(text: str, pattern: str) -> Optional[str]:
    import re
    m = re.search(pattern, text)
    return m.group(1) if m else None


def parse_static_pairs(window: Any) -> Dict[str, Optional[str]]:
    """将资金区域文本中的 标签/值 对尽量配对"""
    text = ""
    try:
        text = window.window_text() or ""
    except Exception:
        return {}
    tokens = [t.strip() for t in text.split() if t.strip()]
    pairs: Dict[str, Optional[str]] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        try:
            float(token.replace(",", ""))
            i += 1
            continue
        except ValueError:
            pass
        pairs[token] = tokens[i + 1] if i + 1 < len(tokens) else None
        i += 2
    return pairs
