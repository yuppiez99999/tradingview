# -*- coding: utf-8 -*-
"""v9.4 -> v9.5 生成脚本（Wind 数据独立核验回填，2026-09-13）
四项修改：①511260 标签修正 ②黄金预期降档 6.0%->2.0% ③危机年目标条款+目标口径下调 ④权益上限前置(验收前45%)
"""
import copy, shutil
import docx
from docx.text.paragraph import Paragraph
import openpyxl
from openpyxl.styles import Font

BASE = r"E:\各种PY程序\28-终极量化交易系统8.4\300万计划"

FINAL_TARGET_OLD = "【最终目标】力争 4.3 年净年化 5%-7%"
FINAL_TARGET_NEW = ("【最终目标】力争 4.3 年净年化 4.5%-5.5%（中性带 4%-5%；乐观情景 6%-7.5%，不构成承诺；"
                    "v9.5 按 Wind 独立核验自 5%-7% 下调）· 最大回撤 常态 < 15%（灾难情景硬上限 20%，对应 L4 熔断触发线 -18%；"
                    "常态目标与灾难硬上限须区分，避免与 L4=-18% 混淆） · 权益敞口权重 验收前 40%-45% / 验收后 50%-55%。")

HARD_OLD = "③ 2030-12-15 强制全仓清算窗口。"
HARD_NEW = ("③ 2030-12-15 强制全仓清算窗口；④ §八券商期权验收清单全勾前，权益上限 45%（对应 L1），"
            "不因批二触发或对向条款而突破。")

BREAKEVEN_OLD_PREFIX = "5% 下限的成本击穿临界点 = 0.86%/年"
BREAKEVEN_NEW = ("【v9.5 修正】组合毛收益按黄金降档后为 5.24%/年：5% 净下限的成本击穿临界点降至 0.24%/年，"
                 "常态预算 0.8% 已越过 → 5% 不再是中性可达下限，目标口径已下调为中性净年化 4%-5%（见 §一）。"
                 "危机年份预算 1.2%-1.5% 用满时净收益约 3.7%-4.0%，目标切换为『控回撤、不追收益』。"
                 "净收益 = 毛 5.24% − 期权年成本。")

BUDGET_OLD = "【预算边界】权益上限 55%（165万）"
BUDGET_NEW = "【预算边界】权益上限 55%（165万；§八验收全勾前 45%）"

EVAL_OLD_PREFIX = "方案骨架合理、规则闭环"
EVAL_NEW = ("方案骨架合理、规则闭环：v9.2 统一口径、v9.3 实测回填、v9.4 补齐执行规格、v9.5 完成 Wind 数据独立核验并下调收益口径。"
            "中性净年化约 4.4%-4.7%（毛 5.24% − 期权成本 0.5%-0.8%），中性带 4%-5%；乐观情景（权益牛+黄金不回吐）6%-7.5%；"
            "危机年（预算用满 1.2%-1.5%）净约 3.7%-4.0%，目标切换为控回撤（见 §三 敏感性表）。"
            "2022 回放经 Wind 独立复算一致（全额部署 MDD -10.8%；权益 -35% 情景 MDD -18.2%）。"
            "执行前提 = §八 清单全部落地（验收前权益上限 45%），此前任何资金不进入真实账户。")

KOUJING_ANCHOR = "【口径说明】8% 仅作为乐观情景上限"
KOUJING_V95 = ("【口径说明·v9.5（Wind 独立核验 2026-09-13）】①511260 经 Wind 快照核验为『国泰十年国债ETF』（非 30 年国债ETF），"
               "σ 实测 2.0%，表内由 σ<3.5% 修正为 σ<2.5%；如需 30 年久期应改用 511090 并重算对冲层风险预算。"
               "②黄金 518880 近 5 年 +144%、2025 单年 +56.9%，预期收益由 6.0% 降档至 2.0%（内含均值回归风险）。"
               "③组合毛收益由 5.86% 修正为 5.24%，净收益敏感性同步重算。"
               "④2022 回放经 Wind 数据独立复算：全额部署年 -6.4% / MDD -10.8%，权益 -35% 情景 MDD -18.2%，与实测附录一致。")
CRISIS_RULE = ("【危机年目标切换（v9.5 新增）】凡年度内触发 L2 及以上熔断，该年度目标切换为『控回撤、不追收益』："
               "以组合回撤不击穿 -18%（L4 线）为硬目标；期权预算按 1.2%-1.5% 用满不视为失败，"
               "净收益低于 5% 不作为复盘不合格或终止计划的依据。")

REVISION_ENH = ("⑪ v9.5 Wind 核验回填（2026-09-13）：①修正 511260 名称（Wind 快照=十年国债ETF国泰，σ<3.5%→<2.5%）；"
                "②黄金预期 6.0%→2.0%，组合毛收益 5.86%→5.24%；③目标口径下调：中性净年化 4%-5%（原 5%-7% 移入乐观情景），"
                "新增危机年『控回撤、不追收益』条款；④新增权益上限前置：§八验收全勾前 45%；"
                "⑤2022 回放经 Wind 独立复算一致（-6.4%/-10.8%，权益-35%情景 -18.2%）。")
REVISION_GAP = ("v9.5 修订说明（Wind 数据独立核验，2026-09-13）：①修正 511260 名称（Wind 快照=十年国债ETF国泰，σ<3.5%→<2.5%）；"
                "②黄金预期 6.0%→2.0%，组合毛收益 5.86%→5.24%，中性净年化 4%-5%（原 5%-7% 移入乐观情景）；"
                "③新增危机年『控回撤、不追收益』条款；④新增权益上限前置：§八验收全勾前 45%；"
                "⑤2022 回放经 Wind 独立复算一致（-6.4%/-10.8%，权益-35%情景 -18.2%）。")

SENS_ROWS = {  # 成本 -> (净年化, 距5%下限, 击穿?)
    "0.5%": ("4.74%", "-0.26pp", "是"),
    "0.8%": ("4.44%", "-0.56pp", "是"),
    "1.0%": ("4.24%", "-0.76pp", "是"),
    "1.2%": ("4.04%", "-0.96pp", "是"),
    "1.5%": ("3.74%", "-1.26pp", "是"),
}


def set_text(par, text):
    runs = par.runs
    if not runs:
        par.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r.text = ""


def insert_after(par, text):
    new_p = copy.deepcopy(par._p)
    par._p.addnext(new_p)
    np_ = Paragraph(new_p, par._parent)
    set_text(np_, text)
    return np_


def iter_all_paragraphs(doc):
    for p in doc.paragraphs:
        yield p
    for tb in doc.tables:
        for row in tb.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p


def patch_docx(src, dst, is_gap_version):
    shutil.copyfile(src, dst)
    doc = docx.Document(dst)

    # 1) 顺序全文替换（段落级，命中即整段重写为替换文本）
    done = set()
    for p in iter_all_paragraphs(doc):
        t = p.text
        if not t.strip():
            continue
        if t.strip().startswith("【最终目标】力争 4.3 年净年化 5%-7%"):
            set_text(p, FINAL_TARGET_NEW); done.add("target")
        elif HARD_OLD in t and "【硬约束】" in t:
            set_text(p, t.replace(HARD_OLD, HARD_NEW)); done.add("hard")
        elif t.strip().startswith(BREAKEVEN_OLD_PREFIX):
            set_text(p, BREAKEVEN_NEW); done.add("breakeven")
        elif BUDGET_OLD in t:
            set_text(p, t.replace(BUDGET_OLD, BUDGET_NEW)); done.add("budget")
        elif t.strip().startswith(EVAL_OLD_PREFIX) and "中性净年化约" in t:
            set_text(p, EVAL_NEW); done.add("eval")
        elif t.strip().startswith("三百万 ETF+期权对冲交易执行手册 · v9."):
            set_text(p, "三百万 ETF+期权对冲交易执行手册 · v9.5"); done.add("title")
        elif "本版本：v9." in t and "适用期限" in t:
            set_text(p, "适用期限：2026-09 ~ 2030-12（实际4.3年）｜资金：300万 | 标的：A股ETF + 个股期权 | "
                        "本版本：v9.5（基于 v9.4 修订 · Wind 数据独立核验回填 2026-09-13）"); done.add("verline")

    # 2) 【口径说明】后插入两段 v9.5 条款
    for p in list(iter_all_paragraphs(doc)):
        if p.text.strip().startswith(KOUJING_ANCHOR):
            insert_after(p, CRISIS_RULE)
            insert_after(p, KOUJING_V95)
            done.add("koujing")
            break

    # 3) 表格单元格修改
    for tb in doc.tables:
        for row in tb.rows:
            cells = [c.text.strip() for c in row.cells]
            joined = " | ".join(cells)
            if "511260" in joined:  # 对冲层国债行
                for c in row.cells:
                    if "30年国债ETF" in c.text:
                        set_text(c.paragraphs[0], "十年国债ETF国泰(511260·Wind核验)")
                    if c.text.strip() == "3.5%":
                        set_text(c.paragraphs[0], "2.5%")
            if "518880" in joined:  # 黄金行：预期收益 6.0% -> 2.0%
                for i, c in enumerate(row.cells):
                    if c.text.strip() == "6.0%" and i >= 3:
                        set_text(c.paragraphs[0], "2.0%")
            if joined.startswith("合计") or " 5.86%(毛)" in joined:
                for c in row.cells:
                    if "5.86%" in c.text:
                        set_text(c.paragraphs[0], c.text.replace("5.86%", "5.24%"))
            if "L0" in cells[0] if cells else False:
                pass
            # 熔断表 L0 行
            if cells and cells[0].startswith("L0"):
                for c in row.cells:
                    if c.text.strip() == "55%":
                        set_text(c.paragraphs[0], "45%(验收前)/55%(验收后)")
            # 建仓矩阵 批二行
            if cells and cells[0].startswith("批二"):
                for c in row.cells:
                    if "55%(满档)" in c.text:
                        set_text(c.paragraphs[0], c.text.replace("55%(满档)", "55%(满档·验收前45%)"))
        # 敏感性表（仅限表头含"期权年成本"的表，且仅匹配第 0 列）
        head = " | ".join(c.text.strip() for c in tb.rows[0].cells)
        if "期权年成本" in head:
            for row in tb.rows[1:]:
                txt = row.cells[0].text.strip()
                if txt in SENS_ROWS:
                    vals = SENS_ROWS[txt]
                    set_text(row.cells[1].paragraphs[0], vals[0])
                    set_text(row.cells[2].paragraphs[0], vals[1])
                    set_text(row.cells[3].paragraphs[0], vals[2])
            for c in tb.rows[0].cells:
                if "毛5.86%" in c.text:
                    set_text(c.paragraphs[0], c.text.replace("毛5.86%", "毛5.24%"))

    # 4) 修订记录追加
    anchor_keys = ("⑩ v9.4 执行规格补齐", "8. 佣金预算行", "8．佣金预算行")
    anchor = None
    for p in doc.paragraphs:
        if any(k in p.text for k in anchor_keys):
            anchor = p
    if anchor is not None:
        insert_after(anchor, REVISION_GAP if is_gap_version else REVISION_ENH)
        done.add("revision")
    else:  # 兜底：文末追加
        p = doc.add_paragraph(REVISION_GAP if is_gap_version else REVISION_ENH)
        done.add("revision(fallback)")

    doc.save(dst)
    return done


def patch_xlsx(src, dst):
    shutil.copyfile(src, dst)
    wb = openpyxl.load_workbook(dst)

    ws = wb["净收益敏感性"]
    ws["B1"] = "净年化(毛5.24%-成本)"
    for r, cost in enumerate(["0.5%", "0.8%", "1.0%", "1.2%", "1.5%"], start=2):
        net, gap, brk = SENS_ROWS[cost]
        assert str(ws.cell(row=r, column=1).value).strip() == cost
        ws.cell(row=r, column=2, value=net)
        ws.cell(row=r, column=3, value=gap)
        ws.cell(row=r, column=4, value=brk)
    ws["A8"] = "5% 下限成本击穿临界点 = 0.24%/年（毛 5.24% 口径）"
    ws["A9"] = "常态预算 0.8% 已越过临界点 → v9.5 目标口径下调为中性净年化 4%-5%（危机年目标=控回撤）"

    ws2 = wb["参数表"]
    ws2["B7"] = "十年国债ETF国泰(511260·Wind核验)"
    ws2["I7"] = "σ<2.5%(实测2.0%)"
    ws2["I8"] = "σ~15%·预期2.0%(v9.5降档)"

    ws3 = wb["量化参数"]
    ws3["C2"] = "45%(验收前)/55%(验收后)"
    ws3["F2"] = "v9.5: 券商期权验收前上限45%"

    if "v9.5修订说明" in wb.sheetnames:
        del wb["v9.5修订说明"]
    ws4 = wb.create_sheet("v9.5修订说明")
    ws4.append(["编号", "修订项", "说明"])
    rows = [
        ("1", "511260 标签修正", "Wind 快照核验 511260.SH=国泰十年国债ETF（非 30 年国债ETF）；σ<3.5% 修正为 σ<2.5%（实测 2.0%）；如需 30 年久期另议 511090 并重算对冲层风险预算"),
        ("2", "黄金预期降档", "6.0%→2.0%（近5年+144%、2025 年+56.9% 后的均值回归风险）；组合毛收益 5.86%→5.24%"),
        ("3", "目标口径下调", "中性净年化 4%-5%（原 5%-7% 移入乐观情景 6%-7.5%）；5% 下限成本击穿临界点 0.86%→0.24%/年"),
        ("4", "危机年目标条款", "触发 L2 及以上年份目标切换『控回撤、不追收益』，以回撤不击穿 -18% 为硬目标，预算 1.2%-1.5% 用满不视为失败"),
        ("5", "权益上限前置", "§八券商期权验收清单全勾前权益上限 45%（对应 L1），验收后恢复 55%"),
        ("6", "2022 回放 Wind 复算", "全额部署 -6.4%/-10.8%；k=1.3 → -8.9%/-14.3%；k=1.638（权益-35%）→ -11.8%/-18.2%，与实测附录一致（数据源：wind-mcp-skill CLI，2026-09-13）"),
    ]
    for r in rows:
        ws4.append(r)
    for c in ws4[1]:
        c.font = Font(bold=True)
    ws4.column_dimensions["A"].width = 6
    ws4.column_dimensions["B"].width = 22
    ws4.column_dimensions["C"].width = 110

    wb.save(dst)


if __name__ == "__main__":
    import os
    jobs = [
        (os.path.join(BASE, "三百万ETF期权五年方略_v9.4_硬缺口闭环版.docx"),
         os.path.join(BASE, "三百万ETF期权五年方略_v9.5_硬缺口闭环版.docx"), True),
        (os.path.join(BASE, "三百万ETF期权五年方略_v9.4_执行增强版.docx"),
         os.path.join(BASE, "三百万ETF期权五年方略_v9.5_执行增强版.docx"), False),
    ]
    for src, dst, gap in jobs:
        d = patch_docx(src, dst, gap)
        print(os.path.basename(dst), "->", sorted(d))
    patch_xlsx(os.path.join(BASE, "v9.4量化参数表.xlsx"),
               os.path.join(BASE, "v9.5量化参数表.xlsx"))
    print("v9.5量化参数表.xlsx -> done")
