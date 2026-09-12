"""生成《三百万ETF期权五年方略_v9.3_执行增强版.docx》.

基于 v9.2 忠实修订 5 处（不覆盖 v9.2）:
  1. 统一回撤目标口径 (<15% 常态目标 vs L4=-18% 灾难硬上限)
  2. 重算股债双杀 (用 511260 2022 实测 +2.65% 修正原 ≤-13% 估计)
  3. 补 73% 裸奔缺口说明 (§六 保护范围)
  4. 新增期权成本敏感性表 (§三 之后)
  5. 理清 1.2% 常态硬上限 vs 1.5% 熔断期临时上浮 的优先级
"""

from __future__ import annotations

from pathlib import Path

from docx import Document

SRC = Path("300万计划/三百万ETF期权五年方略_v9.2_执行增强版.docx")
DST = Path("300万计划/三百万ETF期权五年方略_v9.3_执行增强版.docx")


def _set_text(par, text: str) -> None:
    """覆盖段落文本 (Normal 段落, 单 run 足够)."""
    par.text = text


def main() -> None:
    doc = Document(str(SRC))

    # --- Fix 1: 铁律一 预算优先级 (para 索引 3) ---
    _set_text(
        doc.paragraphs[3],
        "【铁律一·风控优先】当组合回撤触发 L1-L4 任一熔断等级时，无视 IV Rank 档位限制，"
        "保护比例优先上调至该等级对应的「保护仓位」上限；期权净预算临时上浮至 1.5%/年"
        "（仅熔断触发期的临时上限，须于等级回落至 ≤L2 后回退至年度常规硬上限 1.2%；"
        "1.2% 为常态硬上限，优先级高于 1.5% 临时上浮）。",
    )

    # --- Fix 2: 最终目标 回撤口径 (para 7) ---
    _set_text(
        doc.paragraphs[7],
        "【最终目标】力争 4.3 年净年化 5%-7% · 最大回撤 常态 < 15%"
        "（灾难情景硬上限 20%，对应 L4 熔断触发线 -18%；常态目标与灾难硬上限须区分，"
        "避免与 L4=-18% 混淆） · 权益敞口权重 50%-55%。",
    )

    # --- Fix 3: §六 保护范围 补 73% 裸奔缺口 (para 19) ---
    _set_text(
        doc.paragraphs[19],
        "【保护范围】仅覆盖沪深300核心仓（90万）。Collar 50%保护比例对应名义 45万，占权益 27%；"
        "即 73% 权益（红利45+500 15+卫星15，合计75万）明确未受期权保护"
        "（详见《300万计划实测附录_v9.3》：权益放大 -35% 情景下组合回撤达 -19.02%，击穿 15% 目标）。",
    )

    # --- Fix 4: 股债双杀 重算 (para 23) ---
    _set_text(
        doc.paragraphs[23],
        "【全组合极端回撤暴露】中性情景 -9.8%；2022 实测（511260 +2.65% 正向对冲）"
        "全组合最大回撤 -11.17%，优于原「≤-13%」估计，仍在 15% 约束内。"
        "但须提示：该结论依赖『衰退式股债分化』regime；若进入『利率上行型』股债双杀"
        "（长债同步下跌），对冲层反向，原 -13% 估计偏乐观，回撤需重新评估。",
    )

    # --- Fix 5: 熔断表 L4 触发线口径 (table 2, L4 行 触发回撤 单元格) ---
    t2 = doc.tables[2]
    # 行序: 标题 + L0..L4 -> L4 是第 5 行 (index 5)
    l4_row = t2.rows[5]
    l4_row.cells[1].text = "-18%（>15% 目标，属灾难硬上限）"

    # --- 新增 期权成本敏感性表 (插入 §三 预算表 table 1 之后) ---
    heading = doc.add_paragraph(
        "【v9.3 新增】期权成本敏感性（净收益 5% 下限脆弱性）"
    )
    sens = doc.add_table(rows=1, cols=4)
    sens.style = "Light Grid Accent 1"
    hdr = sens.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = (
        "期权年成本",
        "净年化",
        "距 5% 下限",
        "击穿 5%?",
    )
    rows = [
        ("0.5%", "5.36%", "+0.36pp", "否"),
        ("0.8%", "5.06%", "+0.06pp", "否"),
        ("1.0%", "4.86%", "-0.14pp", "是"),
        ("1.2%", "4.66%", "-0.34pp", "是"),
        ("1.5%", "4.36%", "-0.64pp", "是"),
    ]
    for r in rows:
        cells = sens.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = v
    note = doc.add_paragraph(
        "5% 下限的成本击穿临界点 = 0.86%/年；计划预算上限 1.2%/1.5% 均越过该点，"
        "危机年份（预算被实质使用）净收益必然 < 5%。净收益 = 毛 5.86% − 期权年成本。"
    )

    # 将 heading / sens / note 移动到 table1 之后
    t1 = doc.tables[1]
    t1._tbl.addnext(heading._p)
    heading._p.addnext(sens._tbl)
    sens._tbl.addnext(note._p)

    # --- 修订记录追加 v9.3 条目 ---
    doc.add_paragraph(
        "⑨ v9.3 实测回填：补充回撤口径说明（<15% 常态 vs L4=-18% 灾难）、"
        "2022 实测重算股债双杀（-11.17% 优于原 ≤-13%）、§六 补 73% 裸奔缺口、"
        "新增期权成本敏感性表、理清 1.2% 常态硬上限 vs 1.5% 熔断期临时上浮优先级。"
    )

    doc.save(str(DST))
    print(f"已生成: {DST}")


if __name__ == "__main__":
    main()
