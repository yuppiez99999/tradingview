"""
5年投资组合预测分析报告
- 世界顶级对冲基金视角 (Bridgewater/Renaissance/Two Sigma)
- 年化收益率预测
- 最大回撤分析
- 黑天鹅与不可抗力风险评估
"""

import json
import sys
from datetime import datetime

# 统一成本模型（与 annualized_return_forecast.py 共用，消除 0.45% vs 2.8% 矛盾）
from utils.cost_model import get_cost_model

sys.path.insert(0, ".")


class PortfolioProjection:
    """组合5年预测分析器"""

    def __init__(self, positions_file: str, hedge_file: str):
        self.positions_data = self._load_json(positions_file)
        self.hedge_data = self._load_json(hedge_file)
        self.projection = {}

    def _load_json(self, path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"加载文件失败: {path}, {e}")
            return {}

    def analyze_portfolio(self) -> dict:
        """分析组合结构"""
        positions = self.positions_data.get("positions", {})

        style_weights = {}
        risk_counts = {}
        type_counts = {}

        total_value = 0

        for _key, pos in positions.items():
            style = pos.get("style", "unknown")
            risk = pos.get("risk", "unknown")
            pos_type = pos.get("type", "unknown")
            amount = pos.get("phase1_amount", 0)

            style_weights[style] = style_weights.get(style, 0) + amount
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            type_counts[pos_type] = type_counts.get(pos_type, 0) + 1
            total_value += amount

        # 归一化权重
        style_weights_norm = {
            k: round(v / total_value * 100, 2) for k, v in style_weights.items()
        }

        return {
            "total_value": round(total_value, 2),
            "position_count": len(positions),
            "style_weights": style_weights_norm,
            "risk_counts": risk_counts,
            "type_counts": type_counts,
            "avg_stop_loss": round(
                sum(p.get("stop_loss", 0) for p in positions.values()) / len(positions),
                3,
            ),
        }

    def calculate_expected_returns(self) -> dict:
        """计算各风格预期年化收益率"""
        style_returns = {
            "科技": {"base_return": 0.18, "volatility": 0.35, "beta": 1.4},
            "高端制造": {"base_return": 0.15, "volatility": 0.30, "beta": 1.3},
            "成长": {"base_return": 0.16, "volatility": 0.32, "beta": 1.35},
            "新能源": {"base_return": 0.14, "volatility": 0.28, "beta": 1.25},
            "医药": {"base_return": 0.12, "volatility": 0.25, "beta": 1.1},
            "制造": {"base_return": 0.10, "volatility": 0.22, "beta": 1.0},
            "化工": {"base_return": 0.09, "volatility": 0.24, "beta": 1.05},
            "宽基": {"base_return": 0.08, "volatility": 0.20, "beta": 1.0},
            "红利": {"base_return": 0.07, "volatility": 0.15, "beta": 0.8},
            "银行": {"base_return": 0.06, "volatility": 0.12, "beta": 0.7},
            "顺周期": {"base_return": 0.08, "volatility": 0.22, "beta": 1.1},
            "防御": {"base_return": 0.05, "volatility": 0.10, "beta": 0.5},
            "避险": {"base_return": 0.04, "volatility": 0.12, "beta": 0.3},
            "unknown": {"base_return": 0.07, "volatility": 0.20, "beta": 1.0},
        }

        portfolio_analysis = self.analyze_portfolio()
        style_weights = portfolio_analysis["style_weights"]

        weighted_return = 0.0
        weighted_volatility = 0.0
        weighted_beta = 0.0

        for style, weight in style_weights.items():
            info = style_returns.get(style, style_returns["unknown"])
            weight_pct = weight / 100
            weighted_return += info["base_return"] * weight_pct
            weighted_volatility += info["volatility"] * weight_pct
            weighted_beta += info["beta"] * weight_pct

        return {
            "style_returns": style_returns,
            "weighted_return": round(weighted_return, 4),
            "weighted_volatility": round(weighted_volatility, 4),
            "weighted_beta": round(weighted_beta, 4),
            "style_weights": style_weights,
        }

    def project_5_year_performance(self) -> dict:
        """5年业绩预测"""
        returns = self.calculate_expected_returns()
        self.analyze_portfolio()

        base_return = returns["weighted_return"]
        volatility = returns["weighted_volatility"]

        hedge_info = self.hedge_data
        portfolio_beta = hedge_info.get("portfolio_beta", 1.0)
        target_beta = 0.3
        beta_reduction = portfolio_beta - target_beta

        # 对冲有效性系数：此前为魔法数字 0.7686。此处保留名义值，但应改为
        # 由已实现对冲 P&L 回归估计（见 utils/hedge_effectiveness.py 待建）。
        hedge_effectiveness = 0.7686

        hedged_return = base_return * (1 - 0.4 * hedge_effectiveness)
        hedged_volatility = volatility * (1 - beta_reduction * hedge_effectiveness)

        # 成本：统一成本模型（佣金+印花税 / 冲击 / 期权覆盖+期货对冲）
        cost_model = get_cost_model()
        cb = cost_model.breakdown()
        transaction_costs = cb["commission"] + cb["stamp_duty"]
        slippage = cb["market_impact"]
        management_fee = cb["option_overlay"] + cb["futures_basis"]
        total_costs = cost_model.annual_total_cost

        net_annual_return = hedged_return - total_costs

        five_year_total_return = (1 + net_annual_return) ** 5 - 1

        max_drawdown_base = volatility * 2.33
        hedged_max_drawdown = max_drawdown_base * (
            1 - beta_reduction * hedge_effectiveness * 0.6
        )

        sharpe_ratio = net_annual_return / volatility if volatility > 0 else 0
        sortino_ratio = net_annual_return / (volatility * 0.6) if volatility > 0 else 0

        return {
            "base_annual_return": round(base_return * 100, 2),
            "hedged_annual_return": round(hedged_return * 100, 2),
            "net_annual_return": round(net_annual_return * 100, 2),
            "five_year_total_return": round(five_year_total_return * 100, 2),
            "expected_5y_end_value": round(5000000 * (1 + five_year_total_return), 2),
            "max_drawdown": round(hedged_max_drawdown * 100, 2),
            "volatility": round(hedged_volatility * 100, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "sortino_ratio": round(sortino_ratio, 2),
            "beta_exposure": round(target_beta, 3),
            "total_costs": round(total_costs * 100, 2),
            "breakdown": {
                "transaction_costs": round(transaction_costs * 100, 2),
                "slippage": round(slippage * 100, 2),
                "management_fee": round(management_fee * 100, 2),
            },
        }

    def identify_black_swan_risk(self) -> list[dict]:
        """识别黑天鹅风险"""
        portfolio_analysis = self.analyze_portfolio()
        style_weights = portfolio_analysis["style_weights"]

        risks = []

        if style_weights.get("科技", 0) + style_weights.get("高端制造", 0) > 30:
            risks.append(
                {
                    "risk_id": "tech_concentration",
                    "name": "科技股过度集中",
                    "severity": "HIGH",
                    "probability": "MEDIUM",
                    "impact": "极高",
                    "description": "科技股权重超过30%，若AI泡沫破裂或中美科技脱钩，可能导致组合大幅回撤",
                    "scenario": "AI投资热潮退潮，科技股估值回归，预期跌幅30-40%",
                    "mitigation": "分散配置至传统行业，增加防御性资产",
                    "stress_test_loss": "组合可能损失15-20%",
                }
            )

        if portfolio_analysis["risk_counts"].get("高", 0) > 5:
            risks.append(
                {
                    "risk_id": "high_risk_count",
                    "name": "高风险标的过多",
                    "severity": "HIGH",
                    "probability": "HIGH",
                    "impact": "高",
                    "description": "超过5只高风险标的，尾部风险累积",
                    "scenario": "单一标的黑天鹅事件引发连锁反应",
                    "mitigation": "限制单只高风险标的权重不超过5%",
                    "stress_test_loss": "组合可能损失8-12%",
                }
            )

        risks.append(
            {
                "risk_id": "geopolitical",
                "name": "地缘政治风险",
                "severity": "HIGH",
                "probability": "MEDIUM",
                "impact": "极高",
                "description": "中美关系恶化、台湾问题、贸易战升级",
                "scenario": "全面贸易战或军事冲突，市场恐慌性抛售",
                "mitigation": "增加黄金ETF至10%以上，配置港股通标的",
                "stress_test_loss": "组合可能损失20-25%",
            }
        )

        risks.append(
            {
                "risk_id": "interest_rate",
                "name": "利率持续上行",
                "severity": "MEDIUM",
                "probability": "MEDIUM",
                "impact": "高",
                "description": "美联储持续加息，成长股估值承压",
                "scenario": "10年期美债收益率突破5%",
                "mitigation": "增加短久期债券，减少高PE成长股",
                "stress_test_loss": "组合可能损失10-15%",
            }
        )

        risks.append(
            {
                "risk_id": "liquidity_crisis",
                "name": "流动性危机",
                "severity": "HIGH",
                "probability": "LOW",
                "impact": "极高",
                "description": "银行系统危机、影子银行风险、外资大规模撤离",
                "scenario": "类似2008年金融危机重现",
                "mitigation": "保持10%现金储备，分散银行股配置",
                "stress_test_loss": "组合可能损失25-35%",
            }
        )

        risks.append(
            {
                "risk_id": "regulatory",
                "name": "政策监管风险",
                "severity": "MEDIUM",
                "probability": "HIGH",
                "impact": "中高",
                "description": "互联网反垄断、医药集采、新能源补贴退坡",
                "scenario": "政策突变导致特定板块估值重估",
                "mitigation": "关注政策风向，保持灵活仓位",
                "stress_test_loss": "组合可能损失5-10%",
            }
        )

        risks.append(
            {
                "risk_id": "inflation",
                "name": "恶性通胀",
                "severity": "MEDIUM",
                "probability": "LOW",
                "impact": "高",
                "description": "CPI持续高企，央行大幅收紧货币政策",
                "scenario": "中国CPI突破5%",
                "mitigation": "增加黄金、资源股配置",
                "stress_test_loss": "组合可能损失8-12%",
            }
        )

        risks.append(
            {
                "risk_id": "pandemic",
                "name": "全球性疫情复发",
                "severity": "HIGH",
                "probability": "LOW",
                "impact": "极高",
                "description": "新变种病毒导致全球封锁",
                "scenario": "类似2020年全球停摆",
                "mitigation": "增加在线经济标的，配置必需消费品",
                "stress_test_loss": "组合可能损失20-30%",
            }
        )

        return risks

    def generate_comprehensive_report(self) -> dict:
        """生成综合预测报告"""
        portfolio_analysis = self.analyze_portfolio()
        expected_returns = self.calculate_expected_returns()
        five_year = self.project_5_year_performance()
        black_swans = self.identify_black_swan_risk()

        report = {
            "meta": {
                "report_date": datetime.now().strftime("%Y-%m-%d"),
                "report_type": "5年投资组合预测分析",
                "perspective": "世界顶级对冲基金视角 (Bridgewater/Renaissance/Two Sigma)",
                "total_capital": self.positions_data.get("meta", {}).get(
                    "total_capital", 5000000
                ),
            },
            "portfolio_analysis": portfolio_analysis,
            "expected_returns": expected_returns,
            "five_year_projection": five_year,
            "black_swan_risk": black_swans,
            "strategic_recommendations": self._generate_strategic_recommendations(
                five_year, black_swans
            ),
            "scenario_analysis": self._generate_scenario_analysis(
                five_year, black_swans
            ),
        }

        return report

    def _generate_strategic_recommendations(
        self, five_year: dict, black_swans: list[dict]
    ) -> list[str]:
        """生成战略建议"""
        recommendations = []

        if five_year["net_annual_return"] < 8:
            recommendations.append(
                "当前预期年化收益率低于8%基准，建议增加高成长板块配置"
            )

        if five_year["max_drawdown"] > 15:
            recommendations.append(
                "最大回撤预测超过15%，建议增加对冲工具（如期权保护）"
            )

        high_severity_risks = [r for r in black_swans if r["severity"] == "HIGH"]
        if len(high_severity_risks) > 3:
            recommendations.append("存在超过3个高严重性风险，建议重新审视组合风险敞口")

        recommendations.append("建议每季度进行压力测试，动态调整仓位")
        recommendations.append("设置10%现金储备作为流动性缓冲")
        recommendations.append("考虑引入Tail Risk对冲策略（如VIX期权）")
        recommendations.append("科技股集中度较高，建议分散至消费、医疗等防御性板块")

        return recommendations

    def _generate_scenario_analysis(
        self, five_year: dict, black_swans: list[dict]
    ) -> dict:
        """生成情景分析"""
        base_case = {
            "description": "基准情景：经济平稳增长，政策稳定",
            "annual_return": five_year["net_annual_return"],
            "max_drawdown": five_year["max_drawdown"],
            "probability": "40%",
            "end_value": five_year["expected_5y_end_value"],
        }

        bull_case = {
            "description": "乐观情景：AI产业爆发，科技股持续领跑",
            "annual_return": round(five_year["net_annual_return"] * 1.5, 2),
            "max_drawdown": round(five_year["max_drawdown"] * 0.7, 2),
            "probability": "25%",
            "end_value": round(
                5000000 * (1 + five_year["net_annual_return"] * 1.5 / 100) ** 5, 2
            ),
        }

        bear_case = {
            "description": "悲观情景：地缘冲突加剧，经济衰退",
            "annual_return": round(five_year["net_annual_return"] * 0.3, 2),
            "max_drawdown": round(five_year["max_drawdown"] * 1.5, 2),
            "probability": "20%",
            "end_value": round(
                5000000 * (1 + five_year["net_annual_return"] * 0.3 / 100) ** 5, 2
            ),
        }

        crisis_case = {
            "description": "危机情景：黑天鹅事件触发",
            "annual_return": round(-5.0, 2),
            "max_drawdown": round(30.0, 2),
            "probability": "15%",
            "end_value": round(5000000 * (1 - 0.05) ** 5, 2),
        }

        return {
            "base_case": base_case,
            "bull_case": bull_case,
            "bear_case": bear_case,
            "crisis_case": crisis_case,
        }


def print_report(report: dict):
    """打印报告"""
    print("=" * 70)
    print("5年投资组合预测分析报告")
    print("=" * 70)
    print(f"视角: {report['meta']['perspective']}")
    print(f"日期: {report['meta']['report_date']}")
    print(f"总资金: {report['meta']['total_capital']:,}")
    print()

    print("【1】组合结构分析")
    pa = report["portfolio_analysis"]
    print(f"  持仓数: {pa['position_count']}")
    print(f"  持仓价值: {pa['total_value']:,}")
    print(f"  平均止损: {pa['avg_stop_loss']:.2%}")
    print("  风格权重:")
    for style, weight in sorted(pa["style_weights"].items(), key=lambda x: -x[1]):
        print(f"    {style}: {weight:.2f}%")
    print()

    print("【2】预期收益率")
    er = report["expected_returns"]
    print(f"  加权年化收益: {er['weighted_return']*100:.2f}%")
    print(f"  加权波动率: {er['weighted_volatility']*100:.2f}%")
    print(f"  加权Beta: {er['weighted_beta']:.2f}")
    print()

    print("【3】5年业绩预测")
    fy = report["five_year_projection"]
    print(f"  基准年化收益: {fy['base_annual_return']:.2f}%")
    print(f"  对冲后年化收益: {fy['hedged_annual_return']:.2f}%")
    print(f"  净年化收益: {fy['net_annual_return']:.2f}%")
    print(f"  5年总收益: {fy['five_year_total_return']:.2f}%")
    print(f"  5年末预期价值: {fy['expected_5y_end_value']:,.2f}")
    print(f"  最大回撤: {fy['max_drawdown']:.2f}%")
    print(f"  波动率: {fy['volatility']:.2f}%")
    print(f"  Sharpe Ratio: {fy['sharpe_ratio']:.2f}")
    print(f"  Sortino Ratio: {fy['sortino_ratio']:.2f}")
    print(f"  Beta敞口: {fy['beta_exposure']:.3f}")
    print()

    print("【4】情景分析")
    sa = report["scenario_analysis"]
    for _name, scenario in sa.items():
        print(f"  {scenario['description']}")
        print(f"    概率: {scenario['probability']}")
        print(f"    年化收益: {scenario['annual_return']:.2f}%")
        print(f"    最大回撤: {scenario['max_drawdown']:.2f}%")
        print(f"    5年末价值: {scenario['end_value']:,.2f}")
        print()

    print("【5】黑天鹅风险识别")
    for i, risk in enumerate(report["black_swan_risk"], 1):
        severity_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}[risk["severity"]]
        print(f"  {severity_icon} {i}. {risk['name']}")
        print(
            f"     严重度: {risk['severity']} | 概率: {risk['probability']} | 影响: {risk['impact']}"
        )
        print(f"     描述: {risk['description']}")
        print(f"     压力测试损失: {risk['stress_test_loss']}")
        print(f"     缓释措施: {risk['mitigation']}")
        print()

    print("【6】战略建议")
    for i, rec in enumerate(report["strategic_recommendations"], 1):
        print(f"  {i}. {rec}")

    print("=" * 70)


def save_report(report: dict, output_path: str):
    """保存报告"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"报告已保存: {output_path}")


def generate_markdown_report(report: dict) -> str:
    er = report["expected_returns"]
    fy = report["five_year_projection"]
    sa = report["scenario_analysis"]

    md = f"""# 5年投资组合预测分析报告

**视角**: {report['meta']['perspective']}
**日期**: {report['meta']['report_date']}
**总资金**: {report['meta']['total_capital']:,}

---

## 一、组合结构分析

### 1.1 概览

| 项目 | 数值 |
|------|------|
| 持仓数 | {report['portfolio_analysis']['position_count']} |
| 持仓价值 | {report['portfolio_analysis']['total_value']:,} |
| 平均止损 | {report['portfolio_analysis']['avg_stop_loss']:.2%} |

### 1.2 风格权重分布

| 风格 | 权重 |
|------|------|
"""
    for style, weight in sorted(
        report["portfolio_analysis"]["style_weights"].items(), key=lambda x: -x[1]
    ):
        md += f"| {style} | {weight:.2f}% |\n"

    md += (f"""
---

## 二、预期收益率分析

| 指标 | 数值 |
|------|------|
| 加权年化收益 | {er['weighted_return']*100:.2f}% |
| 加权波动率 | {er['weighted_volatility']*100:.2f}% |
| 加权Beta | {er['weighted_beta']:.2f} |

---

## 三、5年业绩预测

| 指标 | 数值 |
|------|------|
| 基准年化收益 | {fy['base_annual_return']:.2f}% |
| 对冲后年化收益 | {fy['hedged_annual_return']:.2f}% |
| **净年化收益** | **{fy['net_annual_return']:.2f}%** |
| 5年总收益 | {fy['five_year_total_return']:.2f}% |
| 5年末预期价值 | {fy['expected_5y_end_value']:,.2f} |
| 最大回撤 | {fy['max_drawdown']:.2f}% |
| 波动率 | {fy['volatility']:.2f}% |
| Sharpe Ratio | {fy['sharpe_ratio']:.2f} |
| Sortino Ratio | {fy['sortino_ratio']:.2f} |
| Beta敞口 | {fy['beta_exposure']:.3f} |

### 成本拆解

| 成本项 | 比例 |
|--------|------|
| 交易成本 | {fy['breakdown']['transaction_costs']:.2f}% |
| 滑点 | {fy['breakdown']['slippage']:.2f}% |
| 管理费 | {fy['breakdown']['management_fee']:.2f}% |
| **总成本** | **{fy['total_costs']:.2f}%** |

---

## 四、情景分析

| 情景 | 概率 | 年化收益 | 最大回撤 | 5年末价值 |
|------|------|----------|----------|-----------|
| 基准情景 | {sa['base_case']['probability']} | {sa['base_case']['annual_return']:.2f}% | """
    f"""{sa['base_case']['max_drawdown']:.2f}% | {sa['base_case']['end_value']:,.2f} |
| 乐观情景 | {sa['bull_case']['probability']} | {sa['bull_case']['annual_return']:.2f}% | """
    f"""{sa['bull_case']['max_drawdown']:.2f}% | {sa['bull_case']['end_value']:,.2f} |
| 悲观情景 | {sa['bear_case']['probability']} | {sa['bear_case']['annual_return']:.2f}% | """
    f"""{sa['bear_case']['max_drawdown']:.2f}% | {sa['bear_case']['end_value']:,.2f} |
| 危机情景 | {sa['crisis_case']['probability']} | {sa['crisis_case']['annual_return']:.2f}% | """
    f"""{sa['crisis_case']['max_drawdown']:.2f}% | {sa['crisis_case']['end_value']:,.2f} |

---

## 五、黑天鹅与不可抗力风险

""")

    for risk in report["black_swan_risk"]:
        severity_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}[risk["severity"]]
        md += f"""### {severity_icon} {risk['name']}

| 项目 | 内容 |
|------|------|
| 严重度 | {risk['severity']} |
| 概率 | {risk['probability']} |
| 影响 | {risk['impact']} |
| 描述 | {risk['description']} |
| 情景 | {risk['scenario']} |
| 压力测试损失 | {risk['stress_test_loss']} |
| 缓释措施 | {risk['mitigation']} |

"""

    md += """
---

## 六、战略建议

"""

    for i, rec in enumerate(report["strategic_recommendations"], 1):
        md += f"{i}. {rec}\n"

    md += f"""

---

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    return md


def main():
    positions_file = "config/positions.json"
    hedge_file = "reports/hedge_decision_20260706.json"

    analyzer = PortfolioProjection(positions_file, hedge_file)
    report = analyzer.generate_comprehensive_report()

    print_report(report)

    json_output = (
        f"reports/five_year_projection_{datetime.now().strftime('%Y-%m-%d')}.json"
    )
    save_report(report, json_output)

    md_content = generate_markdown_report(report)
    md_output = f"reports/five_year_projection_{datetime.now().strftime('%Y-%m-%d')}.md"
    with open(md_output, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Markdown报告已保存: {md_output}")


if __name__ == "__main__":
    main()
