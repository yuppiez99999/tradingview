"""市场规则常量 — 单一事实源。

集中管理 A 股涨跌停板规则、异常波动阈值等跨模块共用常量，
避免在多个文件中复制粘贴导致的状态漂移。

主要用途:
    - 数据清洗流水线 (quality_validator / cross_validation / data_layer)
    - Shadow 账户内部一致性校验 (shadow_real_data_feeder)
    - 实盘/回测中的异常过滤 (handlers / pipelines)

维护原则:
    1. 本模块是唯一允许修改阈值和板别识别规则的地方
    2. 各业务模块通过 `from utils.market_rules import ...` 获取
    3. 任何阈值调整需在 CHANGELOG 和 project_memory.md 同步
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# ============================================================
# 异常波动阈值 (按板别差异化)
# ============================================================

# 内部一致性检查 — 主板/常规 ETF 默认阈值 (±20%)
ABNORMAL_RETURN_THRESHOLD_INTERNAL = 0.20

# 内部一致性检查 — 20cm 板阈值 (±30%, 含涨停缓冲)
ABNORMAL_RETURN_THRESHOLD_20CM = 0.30

# 外部一致性检查 (cross-source) 阈值
CROSS_VALIDATION_THRESHOLD = 0.10

# 标的覆盖率阈值 (80%)
COVERAGE_THRESHOLD = 0.80

# 最小有效权重 (0.1%)
MIN_SIGNIFICANT_WEIGHT = 0.001

# 有效价格下界
MIN_POSITIVE_PRICE = 0.001

# ============================================================
# 20cm 板代码段正则 (A 股官方 2020-08-24 创业板注册制改革后)
# ============================================================
_20CM_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^688\d{3}$"),  # 科创板股票 (688xxx)
    re.compile(r"^689\d{3}$"),  # 科创板 B 股 (689xxx)
    re.compile(r"^300\d{3}$"),  # 创业板注册制股票 (300xxx, 2020-08-24 起)
    re.compile(r"^301\d{3}$"),  # 创业板新股 (301xxx)
    re.compile(r"^588\d{3}$"),  # 科创板 ETF (588xxx)
    re.compile(r"^562\d{3}$"),  # 科创板 ETF 新段 (562xxx)
)

# ------------------------------------------------------------
# 20cm 创业板相关 ETF 显式白名单
# ------------------------------------------------------------
# 背景: 代码段 `159xxx` 同时包含 20cm 的创业板 ETF (跟踪创业板指 399006 /
# 创业板50 / 创业板综合指数等 20% 涨跌幅指数) 和 10cm 的非创业板 ETF
# (如 159919 沪深300 / 159949 中证500 等跟踪 10% 涨跌幅指数).
# 因此无法仅靠代码段正则区分, 必须用显式白名单.
#
# 维护规则:
#   1. 新增 20cm 创业板 ETF 时, 在此添加 6 位代码 (不含交易所后缀)
#   2. 仅添加跟踪 20% 涨跌幅指数的 ETF (创业板指 / 创业板50 / 创业板综合等)
#   3. 跟踪沪深300/中证500/中证1000 等普通宽基的 ETF 不得加入
_20CM_ETF_EXPLICIT_CODES: frozenset[str] = frozenset(
    {
        "159915",  # 易方达创业板ETF (跟踪创业板指 399006)
        "159917",  # 国寿安保创业板ETF
        "159947",  # 华安创业板50ETF (跟踪创业板50指数)
        "159948",  # 博时创业板ETF
        "159949",  # 汇添富创业板ETF
        "159952",  # 广发创业板ETF
        "159955",  # 富国创业板ETF
        "159957",  # 华安创业板ETF
        "159966",  # 工银瑞信创业板ETF
        "159967",  # 华夏创蓝筹ETF (跟踪创业板低波动蓝筹)
        "159976",  # 招商创业板大盘ETF
        "159977",  # 国泰创业板ETF
        "159978",  # 鹏华创业板ETF
        "159995",  # 华夏创业板ETF
    }
)

# 10cm 板代码段 (明确枚举, 用于文档和调试)
_10CM_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^600\d{3}$"),  # 上交所主板 (600xxx)
    re.compile(r"^601\d{3}$"),  # 上交所大盘 (601xxx)
    re.compile(r"^603\d{3}$"),  # 上交所中小板 (603xxx)
    re.compile(r"^605\d{3}$"),  # 上交所主板 (605xxx)
    re.compile(r"^000\d{3}$"),  # 深交所主板 (000xxx)
    re.compile(r"^001\d{3}$"),  # 深交所主板 (001xxx)
    re.compile(r"^002\d{3}$"),  # 深交所主板 (002xxx)
    re.compile(r"^301\d{3}$"),  # 创业板新股 (301xxx, 已在 20cm 列表)
    re.compile(r"^510\d{3}$"),  # 主板 ETF (510xxx)
    re.compile(r"^511\d{3}$"),  # 货币 ETF (511xxx)
    re.compile(r"^512\d{3}$"),  # 宽基/行业 ETF (512xxx)
    re.compile(r"^515\d{3}$"),  # 宽基/行业 ETF (515xxx)
    re.compile(r"^159\d{3}$"),  # 深交所 ETF (159xxx, 含 20cm 白名单例外)
)


def normalize_symbol_code(symbol: str) -> str:
    """将 symbol 归一为 6 位纯数字代码。

    支持格式:
        - "600276"
        - "600276.SH"
        - "sh600276"
        - "SH.600276"

    Args:
        symbol: 原始 symbol 字符串

    Returns:
        6 位代码, 无法识别时返回空字符串
    """
    if not symbol:
        return ""
    code = symbol.strip().upper()
    # 去除交易所前缀/后缀
    code = re.sub(r"^(SH|SZ|BJ)\.?", "", code)
    code = re.sub(r"\.(SH|SZ|BJ)$", "", code)
    # 提取 6 位数字
    m = re.search(r"\d{6}", code)
    return m.group(0) if m else ""


def is_20cm_symbol(symbol: str) -> bool:
    """判断 symbol 是否属于 20% 涨跌停板 (科创板/创业板注册制).

    识别优先级 (任一命中即为 20cm):
        1. 显式白名单 (创业板相关 ETF, 如 159915/159952/159977 等)
           —— 因 `159xxx` 代码段同时含 10cm ETF, 无法仅靠正则区分
        2. 代码段正则 (688/689/300/301/588/562xxx)

    Args:
        symbol: 标的代码, 支持多种格式 (详见 normalize_symbol_code)

    Returns:
        True: 属于 20cm 板, 阈值应用 ABNORMAL_RETURN_THRESHOLD_20CM (±30%)
        False: 属于 10cm 板, 阈值应用 ABNORMAL_RETURN_THRESHOLD_INTERNAL (±20%)
    """
    code = normalize_symbol_code(symbol)
    if not code:
        return False
    # 1. 显式白名单优先 (覆盖代码段无法区分的创业板 ETF)
    if code in _20CM_ETF_EXPLICIT_CODES:
        return True
    # 2. 代码段正则匹配
    return any(p.match(code) for p in _20CM_CODE_PATTERNS)


def get_abnormal_threshold(symbol: str) -> float:
    """根据 symbol 返回差异化异常波动阈值 (单元: 小数, 即 0.30 = 30%).

    在数据清洗/一致性校验的热路径中直接调用, 避免每个调用点
    自行判断板别.

    Args:
        symbol: 标的代码

    Returns:
        阈值 (float), 20cm 板 ±0.30, 10cm 板 ±0.20
    """
    return (
        ABNORMAL_RETURN_THRESHOLD_20CM
        if is_20cm_symbol(symbol)
        else ABNORMAL_RETURN_THRESHOLD_INTERNAL
    )


def classify_board(symbol: str) -> str:
    """返回 symbol 的板别标签 (用于日志/报告分类).

    Args:
        symbol: 标的代码

    Returns:
        "20cm" | "10cm" | "unknown"
    """
    if is_20cm_symbol(symbol):
        return "20cm"
    code = normalize_symbol_code(symbol)
    if not code:
        return "unknown"
    return "10cm" if any(p.match(code) for p in _10CM_CODE_PATTERNS) else "unknown"


def batch_classify(symbols: Iterable[str]) -> dict[str, str]:
    """批量分类板别, 返回 {symbol: board_label} 映射."""
    return {s: classify_board(s) for s in symbols}


def register_20cm_etf(code: str) -> None:
    """运行时注册 20cm 创业板 ETF 代码到显式白名单.

    适用场景: 上线新创业板 ETF 时, 在未升级 market_rules 版本前临时注册.

    Args:
        code: 6 位 ETF 代码 (可含 .SH/.SZ 后缀, 内部归一化)
    """
    normalized = normalize_symbol_code(code)
    if not normalized:
        return
    # frozenset 不可变, 用新对象替换模块引用
    global _20CM_ETF_EXPLICIT_CODES
    if normalized not in _20CM_ETF_EXPLICIT_CODES:
        _20CM_ETF_EXPLICIT_CODES = _20CM_ETF_EXPLICIT_CODES | {normalized}


def is_20cm_etf_explicit(code: str) -> bool:
    """判断 code 是否在显式 20cm ETF 白名单中 (用于调试/审计)."""
    normalized = normalize_symbol_code(code)
    return normalized in _20CM_ETF_EXPLICIT_CODES
