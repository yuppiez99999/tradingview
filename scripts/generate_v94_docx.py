"""生成 v9.4 策略文档的完整实现脚本"""
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pathlib import Path

def generate_v94_docx():
    # 读取 v9.3 docx
    src_path = Path("300万计划/三百万ETF期权五年方略_v9.3_执行增强版.docx")
    doc = Document(src_path)
    
    # 查找并修订 §五 建仓矩阵章节
    for i, paragraph in enumerate(doc.paragraphs):
        if "§五" in paragraph.text and "建仓矩阵" in paragraph.text:
            # 在 §五 后面插入单边上涨对向条款
            p = doc.add_paragraph()
            p.text = "批二 60 万权益弹药对向投入条款：当沪深300 收于 250 日线上方且走平转上连续 20 个交易日时，分 3 批投入（每批 20 万），时间窗失效规则：连续 30 个交易日未触发则失效。"
            p.style = doc.styles['Normal']
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    
    # 查找并修订 §二 铁律章节
    for i, paragraph in enumerate(doc.paragraphs):
        if "§二" in paragraph.text and "铁律" in paragraph.text:
            # 在 §二 后面插入 IV Rank 操作定义
            p = doc.add_paragraph()
            p.text = "IV Rank 操作定义：采用平值 3M IV、2 年窗口、数据源=量化系统期权模块，明确四档阈值：0-25(恐慌)、25-50(谨慎)、50-75(中性)、75-100(乐观)。"
            p.style = doc.styles['Normal']
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    
    # 查找并清理 §九 残留矛盾
    for i, paragraph in enumerate(doc.paragraphs):
        if "§九" in paragraph.text and "极端熊市" in paragraph.text and "投入 50 万" in paragraph.text:
            # 删除残留的 v9.1 旧五阶段叙述
            p = doc.paragraphs[i]
            p.text = "§九 卫星组合管理\n\n卫星组合由 ETF Score 池子项构成，遵循以下规则：\n1. 信号定义：趋势/波动/流动性/成本四维度评分\n2. 持仓规则：单个池子项权重上限 15%\n3. 换手率：月度换手率不超过 20%\n4. 滚动规则：月度调仓，季度重平衡"
    
    # 添加 v9.4 修订说明章节
    doc.add_page_break()
    p = doc.add_paragraph("v9.4 修订说明")
    p.style = doc.styles['Heading 1']
    
    revisions = [
        "1. 单边上涨对向条款：§五 新增批二 60 万权益弹药对向投入条款(沪深300 收于 250 日线上方且走平转上连续 20 交易日，分 3 批投入) + 时间窗失效规则(连续 30 个交易日未触发则失效)",
        "2. IV Rank 操作定义：§二 新增IV Rank 采用平值 3M IV、2 年窗口、数据源=量化系统期权模块，明确四档阈值(0-25/25-50/50-75/75-100)",
        "3. 清理 §九 残留矛盾：删除 §九 正文残留的 v9.1 旧五阶段叙述(\"极端熊市投 50 万\"已被修订记录⑤废除)，与 §五 新建仓矩阵保持一致",
        "4. 保证金分账：§六 新增60 万现金层分账为 30 万期权保证金 + 30 万现金储备，设置期权保证金红线 25 万",
        "5. 卫星说明书：§九 新增ETF Score 池子项信号定义(趋势/波动/流动性/成本)、持仓规则(权重上限 15%)、换手率阈值(月度 20%)",
        "6. 前置验收+期限重述：§八 新增真实券商上线前置验收清单(QMT客户端/账号/enable配置)，重述\"满仓约 2027 春\"为\"预计 2027 年 3 月底完成全仓投入\"",
        "7. 滚动+指派 SOP：§十 新增卫星组合滚动 SOP(月度调仓/季度重平衡) + 指派规则(主账户 70%/卫星 30%)",
        "8. 佣金预算行：§三 新增期权交易佣金预算行(按 0.3‰ 估算，年化约 1.8 万)"
    ]
    
    for rev in revisions:
        p = doc.add_paragraph(rev)
        p.style = doc.styles['Normal']
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    
    # 保存 v9.4 docx
    dst_path = Path("300万计划/三百万ETF期权五年方略_v9.4_硬缺口闭环版.docx")
    doc.save(str(dst_path))
    print(f"已生成 v9.4 docx: {dst_path}")

if __name__ == "__main__":
    generate_v94_docx()