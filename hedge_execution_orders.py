"""
对冲执行单生成器 v2.1
修复: C4 期货品种精确匹配 / C5 期货价格从配置读取 / M11 strike 类型统一 / M18 None 防御 / C9 归档路径统一
"""

import json
import logging
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# C8 修复: 使用动态 PROJECT_ROOT, 不硬编码路径
PROJECT_ROOT = Path(__file__).resolve().parent
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

# 统一路径 (v8.5+)
REPORTS_DIR = PROJECT_ROOT / "reports"

STYLE_BETA_MAP = {
    "科技": 1.20,
    "金融": 0.90,
    "宽基": 0.95,
    "新能源": 1.15,
    "医药": 0.85,
    "资源": 1.10,
    "制造": 1.05,
    "顺周期": 1.10,
    "防御": 0.60,
    "国债": 0.10,
    "成长": 1.20,
    "default": 1.00,
}

# C5 修复: 期货默认价格改为模块级常量, 标注为 fallback (最后兜底)
# 优先级: hedge_positions cfg.est_price > plan.futures_prices > 此处 fallback
FUTURES_FALLBACK_PRICES = {
    "IF": 3800.0,  # 沪深300股指期货
    "IC": 5500.0,  # 中证500股指期货
    "IM": 5800.0,  # 中证1000股指期货
    "AU": 500.0,  # 黄金期货
    "CU": 70000.0,  # 铜期货
    "AL": 20000.0,  # 铝期货
    "RB": 3500.0,  # 螺纹钢期货
}


def _extract_futures_code(instrument: str) -> str:
    """C4 修复: 从合约代码中提取品种代码 (如 'IF2501.CFFEX' -> 'IF', 'au2412' -> 'AU')

    Args:
        instrument: 合约代码或品种名称

    Returns:
        大写品种代码 (如 'IF', 'IC', 'IM', 'AU', 'CU')
    """
    if not instrument:
        return ""
    s = str(instrument).upper().strip()
    # 去掉交易所后缀 (如 .CFFEX, .SHFE)
    if "." in s:
        s = s.split(".")[0]
    # 提取前导字母 (品种代码)
    m = re.match(r"^([A-Za-z]+)", s)
    return m.group(1).upper() if m else s


def _is_commodity_futures(instrument: str, commodity_set: set) -> bool:
    """C4 修复: 精确匹配期货品种, 避免子串匹配导致 IF/IC/IM 被误判

    旧逻辑: any(c in instrument.upper() for c in commodity_futures)
        - 'I' in 'IF' == True  ← 股指期货被误判为商品期货 (铁矿石 I)
    新逻辑: 提取品种代码后精确匹配
        - _extract_futures_code('IF2501') == 'IF', 'IF' not in commodity_set
    """
    code = _extract_futures_code(instrument)
    return code in commodity_set


def _extract_contract_yyyymm(instrument: str, as_of_yyyymm: int = 0) -> tuple[str, int]:
    """从合约代码提取 (品种代码, 合约月份 YYYYMM)。

    P1-2 新增: 支持 5 种合约代码格式:
      - 'IF2608.CFFEX'  -> ('IF', 202608)
      - 'IF2608'        -> ('IF', 202608)
      - 'au2412'        -> ('AU', 202412)
      - 'CF609P15600'   -> ('CF', 202609)  (期权 YMM 短码)
      - 'CU2608'        -> ('CU', 202608)
    as_of_yyyymm: 用于推断期权 YMM 短码的年份 (如 202608 -> 短码 6 推断为 2026)。
                 不传时取系统当前年月。
    解析失败返回 ('', 0)。
    """
    if not instrument:
        return "", 0
    s = str(instrument).upper().strip()
    if "." in s:
        s = s.split(".")[0]
    m = re.match(r"^([A-Za-z]+)(\d+)", s)
    if not m:
        return "", 0
    code = m.group(1)
    digits = m.group(2)
    # 期货统一用 YYMM 月份段 (如 IF2608 = 2026年08月)
    if len(digits) >= 6:
        # 兼容潜在 YYYYMM 格式 (如 IF202608)
        yyyy = int(digits[:4])
        mm = int(digits[4:6]) if len(digits) >= 6 else None
        if yyyy < 2000 or yyyy > 2100:
            return "", 0
        return code, yyyy * 100 + (mm if mm else 1)
    if len(digits) == 4:
        # YYMM: 前2位年, 后2位月
        yy = int(digits[0:2])
        mm = int(digits[2:4])
        if not (1 <= mm <= 12):
            return "", 0
        return code, (2000 + yy) * 100 + mm
    if len(digits) == 3:
        # 期权 YMM 短码 (如 609 -> 202609): 首字符为年份个位, 后两位为月。
        # 年份十位按 as_of 推断: 取「个位 == Y 且不早于 as_of 的最近年份」。
        y = int(digits[0])
        mm = int(digits[1:3])
        if not (1 <= mm <= 12):
            return "", 0
        if as_of_yyyymm <= 0:
            import datetime

            _now = datetime.date.today()
            as_of_yyyymm = _now.year * 100 + _now.month
        as_of_year = as_of_yyyymm // 100
        # 候选年份: as_of 所在十年内的个位 == y
        year = (as_of_year // 10) * 10 + y
        # 若候选年份 < as_of 年份(已过去), 则向后推十年
        if year < as_of_year:
            year += 10
        return code, year * 100 + mm
    return "", 0


def _validate_contract_expiry(
    instrument: str, as_of: Optional[tuple] = None
) -> tuple[bool, str]:
    """校验期货/期权合约是否已到期 (下单前拒绝过期合约)。

    P1-2 新增: 防止提交已到期合约 (如当前 2026-08 却用 2025-07 的 2507 合约)。
    fail-closed: 无法解析合约月份时返回 (True, ...) 放行 (不误杀无法识别的代码);
    能解析且月份已过期的返回 (False, reason) 拒绝下单。

    Args:
        instrument: 合约代码 (如 'IF2608.CFFEX' / 'CF609P15600')
        as_of: 当前日期 (year, month, day); 默认取系统当前日期

    Returns:
        (is_valid, reason): is_valid=False 表示合约已到期, 应拒绝下单
    """
    try:
        import datetime

        if as_of is None:
            now = datetime.date.today()
            as_of = (now.year, now.month, now.day)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"解析 as_of 日期失败, 已降级使用 2026-01-01: {e}")
        as_of = (2026, 1, 1)

    as_of_yyyymm = as_of[0] * 100 + as_of[1]
    code, yyyymm = _extract_contract_yyyymm(instrument, as_of_yyyymm)
    if yyyymm <= 0:
        # 无法解析 (可能是纯品种名/ETF): 不误杀, 放行
        return True, ""
    # 合约月份 >= 当前月份才有效 (期货合约当月仍可交易, 到到期日止)
    if yyyymm < as_of_yyyymm:
        reason = (
            f"合约 {instrument} 已到期: 合约月 {yyyymm} < 当前 {as_of_yyyymm} "
            f"(如 2507 在 2026-08 已过期, 应使用 2608/2609 等活跃合约)"
        )
        return False, reason
    return True, ""


def _get_futures_price(
    instrument: str,
    prices: dict,
    cfg: Optional[dict] = None,
    plan: Optional[dict] = None,
) -> float:
    """C5 修复: 从多个来源获取期货价格, 不再使用单一硬编码值

    优先级:
        1. cfg.est_price (hedge_positions 配置中的预估价格)
        2. plan.futures_prices[code] (决策文件中的价格字典)
        3. prices[instrument] (持仓价格字典)
        4. FUTURES_FALLBACK_PRICES[code] (模块级兜底, 标注为 fallback)

    Returns:
        期货价格 (float), 找不到返回 0.0
    """
    code = _extract_futures_code(instrument)

    # 1. 从 cfg 读取
    if cfg and cfg.get("est_price"):
        try:
            return float(cfg["est_price"])
        except (TypeError, ValueError):
            pass

    # 2. 从 plan.futures_prices 读取
    if plan and isinstance(plan.get("futures_prices"), dict):
        fp = plan["futures_prices"]
        for key in (code, instrument, code.lower(), instrument.lower()):
            if fp.get(key):
                try:
                    return float(fp[key])
                except (TypeError, ValueError):
                    pass

    # 3. 从 prices 字典读取
    for key in (instrument, code, instrument.upper(), code.upper()):
        if key in prices and prices[key] and prices[key] > 0:
            try:
                return float(prices[key])
            except (TypeError, ValueError):
                pass

    # 4. Fallback (最后兜底, 打印警告)
    fallback = FUTURES_FALLBACK_PRICES.get(code, 5000.0)
    logger.warning(
        f"[WARN] 期货 {instrument} (品种={code}) 未找到实时价格, 使用 fallback: {fallback}"
    )
    return fallback


def load_positions():
    # C8 修复: 使用动态 PROJECT_ROOT
    path = PROJECT_ROOT / "config" / "positions.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    positions = {}
    prices = {}
    hedge_positions = data.get("hedge_positions", {})
    for item in data.get("positions", {}).values():
        code = item.get("code")
        qty = (
            item.get("phase1_shares")
            or item.get("total_shares")
            or item.get("shares", 0)
        )
        price = item.get("est_price", 0.0)
        if code and qty:
            positions[code] = float(qty)
            prices[code] = float(price)
    return positions, prices, hedge_positions, data


def calc_portfolio_beta(positions_data: dict) -> float:
    total_weighted_beta = 0.0
    total_weight = 0.0
    for item in positions_data.get("positions", {}).values():
        style = item.get("style", "default")
        weight = item.get("target_weight", 0.0)
        beta = STYLE_BETA_MAP.get(style, STYLE_BETA_MAP["default"])
        total_weighted_beta += weight * beta
        total_weight += weight
    if total_weight > 0:
        return total_weighted_beta / total_weight
    return 0.7


def merge_orders(orders: list) -> list:
    merged = OrderedDict()
    for o in orders:
        key = (o["type"], o.get("instrument", ""), o.get("action", ""))
        if key not in merged:
            merged[key] = o.copy()
        else:
            merged[key]["contracts"] = merged[key].get("contracts", 0) + o.get(
                "contracts", 0
            )
            merged[key]["premium_budget"] = merged[key].get(
                "premium_budget", 0
            ) + o.get("premium_budget", 0)
            merged[key]["budget_pct"] = max(
                merged[key].get("budget_pct", 0), o.get("budget_pct", 0)
            )
            if o.get("priority", "secondary") == "primary":
                merged[key]["priority"] = "primary"
    return list(merged.values())


def _compute_beta_hedge_allocation(beta: float, hedge_value: float) -> tuple:
    """根据 Beta 计算期权和期货的对冲名义金额分配。

    Args:
        beta: 组合 Beta
        hedge_value: 对冲总金额

    Returns:
        (option_notional, futures_notional) 元组
    """
    if beta > 1.1:
        return hedge_value * 0.7, hedge_value * 0.3
    if beta > 0.9:
        return hedge_value * 0.8, hedge_value * 0.2
    return hedge_value * 0.9, hedge_value * 0.1


# 期权对冲标的配置: (标的代码, 合约说明, 配置权重, 虚值比例)
_OPTION_HEDGE_CODES = [
    ("510050.SH", "510050 Put", 0.5, 0.95),
    ("510300.SH", "沪深300ETF Put", 0.3, 0.95),
    ("159915.SZ", "创业板ETF Put", 0.2, 0.95),
]

_OPTION_MULTIPLIER = 10000

# 防御资产配置 (代码, 名称, 持仓金额) — Q-1 修复: 提取到 utils.hedge_constants 单一事实源
from utils.hedge_constants import DEFENSE_ASSETS as _DEFENSE_ASSETS


def _build_beta_option_orders(
    beta: float,
    hedge_value: float,
    option_notional: float,
    prices: dict,
    hedge_positions: dict,
    target: float,
) -> list:
    """构建 Beta 期权保护订单。

    Args:
        beta: 组合 Beta
        hedge_value: 对冲总金额
        option_notional: 期权分配名义金额
        prices: 价格字典
        hedge_positions: 对冲持仓配置
        target: 目标资金

    Returns:
        期权订单列表
    """
    orders = []
    remaining_notional = option_notional

    for code, instrument, weight, otm_pct in _OPTION_HEDGE_CODES:
        if remaining_notional <= 0:
            break
        est_price = prices.get(code, 0.0)
        if est_price <= 0:
            continue
        # C4 修复: weight (0.5/0.3/0.2, 和为1) 是相对 option_notional 总额的占比,
        # 而非相对递减后的 remaining_notional。用总额占比保证三只期权名义金额之和 == option_notional。
        alloc_notional = option_notional * weight
        item_cfg = next(
            (
                c
                for _key, c in hedge_positions.items()
                if isinstance(c, dict)
                and instrument.split()[0] in c.get("instrument", "")
            ),
            None,
        )
        # M18 修复: cfg.get 返回 None 时用 or 兜底
        premium_budget = (
            item_cfg.get("premium_budget") if item_cfg else None
        ) or alloc_notional * 0.15
        one_contract_notional = est_price * _OPTION_MULTIPLIER
        # S4 修复: 名义金额不足以覆盖最小阈值时跳过该期权, 避免极小 alloc 被 max(1,..) 强制开 1 张导致过度对冲
        min_notional = (
            item_cfg.get("min_notional") if item_cfg else None
        ) or one_contract_notional
        if alloc_notional < min_notional:
            logger.info(
                "[SKIP] %s 期权分配名义 %.2f < 最小阈值 %.2f, 跳过 (避免过度对冲)",
                code,
                alloc_notional,
                min_notional,
            )
            continue
        contracts = max(1, int(alloc_notional / one_contract_notional))
        strike = round(est_price * otm_pct, 2)

        orders.append(
            {
                "type": "OPTIONS",
                "action": "BUY_PROTECTION",
                "instrument": instrument,
                "exchange": "SSE" if "SH" in code else "SZSE",
                "contracts": contracts,
                "strike": strike,
                "otm_pct": otm_pct,
                "premium_budget": round(premium_budget, 2),
                "budget_pct": round(premium_budget / target, 4) if target > 0 else 0.0,
                "reason": f"Beta对冲({beta:.2f})优先期权保护",
                "framework": ["优先期权", "Beta对冲", "尾部保护"],
                "beta_hedge_pct": (
                    round(alloc_notional / hedge_value, 2) if hedge_value > 0 else 0.0
                ),
                "priority": "primary",
            }
        )
        remaining_notional -= alloc_notional
    return orders


def _build_beta_futures_order(
    futures_notional: float,
    prices: dict,
    hedge_positions: dict,
    plan: dict,
    target: float,
    beta: float,
) -> list:
    """构建 Beta 期货对冲订单。

    Args:
        futures_notional: 期货分配名义金额
        prices: 价格字典
        hedge_positions: 对冲持仓配置
        plan: 计划字典
        target: 目标资金
        beta: 组合 Beta

    Returns:
        期货订单列表 (空列表表示未触发)
    """
    if futures_notional <= 0:
        return []

    futures_cfg = hedge_positions.get("IF_futures", {})
    target_contracts = futures_cfg.get("target_contracts", 1)
    if target_contracts <= 0:
        return []

    # S5 修复: instrument/multiplier 从配置读取 (默认 IF/300, 兼容 IC/IH/IM);
    # 与文件内既有 cfg.get("multiplier") 模式 (L546/L658) 一致, 不再硬编码。
    instrument = futures_cfg.get("instrument", "IF")
    multiplier = int(futures_cfg.get("multiplier", 300) or 300)
    # C5 修复: 从配置/plan 获取期货价格, 不再硬编码 3800.0
    fut_price = _get_futures_price(instrument, prices, futures_cfg, plan)
    notional = multiplier * fut_price
    n = target_contracts
    return [
        {
            "type": "FUTURES",
            "action": "SELL_SHORT",
            "instrument": instrument,
            "exchange": "CFFEX",
            "contracts": n,
            "multiplier": multiplier,
            "est_price": fut_price,
            "notional": n * notional,
            "estimated_cost": n * notional * 0.00013,
            "budget_pct": round(n * notional / target, 4) if target > 0 else 0.0,
            "reason": f"Beta期货对冲({beta:.2f})",
            "framework": ["Beta对冲"],
            "priority": "primary",
        }
    ]


def _classify_hedge_configs(hedge_positions: dict) -> tuple:
    """将对冲配置分类为期权和期货配置。

    Args:
        hedge_positions: 对冲持仓配置字典

    Returns:
        (option_cfgs, futures_cfgs, commodity_futures_set) 元组
    """
    # C4 修复: 商品期货品种集合 (仅品种代码, 用于精确匹配)
    commodity_futures = {
        "CU",
        "AL",
        "LC",
        "AU",
        "RB",
        "I",
        "J",
        "JM",
        "焦煤",
        "焦炭",
        "铁矿石",
        "螺纹",
        "铜",
        "铝",
        "碳酸锂",
        "黄金",
    }

    option_cfgs: dict = {}
    futures_cfgs: dict = {}

    for key, cfg in hedge_positions.items():
        if not isinstance(cfg, dict):
            continue
        instrument = cfg.get("instrument", key)
        is_option = "Put" in instrument or "Call" in instrument or "期权" in instrument
        is_commodity = _is_commodity_futures(instrument, commodity_futures)

        if is_option:
            option_cfgs[key] = cfg
        elif is_commodity:
            if cfg.get("force_futures", False):
                futures_cfgs[key] = cfg
        else:
            futures_cfgs[key] = cfg

    return option_cfgs, futures_cfgs, commodity_futures


def _merge_option_order(existing: dict, item_cfg: dict) -> None:
    """合并已存在的期权订单 (原地修改)。

    Args:
        existing: 已存在的订单
        item_cfg: 新的配置项
    """
    existing["contracts"] += item_cfg.get("target_contracts") or 0
    existing["premium_budget"] += item_cfg.get("premium_budget") or 0
    existing["reason"] = item_cfg.get("reason") or existing["reason"]
    existing["framework"] = item_cfg.get("framework") or existing["framework"]


def _build_option_order_from_cfg(item_cfg: dict, key: str, target: float) -> dict:
    """从配置项构建期权订单。

    Args:
        item_cfg: 配置项
        key: 配置键
        target: 目标资金

    Returns:
        期权订单字典
    """
    instrument = item_cfg.get("instrument", key)
    # M11 修复: strike 统一为 float
    strike_raw = item_cfg.get("strike")
    try:
        strike_val = float(strike_raw) if strike_raw is not None else 0.0
    except (TypeError, ValueError):
        strike_val = 0.0
    # M18 修复: None 防御
    premium_budget = item_cfg.get("premium_budget") or 0.0
    return {
        "type": "OPTIONS",
        "action": item_cfg.get("direction") or "BUY",
        "instrument": instrument,
        "exchange": item_cfg.get("exchange") or "",
        "contracts": item_cfg.get("target_contracts") or 0,
        "strike": strike_val,
        "premium_budget": float(premium_budget),
        "budget_pct": float(premium_budget) / target if target > 0 else 0.0,
        "reason": item_cfg.get("reason") or "",
        "framework": item_cfg.get("framework") or [],
        "priority": "primary",
    }


def _build_futures_order_from_cfg(
    item_cfg: dict,
    key: str,
    prices: dict,
    plan: dict,
    target: float,
    commodity_futures: set,
    existing_orders: list,
) -> Optional[dict]:
    """从配置项构建期货订单。

    Args:
        item_cfg: 配置项
        key: 配置键
        prices: 价格字典
        plan: 计划字典
        target: 目标资金
        commodity_futures: 商品期货集合
        existing_orders: 已有订单列表

    Returns:
        期货订单字典, None 表示跳过
    """
    instrument = item_cfg.get("instrument", key)
    # P1-2: 合约到期校验——拒绝已过期合约 (如 2507 在 2026-08)。
    # 对无法解析的代码 (纯品种名/ETF) fail-closed 放行; 对可解析且已过期的拒绝。
    # Bug-2 修复: 动态计算当前活跃合约月份 (当月 + 下月), 替代硬编码 "2608"/"2609"
    _now = datetime.now()
    _active_months = {
        _now.strftime("%y%m"),
        (_now.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%y%m"),
    }
    if any(m in instrument for m in _active_months):
        # 当前活跃合约月份, 直接放行 (避免多余解析开销)
        pass
    else:
        _valid, _reason = _validate_contract_expiry(instrument)
        if not _valid:
            logger.warning("[合约到期校验] %s", _reason)
            return None
    if "IF" in instrument and any(
        o["instrument"] == "IF" for o in existing_orders if o["type"] == "FUTURES"
    ):
        return None
    direction = item_cfg.get("direction") or ""
    target_contracts = item_cfg.get("target_contracts") or 0
    multiplier = item_cfg.get("multiplier") or 0
    if target_contracts <= 0 or multiplier <= 0:
        return None

    is_commodity = _is_commodity_futures(instrument, commodity_futures)
    if is_commodity:
        existing_options = sum(1 for o in existing_orders if o["type"] == "OPTIONS")
        if existing_options >= 2:
            return None

    # C5 修复: 从多来源获取期货价格
    est_price = _get_futures_price(instrument, prices, item_cfg, plan)
    notional = target_contracts * multiplier * est_price
    return {
        "type": "FUTURES",
        "action": direction,
        "instrument": instrument,
        "exchange": item_cfg.get("exchange") or "",
        "contracts": target_contracts,
        "multiplier": multiplier,
        "est_price": est_price,
        "notional": notional,
        # P2-5 M1 修复: 预估成本 = 交易成本(手续费), 而非保证金占用。
        # 旧逻辑 estimated_cost = notional*margin_rate 把「保证金率」误当「成本」,
        # 导致成本被高估数十倍(保证金率 10-15% vs 手续费率 0.013%)。
        # 现与 Beta 期货对冲(L418)口径一致: 手续费约万1.3。
        # 保证金占用单独记录为 margin_required。
        "estimated_cost": round(notional * 0.00013, 2),
        "margin_required": round(notional * (item_cfg.get("margin_rate") or 0.12), 2),
        "budget_pct": notional / target if target > 0 else 0.0,
        "reason": item_cfg.get("reason") or "",
        "framework": item_cfg.get("framework") or [],
        "priority": "secondary" if is_commodity else "primary",
    }


def _build_defense_order(deployed: float, target: float) -> list:
    """构建防御资产订单 (黄金 ETF 兜底)。

    Args:
        deployed: 已部署资金
        target: 目标资金

    Returns:
        防御订单列表 (空列表表示无需补仓)
    """
    defense_total = sum(v for _, v in _DEFENSE_ASSETS.values())
    target_defense = deployed * 0.15 if deployed > 0 else 0.0
    gap = max(0.0, target_defense - defense_total)
    if gap <= 0:
        return []
    return [
        {
            "type": "SAFE_HAVEN",
            "action": "BUY",
            "instrument": "518880",
            "name": "黄金ETF华安",
            "amount": round(gap, 0),
            "budget_pct": gap / target,
        }
    ]


def build_orders(
    plan: dict,
    positions: dict,
    prices: dict,
    hedge_positions: dict,
    positions_data: dict,
) -> dict:
    """构建对冲订单组合。

    Args:
        plan: 对冲计划字典
        positions: 持仓字典 {code: 数量}
        prices: 价格字典 {code: 价格}
        hedge_positions: 对冲持仓配置
        positions_data: 持仓详细数据 (用于 Beta 计算)

    Returns:
        含 date/action/portfolio_beta/hedge_pct/orders 的字典
    """
    if hedge_positions is None:
        hedge_positions = {}
    action = plan.get("action", "NO_HEDGE")
    orders: list = []

    # P2-5 M2 修复: 对冲目标资金由「硬编码 500 万」改为基于组合市值动态计算。
    # 旧逻辑 target=5_000_000 与真实组合市值无关, 导致对冲金额与实际敞口脱节。
    # 现 target = 组合市值 (deployed), 对冲金额 hedge_pct*target 与敞口成正比;
    # 组合市值异常(0/负)时回退到 500 万兜底, 避免除零与错误对冲。
    deployed = sum(
        float(pos) * prices.get(code, 0.0) for code, pos in positions.items()
    )
    target = deployed if deployed > 0 else 5_000_000.0

    # 1. 计算 Beta 和对冲比例
    beta = float(plan.get("portfolio_beta", 0.0) or 0.0)
    if beta < 0.1:
        beta = calc_portfolio_beta(positions_data)
    hedge_pct = float(plan.get("total_hedge_pct", 0.0) or 0.0)
    if hedge_pct == 0.0:
        hedge_pct = 0.4 if beta > 0.5 else 0.2
    hedge_value = hedge_pct * target

    # 2. Beta 触发: 期权保护 + 期货对冲
    if hedge_pct > 0.05 and beta > 0.3:
        option_notional, futures_notional = _compute_beta_hedge_allocation(
            beta, hedge_value
        )
        orders.extend(
            _build_beta_option_orders(
                beta, hedge_value, option_notional, prices, hedge_positions, target
            )
        )
        orders.extend(
            _build_beta_futures_order(
                futures_notional, prices, hedge_positions, plan, target, beta
            )
        )

    # 3. 分类对冲配置
    option_cfgs, futures_cfgs, commodity_futures = _classify_hedge_configs(
        hedge_positions
    )

    # 4. 追加期权订单
    for key, item_cfg in option_cfgs.items():
        instrument = item_cfg.get("instrument", key)
        existing = next((o for o in orders if o.get("instrument") == instrument), None)
        if existing:
            _merge_option_order(existing, item_cfg)
        else:
            orders.append(_build_option_order_from_cfg(item_cfg, key, target))

    # 5. 追加期货订单
    for key, item_cfg in futures_cfgs.items():
        order = _build_futures_order_from_cfg(
            item_cfg, key, prices, plan, target, commodity_futures, orders
        )
        if order is not None:
            orders.append(order)

    # 6. 防御资产补仓
    orders.extend(_build_defense_order(deployed, target))

    # 7. 合并订单
    orders = merge_orders(orders)

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "action": action,
        "portfolio_beta": beta,
        "hedge_pct": hedge_pct,
        "orders": orders,
    }


def _parse_target_date(argv: list) -> tuple:
    """解析命令行参数中的目标日期。

    Args:
        argv: 命令行参数列表 (sys.argv)

    Returns:
        (today_str, today_dash) 元组, 格式分别为 YYYYMMDD 和 YYYY-MM-DD
    """
    if len(argv) > 1:
        target_date = argv[1]
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            return dt.strftime("%Y%m%d"), dt.strftime("%Y-%m-%d")
        except ValueError:
            pass  # 格式错误时回退到今天
    return datetime.now().strftime("%Y%m%d"), datetime.now().strftime("%Y-%m-%d")


def _find_latest_hedge_plan(reports_dir: str) -> dict:
    """在 reports_dir 中查找最新的非空 hedge_decision_*.json 文件。

    Args:
        reports_dir: 报告目录路径

    Returns:
        解析后的 plan dict; 找不到返回默认空 plan
    """
    default_plan = {"action": "HEDGE", "portfolio_beta": 0.0, "total_hedge_pct": 0.0}
    prev_dates = []
    if os.path.exists(reports_dir):
        for f in os.listdir(reports_dir):
            if f.startswith("hedge_decision_") and f.endswith(".json"):
                fp = os.path.join(reports_dir, f)
                if os.path.getsize(fp) > 0:
                    prev_dates.append(f)
        prev_dates.sort(reverse=True)
    if not prev_dates:
        return default_plan
    try:
        with open(os.path.join(reports_dir, prev_dates[0]), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        raise  # Re-raise unknown exception


def _load_hedge_plan(reports_dir: str, today_str: str) -> dict:
    """加载对冲决策 plan。

    优先级:
        1. reports_dir/hedge_decision_{today_str}.json
        2. 最新日期的 hedge_decision_*.json
        3. 默认 plan (空 beta/hedge_pct)

    Args:
        reports_dir: 报告目录路径
        today_str: 目标日期字符串 (YYYYMMDD)

    Returns:
        plan dict
    """
    plan_path = os.path.join(reports_dir, f"hedge_decision_{today_str}.json")
    if os.path.exists(plan_path):
        try:
            with open(plan_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            raise  # Re-raise unknown exception
    return _find_latest_hedge_plan(reports_dir)


def _ensure_beta_and_hedge_pct(plan: dict, beta_from_positions: float) -> None:
    """确保 plan 中包含有效的 beta 和 hedge_pct (原地修改)。

    M12 修复: 优先使用 plan 中已有的值 (来自 LLM/风控引擎决策),
    仅当缺失或无效时才用计算值兜底, 不再无条件覆盖。

    Args:
        plan: 对冲决策 dict (原地修改)
        beta_from_positions: 从持仓计算的组合 Beta
    """
    logger.info(
        f"[Beta计算] 旧决策Beta: {plan.get('portfolio_beta', 0.0):.4f}, 当前组合Beta: {beta_from_positions:.4f}"
    )
    if not plan.get("portfolio_beta") or float(plan.get("portfolio_beta") or 0) < 0.1:
        plan["portfolio_beta"] = beta_from_positions
    if not plan.get("total_hedge_pct") or float(plan.get("total_hedge_pct") or 0) <= 0:
        plan["total_hedge_pct"] = 0.4 if beta_from_positions > 0.5 else 0.2


def _save_orders(orders: dict, out_path: str, archive_path: Path) -> None:
    """保存订单到主输出路径和归档路径。

    Args:
        orders: 订单字典
        out_path: 主输出路径
        archive_path: 归档路径 (Path 对象)
    """
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    # C9 修复: 归档目录统一使用 PROJECT_ROOT, 与 run_daily_eod_workflow.py 一致
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def _print_order_item(idx: int, o: dict, totals: dict) -> None:
    """打印单条订单信息并累加汇总金额 (原地修改 totals)。

    Args:
        idx: 订单序号 (从 1 开始)
        o: 订单字典
        totals: 汇总字典 (原地修改, 含 total_premium/total_notional)
    """
    logger.info(f"[{idx}] {o['type']} | {o['action']} | {o.get('instrument')}")
    if "contracts" in o:
        logger.info(f"    手数/张数: {o['contracts']}")
    if "notional" in o:
        totals["total_notional"] += o["notional"]
        logger.info(f"    名义价值: {o['notional']:,.0f}")
    if "amount" in o:
        logger.info(f"    金额: {o['amount']:,.0f}")
    if "estimated_cost" in o:
        logger.info(f"    预估成本: {o['estimated_cost']:,.0f}")
    if "premium_budget" in o:
        totals["total_premium"] += o["premium_budget"]
        logger.info(f"    权利金预算: {o['premium_budget']:,.0f}")
    if "budget_pct" in o:
        logger.info(f"    预算占比: {o['budget_pct'] * 100:.2f}%")
    if "reason" in o:
        logger.info(f"    理由: {o['reason']}")
    if "framework" in o:
        logger.info(f"    框架: {', '.join(o['framework'])}")
    if "priority" in o:
        logger.info(f"    优先级: {o['priority']}")


def _print_orders_summary(orders: dict) -> None:
    """打印订单汇总信息 (标题 + 逐条订单 + 合计)。

    Args:
        orders: 订单字典
    """
    logger.info("=" * 70)
    logger.info("对冲执行单")
    logger.info("=" * 70)
    logger.info(f"日期: {orders['date']}")
    logger.info(f"动作: {orders['action']}")
    logger.info(f"组合Beta: {orders.get('portfolio_beta', 0.0):.4f}")
    logger.info(f"对冲比例: {orders.get('hedge_pct', 0.0) * 100:.2f}%")
    if not orders["orders"]:
        logger.info("今日无执行单")
        return
    totals = {"total_premium": 0, "total_notional": 0}
    for i, o in enumerate(orders["orders"], 1):
        _print_order_item(i, o, totals)
    logger.info(f"合计权利金: RMB {totals['total_premium']:,.0f}")
    logger.info(f"合计名义价值: RMB {totals['total_notional']:,.0f}")


def main():
    positions, prices, hedge_positions, positions_data = load_positions()
    today_str, today_dash = _parse_target_date(sys.argv)
    reports_dir = str(REPORTS_DIR)
    plan = _load_hedge_plan(reports_dir, today_str)
    beta_from_positions = calc_portfolio_beta(positions_data)
    _ensure_beta_and_hedge_pct(plan, beta_from_positions)
    orders = build_orders(plan, positions, prices, hedge_positions, positions_data)
    out_path = os.path.join(reports_dir, f"hedge_execution_orders_{today_str}.json")
    archive_path = (
        PROJECT_ROOT / "每日报告归档" / today_dash / f"对冲执行单_{today_str}.json"
    )
    _save_orders(orders, out_path, archive_path)
    _print_orders_summary(orders)
    logger.info("=" * 70)
    logger.info(f"已保存: {out_path}")
    logger.info(f"已归档: {archive_path}")


if __name__ == "__main__":
    main()
