# -*- coding: utf-8 -*-
"""
LLM 决策灌入次日计划
====================

读取前一日 daily_pnl_report，把 LLM 决策自动写入次日 trade_plan：
- 期货对冲升级（IF 手数）
- Put 尾部保护（510050、510300）
- 建仓顺序调整（防御优先、科技设限）
- 个股止损设置（移动止损/百分比止损）
- 仓位减持调整（减持X%/释放资金）
- 仓位转换（标的A转标的B）
- 板块权重调整（防御/科技/资源）

用法:
    python apply_llm_decisions_to_plan.py [report_date] [plan_date]

默认 report_date = 昨日，plan_date = 下一个交易日
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 强制UTF-8输出
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception as e:
        raise  # Re-raise unknown exception

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # e:\各种PY程序\28-终极量化交易系统8.4

# 报告目录兼容：优先 v8.3，回退 v7.5 兼容，最后查每日报告归档
REPORTS_DIR_V83 = PROJECT_ROOT / "v8.3_institutional" / "reports"
REPORTS_DIR_V75 = PROJECT_ROOT / "v7.5_institutional" / "reports"  # 历史兼容
REPORTS_DIR_ARCHIVE = PROJECT_ROOT.parent / "每日报告归档"  # e:\各种PY程序\每日报告归档


def _find_report(report_date: str) -> Path:
    """自动检测 P&L 报告位置（v8.3 优先，回退 v7.5 兼容，最后查归档）"""
    filename = f"daily_pnl_report_{report_date}.json"
    
    # 1) v8.3_institutional/reports/
    path = REPORTS_DIR_V83 / filename
    if path.exists():
        return path
    
    # 2) v7.5_institutional/reports/ (历史兼容)
    path = REPORTS_DIR_V75 / filename
    if path.exists():
        return path
    
    # 3) 每日报告归档/YYYY-MM-DD/
    if REPORTS_DIR_ARCHIVE.exists():
        date_dir = REPORTS_DIR_ARCHIVE / report_date
        path = date_dir / filename
        if path.exists():
            return path
    
    # 4) 没找到
    return REPORTS_DIR_V83 / filename  # 返回 v8.3 路径（最可能的位置）

# 迁移兼容：优先使用 v8.3_institutional/trade_plans，历史回退 v7.5
PLAN_DIR_V83 = PROJECT_ROOT / "v8.3_institutional" / "trade_plans"
PLAN_DIR_V75 = PROJECT_ROOT / "v7.5_institutional" / "trade_plans"  # 历史兼容


def _find_plan_dir() -> Path:
    """自动检测交易计划目录（v8.3 优先，回退 v7.5 兼容）"""
    if PLAN_DIR_V83.exists():
        return PLAN_DIR_V83
    return PLAN_DIR_V75


def _prev_trading_day(d: datetime) -> datetime:
    """简单回退到最近一个周一至周五（不含节假日）。"""
    day = d - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_llm_decisions(report_date: str, plan_date: str) -> Path:
    plan_dir = _find_plan_dir()
    report_path = _find_report(report_date)
    plan_path = plan_dir / f"trade_plan_{plan_date.replace('-', '')}.json"
    if not report_path.exists():
        raise FileNotFoundError(f"报告不存在: {report_path}")
    if not plan_path.exists():
        raise FileNotFoundError(f"计划不存在: {plan_path}")

    report = _load_json(report_path)
    plan = _load_json(plan_path)

    ai_recs = report.get("ai_recommendations", [])
    rec_text = "\n".join(ai_recs)

    # === 1) 期货对冲升级 ===
    if "IF空头" in rec_text or "增加期货" in rec_text or "提升Beta对冲效率" in rec_text:
        plan.setdefault("llm_overrides", {})
        plan["llm_overrides"]["futures_if_contracts"] = 5
        plan["llm_overrides"]["applied_by"] = f"LLM daily_pnl_report_{report_date}"

    # === 2) 新增 Put 保护 ===
    has_put_rec = any(kw in rec_text for kw in [
        "510050 Put", "510300 Put", "Put保护", "买入Put", "BUY_PUT", "put_protection",
        "期权保护", "Put对冲", "认沽期权"
    ])
    if has_put_rec:
        plan.setdefault("llm_overrides", {})
        puts = plan["llm_overrides"].get("put_protection", [])
        if not any(p.get("code") == "510050" for p in puts):
            puts.append({
                "code": "510050",
                "direction": "BUY_PUT",
                "contracts": 10,
                "strike_basis": "OTM_5pct",
                "note": "LLM decision"
            })
        if not any(p.get("code") == "510300" for p in puts):
            puts.append({
                "code": "510300",
                "direction": "BUY_PUT",
                "contracts": 5,
                "strike_basis": "OTM_5pct",
                "note": "LLM decision"
            })
        plan["llm_overrides"]["put_protection"] = puts

    # === 3) 建仓顺序调整 ===
    has_build_seq = any(kw in rec_text for kw in [
        "建仓顺序", "优先建仓", "调整建仓", "仓位顺序", "build_sequence"
    ])
    if has_build_seq:
        plan.setdefault("llm_overrides", {})
        plan["llm_overrides"]["build_sequence"] = "优先防御底仓，科技成长设限"

    # === 4) 个股止损设置 (DeepSeek 新增) ===
    stop_loss_adjustments = _parse_stop_loss_adjustments(ai_recs)
    if stop_loss_adjustments:
        plan.setdefault("llm_overrides", {})
        plan["llm_overrides"]["stop_loss_adjustments"] = stop_loss_adjustments
        plan["llm_overrides"]["applied_by"] = f"LLM daily_pnl_report_{report_date}"

    # === 5) 仓位减持调整 (DeepSeek 新增) ===
    position_adjustments = _parse_position_adjustments(ai_recs)
    if position_adjustments:
        plan.setdefault("llm_overrides", {})
        plan["llm_overrides"]["position_adjustments"] = position_adjustments
        plan["llm_overrides"]["applied_by"] = f"LLM daily_pnl_report_{report_date}"

    # === 6) 仓位转换 (DeepSeek 新增) ===
    conversions = _parse_position_conversions(ai_recs)
    if conversions:
        plan.setdefault("llm_overrides", {})
        plan["llm_overrides"]["position_conversions"] = conversions
        plan["llm_overrides"]["applied_by"] = f"LLM daily_pnl_report_{report_date}"

    # === 7) 板块权重调整 (DeepSeek 新增) ===
    sector_adjustments = _parse_sector_adjustments(ai_recs)
    if sector_adjustments:
        plan.setdefault("llm_overrides", {})
        plan["llm_overrides"]["sector_adjustments"] = sector_adjustments
        plan["llm_overrides"]["applied_by"] = f"LLM daily_pnl_report_{report_date}"

    # === 元数据记录 ===
    plan.setdefault("metadata", {})
    plan["metadata"]["llm_adjustments"] = {
        "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": f"daily_pnl_report_{report_date}",
        "adjustments": ai_recs
    }

    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan_path


# ═══════════════════════════════════════════════════════════════
# DeepSeek 建议解析器 (新增)
# ═══════════════════════════════════════════════════════════════

# 个股代码正则: 6位数字 + .SH/.SZ (可选)
_STOCK_CODE_PATTERN = re.compile(r'(\d{6})\.(?:SH|SZ|ss|sz)|(\d{6})')

# 标的名称映射 (代码 → 名称, 用于解析 DeepSeek 建议中的中文名称)
_NAME_TO_CODE = {
    '卓胜微': '300782', '同花顺': '300033', '海光信息': '688041',
    '中际旭创': '300308', '北方华创': '002371', '中科曙光': '603019',
    '中芯国际': '688981', '绿的谐波': '688017', '阳光电源': '300274',
    '藏格矿业': '000408', '恒瑞医药': '600276', '长江电力': '600900',
    '中国神华': '601088', '紫金矿业': '601899', '光迅科技': '002281',
    '国盾量子': '000901',
    # ETF
    '黄金ETF': '518880', '黄金ETF华安': '518880',
    '医疗ETF': '512170', '医疗ETF华宝': '512170',
    '新能源车ETF': '515030', '新能源车ETF华夏': '515030',
    '中证500ETF': '510500', '中证500ETF南方': '510500',
    '中证1000ETF': '512100',
    '创业板ETF': '159915', '创业板ETF易方达': '159915',
    '科创50ETF': '588000', '科创50ETF华夏': '588000',
    '科创50ETF易方达': '588080',
    '上证50ETF': '510050', '上证50ETF华夏': '510050',
    '沪深300ETF': '510300', '沪深300ETF华泰柏瑞': '510300',
    '半导体ETF': '512760', '半导体ETF国泰': '512760',
    '银行ETF': '512800', '银行ETF华宝': '512800',
    '证券ETF': '512880', '证券ETF国泰': '512880',
}


def _resolve_code(name_or_code: str) -> str:
    """将中文名称或代码解析为标准 6 位代码"""
    name_or_code = name_or_code.strip()
    # 直接是 6 位代码
    if re.match(r'^\d{6}$', name_or_code):
        return name_or_code
    # 名称映射
    for name, code in _NAME_TO_CODE.items():
        if name in name_or_code:
            return code
    return ''


def _parse_stop_loss_adjustments(ai_recs: list) -> list:
    """解析止损设置建议

    识别模式:
      - "对卓胜微和同花顺设置5%移动止损"
      - "对 300782 设置 5% 止损"
      - "建议对XX设置止损线 -8%"
    """
    adjustments = []
    seen_codes = set()

    for rec in ai_recs:
        rec_text = rec if isinstance(rec, str) else str(rec)
        # 必须包含止损关键词
        if '止损' not in rec_text and 'stop_loss' not in rec_text.lower():
            continue

        # 提取止损百分比 (支持 5%, -5%, 5%移动止损, 止损线-8% 等)
        pct_match = re.search(r'(-?\d+(?:\.\d+)?)\s*%\s*(?:移动)?止损', rec_text)
        if not pct_match:
            pct_match = re.search(r'止损(?:线)?\s*(-?\d+(?:\.\d+)?)\s*%', rec_text)
        if not pct_match:
            pct_match = re.search(r'止损(?:线)?\s*(-?\d+(?:\.\d+)?)', rec_text)
        stop_pct = -5.0  # 默认 -5%
        if pct_match:
            val = float(pct_match.group(1))
            stop_pct = -abs(val) if val > 0 else val  # 强制为负值

        # 提取标的名称/代码 (在"对"之后, "设置"之前)
        # 模式1: "对卓胜微和同花顺设置"
        target_match = re.search(r'对\s*([^,，。；;]+?)\s*设置', rec_text)
        if not target_match:
            # 模式2: "对 300782 设置"
            target_match = re.search(r'对\s*(\S+?)\s*设置', rec_text)
        if not target_match:
            continue

        target_text = target_match.group(1)
        # 按 "和" / "/" / "、" 拆分多个标的
        targets = re.split(r'[和/、]', target_text)

        for target in targets:
            target = target.strip()
            if not target or len(target) > 20:
                continue
            code = _resolve_code(target)
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            adjustments.append({
                "code": code,
                "name": target if not target.isdigit() else '',
                "stop_loss_pct": stop_pct / 100,  # 转为小数 (-0.05)
                "stop_loss_type": "trailing" if "移动" in rec_text else "fixed",
                "note": f"LLM decision: {rec_text[:60]}",
            })

    return adjustments


def _parse_position_adjustments(ai_recs: list) -> list:
    """解析仓位减持/加仓调整建议

    识别模式:
      - "减持10%仓位至51.9万份"
      - "减持医疗ETF 10%仓位"
      - "将释放资金用于增加新能源车ETF持仓"
    """
    adjustments = []
    seen_codes = set()

    for rec in ai_recs:
        rec_text = rec if isinstance(rec, str) else str(rec)
        # 必须包含减持/加仓/调整仓位关键词
        if not any(kw in rec_text for kw in ['减持', '加仓', '调整仓位', '仓位调整', '减仓']):
            continue

        # 提取调整百分比
        pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*(?:仓位|持仓|股份)', rec_text)
        adjust_pct = 10.0  # 默认 10%
        if pct_match:
            adjust_pct = float(pct_match.group(1))

        # 判断方向: 减持/减仓=reduce, 加仓=increase
        is_reduce = any(kw in rec_text for kw in ['减持', '减仓', '降低'])
        action = "reduce" if is_reduce else "increase"

        # 提取标的
        for name, code in _NAME_TO_CODE.items():
            if name in rec_text and code not in seen_codes:
                seen_codes.add(code)
                adjustments.append({
                    "code": code,
                    "name": name,
                    "action": action,
                    "adjust_pct": adjust_pct / 100,  # 0.10
                    "note": f"LLM decision: {rec_text[:60]}",
                })
                break  # 一条建议只识别一个标的

    return adjustments


def _parse_position_conversions(ai_recs: list) -> list:
    """解析仓位转换建议 (标的A转标的B)

    识别模式:
      - "将中证500ETF的700份转换为中证1000ETF"
      - "将A转换为B"
    """
    conversions = []

    for rec in ai_recs:
        rec_text = rec if isinstance(rec, str) else str(rec)
        # 必须包含转换关键词
        if not any(kw in rec_text for kw in ['转换', '转为', '转换为', '调仓至', '切换至']):
            continue

        # 提取 "将A...转换为B" 模式
        conv_match = re.search(r'将\s*([^,，。；;]+?)\s*(?:的\d+份?)?\s*转(?:换)?为?\s*([^,，。；;]+)', rec_text)
        if not conv_match:
            continue

        from_text = conv_match.group(1).strip()
        to_text = conv_match.group(2).strip()

        from_code = _resolve_code(from_text)
        to_code = _resolve_code(to_text)

        # 提取转换数量
        qty_match = re.search(r'(\d+)\s*份', rec_text)
        quantity = int(qty_match.group(1)) if qty_match else 0

        if from_code and to_code:
            conversions.append({
                "from_code": from_code,
                "from_name": from_text,
                "to_code": to_code,
                "to_name": to_text,
                "quantity": quantity,
                "note": f"LLM decision: {rec_text[:60]}",
            })

    return conversions


def _parse_sector_adjustments(ai_recs: list) -> list:
    """解析板块权重调整建议

    识别模式:
      - "建议优先建仓低Beta防御性品种如黄金ETF"
      - "降低科技板块权重, 增加防御板块"
      - "平衡组合Beta至1.0以下"
    """
    sector_adjustments = []
    seen_sectors = set()

    # 板块关键词映射
    sector_keywords = {
        '防御': ['防御', '低Beta', '低波动', '高股息', '黄金', '债券'],
        '科技': ['科技', '半导体', 'AI', '算力', '芯片', '成长'],
        '资源': ['资源', '黄金', '有色', '矿业', '煤炭'],
        '医药': ['医药', '医疗', '健康'],
        '金融': ['金融', '银行', '证券', '保险'],
        '新能源': ['新能源', '光伏', '储能', '电动车'],
    }

    for rec in ai_recs:
        rec_text = rec if isinstance(rec, str) else str(rec)
        rec_lower = rec_text

        for sector, keywords in sector_keywords.items():
            if sector in seen_sectors:
                continue
            # 必须同时包含 "优先"/"增加"/"降低"/"减少" 等动作词
            has_action = any(kw in rec_text for kw in ['优先', '增加', '降低', '减少', '平衡', '提升', '调整'])
            has_sector = any(kw in rec_text for kw in keywords)
            if not (has_action and has_sector):
                continue

            # 判断方向
            is_increase = any(kw in rec_text for kw in ['优先', '增加', '提升', '加强'])
            action = "increase_weight" if is_increase else "reduce_weight"

            sector_adjustments.append({
                "sector": sector,
                "action": action,
                "note": f"LLM decision: {rec_text[:60]}",
            })
            seen_sectors.add(sector)
            break  # 一条建议只归类到一个板块

    return sector_adjustments


def main() -> int:
    if len(sys.argv) > 1:
        report_date = sys.argv[1]
    else:
        today = datetime.now()
        report_date = _prev_trading_day(today).strftime("%Y-%m-%d")

    if len(sys.argv) > 2:
        plan_date = sys.argv[2]
    else:
        today = datetime.now()
        plan_date = today.strftime("%Y-%m-%d")

    try:
        out = apply_llm_decisions(report_date, plan_date)
        print(f"[OK] LLM决策已写入: {out}")
        return 0
    except Exception as e:
        print(f"[FAIL] 失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
