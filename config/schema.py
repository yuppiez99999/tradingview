"""
数据字段映射 Schema — 统一所有模块的数据契约

v5.9 核心问题：positions.json 使用 avg_cost，但代码期望 cost
本文件统一管理字段映射，所有模块必须通过此文件读取数据
"""
from __future__ import annotations

from typing import Any


class FieldMapping:
    POSITION = {
        'shares': ['shares'],
        'cost': ['avg_cost', 'cost', 'avgCost', 'average_cost'],
        'category': ['category', 'sector', 'type'],
        'target_weight': ['target_weight', 'weight', 'targetWeight'],
        'name': ['name', 'stock_name', 'display_name'],
    }

    PORTFOLIO = {
        'positions': ['positions'],
        'cash': ['cash', 'cash_balance', 'available_cash'],
        'prices': ['prices', 'current_prices'],
        'total_value': ['total_value', 'portfolio_value'],
        'last_update': ['last_update', 'update_time'],
    }


def get_field(data: dict[str, Any], field_name: str, mapping: dict[str, list], default: Any = None) -> Any:
    """从数据字典中获取字段值，支持多别名"""
    aliases = mapping.get(field_name, [field_name])
    for alias in aliases:
        if alias in data:
            return data[alias]
    return default


def normalize_position(raw_position: dict[str, Any]) -> dict[str, Any]:
    """标准化持仓数据"""
    return {
        'shares': int(get_field(raw_position, 'shares', FieldMapping.POSITION, 0)),
        'cost': float(get_field(raw_position, 'cost', FieldMapping.POSITION, 0.0)),
        'category': get_field(raw_position, 'category', FieldMapping.POSITION, ''),
        'target_weight': float(get_field(raw_position, 'target_weight', FieldMapping.POSITION, 0.0)),
        'name': get_field(raw_position, 'name', FieldMapping.POSITION, ''),
    }


def normalize_portfolio(raw_data: dict[str, Any]) -> dict[str, Any]:
    """标准化组合数据"""
    positions = {}
    raw_positions = get_field(raw_data, 'positions', FieldMapping.PORTFOLIO, {})
    for code, pos in raw_positions.items():
        positions[code] = normalize_position(pos)

    return {
        'positions': positions,
        'cash': float(get_field(raw_data, 'cash', FieldMapping.PORTFOLIO, 0.0)),
        'prices': get_field(raw_data, 'prices', FieldMapping.PORTFOLIO, {}),
        'total_value': float(get_field(raw_data, 'total_value', FieldMapping.PORTFOLIO, 0.0)),
        'last_update': get_field(raw_data, 'last_update', FieldMapping.PORTFOLIO, ''),
    }
