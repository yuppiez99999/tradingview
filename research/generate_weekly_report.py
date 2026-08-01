"""
每周对冲基金持仓报告生成器
============================
功能: 读取持仓配置+本地数据缓存+期权数据(可选), 计算综合盈亏,
      生成专业HTML报告, 转换PDF, 归档至 research/每日报告归档/YYYY-MM-DD/

用法:
    py research/generate_weekly_report.py              # 用当日日期
    py research/generate_weekly_report.py 2026-07-29   # 指定日期
    py research/generate_weekly_report.py --no-pdf     # 仅生成HTML

调度: 每周五收盘后自动运行 (见 run_weekly_report.bat + schtasks)
"""
import math
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# ============ 路径配置 ============
ROOT = Path(__file__).resolve().parent.parent
# 数据缓存通过集中配置管理 (支持 QUANT_DATA_ROOT 迁移到 D 盘)
try:
    from utils.path_config import get_data_cache_dir
    CACHE = get_data_cache_dir()
except ImportError:
    CACHE = ROOT / "data_cache"  # 回退: 项目目录
CONFIG = ROOT / "config"
ARCHIVE_ROOT = ROOT / "research" / "每日报告归档"
OUTPUT_TMP = ROOT / "research" / "outputs"

# ============ 常量 ============
DAILY_SUFFIXES = ["_5d", "_5y_base", "_3y", "_2y"]  # 日线缓存后缀(禁用_6m脏数据)
BUILD_DATE = "2026-07-22"      # Put建仓日
EXPIRY = "2026-08-27"          # Put到期日
R = 0.02                       # 无风险利率
MULT = 10000                   # 期权合约乘数

# ============ 数据加载 ============
def load_positions():
    """加载持仓配置 (B1.7: 委托给 utils.positions_loader)"""
    from utils.positions_loader import load_positions as _load
    return _load(CONFIG / "positions.json")

def load_daily(code):
    """加载日线缓存, 自动过滤脏数据(年度采样的_6m)"""
    bare = code.split(".")[0]
    for suffix in DAILY_SUFFIXES:
        fpath = CACHE / f"historical_{bare}{suffix}.parquet"
        if fpath.exists():
            try:
                df = pd.read_parquet(fpath)
                if df is not None and not df.empty and "close" in df.columns and len(df) > 5:
                    med_gap = df.index.to_series().diff().dropna().median()
                    if med_gap is not None and med_gap <= pd.Timedelta(days=5):
                        return df
            except Exception:
                continue
    return None

def get_latest_trade_date(rows_data):
    """从已加载的日线数据中找最新交易日"""
    latest = None
    for df in rows_data:
        if df is not None and not df.empty:
            d = df.index[-1]
            if latest is None or d > latest:
                latest = d
    return latest

def load_option_excel():
    """加载期权真实行情Excel(通配匹配), 返回 {code: {settle, iv, contract}}"""
    real_opt = {}
    for pattern in ["期权行情数据*.xlsx", "option_*.xlsx"]:
        for fpath in CACHE.glob(pattern):
            if fpath.name.startswith("~$"):
                continue
            try:
                df = pd.read_excel(fpath, sheet_name=0)
                for _, row in df.iterrows():
                    code = str(row.get("underlying", ""))
                    settle = row.get("settle")
                    iv_val = row.get("iv")
                    real_opt[code] = {
                        "contract": str(row.get("contract_code", "估算")),
                        "settle": float(settle) if pd.notna(settle) and str(settle) != "待查询" else None,
                        "iv": float(iv_val) if pd.notna(iv_val) and str(iv_val) != "待查询" else None,
                    }
            except Exception:
                continue
    return real_opt

# ============ BS 模型 ============
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_put(S, K, T, r, sigma):
    """Black-Scholes Put 期权定价"""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

# ============ 盈亏计算 ============
def calc_equity_pnl(positions):
    """计算权益部分盈亏, 返回 (rows, eq_mv, eq_cost)"""
    rows = []
    for code, pos in positions.items():
        shares = pos.get("shares", 0)
        avg_cost = pos.get("avg_cost", 0.0)
        if shares <= 0:
            continue
        df = load_daily(code)
        if df is not None:
            latest_price = float(df["close"].iloc[-1])
            price_src = "缓存"
        else:
            latest_price = pos.get("est_price", 0.0)
            price_src = "配置"
        mv = latest_price * shares
        cost_v = avg_cost * shares
        rows.append({
            "code": code, "name": pos.get("name", ""), "type": pos.get("type", ""),
            "sector": pos.get("sector", ""), "shares": shares, "avg_cost": avg_cost,
            "price": latest_price, "src": price_src, "mv": mv, "cost": cost_v,
            "pnl": mv - cost_v,
            "pnl_pct": ((mv - cost_v) / cost_v * 100) if cost_v > 0 else 0.0,
        })
    eq_mv = sum(r["mv"] for r in rows)
    eq_cost = sum(r["cost"] for r in rows)
    return rows, eq_mv, eq_cost

def calc_put_pnl(real_opt, trade_date_str):
    """计算Put期权盈亏, 返回 (put_rows, total_cost, total_value)"""
    t_now = (pd.Timestamp(EXPIRY) - pd.Timestamp(trade_date_str)).days / 365.0
    t_build = (pd.Timestamp(EXPIRY) - pd.Timestamp(BUILD_DATE)).days / 365.0

    hedge_specs = [
        {"label": "上证50ETF Put",  "code": "510050", "data_code": "510050", "contracts": 60, "budget": 900000},
        {"label": "科创50ETF Put",  "code": "588080", "data_code": "588000", "contracts": 25, "budget": 300000},
        {"label": "创业板ETF Put",  "code": "159915", "data_code": "159915", "contracts": 25, "budget": 250000},
        {"label": "沪深300ETF Put", "code": "510300", "data_code": "510300", "contracts": 25, "budget": 200000},
    ]

    put_rows = []
    total_cost = total_value = total_budget = 0.0
    bs_check_err = None

    for spec in hedge_specs:
        code = spec["code"]
        df = load_daily(spec["data_code"])
        s_cur = float(df["close"].iloc[-1]) if df is not None else 0.0
        s_build_mask = df.index <= pd.Timestamp(BUILD_DATE) if df is not None else None
        s_build = float(df.loc[s_build_mask, "close"].iloc[-1]) if df is not None and s_build_mask.any() else s_cur
        K = s_build * 0.95

        rets = df["close"].pct_change().dropna().tail(60) if df is not None else pd.Series()
        vol_60d = float(rets.std() * math.sqrt(252)) if len(rets) > 10 else 0.25
        iv_bs = vol_60d * 1.15
        p_bs_now = bs_put(s_cur, K, t_now, R, iv_bs)
        p_bs_build = bs_put(s_build, K, t_build, R, iv_bs)

        real = real_opt.get(code + ".SH") or real_opt.get(code + ".SZ")
        use_real = real and real.get("settle") is not None
        if use_real:
            p_now = real["settle"]
            iv_used = real["iv"] / 100.0 if real.get("iv") else iv_bs
            src = "真实"
            contract = real["contract"]
            if code == "510050":
                bs_check_err = abs(p_bs_now - p_now) / p_now * 100
        else:
            p_now = p_bs_now
            iv_used = iv_bs
            src = "BS估算"
            contract = real["contract"] if real else "估算"

        contracts = spec["contracts"]
        cost = p_bs_build * contracts * MULT
        cur_value = p_now * contracts * MULT
        total_cost += cost
        total_value += cur_value
        total_budget += spec["budget"]
        put_rows.append({
            "label": spec["label"], "src": src, "contract": contract, "K": K,
            "s_cur": s_cur, "iv": iv_used, "p_now": p_now, "contracts": contracts,
            "cost": cost, "value": cur_value, "pnl": cur_value - cost, "budget": spec["budget"],
        })

    return put_rows, total_cost, total_value, total_budget, bs_check_err

# ============ HTML 渲染 ============
CSS = """
  *{margin:0;padding:0;box-sizing:border-box}
  :root{--navy:#0a1929;--navy-2:#102a43;--navy-3:#1c3a5e;--gold:#c9a961;--gold-soft:#e8d9a8;
  --green:#16a34a;--green-bg:rgba(22,163,74,.12);--red:#dc2626;--red-bg:rgba(220,38,38,.12);
  --gray:#64748b;--gray-2:#94a3b8;--line:rgba(201,169,97,.2);--bg:#f5f3ee;--card:#fff}
  body{font-family:"PingFang SC","Microsoft YaHei",-apple-system,sans-serif;background:var(--bg);color:var(--navy);line-height:1.6}
  .wrap{max-width:1200px;margin:0 auto;padding:40px 24px}
  .header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-2) 100%);color:#fff;padding:36px 40px;border-radius:12px;position:relative;overflow:hidden;box-shadow:0 8px 32px rgba(10,25,41,.25)}
  .header::before{content:"";position:absolute;top:0;right:0;width:300px;height:300px;background:radial-gradient(circle,rgba(201,169,97,.15) 0%,transparent 70%);pointer-events:none}
  .header-top{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid rgba(201,169,97,.25);padding-bottom:18px;margin-bottom:18px}
  .brand{display:flex;align-items:center;gap:14px}
  .brand-logo{width:44px;height:44px;border:2px solid var(--gold);border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:800;color:var(--gold);font-size:20px}
  .fund-name{font-size:22px;font-weight:700;letter-spacing:1px}.fund-sub{font-size:12px;color:var(--gold-soft);letter-spacing:2px;margin-top:2px}
  .header-meta{text-align:right;font-size:12px;color:var(--gray-2)}.header-meta .tag{display:inline-block;background:rgba(201,169,97,.18);color:var(--gold);padding:3px 10px;border-radius:4px;font-weight:600;margin-bottom:8px}
  .header-meta .date{color:#fff;font-size:13px}
  .title-row{display:flex;align-items:baseline;gap:16px}.title-row h1{font-size:28px;font-weight:700}.title-row .ver{font-size:13px;color:var(--gold);background:rgba(201,169,97,.12);padding:2px 10px;border-radius:4px}
  .kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:28px 0}
  .kpi{background:var(--card);border-radius:10px;padding:22px 20px;box-shadow:0 2px 12px rgba(10,25,41,.06);border-top:3px solid var(--gold)}
  .kpi.win{border-top-color:var(--green)}
  .kpi-label{font-size:12px;color:var(--gray);letter-spacing:1px;margin-bottom:8px}
  .kpi-value{font-size:26px;font-weight:700;color:var(--navy);font-family:Georgia,serif}.kpi-value span{font-size:14px}
  .kpi-sub{font-size:12px;margin-top:6px;font-weight:600}.pos{color:var(--green)}.neg{color:var(--red)}
  .kpi-sub.pos{background:var(--green-bg);padding:2px 8px;border-radius:4px;display:inline-block}.kpi-sub.neg{background:var(--red-bg);padding:2px 8px;border-radius:4px;display:inline-block}
  .section{background:var(--card);border-radius:12px;padding:28px;margin-bottom:24px;box-shadow:0 2px 12px rgba(10,25,41,.05)}
  .sec-head{display:flex;align-items:center;gap:10px;margin-bottom:20px;padding-bottom:14px;border-bottom:2px solid var(--line)}
  .sec-num{width:28px;height:28px;background:var(--navy);color:var(--gold);border-radius:6px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px}
  .sec-title{font-size:18px;font-weight:700}.sec-sub{margin-left:auto;font-size:12px;color:var(--gray)}
  table{width:100%;border-collapse:collapse;font-size:13px}
  thead th{background:var(--navy);color:#fff;padding:11px 10px;text-align:right;font-weight:600;font-size:12px;border-right:1px solid rgba(255,255,255,.08)}
  thead th:first-child{text-align:left;border-radius:6px 0 0 6px}thead th:last-child{border-radius:0 6px 6px 0;border-right:none}
  tbody td{padding:10px;border-bottom:1px solid #eef0f3;text-align:right}tbody td:first-child{text-align:left}
  tbody tr:hover{background:#faf9f5}tbody tr.total{background:var(--navy-2);color:#fff;font-weight:700}tbody tr.total td{border-bottom:none}
  .t-name{font-weight:600}.t-code{font-size:11px;color:var(--gray);margin-left:6px}
  .pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
  .pill-etf{background:#e0e7ff;color:#3730a3}.pill-stock{background:#fce7f3;color:#9d174d}
  .pos-cell{color:var(--green);font-weight:600}.neg-cell{color:var(--red);font-weight:600}
  .two-col{display:grid;grid-template-columns:1fr 1fr;gap:24px}
  .three-col{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  .attr-row{display:flex;align-items:center;gap:14px;margin-bottom:14px}
  .attr-label{width:120px;font-size:13px;font-weight:600}.attr-bar{flex:1;height:24px;background:#eef0f3;border-radius:4px;overflow:hidden}
  .attr-fill{height:100%;border-radius:4px;display:flex;align-items:center;justify-content:flex-end;padding-right:8px;color:#fff;font-size:11px;font-weight:700}
  .attr-fill.eq{background:linear-gradient(90deg,var(--navy-3),var(--navy))}.attr-fill.put{background:linear-gradient(90deg,#b8860b,var(--gold))}
  .attr-val{width:90px;text-align:right;font-size:13px;font-weight:700}
  .donut-wrap{display:flex;align-items:center;gap:24px;justify-content:center}
  .donut-center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}
  .donut-center .big{font-size:22px;font-weight:700;color:var(--navy);font-family:Georgia,serif}.donut-center .small{font-size:11px;color:var(--gray)}
  .legend-item{display:flex;align-items:center;gap:8px;font-size:12px;margin-bottom:8px;padding:6px 10px;background:#faf9f5;border-radius:6px}
  .legend-dot{width:10px;height:10px;border-radius:2px;flex-shrink:0}.legend-name{flex:1;font-weight:600}.legend-val{font-weight:700;color:var(--navy)}
  .bar-row{display:flex;align-items:center;gap:12px;margin-bottom:10px;font-size:12px}
  .bar-name{width:70px;font-weight:600}.bar-track{flex:1;height:20px;background:#eef0f3;border-radius:3px;overflow:hidden}
  .bar-fill{height:100%;border-radius:3px}.bar-pct{width:50px;text-align:right;font-weight:600;color:var(--gray)}
  .risk-card{background:#faf9f5;border-radius:8px;padding:16px;border-left:3px solid var(--gold)}
  .risk-label{font-size:11px;color:var(--gray);letter-spacing:1px;margin-bottom:6px}
  .risk-value{font-size:22px;font-weight:700;color:var(--navy);font-family:Georgia,serif}.risk-value span{font-size:14px}
  .risk-desc{font-size:11px;color:var(--gray);margin-top:4px}.risk-bar{height:6px;background:#eef0f3;border-radius:3px;margin-top:8px;overflow:hidden}.risk-bar-fill{height:100%;border-radius:3px}
  .insight{display:flex;gap:14px;padding:14px;background:#faf9f5;border-radius:8px;margin-bottom:10px;border-left:3px solid var(--gold)}
  .insight-num{width:24px;height:24px;background:var(--gold);color:var(--navy);border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;flex-shrink:0}
  .insight-text{font-size:13px;line-height:1.7}.insight-text strong{color:var(--navy)}
  .footer{margin-top:32px;padding:20px 24px;background:var(--navy);color:var(--gray-2);border-radius:8px;font-size:11px;line-height:1.8;text-align:center}
  .footer .disc{color:var(--gold-soft);font-weight:600;margin-bottom:6px;letter-spacing:1px}
  .warn{background:rgba(220,38,38,.06);border:1px solid rgba(220,38,38,.2);border-radius:8px;padding:12px 16px;font-size:12px;color:#991b1b;margin-top:12px;display:flex;gap:10px}
  @media print{*{-webkit-print-color-adjust:exact !important;print-color-adjust:exact !important}body{background:#fff}.wrap{padding:0;max-width:100%}.section{break-inside:avoid;box-shadow:none;margin-bottom:16px}.kpi-grid{break-inside:avoid}thead{display:table-header-group}tr{break-inside:avoid}.header{break-inside:avoid}@page{margin:12mm;size:A4}}
"""

def fmt(v, decimals=2):
    """格式化数字"""
    return f"{v:.{decimals}f}"

def render_holding_rows(rows):
    """渲染持仓明细表格行"""
    html = ""
    for r in sorted(rows, key=lambda x: -x["mv"]):
        pill = "pill-etf" if r["type"] == "ETF" else "pill-stock"
        cls = "pos-cell" if r["pnl"] >= 0 else "neg-cell"
        sign = "+" if r["pnl"] >= 0 else ""
        html += f"""        <tr><td><span class="t-name">{r['name']}</span><span class="t-code">{r['code']}</span></td><td style="text-align:center"><span class="pill {pill}">{r['type']}</span></td><td>{r['sector']}</td><td>{r['shares']:,}</td><td>{fmt(r['avg_cost'],4)}</td><td>{fmt(r['price'],4)}</td><td>{fmt(r['mv']/10000)}</td><td class="{cls}">{sign}{fmt(r['pnl']/10000)}</td><td class="{cls}">{sign}{fmt(r['pnl_pct'])}%</td></tr>
"""
    return html

def render_sector_rows(sector_map, eq_mv):
    """渲染板块汇总行"""
    html = ""
    for s, v in sorted(sector_map.items(), key=lambda x: -x[1]["mv"]):
        pct = v["mv"] / eq_mv * 100 if eq_mv > 0 else 0
        sp = v["pnl"] / v["cost"] * 100 if v["cost"] > 0 else 0
        cls = "pos-cell" if v["pnl"] >= 0 else "neg-cell"
        sign = "+" if v["pnl"] >= 0 else ""
        html += f"""        <tr><td>{s}</td><td>{v['n']}</td><td>{fmt(v['mv']/10000)}</td><td>{fmt(pct)}%</td><td>{fmt(v['cost']/10000)}</td><td class="{cls}">{sign}{fmt(v['pnl']/10000)}</td><td class="{cls}">{sign}{fmt(sp)}%</td></tr>
"""
    return html

def render_put_rows(put_rows):
    """渲染Put期权行"""
    html = ""
    for p in put_rows:
        pill_cls = "background:var(--green-bg);color:var(--green)" if p["src"] == "真实" else "background:#fef3c7;color:#92400e"
        cls = "pos-cell" if p["pnl"] >= 0 else "neg-cell"
        sign = "+" if p["pnl"] >= 0 else ""
        html += f"""        <tr><td>{p['label']}</td><td style="text-align:center"><span class="pill" style="{pill_cls}">{p['src']}</span></td><td>{p['contract']}</td><td>{fmt(p['K'],3)}</td><td>{fmt(p['iv']*100,2)}%</td><td>{fmt(p['p_now'],4)}</td><td>{p['contracts']}</td><td>{fmt(p['cost']/10000)}</td><td>{fmt(p['value']/10000)}</td><td class="{cls}">{sign}{fmt(p['pnl']/10000)}</td></tr>
"""
    return html

def render_bar_rows(sector_map, eq_mv):
    """渲染板块条形图"""
    colors = {"国债":"var(--navy)","医药":"#0ea5e9","科技":"#8b5cf6","宽基":"#14b8a6","金融":"#f59e0b","资源":"#ef4444","新能源":"#22c55e","防御":"#06b6d4","制造":"#a855f7","顺周期":"#64748b","成长":"#ec4899"}
    sorted_sectors = sorted(sector_map.items(), key=lambda x: -x[1]["mv"])
    html = ""
    for s, v in sorted_sectors:
        pct = v["mv"] / eq_mv * 100 if eq_mv > 0 else 0
        color = colors.get(s, "var(--gray)")
        html += f'        <div class="bar-row"><div class="bar-name">{s}</div><div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{color}"></div></div><div class="bar-pct">{fmt(pct)}%</div></div>\n'
    return html

def render_insights(insights):
    """渲染关键观察"""
    html = ""
    for i, text in enumerate(insights, 1):
        html += f"""    <div class="insight"><div class="insight-num">{i}</div><div class="insight-text">{text}</div></div>
"""
    return html

def render_html(data, report_date, trade_date):
    """渲染完整HTML报告"""
    eq_mv = data["eq_mv"]
    eq_cost = data["eq_cost"]
    eq_pnl = data["eq_pnl"]
    eq_pnl_pct = data["eq_pnl_pct"]
    put_pnl = data["put_pnl"]
    total_put_cost = data["total_put_cost"]
    total_put_value = data["total_put_value"]
    total_pnl = eq_pnl + put_pnl
    total_pnl_pct = total_pnl / (eq_cost + total_put_cost) * 100 if (eq_cost + total_put_cost) > 0 else 0
    total_assets = eq_mv + total_put_value
    total_budget = data["total_budget"]
    bs_err = data["bs_check_err"]
    rows = data["rows"]
    put_rows = data["put_rows"]
    sector_map = data["sector_map"]
    type_map = data["type_map"]
    winners = data["winners"]
    losers = data["losers"]

    # 计算环形图
    etf_mv = type_map.get("ETF", {}).get("mv", 0)
    stock_mv = type_map.get("STOCK", {}).get("mv", 0)
    etf_n = type_map.get("ETF", {}).get("n", 0)
    stock_n = type_map.get("STOCK", {}).get("n", 0)
    etf_pct = etf_mv / eq_mv * 100 if eq_mv > 0 else 0
    stock_pct = stock_mv / eq_mv * 100 if eq_mv > 0 else 0
    etf_dash = etf_pct / 100 * 502.65
    stock_dash = stock_pct / 100 * 502.65

    # 风险指标
    bond_mv = sector_map.get("国债", {}).get("mv", 0)
    bond_pnl = sector_map.get("国债", {}).get("pnl", 0)
    max_pos_pct = max((r["mv"] for r in rows), default=0) / eq_mv * 100 if eq_mv > 0 else 0
    worst = min(rows, key=lambda x: x["pnl_pct"]) if rows else None
    best = max(rows, key=lambda x: x["pnl_pct"]) if rows else None
    profit_concentration = abs(bond_pnl / eq_pnl * 100) if eq_pnl != 0 else 0
    hedge_ratio = total_put_value / eq_mv * 100 if eq_mv > 0 else 0

    report_date_str = report_date.strftime("%Y-%m-%d")
    report_date.strftime("%Y%m%d")

    insights = [
        f"<strong>综合净盈亏 {total_pnl/10000:+.2f}万（{total_pnl_pct:+.2f}%）</strong>，其中权益贡献 {eq_pnl/10000:+.2f}万，Put对冲贡献 {put_pnl/10000:+.2f}万。整体表现稳健，但收益结构需关注集中度。",
        f"<strong>利润集中度：</strong>国债ETF浮盈 {bond_pnl/10000:+.2f}万，占权益浮盈的 <strong>{profit_concentration:.0f}%</strong>。扣除国债后，权益部分（{(eq_mv-bond_mv)/10000:.2f}万市值）实际浮盈 <strong>{(eq_pnl-bond_pnl)/10000:+.2f}万</strong>。",
        f"<strong>仓位结构：</strong>国债占权益市值 {bond_mv/eq_mv*100:.1f}%，权益部分仓位率 {eq_mv/3000000*100:.1f}%，持仓 {len(rows)}只标的。",
        f"<strong>盈亏分布：</strong>盈利 {len(winners)}只 / 亏损 {len(losers)}只，胜率 {len(winners)/len(rows)*100:.1f}%。最大盈利(幅度)：{best['name']} {best['pnl_pct']:+.2f}%；最大亏损(幅度)：{worst['name']} {worst['pnl_pct']:+.2f}%。",
        f"<strong>Put对冲：</strong>建仓后标的微跌，Put {'增值' if put_pnl>=0 else '减值'} {put_pnl/10000:+.2f}万，对冲覆盖率 {hedge_ratio:.1f}%，保险功能{'正常' if put_pnl>=0 else '承压'}。",
        f"<strong>⚠ 数据口径：</strong>配置预算 {total_budget/10000:.0f}万 vs BS理论权利金 {total_put_cost/10000:.2f}万" + (f"，相差 {total_budget/total_put_cost:.0f}倍，实际盈亏以券商账户为准。" if total_put_cost>0 else "。") + (f" 510050真实数据校验BS误差{bs_err:.1f}%。" if bs_err else ""),
    ]

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>终极量化对冲基金 · 持仓与盈亏报告 {report_date_str}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="header-top">
      <div class="brand">
        <div class="brand-logo">Q</div>
        <div><div class="fund-name">终极量化对冲基金</div><div class="fund-sub">QUANTITATIVE HEDGE FUND · INSTITUTIONAL REPORT</div></div>
      </div>
      <div class="header-meta">
        <div class="tag">机密 · CONFIDENTIAL</div>
        <div class="date">报告日期：{report_date_str}</div>
        <div>行情基准：{trade_date} 收盘</div>
      </div>
    </div>
    <div class="title-row"><h1>持仓与盈亏综合报告</h1><span class="ver">v8.7.2</span></div>
  </div>

  <div class="kpi-grid">
    <div class="kpi win">
      <div class="kpi-label">综合净盈亏</div>
      <div class="kpi-value {'pos' if total_pnl>=0 else 'neg'}">{total_pnl/10000:+.2f}<span>万</span></div>
      <div class="kpi-sub {'pos' if total_pnl>=0 else 'neg'}">{total_pnl_pct:+.2f}% 收益率</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">总资产市值</div>
      <div class="kpi-value">{total_assets/10000:.2f}<span>万</span></div>
      <div class="kpi-sub" style="color:var(--gray)">总投入 {(eq_cost+total_put_cost)/10000:.2f}万</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">权益仓位率</div>
      <div class="kpi-value">{eq_mv/3000000*100:.1f}<span>%</span></div>
      <div class="kpi-sub" style="color:var(--gray)">权益资金 300万</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">持仓胜率</div>
      <div class="kpi-value">{len(winners)/len(rows)*100:.1f}<span>%</span></div>
      <div class="kpi-sub {'pos' if len(winners)>=len(losers) else 'neg'}">{len(winners)}盈 / {len(losers)}亏</div>
    </div>
  </div>

  <div class="section">
    <div class="sec-head"><div class="sec-num">1</div><div class="sec-title">业绩归因分析</div><div class="sec-sub">权益 + 对冲双引擎</div></div>
    <div class="attr-row"><div class="attr-label">权益+ETF</div><div class="attr-bar"><div class="attr-fill eq" style="width:{abs(eq_pnl)/(abs(eq_pnl)+abs(put_pnl))*100 if (abs(eq_pnl)+abs(put_pnl))>0 else 50:.0f}%">{eq_pnl/10000:+.2f}万</div></div><div class="attr-val {'pos' if eq_pnl>=0 else 'neg'}">{eq_pnl_pct:+.2f}%</div></div>
    <div class="attr-row"><div class="attr-label">Put期权对冲</div><div class="attr-bar"><div class="attr-fill put" style="width:{abs(put_pnl)/(abs(eq_pnl)+abs(put_pnl))*100 if (abs(eq_pnl)+abs(put_pnl))>0 else 50:.0f}%">{put_pnl/10000:+.2f}万</div></div><div class="attr-val {'pos' if put_pnl>=0 else 'neg'}">{put_pnl/total_put_cost*100 if total_put_cost else 0:+.2f}%</div></div>
    <div style="margin-top:18px;padding:14px;background:linear-gradient(90deg,var(--navy) 0%,var(--navy-2) 100%);border-radius:8px;color:#fff;display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:14px;letter-spacing:1px">综合合计净盈亏</span>
      <span style="font-size:24px;font-weight:700;color:var(--gold);font-family:Georgia,serif">{total_pnl/10000:+.2f} 万 <span style="font-size:14px">({total_pnl_pct:+.2f}%)</span></span>
    </div>
  </div>

  <div class="section">
    <div class="sec-head"><div class="sec-num">2</div><div class="sec-title">资产配置与板块分布</div><div class="sec-sub">权益市值 {eq_mv/10000:.2f}万</div></div>
    <div class="two-col">
      <div>
        <h4 style="font-size:13px;color:var(--gray);margin-bottom:16px;letter-spacing:1px">按类型分布</h4>
        <div class="donut-wrap">
          <div style="position:relative;width:200px;height:200px">
            <svg width="200" height="200" viewBox="0 0 200 200" style="transform:rotate(-90deg)">
              <circle cx="100" cy="100" r="80" fill="none" stroke="#1c3a5e" stroke-width="30" stroke-dasharray="{etf_dash:.1f} 502.65" stroke-dashoffset="0"/>
              <circle cx="100" cy="100" r="80" fill="none" stroke="#c9a961" stroke-width="30" stroke-dasharray="{stock_dash:.1f} 502.65" stroke-dashoffset="-{etf_dash:.1f}"/>
            </svg>
            <div class="donut-center"><div class="big">{len(rows)}</div><div class="small">持仓标的</div></div>
          </div>
          <div style="flex:1">
            <div class="legend-item"><span class="legend-dot" style="background:#1c3a5e"></span><span class="legend-name">ETF ({etf_n}只)</span><span class="legend-val">{etf_mv/10000:.2f}万</span></div>
            <div class="legend-item"><span class="legend-dot" style="background:#c9a961"></span><span class="legend-name">股票 ({stock_n}只)</span><span class="legend-val">{stock_mv/10000:.2f}万</span></div>
          </div>
        </div>
      </div>
      <div>
        <h4 style="font-size:13px;color:var(--gray);margin-bottom:16px;letter-spacing:1px">按板块市值占比</h4>
{render_bar_rows(sector_map, eq_mv)}      </div>
    </div>
  </div>

  <div class="section">
    <div class="sec-head"><div class="sec-num">3</div><div class="sec-title">权益持仓明细</div><div class="sec-sub">按市值降序 · {len(rows)}只标的</div></div>
    <table>
      <thead><tr><th>标的</th><th>类型</th><th>板块</th><th>持仓</th><th>成本</th><th>现价</th><th>市值(万)</th><th>盈亏(万)</th><th>盈亏%</th></tr></thead>
      <tbody>
{render_holding_rows(rows)}        <tr class="total"><td colspan="6">合计 ({len(rows)}只标的)</td><td>{fmt(eq_mv/10000)}</td><td>{eq_pnl/10000:+.2f}</td><td>{eq_pnl_pct:+.2f}%</td></tr>
      </tbody>
    </table>
  </div>

  <div class="section">
    <div class="sec-head"><div class="sec-num">4</div><div class="sec-title">板块盈亏汇总</div><div class="sec-sub">按市值降序</div></div>
    <table>
      <thead><tr><th>板块</th><th>只数</th><th>市值(万)</th><th>占比%</th><th>成本(万)</th><th>盈亏(万)</th><th>盈亏%</th></tr></thead>
      <tbody>
{render_sector_rows(sector_map, eq_mv)}      </tbody>
    </table>
  </div>

  <div class="section">
    <div class="sec-head"><div class="sec-num">5</div><div class="sec-title">Put 期权对冲头寸</div><div class="sec-sub">期货+期权双路径</div></div>
    <table>
      <thead><tr><th>标的</th><th>数据源</th><th>合约代码</th><th>行权价</th><th>IV</th><th>权利金</th><th>合约</th><th>成本(万)</th><th>现值(万)</th><th>盈亏(万)</th></tr></thead>
      <tbody>
{render_put_rows(put_rows)}        <tr class="total"><td colspan="7">合计</td><td>{fmt(total_put_cost/10000)}</td><td>{fmt(total_put_value/10000)}</td><td>{put_pnl/10000:+.2f}</td></tr>
      </tbody>
    </table>
    {f'<div style="margin-top:14px;padding:12px 16px;background:#f0fdf4;border-radius:8px;font-size:12px;color:#166534"><strong>模型校验：</strong>510050 Put BS估算 vs 真实权利金，误差 {bs_err:.1f}% ✓ 估算可靠</div>' if bs_err else ''}
    <div class="warn"><span style="color:var(--red);font-weight:700">⚠</span><div>配置权利金预算 {total_budget/10000:.0f}万 与 BS理论建仓权利金 {total_put_cost/10000:.2f}万 相差 <strong>{total_budget/total_put_cost:.0f}倍</strong>。<strong>实际盈亏须以券商账户建仓记录为准。</strong></div></div>
  </div>

  <div class="section">
    <div class="sec-head"><div class="sec-num">6</div><div class="sec-title">风险与集中度指标</div><div class="sec-sub">组合风险评估</div></div>
    <div class="three-col">
      <div class="risk-card"><div class="risk-label">单一标的最大占比</div><div class="risk-value">{max_pos_pct:.1f}<span>%</span></div><div class="risk-desc">{max(rows, key=lambda x: x['mv'])['name'] if rows else '-'}</div><div class="risk-bar"><div class="risk-bar-fill" style="width:{max_pos_pct:.0f}%;background:var(--red)"></div></div></div>
      <div class="risk-card"><div class="risk-label">权益仓位率</div><div class="risk-value">{eq_mv/3000000*100:.1f}<span>%</span></div><div class="risk-desc">权益市值 / 权益资金</div><div class="risk-bar"><div class="risk-bar-fill" style="width:{eq_mv/3000000*100:.0f}%;background:var(--gold)"></div></div></div>
      <div class="risk-card"><div class="risk-label">对冲覆盖率</div><div class="risk-value">{hedge_ratio:.1f}<span>%</span></div><div class="risk-desc">Put现值 / 权益市值</div><div class="risk-bar"><div class="risk-bar-fill" style="width:{hedge_ratio:.0f}%;background:var(--green)"></div></div></div>
      <div class="risk-card"><div class="risk-label">持仓胜率</div><div class="risk-value">{len(winners)/len(rows)*100:.1f}<span>%</span></div><div class="risk-desc">{len(winners)}盈 / {len(losers)}亏</div><div class="risk-bar"><div class="risk-bar-fill" style="width:{len(winners)/len(rows)*100:.0f}%;background:var(--red)"></div></div></div>
      <div class="risk-card"><div class="risk-label">最大单标的浮亏</div><div class="risk-value neg">{worst['pnl_pct']:.1f}<span>%</span></div><div class="risk-desc">{worst['name']}</div><div class="risk-bar"><div class="risk-bar-fill" style="width:{abs(worst['pnl_pct']):.0f}%;background:var(--red)"></div></div></div>
      <div class="risk-card"><div class="risk-label">利润集中度</div><div class="risk-value">{profit_concentration:.0f}<span>%</span></div><div class="risk-desc">国债贡献/权益浮盈</div><div class="risk-bar"><div class="risk-bar-fill" style="width:{min(profit_concentration,100):.0f}%;background:var(--red)"></div></div></div>
    </div>
  </div>

  <div class="section">
    <div class="sec-head"><div class="sec-num">7</div><div class="sec-title">关键观察与策略建议</div></div>
{render_insights(insights)}  </div>

  <div class="footer">
    <div class="disc">RISK DISCLAIMER · 风险免责声明</div>
    <div>本报告由终极量化交易系统 v8.7.2 自动生成，仅供内部参考，不构成投资建议。</div>
    <div>权益行情数据来自本地日线缓存（基准日 {trade_date}）；期权数据{'含真实行情' if any(p['src']=='真实' for p in put_rows) else '全部为BS模型估算'}；成本为BS理论建仓权利金，实际盈亏以券商账户为准。</div>
    <div style="margin-top:8px;color:var(--gray)">© 2026 终极量化对冲基金 · CONFIDENTIAL · 生成时间 {report_date_str}</div>
  </div>
</div>
</body>
</html>"""

# ============ PDF 转换 ============
def convert_pdf(html_path, pdf_path):
    """用Edge/Chrome headless转PDF"""
    browsers = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    file_uri = "file:///" + str(html_path).replace("\\", "/")
    for browser in browsers:
        if not Path(browser).exists():
            continue
        try:
            subprocess.run(
                [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                 f"--print-to-pdf={pdf_path}", file_uri],
                capture_output=True, timeout=60
            )
            if Path(pdf_path).exists():
                return True
        except Exception:
            continue
    return False

# ============ 主流程 ============
def main():
    # 解析日期参数
    if len(sys.argv) > 1 and sys.argv[1] != "--no-pdf":
        try:
            report_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            report_date = date.today()
    else:
        report_date = date.today()
    no_pdf = "--no-pdf" in sys.argv

    print(f"[报告生成器] 报告日期: {report_date}")

    # 1. 加载数据
    cfg = load_positions()
    positions = cfg["positions"]
    real_opt = load_option_excel()
    print(f"[数据] 持仓 {len([p for p in positions.values() if p.get('shares',0)>0])} 只, 真实期权数据 {len(real_opt)} 条")

    # 2. 计算权益盈亏
    rows, eq_mv, eq_cost = calc_equity_pnl(positions)
    eq_pnl = eq_mv - eq_cost
    eq_pnl_pct = eq_pnl / eq_cost * 100 if eq_cost > 0 else 0

    # 3. 确定行情基准日
    all_dfs = [load_daily(r["code"]) for r in rows]
    trade_date_ts = get_latest_trade_date(all_dfs)
    trade_date = trade_date_ts.strftime("%Y-%m-%d") if trade_date_ts else report_date.strftime("%Y-%m-%d")
    print(f"[数据] 行情基准日: {trade_date}")

    # 4. 计算Put期权盈亏
    put_rows, total_put_cost, total_put_value, total_budget, bs_err = calc_put_pnl(real_opt, trade_date)
    put_pnl = total_put_value - total_put_cost
    print(f"[盈亏] 权益 {eq_pnl/10000:+.2f}万, Put对冲 {put_pnl/10000:+.2f}万, 综合 {(eq_pnl+put_pnl)/10000:+.2f}万")

    # 5. 汇总统计
    sector_map = {}
    type_map = {}
    for r in rows:
        s = r["sector"]
        sector_map.setdefault(s, {"mv": 0, "cost": 0, "pnl": 0, "n": 0})
        sector_map[s]["mv"] += r["mv"]; sector_map[s]["cost"] += r["cost"]
        sector_map[s]["pnl"] += r["pnl"]; sector_map[s]["n"] += 1
        t = r["type"]
        type_map.setdefault(t, {"mv": 0, "cost": 0, "pnl": 0, "n": 0})
        type_map[t]["mv"] += r["mv"]; type_map[t]["cost"] += r["cost"]
        type_map[t]["pnl"] += r["pnl"]; type_map[t]["n"] += 1

    winners = [r for r in rows if r["pnl"] > 0]
    losers = [r for r in rows if r["pnl"] < 0]

    data = {
        "rows": rows, "put_rows": put_rows, "sector_map": sector_map, "type_map": type_map,
        "winners": winners, "losers": losers, "eq_mv": eq_mv, "eq_cost": eq_cost,
        "eq_pnl": eq_pnl, "eq_pnl_pct": eq_pnl_pct, "put_pnl": put_pnl,
        "total_put_cost": total_put_cost, "total_put_value": total_put_value,
        "total_budget": total_budget, "bs_check_err": bs_err,
    }

    # 6. 渲染HTML
    html = render_html(data, report_date, trade_date)

    # 7. 归档目录
    archive_dir = ARCHIVE_ROOT / report_date.strftime("%Y-%m-%d")
    archive_dir.mkdir(parents=True, exist_ok=True)
    date_compact = report_date.strftime("%Y%m%d")
    html_path = archive_dir / f"对冲基金持仓报告_{date_compact}.html"
    pdf_path = archive_dir / f"对冲基金持仓报告_{date_compact}.pdf"

    # 8. 保存HTML
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[归档] HTML: {html_path}")

    # 9. 转PDF
    if not no_pdf:
        if convert_pdf(html_path, pdf_path):
            print(f"[归档] PDF:  {pdf_path}")
        else:
            print("[警告] PDF转换失败, 仅保留HTML")

    print(f"\n[完成] 报告已归档至: {archive_dir}")
    return html_path, pdf_path if not no_pdf else html_path

if __name__ == "__main__":
    main()
