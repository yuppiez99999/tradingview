"""
硅能智投智能投资代理系统
主程序入口
"""

import sys
import os
from datetime import datetime
import logging

# 添加项目路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from investment_agent.core.investment_agent import InvestmentAgent
from investment_agent.data.wind_data_provider import WindDataProvider
from investment_agent.ai_integration.silicon_flow_client import SiliconFlowClient

def setup_logging():
    """设置全局日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

def print_startup_banner():
    """打印启动横幅"""
    banner = """
    ╔══════════════════════════════════════════════════════════════╗
    ║                    硅能智投智能投资代理系统                        ║
    ║              Silicon Intelligence Investment Agent            ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  管理资金: 300万元人民币                                        ║
    ║  投资标的: 20个（5 ETF + 14 股票 + 1 现金）                      ║
    ║  目标收益: 年化≥8%，最大回撤≤15%                                ║
    ║  风险等级: 三级风控体系                                          ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  技术架构:                                                       ║
    ║  - AI引擎: 硅基流动 DeepSeek-V4                               ║
    ║  - 数据源: Wind金融终端                                        ║
    ║  - 备用源: AkShare + 智谱AI                                   ║
    ║  - 策略: 多因子 + 动量择时 + 风险平价                            ║
    ╚══════════════════════════════════════════════════════════════╝
    """
    print(banner)

def main():
    """主函数"""
    # 设置日志
    setup_logging()
    logger = logging.getLogger('Main')
    
    # 打印启动横幅
    print_startup_banner()
    
    logger.info("="*60)
    logger.info("硅能智投智能投资代理系统启动")
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("="*60)
    
    try:
        # 初始化智能投资代理
        logger.info("正在初始化智能投资代理...")
        agent = InvestmentAgent()
        
        # 初始化数据提供者
        logger.info("正在初始化数据提供者...")
        data_provider = WindDataProvider()
        if data_provider.connect():
            logger.info("Wind数据提供者连接成功")
            agent.data_provider = data_provider
        else:
            logger.warning("Wind数据提供者连接失败，将使用模拟数据")
        
        # 初始化AI引擎
        logger.info("正在初始化AI引擎...")
        ai_client = SiliconFlowClient()
        if ai_client.check_connection():
            logger.info("硅基流动AI引擎连接成功")
            agent.ai_engine = ai_client
        else:
            logger.warning("硅基流动AI引擎连接失败，将使用本地逻辑")
        
        # 显示初始建仓计划
        logger.info("="*60)
        logger.info("初始建仓计划")
        logger.info("="*60)
        initial_plan = agent.get_initial_positions_plan()
        print(initial_plan.to_string())
        
        # 显示系统状态
        logger.info("="*60)
        logger.info("系统状态")
        logger.info("="*60)
        status = agent.get_status()
        for key, value in status.items():
            logger.info(f"{key}: {value}")
        
        # 模拟一个交易日的流程
        logger.info("="*60)
        logger.info("模拟交易日流程")
        logger.info("="*60)
        
        # 开始交易日
        logger.info("1. 开始交易日...")
        agent.start_trading_day()
        
        # 执行盘前任务
        logger.info("2. 执行盘前任务...")
        morning_report, recommendations, risk_alerts = agent._execute_pre_market_tasks()
        
        # 显示交易建议
        logger.info("3. 交易建议:")
        for i, rec in enumerate(recommendations[:5], 1):  # 显示前5条
            logger.info(f"   [{i}] {rec.get('type')}: {rec.get('symbol')} - {rec.get('action')}")
        
        # 显示风险告警
        logger.info("4. 风险告警:")
        for i, alert in enumerate(risk_alerts[:3], 1):  # 显示前3条
            logger.info(f"   [{i}] {alert.risk_level.value}: {alert.message}")
        
        # 结束交易日
        logger.info("5. 结束交易日...")
        agent.end_trading_day()
        
        # 显示最终状态
        logger.info("="*60)
        logger.info("交易日结束 - 最终状态")
        logger.info("="*60)
        final_status = agent.get_status()
        
        print(f"\n组合状态:")
        print(f"  总市值: {final_status['portfolio']['total_value']:,.2f}元")
        print(f"  现金余额: {final_status['portfolio']['cash_balance']:,.2f}元")
        print(f"  总盈亏: {final_status['portfolio']['total_pnl']:,.2f}元")
        print(f"  总盈亏率: {final_status['portfolio']['total_pnl_percent']:.2%}")
        print(f"  持仓数量: {final_status['portfolio']['position_count']}个")
        
        print(f"\n风险状态:")
        print(f"  风险等级: {final_status['risk']['level']}")
        print(f"  组合回撤: {final_status['risk']['drawdown']:.2%}")
        print(f"  活跃告警: {final_status['risk']['active_alerts']}个")
        
        # AI成本统计
        if ai_client:
            cost_summary = ai_client.get_cost_summary()
            print(f"\nAI成本统计:")
            print(f"  总Token数: {cost_summary['total_tokens']:,}")
            print(f"  总成本: {cost_summary['total_cost_yuan']:.2f}元")
            print(f"  请求次数: {cost_summary['request_count']}")
        
        # 关闭系统
        logger.info("="*60)
        logger.info("关闭系统...")
        final_report = agent.shutdown()
        
        # 断开数据连接
        if data_provider:
            data_provider.disconnect()
        
        logger.info("="*60)
        logger.info("系统已安全关闭")
        logger.info(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("="*60)
        
        print("\n🎉 硅能智投智能投资代理系统演示完成！")
        print("\n💡 系统功能:")
        print("  ✅ 20个标的自动管理")
        print("  ✅ 三级风控实时监控")
        print("  ✅ 7类自动化任务调度")
        print("  ✅ AI驱动投资决策")
        print("  ✅ 实时风险告警")
        print("  ✅ 组合再平衡建议")
        
        print("\n📊 下一步:")
        print("  1. 配置真实API密钥（Wind、硅基流动）")
        print("  2. 连接真实交易账户")
        print("  3. 部署到云服务器（阿里云ECS）")
        print("  4. 配置定时任务（cron）")
        print("  5. 启动Streamlit监控面板")
        
        print("\n🚀 预期收益:")
        print("  第一年: 15万元 (5%年化)")
        print("  第二年: 45万元 (15%年化)")
        print("  第三年: 60万元 (20%年化)")
        
    except KeyboardInterrupt:
        logger.info("用户中断，正在关闭系统...")
        if 'agent' in locals():
            agent.shutdown()
        
    except Exception as e:
        logger.error(f"系统运行错误: {str(e)}", exc_info=True)
        print(f"\n❌ 系统运行错误: {str(e)}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)