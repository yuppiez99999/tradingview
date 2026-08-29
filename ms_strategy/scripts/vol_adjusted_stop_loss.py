"""
波动率调整止损止盈生成器 v1.0 — P0 修复

替代"一刀切"的止损止盈参数, 根据每只股票的波动率特征动态调整:

止损 = entry - k × σ_60d × √(holding_days)
止盈 = entry + k_profit × σ_60d × √(holding_days)

高波动股票 (如海光信息 σ=35%) → 自动放宽止损
低波动股票 (如长江电力 σ=15%) → 自动收紧止损

使用方式:
    python vol_adjusted_stop_loss.py
    python vol_adjusted_stop_loss.py --output config/stop_loss_vol_adjusted.yaml
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import yaml

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('vol_stop_loss')


def to_native(obj):
    """递归把 numpy 类型转换为原生 Python 类型, 使 yaml.safe_dump 可用。

    原实现用 yaml.dump 直接序列化含 numpy 标量(如 np.float64)的规则,
    会写出 !!python/object/apply:numpy.core.multiarray.scalar 标签,
    导致 yaml.safe_load 的消费者崩溃, 且强依赖特定 numpy 内部路径。
    """
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_native(v) for v in obj]
    return obj


# ============================================================
# 持仓标的
# ============================================================
PORTFOLIO_STOCKS = [
    ("510300.SH", "沪深300ETF", "核心宽基"),
    ("510500.SH", "中证500ETF", "核心宽基"),
    ("512100.SH", "中证1000ETF", "核心宽基"),
    ("588000.SH", "科创50ETF", "核心宽基"),
    ("159915.SZ", "创业板ETF", "核心宽基"),
    ("688041.SH", "海光信息", "科技成长"),
    ("300308.SZ", "中际旭创", "科技成长"),
    ("300274.SZ", "阳光电源", "科技成长"),
    ("002371.SZ", "北方华创", "科技成长"),
    ("688017.SH", "中芯国际", "科技成长"),
    ("600276.SH", "恒瑞医药", "科技成长"),
    ("603019.SH", "中科曙光", "科技成长"),
    ("600089.SH", "特变电工", "高端制造"),
    ("600875.SH", "东方电气", "高端制造"),
    ("000425.SZ", "徐工机械", "高端制造"),
    ("600406.SH", "国电南瑞", "高端制造"),
    ("600989.SH", "宝武股份", "高端制造"),
    ("601088.SH", "中国神华", "防御红利"),
    ("518880.SH", "黄金ETF", "黄金"),
]

# 板块风险系数 (k 值)
SECTOR_K = {
    "核心宽基": {"stop_k": 2.5, "profit_k": 5.0, "max_holding_days": 90},
    "科技成长": {"stop_k": 3.0, "profit_k": 6.0, "max_holding_days": 120},
    "高端制造": {"stop_k": 2.5, "profit_k": 5.0, "max_holding_days": 90},
    "防御红利": {"stop_k": 2.0, "profit_k": 4.0, "max_holding_days": 120},
    "黄金":    {"stop_k": 1.5, "profit_k": 3.0, "max_holding_days": 180},
}


def get_volatility_from_ifind(code: str) -> Optional[float]:
    """从 iFinD 获取 60 日年化波动率"""
    try:
        import importlib.util
        skill_dir = os.path.join(os.path.expanduser("~"), ".trae", "skills", "ifind-finance-data")
        call_path = os.path.join(skill_dir, "call.py")
        spec = importlib.util.spec_from_file_location("ifind_call", call_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        pure_code = code.split('.')[0]
        result = mod.call("stock", "get_stock_history", {
            "code": pure_code,
            "indicators": "CLOSE",
            "start_date": (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d"),
            "end_date": datetime.now().strftime("%Y-%m-%d"),
        })

        if isinstance(result, dict) and result.get("data"):
            data = result["data"]
            if data.get("rows"):
                closes = [float(r[0]) for r in data["rows"] if r[0]]
                if len(closes) > 20:
                    returns = np.diff(closes) / closes[:-1]
                    vol_daily = np.std(returns)
                    vol_annual = vol_daily * np.sqrt(252)
                    return vol_annual
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.debug(f"iFinD 获取 {code} 波动率失败: {e}")
    return None


def get_volatility_from_wind(code: str) -> Optional[float]:
    """从 Wind MCP 获取波动率"""
    try:
        # 跨平台: 11_量化策略 是独立项目, 路径通过环境变量覆盖 (Mac 上指向实际位置)
        env_file = os.environ.get("QUANT11_ENV_FILE", r"E:\各种PY程序\11_量化策略\.env")
        wind_dir = os.environ.get("WIND_MCP_SKILL_DIR", r"C:\Users\Administrator\.agents\skills\wind-mcp-skill")
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("WIND_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

        import subprocess
        code_parts = code.split('.')
        wind_code = f"{code_parts[0]}.{code_parts[1]}"

        params = json.dumps({
            "windcode": wind_code,
            "begin_date": (datetime.now() - timedelta(days=120)).strftime("%Y%m%d"),
            "end_date": datetime.now().strftime("%Y%m%d"),
            "period": "10",
            "aftime": "0",
        }, ensure_ascii=False)

        env = os.environ.copy()
        env["WIND_API_KEY"] = api_key

        result = subprocess.run(
            ["node", "scripts/cli.mjs", "call", "stock_data", "get_stock_kline", params],
            cwd=wind_dir, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60, env=env,
        )

        if result.returncode == 0 and result.stdout.strip():
            stdout = result.stdout.strip()
            if "#< CLIXML" in stdout:
                stdout = stdout.split("\n")[0]
            outer = json.loads(stdout)
            text = (outer.get("content", [{}])[0] or {}).get("text", "")
            if text:
                inner = json.loads(text)
                data = inner.get("data", {})
                rows = data.get("rows", [])
                columns = [c["name"] for c in data.get("columns", [])]
                col_map = {c: i for i, c in enumerate(columns)}
                closes = []
                for row in rows:
                    val = row[col_map.get("MATCH", 0)]
                    if isinstance(val, str) and ("INVALID" in val.upper() or val == ""):
                        continue
                    closes.append(float(val))
                if len(closes) > 20:
                    returns = np.diff(closes) / closes[:-1]
                    vol_daily = np.std(returns)
                    vol_annual = vol_daily * np.sqrt(252)
                    return vol_annual
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.debug(f"Wind 获取 {code} 波动率失败: {e}")
    return None


def estimate_volatility(code: str, name: str, sector: str) -> float:
    """获取股票波动率, 多源回退"""
    # 1. 尝试 iFinD
    vol = get_volatility_from_ifind(code)
    if vol and vol > 0:
        return vol

    # 2. 尝试 Wind MCP
    vol = get_volatility_from_wind(code)
    if vol and vol > 0:
        return vol

    # 3. 回退: 按板块估算
    fallback = {
        "核心宽基": 0.18,
        "科技成长": 0.30,
        "高端制造": 0.22,
        "防御红利": 0.15,
        "黄金": 0.12,
    }
    vol = fallback.get(sector, 0.20)
    logger.warning(f"{name} ({code}) 使用板块默认波动率: {vol:.1%}")
    return vol


def compute_vol_adjusted_stop_loss(entry_price: float,
                                     annual_vol: float,
                                     sector: str,
                                     holding_days: int = 20) -> dict:
    """计算波动率调整止损止盈

    止损 = entry - k_stop × σ_daily × √(holding_days)
    止盈 = entry + k_profit × σ_daily × √(holding_days)

    其中:
    - σ_daily = annual_vol / √252
    - k_stop: 板块风险系数 (科技 3.0, 防御 2.0)
    - k_profit: 止盈系数 (通常 = 2 × k_stop)
    - holding_days: 预期持仓天数
    """
    config = SECTOR_K.get(sector, {"stop_k": 2.5, "profit_k": 5.0, "max_holding_days": 90})

    k_stop = config["stop_k"]
    k_profit = config["profit_k"]
    config["max_holding_days"]

    # 日波动率
    sigma_daily = annual_vol / np.sqrt(252)

    # 时间调整
    time_factor = np.sqrt(holding_days)

    # 止损止盈 (百分比)
    stop_loss_pct = -(k_stop * sigma_daily * time_factor)
    take_profit_pct = k_profit * sigma_daily * time_factor

    # 限制极端值
    stop_loss_pct = max(stop_loss_pct, -0.20)  # 最大止损 20%
    take_profit_pct = min(take_profit_pct, 0.50)  # 最大止盈 50%

    # ATR 近似 (日波动率 × 价格 × 1.5)
    atr_approx = entry_price * sigma_daily * 1.5

    # 价格计算
    stop_loss_price = entry_price * (1 + stop_loss_pct)
    take_profit_price = entry_price * (1 + take_profit_pct)

    return {
        "entry_price": entry_price,
        "annual_volatility": round(annual_vol, 4),
        "daily_volatility": round(sigma_daily, 6),
        "stop_k": k_stop,
        "profit_k": k_profit,
        "holding_days": holding_days,
        "stop_loss_pct": round(stop_loss_pct * 100, 2),
        "stop_loss_price": round(stop_loss_price, 4),
        "take_profit_pct": round(take_profit_pct * 100, 2),
        "take_profit_price": round(take_profit_price, 4),
        "atr_approx": round(atr_approx, 4),
        "atr_stop_loss_price": round(entry_price - k_stop * atr_approx, 4),
        "atr_stop_loss_pct": round(-(k_stop * atr_approx / entry_price) * 100, 2),
        "active_stop_loss_pct": round(stop_loss_pct * 100, 2),
        "active_stop_loss_price": round(stop_loss_price, 4),
        "trailing_stop": True,
        "vol_source": "computed",
    }


def generate_vol_adjusted_rules(base_prices: Optional[dict] = None) -> dict:
    """生成全部持仓的波动率调整止损规则

    Args:
        base_prices: {code: price} 基准价格, 如为 None 则从数据源获取

    Returns:
        完整的 YAML 配置字典
    """
    rules = {
        "version": "6.0-vol-adjusted",
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "notes": "波动率调整止损止盈 — 替代一刀切参数, 根据各股波动率自动调整",
        "global_settings": {
            "method": "volatility_adjusted",
            "formula": "stop = entry - k × σ_daily × √(holding_days)",
            "enable_trailing_stop": True,
            "rebalance_on_vol_change": True,
            "vol_recalc_frequency": "monthly",
        },
        "assets": []
    }

    for code, name, sector in PORTFOLIO_STOCKS:
        logger.info(f"  处理 {name} ({code})...")

        # 获取基准价格
        if base_prices and code in base_prices:
            entry_price = base_prices[code]
        else:
            # 从现有 stop_loss_rules_auto.yaml 读取
            entry_price = 10.0  # 默认值, 会被覆盖

        # 获取波动率
        annual_vol = estimate_volatility(code, name, sector)

        # 计算止损止盈
        config = SECTOR_K.get(sector, {"stop_k": 2.5, "profit_k": 5.0, "max_holding_days": 90})
        holding_days = min(config["max_holding_days"] // 4, 20)  # 默认 20 天

        result = compute_vol_adjusted_stop_loss(
            entry_price=entry_price,
            annual_vol=annual_vol,
            sector=sector,
            holding_days=holding_days,
        )

        result["code"] = code
        result["name"] = name
        result["sector"] = sector
        result["risk_level"] = "high" if annual_vol > 0.25 else ("medium" if annual_vol > 0.18 else "low")
        result["position_weight"] = 0.04  # 默认, 由 portfolio.yaml 覆盖
        result["monitoring_indicators"] = ["rsi", "macd", "boll"]
        result["notes"] = (f"波动率调整: σ_annual={annual_vol:.1%}, "
                          f"k_stop={result['stop_k']}, k_profit={result['profit_k']}, "
                          f"持仓={holding_days}天")

        rules["assets"].append(result)

        logger.info(f"    σ={annual_vol:.1%}, 止损={result['stop_loss_pct']:.1f}%, "
                    f"止盈={result['take_profit_pct']:.1f}%")

    return rules


def main():
    logger.info("=" * 70)
    logger.info("波动率调整止损止盈生成器 v1.0")
    logger.info("=" * 70)

    # 从现有配置读取基准价格
    existing_config_path = os.environ.get("QUANT11_STOPLOSS_CONFIG", r"E:\各种PY程序\11_量化策略\config\stop_loss_rules_auto.yaml")
    base_prices = {}
    if os.path.exists(existing_config_path):
        with open(existing_config_path, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
        if existing and "assets" in existing:
            for asset in existing["assets"]:
                code = asset.get("code", "")
                base_price = asset.get("base_price", 0)
                if base_price > 0:
                    base_prices[code] = base_price

    # 生成规则
    rules = generate_vol_adjusted_rules(base_prices)

    # 对比表
    logger.info("\n" + "=" * 70)
    logger.info("波动率调整止损止盈对比表")
    logger.info("=" * 70)
    logger.info(f"{'代码':<14} {'名称':<10} {'年化σ':>8} {'止损%':>8} {'止盈%':>8} {'旧止损%':>8} {'改善':>8}")
    logger.info("─" * 70)

    # 读取旧配置对比
    old_stops = {}
    if os.path.exists(existing_config_path):
        with open(existing_config_path, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
        if existing and "assets" in existing:
            for asset in existing["assets"]:
                old_stops[asset["code"]] = asset.get("stop_loss_pct", 0)

    for asset in rules["assets"]:
        code = asset["code"]
        name = asset["name"]
        vol = asset["annual_volatility"]
        new_stop = asset["stop_loss_pct"]
        new_profit = asset["take_profit_pct"]
        old_stop = old_stops.get(code, -12.0)
        diff = new_stop - old_stop
        diff_str = f"{diff:+.1f}%" if diff != 0 else "—"
        logger.info(f"{code:<14} {name:<10} {vol:>7.1%} {new_stop:>7.1f}% {new_profit:>7.1f}% {old_stop:>7.1f}% {diff_str:>8}")

    logger.info("─" * 70)
    logger.info(f"共 {len(rules['assets'])} 只标的\n")

    # 保存 (写入本子项目 ms_strategy/config/, 不再写到其他项目目录)
    output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "stop_loss_vol_adjusted.yaml")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(to_native(rules), f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"配置已保存: {output_path}")

    # 同时保存 JSON (写入本子项目 ms_strategy/reports/)
    json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "stop_loss_vol_adjusted.json")
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON 报告: {json_path}")


if __name__ == "__main__":
    main()
