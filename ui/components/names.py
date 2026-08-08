# -*- coding: utf-8 -*-
"""共享标的名称映射 — 所有 UI 页面统一使用"""

# ═══════════════════════════════════════════════════════════════
# 完整标的名称映射（覆盖系统所有可能出现的代码）
# 优先加载：positions.json name → portfolio.yaml → 本字典
# ═══════════════════════════════════════════════════════════════
STOCK_NAME_MAP = {
    # ── v5.1 主组合 15 只标的 ──
    # 高端制造(含算力) 40%
    "300308": "中际旭创", "688041": "海光信息", "002371": "北方华创",
    "688981": "中芯国际", "300750": "宁德时代", "000425": "徐工机械",
    # 顺周期 20%
    "601088": "中国神华", "600219": "南山铝业", "600019": "宝钢股份",
    # 资源 20%
    "000408": "藏格矿业", "159980": "有色ETF大成",
    # 防御 20%
    "600900": "长江电力", "600276": "恒瑞医药", "603259": "药明康德", "002422": "科伦药业",
    # 其他 v5.1 标的
    "600995": "南网储能", "688017": "绿的谐波", "300124": "汇川技术",
    "002475": "立讯精密", "300274": "阳光电源", "600989": "宝丰能源",
    # ── ETF (24只国家队监控) ──
    "510300": "沪深300ETF华泰柏瑞", "510310": "沪深300ETF易方达",
    "159919": "沪深300ETF嘉实", "510500": "中证500ETF南方",
    "510050": "上证50ETF华夏", "159915": "创业板ETF易方达",
    "588000": "科创50ETF华夏", "588080": "科创50ETF易方达",
    "560010": "中证1000ETF富国", "512100": "中证1000ETF南方",
    "515080": "中证红利ETF易方达", "512890": "红利低波100ETF华泰柏瑞",
    "512880": "证券ETF国泰", "512800": "银行ETF华宝",
    "512170": "医疗ETF华宝", "512010": "医药ETF易方达",
    "512760": "半导体ETF国泰", "515030": "新能源车ETF华夏",
    "518880": "华安黄金ETF",
    # ── ETF 扩展 ──
    "511360": "短融ETF海富通", "511260": "十年国债ETF国泰",
    "511520": "政金债ETF富国", "510880": "红利ETF华泰柏瑞",
    "159981": "能源化工ETF建信",
    # ── 大炼化观察仓 7 只 ──
    "600346": "恒力石化", "002493": "荣盛石化", "000301": "东方盛虹",
    "000059": "华锦股份", "002648": "卫星化学", "601233": "桐昆股份",
    "603225": "新凤鸣",
}

# 资产风格分类
GROWTH_STOCKS = {
    "300308", "688041", "002371", "688981", "300750", "688017", "300124", "002475"
}
CYCLICAL_STOCKS = {
    "601088", "600219", "600019", "000425", "600995", "600989"
}
DEFENSE_STOCKS = {
    "600900", "600276", "603259", "002422", "300274"
}
RESOURCE_STOCKS = {
    "000408", "518880", "159980"
}
WATCHLIST_STOCKS = {
    "600346", "002493", "000301", "000059", "002648", "601233", "603225"
}

_STYLE_CACHE = {}

def get_style(code: str) -> tuple:
    """返回 (风格标签, 颜色) — 有缓存"""
    if code in _STYLE_CACHE:
        return _STYLE_CACHE[code]
    if code in GROWTH_STOCKS:
        r = ("成长", "#722ED1")
    elif code in CYCLICAL_STOCKS:
        r = ("周期", "#FAAD14")
    elif code in DEFENSE_STOCKS:
        r = ("防御", "#52C41A")
    elif code in RESOURCE_STOCKS:
        r = ("资源", "#1890FF")
    elif code in WATCHLIST_STOCKS:
        r = ("观察", "#8C8C8C")
    elif code.startswith(("51", "15", "58", "56")):
        r = ("ETF", "#13C2C2")
    else:
        r = ("个股", "#F5222D")
    _STYLE_CACHE[code] = r
    return r


def resolve_name(code: str, positions_name: str = "", yaml_names: dict = None) -> str:
    """四级回退获取中文名"""
    if positions_name:
        return positions_name
    yaml_names = yaml_names or {}
    if code in yaml_names:
        return yaml_names[code]
    return STOCK_NAME_MAP.get(code, code)


def resolve_name_safe(code: str, record: dict = None, yaml_names: dict = None) -> str:
    """从record字典安全提取名称"""
    name = ""
    if record:
        name = str(record.get('name', '') or record.get('名称', '') or
                   record.get('证券名称', '') or '')
    return resolve_name(code, name, yaml_names)
