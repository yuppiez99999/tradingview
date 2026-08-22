"""supply_chain_risk — 供应链综合风险智能决策系统

源自 supply_chain_risk_system v1.0.0，基于金融风控 + 能源成本双领域的
供应商风险评估。混合架构（规则引擎 70% + ML 30%），模型已预训练，即装即用。

主要 API:
    evaluate_supplier  — 评估单个供应商（金融+能源双维度）
    evaluate_batch     — 批量评估（CSV 输入/输出）
    load_engine        — 加载综合决策引擎

评分体系:
    80-100  ✅ 通过   — 最高信用额度
    65-79   🟡 关注   — 加强尽调
    50-64   🟠 限制   — 限制交易金额
    0-49    🔴 �拒绝   — 暂缓合作

Usage:
    from utils.supply_chain_risk import evaluate_supplier
    result = evaluate_supplier({
        'category': 'banking', 'data_type': 'credit_score',
        'data_quality_score': 97.5, 'completeness': 98.0,
        'accuracy': 97.8, 'timeliness': 96.5, 'compliance_score': 100.0,
    })
"""
from __future__ import annotations

from .predict import evaluate_batch, evaluate_supplier, load_engine

__all__ = ["evaluate_supplier", "evaluate_batch", "load_engine"]
