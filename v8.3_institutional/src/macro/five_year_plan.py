"""
十五五规划适配分析模块 v1.0
借鉴：TradingAgents-AShare macro_analyst + QuantDinger policy 矩阵 + FinceptTerminal policy_analysis

功能：
  1. 十五五规划（2026-2030）核心产业方向映射
  2. 持仓标的与规划方向对齐度评分
  3. 政策驱动的权重调整建议
  4. 生成十五五适配报告
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

# ============================================================
# 十五五规划核心产业方向定义
# 借鉴 QuantDinger 的 broker-market policy 矩阵设计
# ============================================================

# 十五五规划（2026-2030）七大战略方向
FIFTEEN_FIVE_POLICIES = {
    "新质生产力": {
        "weight": 0.25,
        "description": "以科技创新为核心驱动力，培育新产业、新模式、新动能",
        "keywords": [
            "AI",
            "人工智能",
            "大模型",
            "算力",
            "机器人",
            "量子计算",
            "低空经济",
            "商业航天",
            "6G",
            "脑机接口",
        ],
        "target_sectors": ["半导体", "AI算力", "软件服务", "通信设备", "航空航天"],
        "relevance_score": 95,  # 政策优先级 0-100
    },
    "制造强国": {
        "weight": 0.20,
        "description": "推动制造业高端化、智能化、绿色化发展",
        "keywords": [
            "高端装备",
            "智能制造",
            "数控机床",
            "工业母机",
            "新材料",
            "新能源汽车",
            "轨道交通",
            "船舶制造",
            "航空航天",
        ],
        "target_sectors": ["高端制造", "机械设备", "汽车", "新材料", "军工"],
        "relevance_score": 90,
    },
    "数字中国": {
        "weight": 0.15,
        "description": "加快数字化发展，建设数字中国",
        "keywords": [
            "数据要素",
            "大数据",
            "云计算",
            "物联网",
            "区块链",
            "工业互联网",
            "智慧城市",
            "数字政府",
            "数字人民币",
        ],
        "target_sectors": ["软件服务", "云计算", "数据要素", "金融科技", "通信"],
        "relevance_score": 88,
    },
    "绿色低碳": {
        "weight": 0.15,
        "description": "推动能源革命，实现碳达峰碳中和目标",
        "keywords": [
            "光伏",
            "风电",
            "储能",
            "氢能",
            "核能",
            "碳交易",
            "新型电力系统",
            "特高压",
            "节能环保",
            "新能源车",
        ],
        "target_sectors": ["新能源", "储能", "电力", "环保", "新能源汽车"],
        "relevance_score": 85,
    },
    "健康中国": {
        "weight": 0.10,
        "description": "全面推进健康中国建设，发展生物医药产业",
        "keywords": [
            "创新药",
            "生物制药",
            "医疗器械",
            "精准医疗",
            "基因治疗",
            "中医药",
            "智慧医疗",
            "养老产业",
            "健康管理",
        ],
        "target_sectors": ["医药", "医疗器械", "生物科技", "医疗服务"],
        "relevance_score": 80,
    },
    "安全发展": {
        "weight": 0.10,
        "description": "统筹发展和安全，保障粮食/能源/产业链安全",
        "keywords": ["粮食安全", "能源安全", "种业", "关键矿产", "稀土", "信创", "国产替代", "网络安全", "军工"],
        "target_sectors": ["农业", "能源", "矿产", "信息安全", "军工"],
        "relevance_score": 82,
    },
    "区域协调": {
        "weight": 0.05,
        "description": "推进区域协调发展和新型城镇化",
        "keywords": ["城市群", "都市圈", "县域经济", "乡村振兴", "新型城镇化"],
        "target_sectors": ["基建", "建材", "交通运输", "房地产"],
        "relevance_score": 65,
    },
}


# ============================================================
# 持仓标的与十五五方向对齐映射表
# ============================================================

# 当前持仓标的的十五五适配评分（0-100）
# 评分依据：公司主营业务与十五五规划七大方向的匹配度
STOCK_POLICY_ALIGNMENT = {
    # 个股
    "601088": {  # 中国神华
        "name": "中国神华",
        "alignments": {
            "绿色低碳": 75,  # 煤炭清洁利用+能源安全
            "安全发展": 85,  # 能源安全核心标的
        },
        "overall_score": 78,
        "rationale": "煤炭龙头+能源安全核心标的，十五五期间受益于能源保供政策",
    },
    "600276": {  # 恒瑞医药
        "name": "恒瑞医药",
        "alignments": {
            "健康中国": 92,  # 创新药龙头，健康中国核心受益
            "新质生产力": 80,  # 生物科技创新
        },
        "overall_score": 88,
        "rationale": "创新药龙头，健康中国+新质生产力双驱动",
    },
    "688041": {  # 海光信息
        "name": "海光信息",
        "alignments": {
            "新质生产力": 90,  # 国产GPU，AI算力核心
            "安全发展": 85,  # 信创/国产替代
            "数字中国": 85,
        },
        "overall_score": 90,
        "rationale": "国产GPU龙头，AI算力+信创双驱动，十五五安全发展与新质生产力核心标的",
    },
    "300308": {  # 中际旭创
        "name": "中际旭创",
        "alignments": {
            "新质生产力": 92,  # 光模块全球龙头，AI算力出口核心
            "数字中国": 88,
            "绿色低碳": 75,  # 数据中心节能与绿色算力
        },
        "overall_score": 90,
        "rationale": "光模块全球龙头，AI算力基础设施核心出口商，十五五新质生产力旗舰",
    },
    "300274": {  # 阳光电源
        "name": "阳光电源",
        "alignments": {
            "绿色低碳": 90,  # 光伏逆变器+储能全球龙头
            "新质生产力": 80,
        },
        "overall_score": 85,
        "rationale": "光伏+储能双龙头，十五五绿色低碳转型核心受益标的",
    },
    "002371": {  # 北方华创
        "name": "北方华创",
        "alignments": {
            "新质生产力": 90,
            "制造强国": 88,  # 半导体设备龙头
            "安全发展": 80,  # 国产替代
        },
        "overall_score": 88,
        "rationale": "半导体设备平台型龙头，国产替代核心，十五五制造强国+新质生产力",
    },
    "688017": {  # 绿的谐波
        "name": "绿的谐波",
        "alignments": {
            "新质生产力": 88,  # 机器人精密减速器
            "制造强国": 85,
        },
        "overall_score": 85,
        "rationale": "机器人核心零部件供应商，受益于十五五新质生产力+制造强国",
    },
    "600089": {  # 特变电工
        "name": "特变电工",
        "alignments": {
            "绿色低碳": 85,  # 新能源设备+特高压
            "制造强国": 80,
            "安全发展": 75,  # 能源安全/特高压外送
        },
        "overall_score": 80,
        "rationale": "新能源设备+输变电龙头，特高压与新能源双驱动",
    },
    "600875": {  # 东方电气
        "name": "东方电气",
        "alignments": {
            "绿色低碳": 88,  # 风电/水电/氢能设备
            "制造强国": 80,
        },
        "overall_score": 82,
        "rationale": "清洁能源装备龙头，风电+氢能+水电，十五五绿色低碳核心装备标的",
    },
    "000425": {  # 徐工机械
        "name": "徐工机械",
        "alignments": {
            "制造强国": 85,  # 高端装备/智能制造
            "安全发展": 70,  # 应急装备
        },
        "overall_score": 75,
        "rationale": "工程机械龙头，高端制造+智能化转型，十五五制造强国受益",
    },
    "600406": {  # 国电南瑞
        "name": "国电南瑞",
        "alignments": {
            "数字中国": 80,  # 电网自动化/数字化
            "绿色低碳": 82,  # 新能源并网/新型电力系统
            "新质生产力": 75,
        },
        "overall_score": 78,
        "rationale": "电网自动化龙头，新型电力系统+数字化核心供应商",
    },
    "600989": {  # 宝丰能源
        "name": "宝丰能源",
        "alignments": {
            "绿色低碳": 78,  # 煤制烯烃+绿氢布局
            "安全发展": 72,  # 煤化工供应链安全
        },
        "overall_score": 72,
        "rationale": "煤制烯烃龙头，积极布局绿氢，十五五绿色低碳转型受益",
    },
    "600036": {  # 招商银行
        "name": "招商银行",
        "alignments": {
            "数字中国": 65,  # 金融科技
            "安全发展": 60,  # 金融安全
        },
        "overall_score": 55,
        "rationale": "零售银行龙头，金融科技有布局，但与十五五产业方向关联度一般",
    },
    "600900": {  # 长江电力
        "name": "长江电力",
        "alignments": {
            "绿色低碳": 80,  # 水电清洁能源
            "安全发展": 75,  # 能源安全/水电基荷
        },
        "overall_score": 68,
        "rationale": "水电龙头，清洁能源+能源安全，但属于成熟防御资产，成长弹性有限",
    },
    "600219": {  # 南山铝业
        "name": "南山铝业",
        "alignments": {
            "绿色低碳": 78,  # 铝轻量化/再生铝
            "制造强国": 72,  # 汽车轻量化材料
        },
        "overall_score": 70,
        "rationale": "铝产业链龙头，汽车轻量化+航空铝材，十五五绿色低碳与制造强国受益",
    },
    "600019": {  # 宝钢股份
        "name": "宝钢股份",
        "alignments": {
            "制造强国": 72,  # 高端钢材/汽车板
            "绿色低碳": 68,  # 低碳冶金
        },
        "overall_score": 65,
        "rationale": "钢铁龙头，高端制造材料供应商，但传统周期属性较强",
    },
    "000792": {  # 盐湖股份
        "name": "盐湖股份",
        "alignments": {
            "绿色低碳": 80,  # 锂资源/新能源汽车供应链
            "安全发展": 72,  # 关键矿产安全
        },
        "overall_score": 72,
        "rationale": "钾肥+锂盐龙头，锂资源自主可控，十五五安全发展与绿色低碳受益",
    },
    "000858": {  # 五粮液
        "name": "五粮液",
        "alignments": {
            "健康中国": 55,  # 适度饮酒与健康关联较弱
            "区域协调": 50,  # 四川区域龙头
        },
        "overall_score": 45,
        "rationale": "白酒龙头，消费属性强，但与十五五核心产业方向关联度低",
    },
    "601318": {  # 中国平安
        "name": "中国平安",
        "alignments": {
            "数字中国": 60,  # 金融科技/科技赋能金融
            "安全发展": 58,  # 金融安全
        },
        "overall_score": 50,
        "rationale": "综合金融龙头，金融科技有布局，但与十五五产业政策主线匹配度一般",
    },
    "688981": {  # 中芯国际
        "name": "中芯国际",
        "alignments": {
            "新质生产力": 88,  # 晶圆代工
            "安全发展": 90,  # 芯片制造国产替代核心
            "制造强国": 82,
        },
        "overall_score": 88,
        "rationale": "中国大陆晶圆代工龙头，国产替代核心，十五五安全发展+新质生产力",
    },
    "603019": {  # 中科曙光
        "name": "中科曙光",
        "alignments": {
            "新质生产力": 90,  # 智算中心/服务器
            "数字中国": 92,  # 数字基础设施
            "安全发展": 78,  # 信创
        },
        "overall_score": 90,
        "rationale": "智算中心与服务器龙头，数字中国核心基础设施，十五五新质生产力旗舰",
    },
    # ETF
    "510300": {  # 沪深300ETF
        "name": "沪深300ETF",
        "alignments": {
            "制造强国": 80,
            "数字中国": 75,
            "绿色低碳": 70,
            "新质生产力": 75,
        },
        "overall_score": 75,
        "rationale": "宽基指数覆盖十五五全方向，但缺乏聚焦",
    },
    "510500": {  # 中证500ETF
        "name": "中证500ETF",
        "alignments": {
            "新质生产力": 82,
            "制造强国": 80,
            "数字中国": 75,
        },
        "overall_score": 80,
        "rationale": "中盘成长覆盖大量专精特新企业，与十五五新质生产力、制造强国匹配",
    },
    "512100": {  # 中证1000ETF
        "name": "中证1000ETF",
        "alignments": {
            "新质生产力": 85,
            "制造强国": 82,
            "数字中国": 80,
        },
        "overall_score": 82,
        "rationale": "小盘成长覆盖大量专精特新企业，与十五五新质生产力高度匹配",
    },
    "588000": {  # 科创50ETF
        "name": "科创50ETF",
        "alignments": {
            "新质生产力": 95,
            "数字中国": 90,
            "制造强国": 85,
            "健康中国": 80,
        },
        "overall_score": 92,
        "rationale": "科创板核心标的，十五五规划最直接受益的ETF，新质生产力旗舰",
    },
    "159915": {  # 创业板ETF
        "name": "创业板ETF",
        "alignments": {
            "新质生产力": 88,
            "数字中国": 85,
            "绿色低碳": 82,
            "健康中国": 78,
        },
        "overall_score": 85,
        "rationale": "创业板覆盖成长创新企业，十五五多方向受益",
    },
    "515180": {  # 中证红利ETF
        "name": "中证红利ETF",
        "alignments": {
            "安全发展": 65,  # 高股息防御
            "区域协调": 55,
        },
        "overall_score": 55,
        "rationale": "红利策略偏防御，与十五五产业政策主线匹配度一般",
    },
    "518880": {  # 华安黄金ETF
        "name": "华安黄金ETF",
        "alignments": {
            "安全发展": 70,  # 避险资产，安全底线
        },
        "overall_score": 45,
        "rationale": "黄金ETF与十五五产业政策关联度低，主要作为避险配置和康波周期对冲",
    },
}


# ============================================================
# 十五五适配分析器
# ============================================================


class FifteenFivePlanAnalyzer:
    """
    十五五规划适配分析器 v1.0
    借鉴 FinceptTerminal policy_analysis 的政策分析框架
    """

    def __init__(self):
        self.policies = FIFTEEN_FIVE_POLICIES
        self.alignments = STOCK_POLICY_ALIGNMENT

    # ---------- 政策概览 ----------

    def get_policy_overview(self) -> List[Dict]:
        """获取十五五政策方向概览"""
        overview = []
        for name, detail in self.policies.items():
            overview.append(
                {
                    "direction": name,
                    "weight": detail["weight"],
                    "description": detail["description"],
                    "relevance_score": detail["relevance_score"],
                    "target_sectors": detail["target_sectors"],
                    "keywords": detail["keywords"][:5],  # 前5个关键词
                }
            )
        # 按权重排序
        overview.sort(key=lambda x: x["weight"], reverse=True)
        return overview

    # ---------- 持仓适配分析 ----------

    def analyze_holdings(self, positions: Optional[Dict] = None) -> List[Dict]:
        """
        分析持仓与十五五规划的对齐度

        Args:
            positions: 持仓字典 {code: {shares, avg_cost, ...}}，None则使用内置映射

        Returns:
            每个标的的对齐分析结果列表
        """
        results = []

        for code, alignment in self.alignments.items():
            pos_info = positions.get(code, {}) if positions else {}
            shares = pos_info.get("shares", 0) if pos_info else 0

            result = {
                "code": code,
                "name": alignment["name"],
                "shares": shares,
                "overall_score": alignment["overall_score"],
                "rationale": alignment["rationale"],
                "alignments": alignment["alignments"],
                "top_policies": sorted(alignment["alignments"].items(), key=lambda x: x[1], reverse=True)[:3],
            }

            # 分级判定
            if alignment["overall_score"] >= 90:
                result["grade"] = "A - 高度契合"
            elif alignment["overall_score"] >= 75:
                result["grade"] = "B - 良好契合"
            elif alignment["overall_score"] >= 60:
                result["grade"] = "C - 一般契合"
            else:
                result["grade"] = "D - 关联度低"

            results.append(result)

        results.sort(key=lambda x: x["overall_score"], reverse=True)
        return results

    # ---------- 权重调整建议 ----------

    def get_weight_adjustments(self, positions: Optional[Dict] = None) -> List[Dict]:
        """
        基于十五五适配评分生成权重调整建议
        借鉴 QuantDinger policy 矩阵的配置推荐逻辑
        """
        analysis = self.analyze_holdings(positions)
        adjustments = []

        # 计算加权平均适配分
        total_score = sum(a["overall_score"] for a in analysis)
        avg_score = total_score / max(len(analysis), 1)

        for a in analysis:
            deviation = a["overall_score"] - avg_score

            if deviation >= 10:
                suggestion = "强烈建议超配"
                adjust_pct = min(5.0, round(deviation / 10, 1))
            elif deviation >= 5:
                suggestion = "建议超配"
                adjust_pct = min(3.0, round(deviation / 15, 1))
            elif deviation >= -5:
                suggestion = "维持当前权重"
                adjust_pct = 0
            elif deviation >= -10:
                suggestion = "建议适度低配"
                adjust_pct = max(-3.0, round(deviation / 15, 1))
            else:
                suggestion = "建议低配"
                adjust_pct = max(-5.0, round(deviation / 10, 1))

            adjustments.append(
                {
                    "code": a["code"],
                    "name": a["name"],
                    "grade": a["grade"],
                    "fifteen_score": a["overall_score"],
                    "deviation_from_avg": round(deviation, 1),
                    "suggestion": suggestion,
                    "weight_adjust_pct": adjust_pct,
                }
            )

        return adjustments

    # ---------- 报告生成 ----------

    def generate_report(self, positions: Optional[Dict] = None, save_dir: Optional[str] = None) -> str:
        """生成十五五适配分析报告"""
        overview = self.get_policy_overview()
        analysis = self.analyze_holdings(positions)
        adjustments = self.get_weight_adjustments(positions)

        lines = []
        lines.append("# 十五五规划适配分析报告")
        lines.append("")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("**分析引擎**: FifteenFivePlanAnalyzer v1.0")
        lines.append("**规划周期**: 2026-2030（十五五规划）")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 一、十五五政策概览
        lines.append("## 一、十五五规划七大战略方向")
        lines.append("")
        lines.append("| 战略方向 | 权重 | 政策优先级 | 核心领域 |")
        lines.append("|---------|------|-----------|---------|")
        for o in overview:
            keywords_str = "、".join(o["keywords"][:3])
            lines.append(f"| **{o['direction']}** | {o['weight']:.0%} | {o['relevance_score']} | {keywords_str} |")
        lines.append("")

        # 二、持仓适配评级
        lines.append("---")
        lines.append("## 二、持仓标的十五五适配评级")
        lines.append("")
        lines.append("| 标的 | 代码 | 适配评分 | 等级 | 核心契合方向 | 投资逻辑 |")
        lines.append("|------|------|---------|------|------------|---------|")
        for a in analysis:
            top = a["top_policies"]
            top_str = " / ".join(f"{p[0]}({p[1]})" for p in top)
            lines.append(
                f"| **{a['name']}** | {a['code']} | {a['overall_score']} | {a['grade']} | {top_str} | {a['rationale']} |"
            )
        lines.append("")

        # 三、权重调整建议
        lines.append("---")
        lines.append("## 三、十五五驱动的权重调整建议")
        lines.append("")
        lines.append("| 标的 | 十五五评分 | 偏离均值 | 建议 | 调整幅度 |")
        lines.append("|------|-----------|---------|------|---------|")
        for adj in adjustments:
            direction = "+" if adj["weight_adjust_pct"] > 0 else ""
            lines.append(
                f"| {adj['name']} | {adj['fifteen_score']} | {adj['deviation_from_avg']:+.1f} | **{adj['suggestion']}** | {direction}{adj['weight_adjust_pct']:.1f}% |"
            )
        lines.append("")

        # 四、综合建议
        lines.append("---")
        lines.append("## 四、综合投资建议")
        lines.append("")

        # 找出前3名和后3名
        top3 = [a for a in analysis[:3]]
        bottom3 = [a for a in analysis[-3:] if a["overall_score"] < 60]

        lines.append("### 核心配置（十五五高适配）")
        for t in top3:
            lines.append(f"- **{t['name']}**：十五五适配评分 {t['overall_score']}，{t['rationale']}")
        lines.append("")

        if bottom3:
            lines.append("### 关注标的（十五五低适配）")
            for b in bottom3:
                lines.append(f"- **{b['name']}**：十五五适配评分 {b['overall_score']}，{b['rationale']}")
            lines.append("")

        lines.append("---")
        lines.append("*本报告由十五五规划适配分析引擎 v1.0 自动生成*")
        lines.append("*数据参考: TradingAgents-AShare / QuantDinger / FinceptTerminal*")

        report = "\n".join(lines)

        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            filepath = os.path.join(save_dir, f"十五五规划适配_{datetime.now().strftime('%Y%m%d')}.md")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"[FifteenFive] 报告已保存: {filepath}")

        return report


# ============================================================
# 快速测试
# ============================================================

if __name__ == "__main__":
    analyzer = FifteenFivePlanAnalyzer()

    print("\n=== 十五五政策概览 ===")
    for o in analyzer.get_policy_overview():
        print(f"  {o['direction']}: 权重={o['weight']:.0%}, 优先级={o['relevance_score']}")

    print("\n=== 持仓适配分析 ===")
    for a in analyzer.analyze_holdings():
        print(f"  {a['name']}: 评分={a['overall_score']}, 等级={a['grade']}")

    print("\n=== 权重调整建议 ===")
    for adj in analyzer.get_weight_adjustments():
        print(f"  {adj['name']}: {adj['suggestion']} ({adj['weight_adjust_pct']:+.1f}%)")
