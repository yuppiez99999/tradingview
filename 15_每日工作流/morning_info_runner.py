"""
晨间信息采集工作流 — 统一调度信息采集类报告
================================================
被 run_daily_morning.py --phase info 调用。

7 项任务:
  1. 晨间行情摘要 (morning_market_fetcher, 含 DeepSeek AI)
  2. 康波周期分析 (28 项目内 v8.3_institutional/src/macro/kondratiev.py)
  3. 实时ETF资金流向 (11_量化策略/engine/etf_flow.py)
  4. 舆情综合日报 + 动力煤舆情日报 (28 项目内 sentiment_hub.run_all)
  5. CNEMC 空气质量日报 (15_每日工作流/cnemc_air_quality_runner)
  6. iFinD 自动标的研判 (复制源文件或占位)
  7. 棉花加仓方案归档 (复制源文件)

用法:
  python morning_info_runner.py                # 当日
  python morning_info_runner.py --force        # 强制重新生成
  python morning_info_runner.py --date 2026-07-27
"""

import argparse
import glob as _glob
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# UTF-8 编码修复
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ═══════════════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent                    # 28/15_每日工作流
PROJECT_ROOT = SCRIPT_DIR.parent                                # 28-终极量化交易系统8.4
BASE_ROOT = PROJECT_ROOT.parent                                 # e:\各种PY程序
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"                         # 项目内归档根

# 跨目录复用的模块路径
WORKFLOW_15 = BASE_ROOT / "15_每日工作流"                       # 晨间行情/CNEMC/DeepSeek 摘要
STRATEGY_11 = BASE_ROOT / "11_量化策略"                          # ETF 资金流向

# 28 项目内模块
V83_SRC = PROJECT_ROOT / "v8.3_institutional" / "src"

# 将复用模块加入 sys.path
for _p in (str(PROJECT_ROOT), str(WORKFLOW_15), str(STRATEGY_11), str(V83_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _archive_today(target_date: str) -> Path:
    """返回归档目录, 不存在则创建"""
    d = ARCHIVE_DIR / target_date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exists_nonempty(path: Path, min_size: int = 500) -> bool:
    """检查文件存在且非空"""
    return path.is_file() and path.stat().st_size > min_size


# ═══════════════════════════════════════════════════════════════
# 任务 1: 晨间行情摘要 (含 DeepSeek AI)
# ═══════════════════════════════════════════════════════════════
def task_morning_market(archive: Path, target_date: str, force: bool) -> bool:
    """调用 morning_market_fetcher.main() 生成晨间行情摘要"""
    date_short = target_date.replace('-', '')
    out_file = archive / f"晨间行情摘要_{date_short}.md"
    if _exists_nonempty(out_file) and not force:
        return True
    try:
        from morning_market_fetcher import main as market_main
        result = market_main(output_dir=str(archive))
        ok = bool(result and result.get("path"))
        if ok:
            pass
        else:
            pass
        return ok
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 2: 康波周期分析 (28 项目内模块)
# ═══════════════════════════════════════════════════════════════
def task_kondratiev(archive: Path, target_date: str, force: bool) -> bool:
    """调用 28 项目内 KondratievCycleAnalyzer.generate_report()"""
    date_short = target_date.replace('-', '')
    out_file = archive / f"康波周期分析_{date_short}.md"
    if _exists_nonempty(out_file) and not force:
        return True
    try:
        from utils.kondratiev_cycle import KondratievCycleAnalyzer
        analyzer = KondratievCycleAnalyzer()
        report = analyzer.generate_report(save_dir=None)
        if not report or len(report) < 100:
            return False
        out_file.write_text(report, encoding='utf-8')
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 3: 实时 ETF 资金流向 (11_量化策略/engine/etf_flow.py)
# ═══════════════════════════════════════════════════════════════
def task_etf_flow(archive: Path, target_date: str, force: bool) -> bool:
    """调用 utils.etf_flow_monitor.ETFRealTimeTracker 生成 ETF 资金流向报告"""
    date_short = target_date.replace('-', '')
    pattern = str(archive / f"实时ETF资金流向_{date_short}_*.md")
    existing = _glob.glob(pattern)
    if existing and not force:
        return True
    try:
        from utils.etf_flow_monitor import ETFRealTimeTracker
        tracker = ETFRealTimeTracker()
        flow_data = tracker.get_all_etf_fund_flows()
        signals = tracker.detect_signals(flow_data)
        ts = datetime.now().strftime("%H%M%S")
        out_file = archive / f"实时ETF资金流向_{date_short}_{ts}.md"
        lines = [f"# 实时ETF资金流向报告 {target_date}\n",
                 f"\n生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n",
                 "## 一、ETF 资金流向明细\n\n",
                 "| 代码 | 名称 | 类别 | 净流入(亿) | 涨跌% | 趋势 | 数据源 |\n",
                 "|------|------|------|-----------|-------|------|--------|\n"]
        for code, d in flow_data.items():
            lines.append(f"| {code} | {d.get('name','')} | {d.get('category','')} | "
                         f"{d.get('net_flow_yi',0):+.2f} | {d.get('change_pct',0):+.2f} | "
                         f"{d.get('trend','')} | {d.get('source','')} |\n")
        lines.append(f"\n## 二、信号检测 (共 {len(signals)} 条)\n\n")
        if signals:
            lines.append("| 代码 | 信号 | 强度 | 说明 |\n|------|------|------|------|\n")
            for s in signals:
                desc = (f"净流入{s.get('net_flow_yi',0):+.2f}亿, "
                        f"涨跌{s.get('change_pct',0):+.2f}%, "
                        f"{s.get('trend','')}")
                lines.append(f"| {s.get('code','')} | {s.get('signal_type','')} | "
                             f"{s.get('confidence','')} | {desc} |\n")
        else:
            lines.append("无显著信号\n")
        out_file.write_text("".join(lines), encoding='utf-8')
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 4: 舆情综合日报 + 动力煤舆情日报 (28 项目内 sentiment_hub)
# ═══════════════════════════════════════════════════════════════
def task_sentiment(archive: Path, target_date: str, force: bool) -> bool:
    """调用 28 项目内 sentiment_hub.run_all()"""
    date_short = target_date.replace('-', '')
    sentiment_md = archive / f"舆情综合日报_{date_short}.md"
    coal_md = archive / f"动力煤舆情日报_{date_short}.md"
    if _exists_nonempty(sentiment_md) and _exists_nonempty(coal_md) and not force:
        return True
    try:
        from nlp.sentiment_hub import run_all
        result = run_all(
            target_date=target_date,
            output_dir=str(archive),
            run_trend=True,
            run_coal=True,
            force=force,
        )
        return bool(result.get("ok"))
    except ImportError:
        placeholder = f"# 舆情综合日报 {target_date}\n\n> ⚠️ 舆情模块 `nlp.sentiment_hub` 导入失败，本报告为占位。\n> 该模块已实现 (规则引擎 + Wind MCP 新闻扫描, 受 SENTIMENT_HUB_USE_WIND_NEWS 环境变量控制)。\n> 排查方向: 确认 nlp/ 目录在 sys.path 且 sentiment_hub.py 无语法错误。\n\n生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        sentiment_md.write_text(placeholder, encoding='utf-8')
        coal_md.write_text(f"# 动力煤舆情日报 {target_date}\n\n> ⚠️ 占位（同舆情综合日报，sentiment_hub 导入失败）\n", encoding='utf-8')
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 5: CNEMC 空气质量日报 (15_每日工作流/cnemc_air_quality_runner)
# ═══════════════════════════════════════════════════════════════
def task_cnemc(archive: Path, target_date: str, force: bool) -> bool:
    """调用 cnemc_air_quality_runner.generate_cnemc_report()"""
    date_short = target_date.replace('-', '')
    out_file = archive / f"空气质量CNEMC日报_{date_short}.md"
    if _exists_nonempty(out_file) and not force:
        return True
    try:
        from cnemc_air_quality_runner import generate_cnemc_report
        result = generate_cnemc_report(output_dir=str(archive), target_date=target_date)
        return bool(result.get("ok"))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 6: iFinD 自动标的研判 (自动运行 ifind_auto_analysis.py)
# ═══════════════════════════════════════════════════════════════
def _wind_code_with_suffix(code: str) -> str:
    """将 6 位代码转为 Wind 格式 (000xxx.SZ / 6xxxxx.SH / 5xxxxx.SH)"""
    if not code or len(code) != 6 or not code.isdigit():
        return code
    if code.startswith(("60", "68", "51", "58", "50")):
        return code + ".SH"
    return code + ".SZ"


def _load_portfolio_for_research() -> list:
    """读取 portfolio.yaml 提取可研报标的 (剔除 CASH)"""
    portfolio_path = PROJECT_ROOT / "configs" / "portfolio.yaml"
    if not portfolio_path.is_file():
        return []
    try:
        import yaml as _yaml
        for enc in ("utf-8", "gbk", "utf-8-sig"):
            try:
                data = _yaml.safe_load(portfolio_path.read_text(encoding=enc))
                break
            except UnicodeDecodeError:
                continue
        else:
            return []
        assets = data.get("assets", []) or []
        result = []
        for a in assets:
            code = str(a.get("code", "")).strip()
            if not code or code.upper() == "CASH":
                continue
            result.append({
                "code": code,
                "name": a.get("name", code),
                "category": a.get("category", ""),
                "weight": a.get("weight", 0),
            })
        return result
    except Exception:
        return []


def task_ifind_analysis(archive: Path, target_date: str, force: bool) -> bool:
    """标的研判报告 — 用 Wind MCP 新闻搜索 + 实时行情 + LLM 生成 (2026-08-18 替代 iFinD MCP)

    流程:
      1. 读取 configs/portfolio.yaml 提取持仓标的
      2. 对每个标的调用 wind_get_quote() + wind_search_news() 抓取行情与新闻
      3. 汇总后用 LLM (DeepSeek → GLM → Ollama) 生成研判报告
    """
    date_short = target_date.replace('-', '')
    dst = archive / f"iFinD自动标的研判报告_{date_short}.md"

    if _exists_nonempty(dst) and not force:
        return True

    # 1. 加载持仓标的
    assets = _load_portfolio_for_research()
    if not assets:
        dst.write_text(
            f"# 标的研判报告\n\n**日期**: {target_date}\n**状态**: 未找到持仓配置\n",
            encoding="utf-8",
        )
        return True

    # 2. Wind MCP 抓取行情 + 新闻
    try:
        import sys as _sys
        _tools_path = str(PROJECT_ROOT)
        if _tools_path not in _sys.path:
            _sys.path.insert(0, _tools_path)
        from tools.wind_mcp_fetcher import wind_get_quote, wind_search_news
    except Exception as e:
        dst.write_text(
            f"# 标的研判报告\n\n**日期**: {target_date}\n**状态**: Wind MCP 不可用 ({e})\n",
            encoding="utf-8",
        )
        return True

    import time as _time
    holdings_data = []
    news_count = 0
    quote_count = 0
    for i, a in enumerate(assets):
        code = a["code"]
        name = a["name"]
        wind_code = _wind_code_with_suffix(code)
        is_fund = code.startswith(("51", "58", "50", "52"))
        entry = {"code": code, "name": name, "category": a["category"],
                 "weight": a["weight"], "quote": None, "news": []}
        # 行情
        try:
            q = wind_get_quote(wind_code, is_fund=is_fund)
            if q and q.get("price"):
                entry["quote"] = q
                quote_count += 1
        except Exception:
            pass
        if i > 0 and i % 5 == 0:
            _time.sleep(0.3)
        # 新闻 (用名称搜索, Wind 要求无空格)
        try:
            news = wind_search_news(name, top_k=5)
            if news:
                entry["news"] = news[:5]
                news_count += len(entry["news"])
        except Exception:
            pass
        if i > 0 and i % 5 == 0:
            _time.sleep(0.3)
        holdings_data.append(entry)


    # 3. 构造 LLM prompt
    holdings_block_lines = []
    for e in holdings_data:
        q = e.get("quote") or {}
        price = q.get("price")
        chg = q.get("change")
        chg_pct = q.get("change_pct")
        line = f"- **{e['name']}** ({e['code']}/{e['category']}, 权重={e['weight']}): "
        if price is not None:
            line += f"现价={price}, 涨跌={chg}, 涨跌幅={chg_pct}%"
        else:
            line += "行情缺失"
        holdings_block_lines.append(line)
    holdings_block = "\n".join(holdings_block_lines)

    news_block_lines = []
    for e in holdings_data:
        if not e.get("news"):
            continue
        news_block_lines.append(f"#### {e['name']} ({e['code']})")
        for n in e["news"][:3]:
            title = n.get("title") or n.get("text", "")[:80]
            pub = n.get("publish_time") or n.get("date") or ""
            news_block_lines.append(f"- [{pub}] {title}")
    news_block = "\n".join(news_block_lines) or "(无新闻数据)"

    system_prompt = (
        "你是一位资深证券分析师, 服务于量化对冲基金。"
        "请基于持仓标的的实时行情与近期新闻, 生成简洁的标的研判报告。"
        "每个标的给出: 短期趋势(向上/震荡/向下)、关键催化、风险点、操作建议(持有/加仓/减仓/观望)。"
        "禁止编造数据; 数据不足时明确标注。报告总长度 1500-2500 字。"
    )
    user_prompt = f"""请基于以下持仓数据生成《标的研判报告》

# 报告日期: {target_date}
# 数据源: Wind MCP (行情 + 财经新闻)

## 一、持仓行情快照
{holdings_block}

## 二、近期新闻汇总
{news_block}

## 输出要求 (Markdown)

```markdown
# 标的研判报告

**日期**: {target_date}
**数据源**: Wind MCP (行情 + 财经新闻)
**分析师**: AI 研报引擎 (LLM 驱动)

## 一、组合概览
(整体持仓结构点评, 3-5 句)

## 二、标的逐项研判

### {holdings_data[0]['name'] if holdings_data else ''} ({holdings_data[0]['code'] if holdings_data else ''})
- 现价/涨跌:
- 短期趋势: [向上/震荡/向下]
- 关键催化: (从新闻提取)
- 风险点:
- 操作建议: [持有/加仓/减仓/观望]

(其余标的依次列出)

## 三、组合建议
- 调仓方向:
- 风险提示:

---
*本研报由 LLM 基于 Wind MCP 数据生成, 仅供参考*
```
"""

    # 4. 调用 LLM
    report_content = None
    try:
        from llm_client import chat
        report_content = chat(
            user_prompt, system=system_prompt,
            temperature=0.4, max_tokens=3500,
        )
    except Exception as e:
        pass

    if not report_content or len(report_content) < 400:
        report_content = _fallback_ifind_report(
            target_date, holdings_data, quote_count, news_count
        )
    else:
        import re as _re
        report_content = _re.sub(r"^\[[^\]]+\]\s*", "", report_content.strip())

    dst.write_text(report_content, encoding="utf-8")
    dst.stat().st_size / 1024
    return True


def _fallback_ifind_report(target_date: str, holdings_data: list,
                           quote_count: int, news_count: int) -> str:
    """LLM 不可用时的标的研判降级模板"""
    lines = ["# 标的研判报告",
             "",
             f"**日期**: {target_date}",
             "**状态**: LLM 不可用, 规则引擎降级模板",
             "**数据源**: Wind MCP (行情 + 财经新闻)",
             "",
             "## 一、持仓行情快照",
             "",
             "| 标的 | 代码 | 类别 | 权重 | 现价 | 涨跌幅 |",
             "|------|------|------|------|------|--------|"]
    for e in holdings_data:
        q = e.get("quote") or {}
        price = q.get("price", "N/A")
        chg_pct = q.get("change_pct", "N/A")
        lines.append(f"| {e['name']} | {e['code']} | {e['category']} | {e['weight']} | {price} | {chg_pct}% |")
    lines.extend([
        "",
        f"## 二、新闻摘要 (共 {news_count} 条)",
        "",
    ])
    for e in holdings_data:
        if not e.get("news"):
            continue
        lines.append(f"### {e['name']} ({e['code']})")
        for n in e["news"][:3]:
            title = n.get("title") or n.get("text", "")[:80]
            lines.append(f"- {title}")
        lines.append("")
    lines.extend([
        "## 三、风险提示",
        "",
        "1. 本报告为规则引擎降级模板 (LLM 不可用)",
        "2. 行情数据来自 Wind MCP, 新闻为近期财经报道",
        "3. 不构成投资建议",
        "",
        "---",
        f"*生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}*",
    ])
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 任务 7: 大宗商品交易机会扫描 (基本面研报, LLM 驱动)
# ═══════════════════════════════════════════════════════════════
_COMMODITY_SECTORS = [
    ("工业金属", ["铜", "铝", "锌"], "AI算力/电气化/新能源, 库存周期, 美元反向"),
    ("贵金属", ["黄金", "白银"], "央行购金/避险/实际利率, 光伏工业需求"),
    ("黑色系", ["螺纹钢", "铁矿石", "焦煤"], "地产基建/钢厂利润/铁水产量, 季节性"),
    ("能源化工", ["原油", "动力煤", "PTA", "甲醇"], "OPEC+/地缘/炼厂开工, 美元/季节性"),
    ("农产品", ["豆粕", "豆油", "棉花", "玉米"], "美豆面积/天气/生猪存栏, 进口配额"),
    ("化工建材", ["玻璃", "纯碱", "PVC"], "地产竣工/光伏玻璃/纯碱需求, 产能利用率"),
    ("碳市场", ["CEA碳排放权"], "全国碳市场扩容/配额收紧/CCER重启"),
]


def _extract_market_signals(archive: Path, date_short: str) -> dict:
    """从 morning_market_data_{date}.json 提取商品行情快照

    针对 morning_market_fetcher 输出结构精确提取:
      global/usd_index, wti_crude, brent_crude, lme_copper, natgas_us, us_bond_10y
      coal/spot_price, pit_price
      copper/inventory, basis
      carbon/cea_price
    """
    import json as _json

    signals = {}
    mk_json = archive / f"morning_market_data_{date_short}.json"
    if not mk_json.is_file():
        return signals

    try:
        data = _json.loads(mk_json.read_text(encoding="utf-8"))
    except Exception:
        return signals

    def _first_price(payload) -> float | None:
        """从 payload.data 中提取第一个有效价格 (rows[0][2])"""
        if not isinstance(payload, dict):
            return None
        blocks = payload.get("data")
        if not blocks:
            return None
        if isinstance(blocks, dict):
            blocks = [blocks]
        if not isinstance(blocks, list):
            return None
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for row in block.get("rows", []):
                if isinstance(row, list) and len(row) > 2:
                    val = row[2]
                    if isinstance(val, (int, float)) and val > 0:
                        return float(val)
        return None

    mappings = [
        ("global", "usd_index", "美元指数", "宏观"),
        ("global", "us_bond_10y", "美债10Y收益率", "宏观"),
        ("global", "us_bond_2y", "美债2Y收益率", "宏观"),
        ("global", "wti_crude", "WTI原油", "能源化工"),
        ("global", "brent_crude", "ICE布油", "能源化工"),
        ("global", "lme_copper", "LME铜", "工业金属"),
        ("global", "natgas_us", "美国天然气", "能源化工"),
        ("coal", "spot_price", "动力煤现货", "能源化工"),
        ("coal", "pit_price", "动力煤坑口", "能源化工"),
        ("copper", "inventory", "沪铜库存", "工业金属"),
        ("copper", "basis", "沪铜基差", "工业金属"),
        ("carbon", "cea_price", "碳排放权CEA", "碳市场"),
        ("carbon", "cea_volume", "CEA成交量", "碳市场"),
    ]

    for cat, key, name, sector in mappings:
        payload = data.get(cat, {}).get(key)
        if payload is None:
            continue
        price = _first_price(payload)
        if price is not None:
            signals[name] = {"price": price, "category": sector, "key": f"{cat}/{key}"}

    return signals


def _load_sentiment_brief(archive: Path, date_short: str) -> str:
    """读取舆情综合+动力煤日报摘要 (前 30 行)"""
    briefs = []
    for fname in (f"动力煤舆情日报_{date_short}.md", f"舆情综合日报_{date_short}.md"):
        fp = archive / fname
        if fp.is_file():
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()[:30]
                briefs.append(f"### {fname}\n" + "\n".join(lines))
            except Exception:
                pass
    return "\n\n".join(briefs)


def _build_commodity_prompt(target_date: str, market_signals: dict,
                            kondratiev_signals: list, sentiment_brief: str) -> tuple[str, str]:
    """构造 LLM prompt (system, user)"""
    mk_lines = []
    for name, info in market_signals.items():
        mk_lines.append(f"- {name}: {info['price']} (类别: {info['category']})")
    market_block = "\n".join(mk_lines[:40]) if mk_lines else "(无实时行情数据, 请基于一般基本面知识生成)"

    kond_lines = []
    for s in kondratiev_signals:
        kond_lines.append(
            f"- {s.get('name','')}: 驱动={s.get('driver','')}, "
            f"周期敏感度={s.get('phase_sensitivity','')}, "
            f"康波建议={s.get('kondratiev_recommendation','')}, "
            f"当前信号={s.get('current_signal','')}"
        )
    kond_block = "\n".join(kond_lines) if kond_lines else "(康波信号不可用)"

    sector_lines = []
    for sector, varieties, driver in _COMMODITY_SECTORS:
        sector_lines.append(f"- **{sector}** ({', '.join(varieties)}): {driver}")
    sector_block = "\n".join(sector_lines)

    system = (
        "你是一位资深大宗商品基本面分析师, 服务于顶级对冲基金。"
        "你擅长从供需格局、库存周期、季节性、政策影响、产业链上下游、"
        "全球宏观(美元/美债/地缘)等多维度研判商品交易机会。"
        "报告需严谨、有数据支撑、区分短期(1-2周)/中期(1-3月)机会, "
        "并明确给出方向(看多/看空/震荡)、关键驱动因素、风险提示。"
        "禁止编造不存在的数据; 数据不足时明确标注'数据缺失, 基于行业常识推断'。"
    )

    user = f"""请基于以下输入生成《大宗商品交易机会扫描 - 基本面研报》

# 报告日期: {target_date}

## 输入数据

### 1. 实时行情快照 (来自 Wind MCP)
{market_block}

### 2. 康波周期商品信号 (第六轮康波复苏→繁荣期)
{kond_block}

### 3. 舆情摘要
{sentiment_brief if sentiment_brief else '(舆情数据不可用)'}

## 需要覆盖的板块与品种
{sector_block}

## 输出要求

请按以下结构生成 Markdown 报告:

```markdown
# 大宗商品交易机会扫描 - 基本面研报

**日期**: {target_date}
**分析师**: AI 基本面研报引擎 (LLM 驱动)
**数据源**: Wind MCP > 舆情Hub > 康波周期模型

## 一、宏观背景
(美元指数/美债/全球流动性/地缘风险 对商品的综合影响, 3-5 句)

## 二、板块逐项扫描

### 2.1 工业金属 (铜/铝/锌)
**铜**:
- 基本面驱动: (AI算力/电气化/库存/美元)
- 供需格局: (全球库存水平/冶炼费/废铜替代)
- 交易机会: 方向=[看多/看空/震荡], 时间窗口=[短期/中期], 关键价位=(基于行情推断)
- 风险提示: (什么情况下判断失效)

(铝/锌 类似)

### 2.2 贵金属 (黄金/白银)
...

### 2.3 黑色系 (螺纹钢/铁矿石/焦煤)
...

### 2.4 能源化工 (原油/动力煤/PTA/甲醇)
...

### 2.5 农产品 (豆粕/豆油/棉花/玉米)
...

### 2.6 化工建材 (玻璃/纯碱/PVC)
...

### 2.7 碳市场 (CEA碳排放权)
...

## 三、交易机会汇总表

| 板块 | 品种 | 方向 | 时间窗口 | 信心度 | 关键驱动 | 主要风险 |
|------|------|------|----------|--------|----------|----------|
| ... | ... | 看多 | 中期 | 高/中/低 | ... | ... |

## 四、组合配置建议
- 超配板块: (理由)
- 标配板块: (理由)
- 低配板块: (理由)
- 对冲建议: (跨品种对冲/期现对冲)

## 五、风险提示
1. (宏观风险)
2. (政策风险)
3. (黑天鹅风险)

---
*本研报由 LLM 基于实时行情+康波周期+舆情生成, 仅供参考, 不构成投资建议*
```

请严格按上述结构生成, 每个品种至少 3 行分析, 总长度 2000-3500 字。
"""
    return system, user


def _fallback_commodity_report(target_date: str, market_signals: dict,
                               kondratiev_signals: list) -> str:
    """LLM 不可用时的规则引擎降级模板"""
    mk_lines = [f"| {name} | {info['price']} | {info['category']} |"
                for name, info in market_signals.items()]
    market_table = "\n".join(mk_lines[:30]) if mk_lines else "| (无数据) | - | - |"

    kond_lines = [f"| {s.get('name','')} | {s.get('driver','')} | "
                  f"{s.get('kondratiev_recommendation','')} | {s.get('current_signal','')} |"
                  for s in kondratiev_signals]
    kond_table = "\n".join(kond_lines) if kond_lines else "| (康波信号不可用) | - | - | - |"

    return f"""# 大宗商品交易机会扫描 - 基本面研报

**日期**: {target_date}
**状态**: LLM 不可用, 规则引擎降级模板
**数据源**: Wind MCP > 康波周期模型

## 一、实时行情快照

| 名称 | 价格 | 类别 |
|------|------|------|
{market_table}

## 二、康波周期商品信号

| 商品 | 驱动 | 康波建议 | 当前信号 |
|------|------|----------|----------|
{kond_table}

## 三、板块扫描框架

| 板块 | 品种 | 核心驱动 |
|------|------|----------|
| 工业金属 | 铜/铝/锌 | AI算力/电气化/库存周期/美元反向 |
| 贵金属 | 黄金/白银 | 央行购金/避险/实际利率/光伏 |
| 黑色系 | 螺纹钢/铁矿石/焦煤 | 地产基建/钢厂利润/铁水产量 |
| 能源化工 | 原油/动力煤/PTA/甲醇 | OPEC+/地缘/炼厂开工/季节性 |
| 农产品 | 豆粕/豆油/棉花/玉米 | 美豆面积/天气/生猪存栏/配额 |
| 化工建材 | 玻璃/纯碱/PVC | 地产竣工/光伏玻璃/产能利用率 |
| 碳市场 | CEA碳排放权 | 全国碳市场扩容/配额收紧/CCER重启 |

## 四、风险提示

1. **宏观风险**: 美元指数 {market_signals.get('美元指数', {}).get('price', 'N/A')} 反弹风险
2. **政策风险**: 十五五规划落地节奏、环保限产、出口管制
3. **数据风险**: 库存/基差/供需平衡表数据未接入, 基本面判断依赖 LLM 通用知识

---
*本研报由规则引擎降级生成 (LLM 不可用), 仅供参考, 不构成投资建议*
*生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}*
"""


def task_commodity_fundamental_scan(archive: Path, target_date: str, force: bool) -> bool:
    """大宗商品交易机会扫描 - 从基本面出发的研报 (LLM 驱动)

    替代原棉花加仓方案任务 (2026-08-18 起):
      - 数据输入: morning_market_data_{date}.json + 康波周期信号 + 舆情日报
      - LLM 驱动: DeepSeek → 豆包 → GLM → Ollama 降级链
      - 覆盖: 工业金属/贵金属/黑色系/能源化工/农产品/化工建材/碳市场
      - 输出: 大宗商品交易机会扫描_{date}.md
    """
    date_short = target_date.replace('-', '')
    out_file = archive / f"大宗商品交易机会扫描_{date_short}.md"
    if _exists_nonempty(out_file) and not force:
        return True

    market_signals = _extract_market_signals(archive, date_short)

    kondratiev_signals = []
    try:
        from utils.kondratiev_cycle import KondratievCycleAnalyzer
        kondratiev_signals = KondratievCycleAnalyzer().get_commodity_signals()
    except Exception:
        pass

    sentiment_brief = _load_sentiment_brief(archive, date_short)
    if sentiment_brief:
        pass

    system_prompt, user_prompt = _build_commodity_prompt(
        target_date, market_signals, kondratiev_signals, sentiment_brief
    )

    report_content = None
    try:
        from llm_client import chat
        report_content = chat(
            user_prompt, system=system_prompt,
            temperature=0.4, max_tokens=4000,
        )
    except Exception:
        pass

    if not report_content or len(report_content) < 500:
        report_content = _fallback_commodity_report(
            target_date, market_signals, kondratiev_signals
        )
    else:
        # 清理 LLM 前缀标记 (如 "[Ollama qwen2.5:3b] ...")
        import re as _re
        report_content = _re.sub(r"^\[[^\]]+\]\s*", "", report_content.strip())

    out_file.write_text(report_content, encoding="utf-8")
    out_file.stat().st_size / 1024
    return True


# ═══════════════════════════════════════════════════════════════
# 主调度
# ═══════════════════════════════════════════════════════════════
TASKS = [
    ("晨间行情摘要", task_morning_market),
    ("康波周期分析", task_kondratiev),
    ("实时ETF资金流向", task_etf_flow),
    ("舆情综合+动力煤", task_sentiment),
    ("CNEMC空气质量", task_cnemc),
    ("iFinD自动研判", task_ifind_analysis),
    ("大宗商品基本面扫描", task_commodity_fundamental_scan),
]


def _run_task(name_fn_tuple, archive, target_date, force):
    """执行单个任务并返回 (name, ok) — 供 ThreadPoolExecutor 调用"""
    name, fn = name_fn_tuple
    try:
        return name, bool(fn(archive, target_date, force))
    except Exception:
        return name, False


def run_all(target_date: str = None, force: bool = False,
            max_workers: int = 4) -> dict:
    """运行全部信息采集任务 (两阶段并行)

    阶段1: 并行执行任务 1-6 (彼此独立)
    阶段2: 执行任务 7 (大宗商品扫描, 依赖任务1的 morning_market_data json + 任务4的舆情日报)

    Args:
        target_date: 目标日期 YYYY-MM-DD (默认今日)
        force: 强制重新生成 (忽略已存在文件)
        max_workers: 线程池大小 (默认 4)

    Returns:
        {"ok": bool, "success": int, "total": int, "archive_dir": str, "date": str}
    """
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')
    archive = _archive_today(target_date)

    success, total = 0, 0

    # 阶段1: 任务 1-6 并行 (彼此无依赖)
    phase1_tasks = TASKS[:6]
    total += len(phase1_tasks)
    with ThreadPoolExecutor(max_workers=min(max_workers, len(phase1_tasks))) as pool:
        futures = {
            pool.submit(_run_task, t, archive, target_date, force): t[0]
            for t in phase1_tasks
        }
        for fut in as_completed(futures):
            _name, ok = fut.result()
            if ok:
                success += 1

    # 阶段2: 任务 7 (依赖阶段1的任务1行情json + 任务4舆情日报)
    phase2_tasks = TASKS[6:]
    total += len(phase2_tasks)
    for t in phase2_tasks:
        _name, ok = _run_task(t, archive, target_date, force)
        if ok:
            success += 1

    return {
        "ok": success == total,
        "success": success,
        "total": total,
        "archive_dir": str(archive),
        "date": target_date,
    }


def main():
    parser = argparse.ArgumentParser(description="晨间信息采集工作流")
    parser.add_argument('--force', action='store_true', help='强制重新生成')
    parser.add_argument('--date', type=str, default=None, help='目标日期 YYYY-MM-DD')
    args = parser.parse_args()
    result = run_all(target_date=args.date, force=args.force)
    sys.exit(0 if result["ok"] else 1)


if __name__ == '__main__':
    main()
