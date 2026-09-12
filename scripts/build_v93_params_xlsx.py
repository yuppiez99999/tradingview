"""生成《v9.3量化参数表.xlsx》.

基于 v9.2量化参数表.xlsx 新增:
  - sheet '净收益敏感性': 期权成本多档 -> 净年化, 5% 下限击穿临界点 0.86%/年
  - sheet 'v9.3修订说明': 5 处文本修订的留痕
不覆盖 v9.2 原文件.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

SRC = Path("300万计划/v9.2量化参数表.xlsx")
DST = Path("300万计划/v9.3量化参数表.xlsx")


def main() -> None:
    wb = openpyxl.load_workbook(str(SRC))

    # --- 净收益敏感性 sheet ---
    ws = wb.create_sheet("净收益敏感性")
    ws.append(["期权年成本", "净年化(毛5.86%-成本)", "距5%下限", "击穿5%?"])
    data = [
        ("0.5%", "5.36%", "+0.36pp", "否"),
        ("0.8%", "5.06%", "+0.06pp", "否"),
        ("1.0%", "4.86%", "-0.14pp", "是"),
        ("1.2%", "4.66%", "-0.34pp", "是"),
        ("1.5%", "4.36%", "-0.64pp", "是"),
    ]
    for r in data:
        ws.append(list(r))
    ws.append([])
    ws.append(["5% 下限成本击穿临界点 = 0.86%/年"])
    ws.append(["计划预算上限 1.2%/1.5% 均越过该点, 危机年份净收益必然 < 5%"])

    # --- v9.3 修订说明 sheet ---
    rs = wb.create_sheet("v9.3修订说明")
    rs.append(["编号", "修订项", "说明"])
    fixes = [
        ("1", "统一回撤目标口径", "最终目标: 常态 <15% 为目标, 灾难硬上限20% 对应 L4=-18%; 消除与 L4 触发线的口径矛盾 (L4 单元格已标注)"),
        ("2", "重算股债双杀", "2022 实测 511260 +2.65% 正向对冲, 全组合 MDD -11.17% 优于原 ≤-13%; 提示利率上行型股债双杀下对冲层反向"),
        ("3", "补 73% 裸奔缺口", "§六 保护范围明确: 73% 权益 (红利+500+卫星 75万) 未受期权保护; 引用实测附录股灾扫描 -35%->-19.02%"),
        ("4", "新增期权成本敏感性表", "§三 之后新增: 成本 0.5/0.8/1.0/1.2/1.5% -> 净年化, 5% 下限击穿临界点 0.86%/年"),
        ("5", "理清 1.2% vs 1.5% 优先级", "铁律一: 1.5% 仅熔断期临时上浮, 回落≤L2 须回退至 1.2% 常态硬上限; 1.2% 优先级更高"),
    ]
    for r in fixes:
        rs.append(list(r))

    # 把新 sheet 移到合适位置 (放在最前两张之后)
    wb.move_sheet("净收益敏感性", offset=-len(wb.sheetnames) + 2)
    wb.move_sheet("v9.3修订说明", offset=-len(wb.sheetnames) + 3)

    wb.save(str(DST))
    print("已生成:", DST)
    print("sheets:", wb.sheetnames)


if __name__ == "__main__":
    main()
