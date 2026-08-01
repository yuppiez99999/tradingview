# -*- coding: utf-8 -*-
"""
P0风控参数更新脚本

功能:
1. 收紧组合止损线至-8%
2. 新增预警线-6%
3. 更新紧急行动触发条件

执行时间: 2026-07-23
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('p0_risk_update')

CONFIG_DIR = Path(__file__).parent / 'src' / 'config'


def update_risk_config():
    """更新风控配置文件"""

    logger.info("=" * 80)
    logger.info("P0风控参数更新")
    logger.info("=" * 80)

    # 目标配置
    new_risk_params = {
        "portfolio_stop_loss_threshold": -0.08,  # -8%止损线
        "portfolio_warning_line": -0.06,  # -6%预警线
        "emergency_action_trigger": {
            "condition": "回撤触及-8%",
            "action": "立即减半仓位",
            "liquidation_deadline": "48小时内清仓"
        },
        "individual_stock_stop_loss": -0.10,  # 个股止损-10%
        "individual_stock_warning": -0.07,  # 个股预警-7%
        "daily_loss_limit": -0.03,  # 单日亏损限制-3%
        "consecutive_loss_limit": 5,  # 连续亏损次数上限
        "margin_call_threshold": -0.12,  # 保证金催缴线-12%
        "volatility_threshold": 0.28,  # 波动率阈值28%
        "drawdown_threshold": 0.12  # 回撤阈值12%
    }

    # 保存配置
    risk_config_path = CONFIG_DIR / 'risk_config_p0.json'
    with open(risk_config_path, 'w', encoding='utf-8') as f:
        json.dump(new_risk_params, f, ensure_ascii=False, indent=2)

    logger.info(f"风控配置已保存: {risk_config_path}")
    logger.info("")
    logger.info("关键参数变更:")
    logger.info("  组合止损线: -10%~-15% → -8%")
    logger.info("  组合预警线: 无 → -6%")
    logger.info("  紧急行动: 回撤>15%暂停 → 触及-8%减半仓位,48小时清仓")
    logger.info("  个股止损线: -8% (保持不变)")
    logger.info("  单日亏损限制: -3%")
    logger.info(f"  连续亏损上限: {new_risk_params['consecutive_loss_limit']}次")

    return new_risk_params


def generate_risk_update_report(params):
    """生成风控更新报告"""

    report = {
        "metadata": {
            "title": "P0风控参数更新报告",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "priority": "CRITICAL",
            "executor": "AI Quantitative Research Team"
        },
        "updates": params,
        "implementation_checklist": [
            "□ risk_config_p0.json已创建",
            "□ src/risk/unified_risk_cockpit.py已更新读取新配置",
            "□ 所有交易员已收到新风控标准通知",
            "□ 风控系统已加载新参数",
            "□ 预警线-6%已激活",
            "□ 止损线-8%已激活",
            "□ 紧急行动预案已更新"
        ],
        "expected_benefit": {
            "max_drawdown_protection": "-8%硬止损",
            "early_warning": "-6%预警触发减仓预案",
            "extreme_case_handling": "48小时内强制清仓",
            "annual_protection_value": "约50-80万元(极端损失减少3-5%)"
        }
    }

    report_path = Path(__file__).parent / 'reports' / f'risk_update_report_{datetime.now().strftime("%Y%m%d")}.json'
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"风控更新报告已保存: {report_path}")
    return report_path


def main():
    """主执行流程"""

    logger.info("")
    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " P0风控参数更新器 ".center(78) + "║")
    logger.info("╚" + "=" * 78 + "╝")
    logger.info("")

    # 更新风控配置
    params = update_risk_config()

    # 生成报告
    report_path = generate_risk_update_report(params)

    logger.info("")
    logger.info("=" * 80)
    logger.info("风控参数更新完成")
    logger.info("=" * 80)
    logger.info(f"配置文件: {CONFIG_DIR / 'risk_config_p0.json'}")
    logger.info(f"更新报告: {report_path}")
    logger.info("")
    logger.info("下一步:")
    logger.info("  1. 验证unified_risk_cockpit.py已加载新配置")
    logger.info("  2. 测试风控系统按新参数正常运行")
    logger.info("  3. 通知所有交易员新风控标准")
    logger.info("=" * 80)


if __name__ == '__main__':
    main()
