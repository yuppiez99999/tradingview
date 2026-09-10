"""统一入口 CLI 解析器 — 由 `量化策略系统_统一入口_v8.6.py` 的 main() 迁出（审计批次三 item 11 续做）。

迁出原则：**字节级等价** —— 选项 / 默认值 / 帮助文本 / 互斥关系 / 子选项声明均原样搬运，未改一字。
唯一改动是把原 `MODES` 第 4 元（handler）拆出：本模块只保留 `(flag, dest, help)` 声明。

**为什么 handler 不在这里**：37 个 handler 中 19 个是 `cli.handlers.*` 导入的、18 个是入口本地定义的，
若在此模块直接引用会形成 `cli_parser ↔ 入口` 循环导入 ⇒ handler 由入口按 dest 绑定（见 `MODE_HANDLERS`），
`mode_dispatch.dispatch()` 对缺失绑定 fail-closed（不静默跳过）。
"""
from __future__ import annotations

import argparse

MODE_SPECS = [
    ("--daily", "daily", "三阶段交易工作流 (盘前计划/盘中策略/盘后报告)"),
    ("--live", "live", "实时监控模式"),
    ("--report", "report", "报告生成模式"),
    ("--rebalance", "rebalance", "再平衡模式"),
    ("--backtest", "backtest", "回测模式"),
    ("--risk", "risk", "风险监控模式"),
    ("--check", "check", "快速检查模式"),
    ("--hypothesis", "hypothesis", "假设验证模式"),
    ("--etf-flow", "etf_flow", "ETF资金流向监控"),
    ("--portfolio-opt", "portfolio_opt", "投资组合优化"),
    ("--kommo-monitor", "kommo_monitor", "康波周期监控"),
    ("--commodity-fund", "commodity_fund", "大宗商品基本面"),
    ("--train-model", "train_model", "时序预测训练"),
    ("--train-enhanced", "train_enhanced", "ML增强训练 v2.0 - 四维优化(标签+窗口+特征+权重)"),
    ("--kondratiev", "kondratiev", "康波周期+十五五交叠分析"),
    ("--fifteen-five", "fifteen_five", "十五五规划适配分析"),
    ("--social-security", "social_security", "社保基金ETF风格追踪"),
    ("--macro-analysis", "macro_analysis", "宏观综合分析（康波+十五五+社保ETF一键运行）"),
    ("--ai-decision", "ai_decision", "AI盘中决策 v5.10 - 场景路由+并行对冲+Wind MCP动态数据"),
    ("--futures-options", "futures_options", "期货期权扫描"),
    ("--unified-monitor", "unified_monitor", "统一监控模式 - 一键启动所有模块"),
    ("--ai-hedge", "ai_hedge", "AI Hedge Fund - 20位大师级AI分析师联合决策(含对冲分析师)"),
    ("--ml-signal", "ml_signal", "ML模型预测信号"),
    ("--ml-enhanced", "ml_enhanced", "ML增强预测 v2.0 - 四维优化模型信号"),
    ("--hedge", "hedge", "对冲分析 v5.10 — Taleb+Burry+Druck 多指数期货/期权风险对冲"),
    ("--hedge-rebalance", "hedge_rebalance", "对冲+再平衡联动分析 v5.10 — 组合自触发+多指数Beta加权"),
    ("--hedge-detail", "hedge_detail", "期货对冲明细 v5.10 — 完整对冲规格+Beta表+触发机制+回测"),
    ("--stress-test", "stress_test", "极端压力测试 v5.10 — 6历史情景+蒙特卡洛+硬止损"),
    ("--stop-loss", "stop_loss", "止损配置 v5.10 — 查看/更新止损止盈规则(含ATR动态止损)"),
    ("--ml-significance", "ml_significance", "ML显著性验证 v5.10 — Bootstrap+置换检验+Rank IC"),
    ("--kronos", "kronos", "Kronos 金融K线预测 — 时序基础模型信号"),
    ("--gemma", "gemma", "Gemma 4 分析增强 — 新闻情绪/异动解读/报告生成"),
    ("--dcf", "dcf", "DCF 估值模型 — WACC + 收入预测 + 敏感性分析 (Excel)"),
    ("--comps", "comps", "可比公司分析 — 运营指标 + 估值倍数 + 统计分位 (Excel)"),
    ("--hedge-execute", "hedge_execute", "期权对冲订单执行器 — 撮合执行 PENDING 期权订单 (P0修复)"),
    ("--rebalance-execute", "rebalance_execute", "再平衡撮合执行器 — 撮合再平衡订单并落盘成交回报 (G2/G4修复)"),
    ("--factor-research", "factor_research", "因子研究 — Wind MCP 真实行情驱动闭环 (提案/实现/评审/可选ML组合)"),
]


def build_parser() -> argparse.ArgumentParser:
    mode_lines = "\n".join(
        f"  {flag:<20s} {help_text}" for flag, _, help_text in MODE_SPECS
    )

    parser = argparse.ArgumentParser(
        description="量化策略系统 v5.10 — AI决策驱动 + 对冲再平衡联动v5.10",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
运行模式:
{mode_lines}

示例:
  python "量化策略系统 v5.10.py" --live              # 启动实时监控
  python "量化策略系统 v5.10.py" --report            # 生成报告
  python "量化策略系统 v5.10.py" --rebalance         # 执行再平衡
  python "量化策略系统 v5.10.py" --risk              # 风险监控
  python "量化策略系统 v5.10.py" --check             # 系统检查
  python "量化策略系统 v5.10.py" --etf-flow          # ETF资金流向监控
  python "量化策略系统 v5.10.py" --hypothesis --list # 列出假设
  python "量化策略系统 v5.10.py" --portfolio-opt     # 投资组合优化
  python "量化策略系统 v5.10.py" --kommo-monitor     # 康波周期监控
  python "量化策略系统 v5.10.py" --commodity-fund    # 大宗商品基本面
  python "量化策略系统 v5.10.py" --train-model       # 时序预测训练
  python "量化策略系统 v5.10.py" --kondratiev        # 康波周期+十五五交叠分析
  python "量化策略系统 v5.10.py" --fifteen-five      # 十五五规划适配分析
  python "量化策略系统 v5.10.py" --social-security   # 社保基金ETF风格追踪
  python "量化策略系统 v5.10.py" --macro-analysis    # 宏观综合分析
  python "量化策略系统 v5.10.py" --ai-decision       # AI盘中决策 (v5.10: 并行对冲+Wind MCP)
  python "量化策略系统 v5.10.py" --ai-decision --scene=rebalancing_analysis  # 再平衡深度分析
  python "量化策略系统 v5.10.py" --ai-decision --no-wind  # AI决策 (禁用Wind数据)
  python "量化策略系统 v5.10.py" --futures-options   # 期货期权扫描
  python "量化策略系统 v5.10.py" --unified-monitor    # 统一监控
  python "量化策略系统 v5.10.py" --ai-hedge          # AI Hedge Fund
  python "量化策略系统 v5.10.py" --ml-signal         # ML模型预测信号
  python "量化策略系统 v5.10.py" --hedge             # 对冲分析 (多指数期货/期权)
  python "量化策略系统 v5.10.py" --hedge --no-ai     # 对冲分析 (仅规则引擎)
  python "量化策略系统 v5.10.py" --hedge-rebalance                        # 对冲+再平衡联动分析 v5.10 (组合自触发)
  python "量化策略系统 v5.10.py" --hedge-rebalance --mode=tail_only       # 尾部保护模式 (默认)
  python "量化策略系统 v5.10.py" --hedge-rebalance --mode=dynamic         # 动态对冲模式
  python "量化策略系统 v5.10.py" --hedge-rebalance --show-reasoning       # 含详细推理过程
  python "量化策略系统 v5.10.py" --hedge-rebalance --auto-execute         # 自动化执行(需二次确认)
  python "量化策略系统 v5.10.py" --hedge-detail          # 终端打印完整明细
  python "量化策略系统 v5.10.py" --factor-research --factor-symbols 600036.SH,000001.SZ,588000.SH  # 因子研究(Wind真实数据)  # noqa: E501
  python "量化策略系统 v5.10.py" --factor-research --factor-symbols 600036.SH,000001.SZ --factor-use-llm --factor-combine  # 含GLM-5+ML组合  # noqa: E501
  python "量化策略系统 v5.10.py" --hedge-detail --json   # JSON 输出
  python "量化策略系统 v5.10.py" --hedge-detail -o hedge.json  # 保存到文件
  python "量化策略系统 v5.10.py" --kronos --kronos-code 000001                        # 单股预测
  python "量化策略系统 v5.10.py" --kronos --kronos-code 600519 --kronos-name 茅台     # 单股预测(带名称)
  python "量化策略系统 v5.10.py" --kronos --kronos-batch '[{{"code":"000001","name":"平安银行"}},{{"code":"600519","name":"贵州茅台"}}]'  # 批量预测  # noqa: E501
  python "量化策略系统 v5.10.py" --gemma --gemma-news '央行宣布降息25个基点'                       # 新闻情绪分析
  python "量化策略系统 v5.10.py" --gemma --gemma-stock 600519                                      # 股票基本面分析
  python "量化策略系统 v5.10.py" --gemma --gemma-prompt '分析当前A股市场走势'                       # 自定义分析

架构特点 (借鉴Vibe-Trading):
  • Connector-first: 统一数据源抽象，支持多连接器配置
  • 策略注册表: 中心化策略管理与版本控制
  • 假设验证: 支持统计检验与随机对照试验
  • 研究目标: 支持目标生命周期管理
  • 实时反馈: 长时间任务的进度可视化
  • ETF资金流向: 国家队资金监控，投资决策参考
  • 投资组合优化: 等权重/风险平价/风险配比/因子配比/自定义配置
  • 康波周期监控: 商品价格+宏观指标+产业库存三维度
  • 时序预测: Transformer模型骨架集成
        """,
    )

    # ── 注册运行模式（数据驱动，声明一次即可） ──
    mode_group = parser.add_mutually_exclusive_group(required=True)
    for flag, dest, help_text in MODE_SPECS:
        mode_group.add_argument(flag, dest=dest, action="store_true", help=help_text)

    # ── 子阶段 / 通用选项 / 模式专属选项 ──
    # --daily 子阶段
    parser.add_argument(
        "--phase",
        choices=["premarket", "intraday", "postmarket", "all"],
        default="all",
        help="三阶段工作流子阶段 (配合 --daily 使用)",
    )

    # 通用选项
    parser.add_argument("--no-ai", action="store_true", help="禁用AI分析模块")
    parser.add_argument("--output", "-o", default=None, help="输出报告文件名")
    parser.add_argument("--sync-sl", action="store_true", help="同步止损止盈规则")
    parser.add_argument(
        "--include-valuation",
        action="store_true",
        help="报告生成时追加 DCF + Comps 估值摘要",
    )

    # v5.9: AI 决策场景路由选项
    parser.add_argument(
        "--scene",
        type=str,
        default="intraday_decision",
        choices=[
            "intraday_decision",
            "rebalancing_analysis",
            "macro_analysis",
            "report_generation",
        ],
        help="AI决策场景 (默认: intraday_decision=盘中并行对冲)",
    )
    parser.add_argument(
        "--no-wind", action="store_true", help="禁用 Wind MCP 数据 (使用降级数据源)"
    )
    parser.add_argument(
        "--interval", type=int, default=300, help="AI决策检查间隔(秒, 默认 300)"
    )

    # AI Hedge Fund 选项
    parser.add_argument(
        "--ticker", "-t", nargs="+", default=None, help="AI Hedge Fund: 股票代码列表"
    )
    parser.add_argument(
        "--analysts",
        "-a",
        nargs="*",
        default=None,
        help="AI Hedge Fund: 选择分析师 (默认全部)",
    )
    parser.add_argument(
        "--show-reasoning", action="store_true", help="AI Hedge Fund: 显示分析详情"
    )
    parser.add_argument(
        "--model", type=str, default=None, help="AI Hedge Fund: LLM 模型名"
    )
    parser.add_argument(
        "--provider", type=str, default=None, help="AI Hedge Fund: LLM 提供商"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="AI Hedge Fund/回测: 开始日期 YYYY-MM-DD",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="AI Hedge Fund/回测: 结束日期 YYYY-MM-DD",
    )

    # v5.10: 对冲明细选项
    parser.add_argument(
        "--json", action="store_true", help="期货对冲明细: 输出 JSON 格式"
    )

    # v5.9: 对冲-再平衡联动选项
    parser.add_argument(
        "--auto-execute",
        action="store_true",
        help="对冲-再平衡联动: 自动化执行 (需二次确认)",
    )
    parser.add_argument(
        "--mode",
        dest="hedge_mode",
        type=str,
        default="tail_only",
        choices=["tail_only", "dynamic", "fixed", "none"],
        help="对冲-再平衡联动: 对冲模式 (默认 tail_only=仅尾部保护)",
    )

    # v8.6 P0 (2026-08-06): 期权对冲订单执行器选项
    parser.add_argument(
        "--date", type=str, default=None, help="期权对冲执行器: 目标交易日 YYYY-MM-DD"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="期权对冲执行器: 干跑模式 (不落盘不更新持仓)",
    )
    parser.add_argument(
        "--confirm-only",
        action="store_true",
        help="期权对冲执行器: 仅输出待确认订单, 不撮合",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="UE-1 实盘门控: 真实 broker 就绪时确认真实下单 (防裸实盘双签)",
    )

    # 假设验证选项
    parser.add_argument("--list", action="store_true", help="列出研究假设")
    parser.add_argument("--register", type=str, help="注册新假设: id|名称|描述")
    parser.add_argument("--validate", type=str, help="验证指定假设")

    # ML信号选项
    parser.add_argument(
        "--threshold", type=float, default=0.55, help="ML信号: 买入信号阈值 (默认 0.55)"
    )
    parser.add_argument("--no-ml", action="store_true", help="跳过ML模型信号扫描")

    # Kronos 预测选项
    parser.add_argument(
        "--kronos-code", type=str, default=None, help="Kronos: 单股代码 (如 000001)"
    )
    parser.add_argument(
        "--kronos-name", type=str, default=None, help="Kronos: 股票名称 (可选)"
    )
    parser.add_argument(
        "--kronos-pred-len", type=int, default=24, help="Kronos: 预测窗口 (默认 24)"
    )
    parser.add_argument(
        "--kronos-device",
        type=str,
        default="cpu",
        help="Kronos: 运行设备 (cpu 或 cuda:0)",
    )
    parser.add_argument(
        "--kronos-batch", type=str, default=None, help="Kronos: 批量预测 JSON 数组"
    )
    parser.add_argument(
        "--kronos-output",
        type=str,
        default=None,
        help="Kronos: 输出文件名 (保存到 reports/)",
    )
    # Gemma 分析选项
    parser.add_argument(
        "--gemma-model",
        type=str,
        default=None,
        help="Gemma: Ollama 模型名 (默认 qwen2.5:7b)",
    )
    parser.add_argument(
        "--gemma-news", type=str, default=None, help="Gemma: 新闻内容 (用于情绪分析)"
    )
    parser.add_argument(
        "--gemma-stock", type=str, default=None, help="Gemma: 股票代码 (用于基本面分析)"
    )
    parser.add_argument(
        "--gemma-prompt", type=str, default=None, help="Gemma: 自定义提示"
    )
    parser.add_argument(
        "--gemma-output",
        type=str,
        default=None,
        help="Gemma: 输出文件名 (保存到 reports/)",
    )
    # ── v5.7 Phase 2: 高级训练选项 ──
    parser.add_argument(
        "--optuna", action="store_true", help="训练: 启用 Optuna 贝叶斯超参数优化"
    )
    parser.add_argument(
        "--triple-barrier",
        action="store_true",
        help="训练: 启用 Triple Barrier 标签 (替代简单涨跌标签)",
    )
    parser.add_argument(
        "--stacking", action="store_true", help="训练/预测: 启用 Stacking 多模型集成"
    )
    parser.add_argument(
        "--optuna-trials",
        type=int,
        default=100,
        help="训练: Optuna 试验次数 (默认 100)",
    )
    parser.add_argument(
        "--horizon", type=int, default=1, help="训练: 预测窗口 T+N (1/5/10, 默认 1)"
    )
    parser.add_argument(
        "--trials", type=int, default=50, help="训练: Optuna 试验次数 (默认 50)"
    )
    parser.add_argument(
        "--skip-ml-filter", action="store_true", help="AI Hedge Fund: 跳过ML预筛选"
    )
    parser.add_argument(
        "--mlflow",
        action="store_true",
        help="训练: 启用 MLflow 实验追踪 (需 pip install mlflow)",
    )

    # 因子研究 (Wind MCP) 选项
    parser.add_argument(
        "--factor-symbols",
        type=str,
        default=None,
        help="因子研究: 逗号分隔 Wind 代码 (如 600036.SH,000001.SZ,588000.SH)",
    )
    parser.add_argument(
        "--factor-days", type=int, default=300, help="因子研究: Wind 回溯交易日数"
    )
    parser.add_argument(
        "--factor-use-llm",
        action="store_true",
        help="因子研究: 启用 GLM-5 生成因子 (默认仅规则模板)",
    )
    parser.add_argument(
        "--factor-combine",
        action="store_true",
        help="因子研究: 跑完后做 ML 组合阶段",
    )
    parser.add_argument(
        "--no-factor-persist",
        action="store_true",
        help="因子研究: 跑完不将 accepted 因子写入 AlphaFactorLibrary 持久化",
    )

    # ── ETF期权联动对冲组合策略 (v1.0) ──
    parser.add_argument(
        "--etf-combo",
        action="store_true",
        help="ETF现货与期权联动对冲组合策略 (备兑看涨/领口/CSP/垂直价差/日历价差)",
    )
    parser.add_argument(
        "--etf-combo-monitor",
        action="store_true",
        help="ETF期权联动: 全组合监控 (Greeks/保证金/行权风险)",
    )
    parser.add_argument(
        "--etf-combo-roll",
        action="store_true",
        help="ETF期权联动: 全组合滚仓 (DTE≤5触发)",
    )
    parser.add_argument(
        "--etf-combo-backtest",
        action="store_true",
        help="ETF期权联动: 组合策略回测",
    )

    return parser
