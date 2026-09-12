"""
把长江电力 (600900) 加入 500万建仓计划，并重新生成 7月6日计划文件。

该脚本从 JSON 文件加载现有计划，缩放已有标的权重以腾出 5% 给长江电力，
然后写入更新后的 JSON。 不再依赖 `_archive_dead_code` 中的 Python 生成器。
"""

import json
import os
import shutil
import sys

from utils.datetime_utils import now_bj

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PLAN_JSON = os.path.join(_BASE_DIR, "500万建仓计划_20260706.json")
_PLAN_MD = os.path.join(_BASE_DIR, "500万建仓计划_20260706.md")


def _load_json() -> dict:
    with open(_PLAN_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_json(data: dict) -> None:
    with open(_PLAN_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _scale_existing_weights(data: dict, scale: float) -> None:
    target_portfolio = data.setdefault("target_portfolio", {})
    position_plan = data.setdefault("position_plan", {})

    for _code, cfg in list(target_portfolio.items()):
        cfg["weight"] = round(float(cfg.get("weight", 0)) * scale, 4)
        cfg["target_amount"] = round(5_000_000 * cfg["weight"], 0)
        cfg["total_shares"] = int(
            cfg["target_amount"] / cfg["est_price"] / cfg.get("lots", 100)
        ) * cfg.get("lots", 100)
        cfg["actual_amount"] = round(cfg["total_shares"] * cfg["est_price"], 0)

    for code, cfg in list(position_plan.items()):
        cfg["target_weight"] = round(float(cfg.get("target_weight", 0)) * scale, 4)
        cfg["target_amount"] = round(5_000_000 * cfg["target_weight"], 0)
        cfg["est_price"] = target_portfolio.get(code, {}).get(
            "est_price", cfg.get("est_price")
        )
        lots = target_portfolio.get(code, {}).get("lots", 100)
        cfg["total_shares"] = int(cfg["target_amount"] / cfg["est_price"] / lots) * lots
        cfg["actual_amount"] = round(cfg["total_shares"] * cfg["est_price"], 0)

        new_phases = []
        cumulative_ratio = 0.0
        for phase in cfg.get("phases", []):
            cumulative_ratio = round(
                cumulative_ratio + float(phase.get("capital_ratio", 0)), 3
            )
            phase["target_amount"] = round(
                cfg["target_amount"] * float(phase.get("capital_ratio", 0)), 0
            )
            phase["shares"] = (
                int(phase["target_amount"] / cfg["est_price"] / lots) * lots
            )
            phase["actual_amount"] = round(phase["shares"] * cfg["est_price"], 0)
            phase["cumulative_ratio"] = cumulative_ratio
            new_phases.append(phase)
        cfg["phases"] = new_phases


def _add_yangtze_power(data: dict) -> None:
    target_portfolio = data.setdefault("target_portfolio", {})
    position_plan = data.setdefault("position_plan", {})

    target_portfolio["sh600900"] = {
        "name": "长江电力",
        "type": "个股",
        "risk": "低",
        "style": "防御",
        "weight": 0.05,
        "est_price": 28.0,
        "lots": 100,
        "reason": "水电龙头，高股息+防御属性，组合稳定器",
    }

    est_price = 28.0
    lots = 100
    target_amount = round(5_000_000 * 0.05, 0)
    total_shares = int(target_amount / est_price / lots) * lots
    actual_amount = round(total_shares * est_price, 0)

    position_plan["sh600900"] = {
        "code": "600900",
        "name": "长江电力",
        "type": "个股",
        "risk": "低",
        "style": "防御",
        "target_weight": 0.05,
        "target_amount": target_amount,
        "est_price": est_price,
        "total_shares": total_shares,
        "actual_amount": actual_amount,
        "reason": "水电龙头，高股息+防御属性，组合稳定器",
        "stop_loss": -0.05,
        "phases": [
            {
                "phase": 1,
                "name": "第一阶段-底仓建立",
                "start": "2026-07-06",
                "capital_ratio": 0.35,
                "target_amount": round(target_amount * 0.35, 0),
                "shares": int(round(target_amount * 0.35, 0) / est_price / lots) * lots,
                "actual_amount": round(
                    int(round(target_amount * 0.35, 0) / est_price / lots)
                    * lots
                    * est_price,
                    0,
                ),
                "cumulative_ratio": 0.35,
                "code": "sh600900",
            },
            {
                "phase": 2,
                "name": "第二阶段-配置完善",
                "start": "2026-07-20",
                "capital_ratio": 0.30,
                "target_amount": round(target_amount * 0.30, 0),
                "shares": int(round(target_amount * 0.30, 0) / est_price / lots) * lots,
                "actual_amount": round(
                    int(round(target_amount * 0.30, 0) / est_price / lots)
                    * lots
                    * est_price,
                    0,
                ),
                "cumulative_ratio": 0.65,
                "code": "sh600900",
            },
            {
                "phase": 3,
                "name": "第三阶段-防御补充",
                "start": "2026-08-10",
                "capital_ratio": 0.20,
                "target_amount": round(target_amount * 0.20, 0),
                "shares": int(round(target_amount * 0.20, 0) / est_price / lots) * lots,
                "actual_amount": round(
                    int(round(target_amount * 0.20, 0) / est_price / lots)
                    * lots
                    * est_price,
                    0,
                ),
                "cumulative_ratio": 0.85,
                "code": "sh600900",
            },
            {
                "phase": 4,
                "name": "第四阶段-最终调整",
                "start": "2026-09-01",
                "capital_ratio": 0.15,
                "target_amount": round(target_amount * 0.15, 0),
                "shares": int(round(target_amount * 0.15, 0) / est_price / lots) * lots,
                "actual_amount": round(
                    int(round(target_amount * 0.15, 0) / est_price / lots)
                    * lots
                    * est_price,
                    0,
                ),
                "cumulative_ratio": 1.0,
                "code": "sh600900",
            },
        ],
    }


def _update_metadata(data: dict) -> None:
    data["metadata"]["target_count"] = len(data.get("target_portfolio", {}))
    data["metadata"]["generated_at"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")


def _update_style_summary(data: dict) -> None:
    style_summary = {}
    for code, info in data.get("position_plan", {}).items():
        style = info.get("style", "其他")
        if style not in style_summary:
            style_summary[style] = {
                "amount": 0,
                "weight": 0,
                "codes": [],
                "risk_distribution": {},
            }
        style_summary[style]["amount"] += info.get("actual_amount", 0)
        style_summary[style]["weight"] += info.get("target_weight", 0)
        style_summary[style]["codes"].append(code)
        risk = info.get("risk", "中")
        style_summary[style]["risk_distribution"][risk] = style_summary[style][
            "risk_distribution"
        ].get(risk, 0) + info.get("actual_amount", 0)
    data["style_summary"] = style_summary


def _update_phase_summary(data: dict) -> None:
    phase_summary = []
    for i, phase in enumerate(
        [
            {
                "phase": 1,
                "name": "第一阶段-底仓建立",
                "start": "2026-07-06",
                "duration_days": 10,
                "capital_ratio": 0.35,
                "strategy": "优先建立高端制造核心仓位（科创50、半导体、高端装备），同步配置黄金ETF和中国神华作为稳定器",
            },
            {
                "phase": 2,
                "name": "第二阶段-配置完善",
                "start": "2026-07-20",
                "duration_days": 15,
                "capital_ratio": 0.30,
                "strategy": "利用月中波动窗口分批加仓，关注大宗商品价格趋势，择机增加资源板块",
            },
            {
                "phase": 3,
                "name": "第三阶段-防御补充",
                "start": "2026-08-10",
                "duration_days": 15,
                "capital_ratio": 0.20,
                "strategy": "结合中报披露窗口，优选医药板块回调时点建仓，配置债券类资产",
            },
            {
                "phase": 4,
                "name": "第四阶段-最终调整",
                "start": "2026-09-01",
                "duration_days": 20,
                "capital_ratio": 0.15,
                "strategy": "审视前三阶段执行偏差，补齐偏离标的，配置短融ETF管理剩余现金",
            },
        ]
    ):
        phase_capital = 5_000_000 * phase["capital_ratio"]
        phase_assets = []
        total_actual = 0
        for code, info in data.get("position_plan", {}).items():
            ph = info.get("phases", [])[i] if i < len(info.get("phases", [])) else None
            if ph:
                phase_assets.append(
                    {
                        "code": code,
                        "name": info.get("name", ""),
                        "shares": ph.get("shares", 0),
                        "amount": ph.get("actual_amount", 0),
                    }
                )
                total_actual += ph.get("actual_amount", 0)
        phase_summary.append(
            {
                "phase": phase["phase"],
                "name": phase["name"],
                "start": phase["start"],
                "duration_days": phase["duration_days"],
                "capital_ratio": phase["capital_ratio"],
                "capital_amount": round(phase_capital, 0),
                "asset_count": len([a for a in phase_assets if a["shares"] > 0]),
                "total_actual": round(total_actual, 0),
                "strategy": phase["strategy"],
                "assets": phase_assets,
            }
        )
    data["phase_summary"] = phase_summary


def main() -> int:
    if not os.path.isfile(_PLAN_JSON):
        print(f"❌ 计划文件不存在: {_PLAN_JSON}")
        return 1

    print("=" * 70)
    print("加入长江电力到 2026 交易计划")
    print("=" * 70)

    data = _load_json()

    print("📌 现有标的数量:", len(data.get("target_portfolio", {})))
    _scale_existing_weights(data, scale=0.95)
    _add_yangtze_power(data)
    _update_metadata(data)
    _update_style_summary(data)
    _update_phase_summary(data)

    _save_json(data)
    print("✅ 已更新 JSON:", _PLAN_JSON)
    print("📌 最新标的数量:", len(data.get("target_portfolio", {})))

    if os.path.isfile(_PLAN_MD):
        backup_md = _PLAN_MD + ".bak_" + now_bj().strftime("%Y%m%d%H%M%S")
        shutil.copy2(_PLAN_MD, backup_md)
        print("🗂️ 旧 Markdown 已备份:", backup_md)

    print("⚠️  Markdown 报告建议用生成器重导，或直接使用更新后的 JSON 执行 workflow")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
