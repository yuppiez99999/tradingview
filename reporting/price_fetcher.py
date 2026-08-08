"""价格获取 (B3.2 拆分自 PortfolioAnalyzer)

提供以下纯函数:
  - to_sina_code: 将标准代码转为新浪代码
  - fetch_sina_realtime: 通过新浪财经 API 批量获取实时行情
  - fetch_market_prices: 获取所有持仓标的的收盘价格
  - assess_data_source_health: 评估数据源健康状态

模块级 _SINA_SESSION 与原 generate_daily_report.py 保持一致,
绕过系统代理以避免国内金融 API 被代理拦截。
"""

from typing import Any, Dict, List

# B-4.1: 统一无代理 Session 工厂 (绕过系统代理, 避免新浪 API 被拦截)
from utils.http_session import make_no_proxy_session

_SINA_SESSION = make_no_proxy_session("sina")


def to_sina_code(code: str) -> str:
    """将标准代码转为新浪代码: 688041.SH -> sh688041, 000333.SZ -> sz000333

    RC2 修复: 期货前缀 hf_ 必须保持小写, 新浪 API 响应按小写前缀匹配
    原代码 code.upper() 将 hf_IF2407 转为 HF_IF2407, 与新浪响应不匹配, 期货价格永远拿不到
    """
    code = code.strip()
    # RC2 修复: 期货前缀保持小写 (hf_/nf_cf_), 仅对股票代码做大小写标准化
    if code.lower().startswith("hf_") or code.lower().startswith("nf_"):
        # 期货代码: 保持前缀小写, 合约代码大写 (如 hf_IF2407)
        prefix_end = code.index("_") + 1
        return code[:prefix_end].lower() + code[prefix_end:].upper()
    # 股票代码: 统一大写处理后缀
    code_upper = code.upper()
    if "." in code_upper:
        num, suffix = code_upper.split(".", 1)
        if suffix in ("SH", "SS"):
            return f"sh{num}"
        if suffix == "SZ":
            return f"sz{num}"
    # 无后缀: 6/5/9 开头为上交所, 0/3 开头为深交所
    if code_upper.startswith(("6", "5", "9")):
        return f"sh{code_upper}"
    return f"sz{code_upper}"


def fetch_sina_realtime(codes: List[str]) -> Dict[str, Dict]:
    """通过新浪财经 API 批量获取实时行情

    API: https://hq.sinajs.cn/list=sh688041,sz000333
    返回字段(逗号分隔):
      0=名称, 1=今开, 2=昨收, 3=当前价, 4=最高, 5=最低, 8=成交量, 9=成交额
    """
    if not codes:
        return {}
    sina_codes = [to_sina_code(c) for c in codes]
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    try:
        resp = _SINA_SESSION.get(
            url,
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"新浪实时行情 HTTP {resp.status_code}")
            return {}
        text = resp.text
    except Exception as e:
        print(f"新浪实时行情获取失败: {e}")
        return {}

    result = {}
    for orig_code, sina_code in zip(codes, sina_codes):
        # 提取 var hq_str_sh688041="..."; 中的内容
        prefix = f'hq_str_{sina_code}="'
        idx = text.find(prefix)
        if idx < 0:
            continue
        start = idx + len(prefix)
        end = text.find('"', start)
        if end < 0:
            continue
        content = text[start:end]
        if not content or content == "":
            continue
        fields = content.split(",")
        if len(fields) < 6:
            continue
        try:
            name = fields[0]
            open_price = float(fields[1])  # 今开
            prev_close = float(fields[2])  # 昨收
            current = float(fields[3])  # 当前价/收盘价
            high = float(fields[4])
            low = float(fields[5])
        except (ValueError, IndexError):
            continue
        if current <= 0 or prev_close <= 0:
            continue
        change_pct = (current - prev_close) / prev_close * 100
        result[orig_code] = {
            "close": current,
            "prev_close": prev_close,
            "open": open_price,
            "high": high,
            "low": low,
            "change_pct": round(change_pct, 2),
            "source": "sina_realtime",
            "name": name,
        }
    return result


def _build_code_to_cost_map(positions: Dict[str, Any]) -> Dict[str, float]:
    """构建代码 -> 成本价 (est_price) 映射, 用于比例验证"""
    code_to_cost = {}
    for _key, pos in positions.items():
        code = pos.get("code", "")
        if code:
            # 成本价 = 第一次交易开盘价 (est_price, 已被 _apply_positions_snapshot 覆盖)
            cost = pos.get("est_price") or pos.get("actual_avg_cost") or pos.get("avg_cost", 0)
            code_to_cost[code] = float(cost) if cost else 0
    return code_to_cost


def _validate_price_range(code: str, close, cost_price: float) -> bool:
    """验证价格绝对范围和相对成本价比例, 返回 True 表示价格有效"""
    # 绝对范围验证
    code_num = code.split(".")[0]
    is_etf = code_num.startswith(("5", "1")) and len(code_num) == 6
    max_price = 50 if is_etf else 2000  # A股最贵茅台~1500, 2000已留余量
    min_price = 0.1 if is_etf else 0.5
    if close is None or close <= 0:
        return False
    if close > max_price or close < min_price:
        print(f"价格异常 {code}: close={close} (超出范围 {min_price}-{max_price}), 跳过")
        return False

    # 相对成本价比例验证 (防止指数点位冒充股价)
    # 收盘价一般不会超过成本价的 3 倍或低于 0.3 倍
    if cost_price > 0:
        ratio = close / cost_price
        if ratio > 3.0 or ratio < 0.3:
            print(
                f"价格可疑 {code}: close={close} vs cost={cost_price} (比例 {ratio:.2f}x 超出 0.3-3.0), 跳过"
            )
            return False
    return True


def _fetch_price_from_provider(code: str, cost_price: float, data_provider):
    """从 data_provider 获取单个标的的价格数据, 验证后返回价格字典或 None"""
    if not data_provider:
        return None
    market_data = data_provider.get_market_data(code)
    if not market_data:
        return None

    # 优先级: close > last > price > (不用 index_price, 它是指数点位)
    close = market_data.get("close") or market_data.get("last") or market_data.get("price")
    prev_close = market_data.get("prev_close")

    if not _validate_price_range(code, close, cost_price):
        return None

    change_pct = market_data.get("change_pct")
    if change_pct is None and close and prev_close and prev_close > 0:
        change_pct = (close - prev_close) / prev_close * 100

    p_source = market_data.get("source", "unknown")
    return {"close": close, "prev_close": prev_close, "change_pct": change_pct, "source": p_source}


def _get_fallback_prices() -> Dict[str, Dict]:
    """获取预定义的兜底价格 (当 Wind MCP 不可用时的最后防线)

    ⚠ 注意: change_pct 为历史快照时的日内涨跌，已过时。
       报告生成时会自动检测 source='fallback' 并将 daily_pnl 标记为 None，
       data_integrity 标记为 'FALLBACK_PRICE'（而非 'REAL'）。
       价格基于 2026-07-09 持仓快照实际成交均价; 5个新标的(000680等)使用计划价
    """
    return {
        "510300": {"close": 4.89, "prev_close": 4.887, "change_pct": 0.06, "source": "fallback"},
        "510500": {"close": 8.92, "prev_close": 8.897, "change_pct": 0.26, "source": "fallback"},
        "512100": {"close": 3.51, "prev_close": 3.494, "change_pct": 0.46, "source": "fallback"},
        "588000": {"close": 1.0505, "prev_close": 1.0505, "change_pct": 0.0, "source": "fallback"},
        "159915": {"close": 4.05, "prev_close": 4.029, "change_pct": 0.52, "source": "fallback"},
        "515180": {"close": 5.0025, "prev_close": 5.0025, "change_pct": 0.0, "source": "fallback"},
        "688041": {"close": 85.0425, "prev_close": 85.0425, "change_pct": 0.0, "source": "fallback"},
        "300308": {"close": 120.06, "prev_close": 120.06, "change_pct": 0.0, "source": "fallback"},
        "300274": {"close": 45.0225, "prev_close": 45.0225, "change_pct": 0.0, "source": "fallback"},
        "002371": {"close": 350.175, "prev_close": 350.175, "change_pct": 0.0, "source": "fallback"},
        "688017": {"close": 180.09, "prev_close": 180.09, "change_pct": 0.0, "source": "fallback"},
        "600276": {"close": 50.025, "prev_close": 50.025, "change_pct": 0.0, "source": "fallback"},
        "600089": {"close": 25.0125, "prev_close": 25.0125, "change_pct": 0.0, "source": "fallback"},
        "600875": {"close": 29.5, "prev_close": 29.29, "change_pct": 0.71, "source": "fallback"},
        "000425": {"close": 8.5042, "prev_close": 8.5042, "change_pct": 0.0, "source": "fallback"},
        "600406": {"close": 23.0, "prev_close": 22.88, "change_pct": 0.52, "source": "fallback"},
        "600989": {"close": 20.5, "prev_close": 20.39, "change_pct": 0.54, "source": "fallback"},
        "600036": {"close": 38.019, "prev_close": 38.019, "change_pct": 0.0, "source": "fallback"},
        "600900": {"close": 27.0635, "prev_close": 27.0635, "change_pct": 0.0, "source": "fallback"},
        "601088": {"close": 40.7204, "prev_close": 40.7204, "change_pct": 0.0, "source": "fallback"},
        "518880": {"close": 5.8529, "prev_close": 5.8529, "change_pct": 0.0, "source": "fallback"},
        "688981": {"close": 95.0475, "prev_close": 95.0475, "change_pct": 0.0, "source": "fallback"},
        "603019": {"close": 94.4672, "prev_close": 94.4672, "change_pct": 0.0, "source": "fallback"},
        "600219": {"close": 4.1921, "prev_close": 4.1921, "change_pct": 0.0, "source": "fallback"},
        "600019": {"close": 5.6128, "prev_close": 5.6128, "change_pct": 0.0, "source": "fallback"},
        # 2026-07-09 新增 5 标的 (持仓为0, 仅用于价格查询)
        "000680": {"close": 7.50, "prev_close": 7.50, "change_pct": 0.0, "source": "fallback"},
        "000333": {"close": 75.00, "prev_close": 75.00, "change_pct": 0.0, "source": "fallback"},
        "000408": {"close": 35.00, "prev_close": 35.00, "change_pct": 0.0, "source": "fallback"},
        "000975": {"close": 15.00, "prev_close": 15.00, "change_pct": 0.0, "source": "fallback"},
        "002422": {"close": 28.00, "prev_close": 28.00, "change_pct": 0.0, "source": "fallback"},
        # 2026-07-10 新计划 500万 20 标的补充 (auto_trade_plan_500w_2026-2030.json)
        "588080": {"close": 1.05, "prev_close": 1.05, "change_pct": 0.0, "source": "fallback"},  # 科创50ETF易方达
        "512880": {"close": 1.10, "prev_close": 1.10, "change_pct": 0.0, "source": "fallback"},  # 证券ETF国泰
        "510050": {"close": 3.00, "prev_close": 3.00, "change_pct": 0.0, "source": "fallback"},  # 上证50ETF华夏
        "512800": {"close": 1.40, "prev_close": 1.40, "change_pct": 0.0, "source": "fallback"},  # 银行ETF华宝
        "515030": {"close": 1.50, "prev_close": 1.50, "change_pct": 0.0, "source": "fallback"},  # 新能源车ETF华夏
        "512760": {"close": 1.30, "prev_close": 1.30, "change_pct": 0.0, "source": "fallback"},  # 半导体ETF国泰
        "512170": {"close": 0.50, "prev_close": 0.50, "change_pct": 0.0, "source": "fallback"},  # 医疗ETF华宝
        "300033": {"close": 150.00, "prev_close": 150.00, "change_pct": 0.0, "source": "fallback"},  # 同花顺
        "601899": {"close": 18.00, "prev_close": 18.00, "change_pct": 0.0, "source": "fallback"},  # 紫金矿业
        "002281": {"close": 35.00, "prev_close": 35.00, "change_pct": 0.0, "source": "fallback"},  # 光迅科技
        "000901": {"close": 45.00, "prev_close": 45.00, "change_pct": 0.0, "source": "fallback"},  # 国盾量子
    }


def _correct_price_anomalies(
    prices: Dict[str, Dict],
    code_to_cost: Dict[str, float],
    fallback_prices: Dict[str, Dict],
) -> None:
    """价格异常修正: 对无成本价的个股, 如果价格 > 500 且不是 ETF, 使用 fallback"""
    for code, pd in list(prices.items()):
        if pd.get("close") is None:
            continue
        close = pd.get("close")
        code_num = code.split(".")[0]
        is_etf = code_num.startswith(("5", "1")) and len(code_num) == 6
        cost_price = code_to_cost.get(code, 0) or 0
        if cost_price == 0 and not is_etf and close > 500:
            fb = fallback_prices.get(code_num)
            if fb:
                prices[code] = {
                    "close": fb.get("close", close),
                    "prev_close": fb.get("prev_close", pd.get("prev_close")),
                    "change_pct": fb.get("change_pct", pd.get("change_pct")),
                    "source": "fallback_corrected",
                }
                print(f"价格异常修正 {code}: 使用 fallback 价格 close={fb.get('close')}")


def fetch_market_prices(
    positions_data: Dict[str, Any],
    data_provider=None,
    init_data_provider_fn=None,
) -> Dict[str, Dict]:
    """获取所有持仓标的的收盘价格

    价格验证规则 (防止 data_provider 返回指数点位):
      1. 不使用 index_price 字段 (它是指数点位 ~3000-4700, 不是股价)
      2. 优先使用 close / last / price 字段 (个股实际收盘价)
      3. 绝对范围: ETF 0.1-50, 股票 0.5-2000
      4. 相对范围: close 必须在 cost_price 的 0.3x ~ 3x 之间

    Args:
        positions_data: positions.json 加载后的字典
        data_provider: 已初始化的 MarketDataProvider (可选)
        init_data_provider_fn: 可选的回调, 用于延迟初始化 data_provider

    Returns:
        code -> {"close", "prev_close", "change_pct", "source"} 的字典
    """
    # 延迟初始化 data_provider (由调用方提供回调)
    if data_provider is None and init_data_provider_fn is not None:
        try:
            init_data_provider_fn()
        except Exception as e:
            print(f"数据源初始化失败: {e}")

    prices = {}
    positions = positions_data.get("positions", {})

    # 构建代码 -> 成本价 (est_price) 映射, 用于比例验证
    code_to_cost = _build_code_to_cost_map(positions)

    print(f"获取 {len(code_to_cost)} 个标的的收盘价格...")

    # 使用 data_provider 获取实时价格
    for code, cost_price in code_to_cost.items():
        try:
            price_data = _fetch_price_from_provider(code, cost_price, data_provider)
            if price_data:
                prices[code] = price_data
        except Exception as e:
            print(f"获取 {code} 价格失败: {e}")

    # 新浪实时行情补充 (当 Wind MCP / iFinD MCP 都失败时)
    # 批量获取所有未拿到价格的标的
    missing_codes = [c for c in code_to_cost.keys() if c not in prices]
    if missing_codes:
        sina_prices = fetch_sina_realtime(missing_codes)
        for code, sp in sina_prices.items():
            prices[code] = sp
        if sina_prices:
            print(f"新浪实时行情获取成功: {len(sina_prices)} / {len(missing_codes)} 个标的")

    # 使用预定义的兜底价格（当Wind MCP不可用时的最后防线）
    fallback_prices = _get_fallback_prices()

    # 合并价格数据，避免覆盖实时数据
    for code, fb_price in fallback_prices.items():
        if code not in prices:
            prices[code] = fb_price

    # 价格异常修正：对无成本价的个股，如果价格 > 500 且不是 ETF，可能是后复权价/指数点位，使用 fallback
    _correct_price_anomalies(prices, code_to_cost, fallback_prices)

    return prices


def assess_data_source_health(pnl_data: Dict) -> Dict[str, Any]:
    """评估当前报告使用的数据源健康状态"""
    details = pnl_data.get("details", [])
    snapshot_count = sum(1 for d in details if d.get("calc_mode") == "snapshot")
    fallback_count = sum(1 for d in details if d.get("data_integrity") == "FALLBACK_PRICE")
    no_data_count = sum(1 for d in details if d.get("data_integrity") == "NO_MARKET_DATA")
    real_count = sum(1 for d in details if d.get("data_integrity") == "REAL")

    total = len(details) if details else 1
    snapshot_ratio = snapshot_count / total if total else 0
    fallback_ratio = fallback_count / total if total else 0
    no_data_ratio = no_data_count / total if total else 0
    real_ratio = real_count / total if total else 0

    if no_data_ratio > 0.5:
        status = "NOSIGNAL_MAJORITY"  # 多数标的数据不可用 — 报告不可信
    elif no_data_ratio > 0:
        status = "NOSIGNAL_PARTIAL"  # 部分标的数据缺失
    elif fallback_ratio > 0.5:
        status = "FALLBACK_HEAVY"  # 多数使用fallback价格 — 日内涨跌不可信
    elif real_ratio == 1.0:
        status = "HEALTHY"  # 全部真实数据（含plan模式但数据源为实时行情）
    elif real_ratio >= 0.8:
        status = "HEALTHY"
    elif snapshot_ratio < 0.5:
        status = "PARTIAL_SNAPSHOT"
    else:
        status = "HEALTHY"

    return {
        "status": status,
        "snapshot_ratio": round(snapshot_ratio, 2),
        "fallback_ratio": round(fallback_ratio, 2),
        "no_data_ratio": round(no_data_ratio, 2),
        "real_ratio": round(real_ratio, 2),
        "snapshot_count": snapshot_count,
        "fallback_count": fallback_count,
        "no_data_count": no_data_count,
        "real_count": real_count,
        "total_positions": total,
    }
