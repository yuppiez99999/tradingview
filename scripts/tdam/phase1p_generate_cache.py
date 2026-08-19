"""Phase 1' 盘后离线记忆增强作业 — 生成专家 prompt 上下文缓存.

盘后 (15:30 后) 调用 TDAM REST 检索相关记忆, 保存为本地缓存文件。
盘中决策只读本地缓存, 不实时调 TDAM (符合"交易核心链路不依赖外部服务"硬约束)。

缓存路径: reports/tdam_cache/expert_context_YYYY-MM-DD.json

用法:
    # 盘后作业 (默认今天)
    py -3.8 scripts/tdam/phase1p_generate_cache.py

    # 指定日期
    py -3.8 scripts/tdam/phase1p_generate_cache.py --date 2026-08-06

    # 离线模式 (不调 TDAM, 仅读已有缓存)
    py -3.8 scripts/tdam/phase1p_generate_cache.py --offline

    # 指定 TDAM 地址
    py -3.8 scripts/tdam/phase1p_generate_cache.py --base-url http://192.168.1.100:8125

关联文档: TDAM_cairn_对接方案.md Phase 1'
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 加载 .env (含 TDAM_TEAM_ID/TDAM_AGENT_ID/TDAM_BASE_URL, 2026-08-07 固化)
try:
    from utils.env_loader import load_dotenv

    load_dotenv(env_path=str(PROJECT_ROOT / ".env"))
except ImportError:
    pass

import os  # noqa: E402

from utils.tdam_client import TDAMClient, TDAMConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tdam_phase1p")

CACHE_DIR = PROJECT_ROOT / "reports" / "tdam_cache"

# ============================================================
# 六专家检索查询定义
# ============================================================
# 每个专家需要检索的历史结论和知识
# 优先级: 估值(22%) / 风险(22%) / 宏观(10%) 最吃历史结论
EXPERT_QUERIES: list[dict[str, Any]] = [
    # 估值专家 (22% 权重)
    {
        "expert": "valuation",
        "weight": 0.22,
        "queries": [
            "估值定价框架 DCF 方法",
            "历史估值分位 同业比较",
            "PE PB 估值区间 判断",
        ],
        "asset_type": "wiki",
    },
    # 风险专家 (22% 权重)
    {
        "expert": "risk",
        "weight": 0.22,
        "queries": [
            "风险预算 回撤控制 最大回撤限制",
            "Kill Switch 熔断机制 风控阈值",
            "Shadow Account fail-fast 回撤触发",
        ],
        "asset_type": "wiki",
    },
    # 宏观专家 (10% 权重)
    {
        "expert": "macro",
        "weight": 0.10,
        "queries": [
            "市场 Regime 判断 进攻防守切换",
            "VIX 波动率 Regime 权重调整",
            "板块轮动 资金流向 主线识别",
        ],
        "asset_type": "wiki",
    },
    # 因子专家
    {
        "expert": "factor",
        "weight": 0.15,
        "queries": [
            "因子 IC_IR 信息系数 反向信号",
            "GNN 供应链因子 Gate1 结论",
            "气象因子引擎 接口 天气数据",
        ],
        "asset_type": "wiki",
    },
    # 策略专家
    {
        "expert": "strategy",
        "weight": 0.15,
        "queries": [
            "V9 Regime-Specific LGB 模型基线",
            "IC 加权组合 因子信号方向",
            "walk-forward 回测 PurgedKFold",
        ],
        "asset_type": "wiki",
    },
    # 自我进化框架
    {
        "expert": "evolution",
        "weight": 0.16,
        "queries": [
            "自我进化框架 观察期 评估样本",
            "EvolutionOrchestrator 决策日志",
            "VolRegimeWeighter 波动率 Regime",
        ],
        "asset_type": "wiki",
    },
]


def generate_expert_cache(
    client: TDAMClient,
    date_str: str,
) -> dict[str, Any]:
    """为六专家生成记忆检索缓存.

    Args:
        client: TDAM 客户端
        date_str: 日期字符串 (YYYY-MM-DD)

    Returns:
        缓存数据字典 (含每个专家的检索结果)
    """
    cache_data: dict[str, Any] = {
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "tdam_base_url": client.config.base_url,
        "experts": {},
        "stats": {
            "total_queries": 0,
            "success_count": 0,
            "degraded_count": 0,
            "total_items": 0,
        },
    }

    for expert_config in EXPERT_QUERIES:
        expert_name = expert_config["expert"]
        queries = expert_config["queries"]
        asset_type = expert_config["asset_type"]
        weight = expert_config["weight"]

        expert_cache: dict[str, Any] = {
            "weight": weight,
            "queries": [],
            "success": True,
            "items_count": 0,
        }

        for query in queries:
            cache_data["stats"]["total_queries"] += 1

            result = client.search_memory(
                query=query,
                asset_type=asset_type,
                top_k=3,
            )

            query_result = {
                "query": query,
                "asset_type": asset_type,
                "success": result.success,
                "degraded": result.degraded,
                "items": result.items if result.success else [],
                "items_count": result.total,
                "latency_ms": round(result.latency_ms, 1),
                "error": result.error,
            }
            expert_cache["queries"].append(query_result)

            if result.success:
                cache_data["stats"]["success_count"] += 1
                cache_data["stats"]["total_items"] += result.total
            else:
                cache_data["stats"]["degraded_count"] += 1
                if result.degraded:
                    expert_cache["success"] = False

            # 保存单个查询的缓存 (供 save_cache 用)
            client.save_cache(query, result, date_str)

        expert_cache["items_count"] = sum(
            q["items_count"] for q in expert_cache["queries"]
        )
        cache_data["experts"][expert_name] = expert_cache

        status = "OK" if expert_cache["success"] else "DEGRADED"
        logger.info(
            "  [%s] %s: %d queries, %d items (%s)",
            expert_name, status,
            len(expert_cache["queries"]),
            expert_cache["items_count"],
            f"weight={weight:.0%}",
        )

    return cache_data


def main() -> None:
    """主入口."""
    parser = argparse.ArgumentParser(
        description="Phase 1' 盘后离线记忆增强作业 — 生成专家 prompt 上下文缓存",
    )
    parser.add_argument(
        "--date", "-d",
        default="",
        help="日期 (YYYY-MM-DD, 默认今天)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TDAM_BASE_URL", "http://127.0.0.1:8420"),
        help="TDAM 服务地址 (默认: $TDAM_BASE_URL 或 http://127.0.0.1:8420)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="离线模式 (不调 TDAM, 仅读已有缓存)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="请求超时秒数 (默认: 15)",
    )
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    logger.info("=== Phase 1' 盘后记忆增强作业 ===")
    logger.info("日期: %s", date_str)
    logger.info("TDAM 地址: %s", args.base_url)
    logger.info("离线模式: %s", args.offline)
    logger.info("")

    # 创建客户端
    config = TDAMConfig(
        base_url=args.base_url,
        timeout=args.timeout,
        offline=args.offline,
        cache_dir=CACHE_DIR,
    )
    client = TDAMClient(config)

    # 健康检查 (非离线模式)
    if not args.offline:
        logger.info("TDAM 健康检查...")
        if not client.health_check():
            logger.warning(
                "TDAM 不可达 (%s), 切换降级模式 (返回空结果)",
                args.base_url,
            )
        else:
            logger.info("TDAM 健康检查通过 ✓")
    logger.info("")

    # 生成缓存
    logger.info("开始检索六专家记忆...")
    cache_data = generate_expert_cache(client, date_str)

    # 写入汇总缓存文件
    cache_dir = CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"expert_context_{date_str}.json"
    cache_path.write_text(
        json.dumps(cache_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 输出统计
    stats = cache_data["stats"]
    logger.info("")
    logger.info("=== 缓存生成完成 ===")
    logger.info("总查询数: %d", stats["total_queries"])
    logger.info("成功: %d | 降级: %d", stats["success_count"], stats["degraded_count"])
    logger.info("总检索条目: %d", stats["total_items"])
    logger.info("缓存文件: %s (%.1f KB)", cache_path, cache_path.stat().st_size / 1024)

    if stats["degraded_count"] > 0:
        logger.warning(
            "⚠️ %d 个查询降级 (TDAM 不可用或 Feature Flag 关闭), "
            "盘中决策将使用空缓存或昨日缓存",
            stats["degraded_count"],
        )


if __name__ == "__main__":
    main()
