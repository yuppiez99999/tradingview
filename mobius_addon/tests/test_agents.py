"""mobius_addon 单元测试：mock 网络 / GLM-5 / 文件读取，验证各智能体逻辑。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _common as _c  # noqa: E402


def _set_glm5(fn):
    _c._GLM5 = fn


def _reset_glm5():
    _c._GLM5 = None


def test_file_loader_txt(tmp_path):
    from agents.file_loader import load_text

    p = tmp_path / "note.md"
    p.write_text("低波动率异象 摘要内容", encoding="utf-8")
    assert "低波动率异象" in load_text(str(p))


def test_file_loader_unsupported(tmp_path):
    from agents.file_loader import load_text

    p = tmp_path / "x.exe"
    p.write_text("x", encoding="utf-8")
    try:
        load_text(str(p))
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass


def test_extractor_glm5_parse():
    _set_glm5(
        lambda m: json.dumps(
            {
                "title": "T",
                "authors": ["A"],
                "year": 2024,
                "method_type": "factor",
                "factor_logic": "动量因子",
                "backtest_setup": "A股日频",
                "key_findings": "显著",
                "reproducibility": "high",
            },
            ensure_ascii=False,
        )
    )
    try:
        from agents.extractor import extract_paper

        d = extract_paper("摘要文本")
        assert d["method_type"] == "factor"
        assert d["factor_logic"] == "动量因子"
    finally:
        _reset_glm5()


def test_extractor_rule_fallback():
    _set_glm5(lambda m: "无法解析的回复")
    try:
        from agents.extractor import extract_paper

        d = extract_paper("这是一篇关于低波动率因子的论文摘要")
        assert "低波动率" in d["factor_logic"]
    finally:
        _reset_glm5()


def test_report_extractor_normalize_rating():
    from agents.report_extractor import normalize_rating

    assert normalize_rating("买入") == "buy"
    assert normalize_rating("BUY") == "buy"
    assert normalize_rating("中性") == "neutral"
    assert normalize_rating("xyz") == "neutral"


def test_report_extractor_rule_fallback():
    _set_glm5(lambda m: "no json")
    try:
        from agents.report_extractor import extract_report

        d = extract_report("强烈推荐 600519 贵州茅台 目标价 2000")
        assert d["stock_code"] == "600519"
        assert d["rating"] == "neutral"
    finally:
        _reset_glm5()


def test_system_judge_position_conflict(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    positions = {
        "meta": {"total_capital": 5000000},
        "positions": {
            "600519.SH": {
                "code": "600519.SH",
                "name": "贵州茅台",
                "target_weight": 0.12,
                "sector": "消费",
                "style": "消费",
            }
        },
    }
    (cfg / "positions.json").write_text(json.dumps(positions), encoding="utf-8")
    import agents.system_judge as sj

    saved = _c.get_sys_root
    _c.get_sys_root = lambda: str(tmp_path)
    try:
        res = sj.judge({"stock_code": "600519", "industry": "消费"})
        assert res["verdict"] == "position_conflict"
        assert "position_check" in res["judge_details"]
    finally:
        _c.get_sys_root = saved


def test_system_judge_low_weight_no_conflict(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    positions = {
        "meta": {"total_capital": 5000000},
        "positions": {
            "600519.SH": {
                "code": "600519.SH",
                "name": "贵州茅台",
                "target_weight": 0.02,
                "sector": "消费",
                "style": "消费",
            }
        },
    }
    (cfg / "positions.json").write_text(json.dumps(positions), encoding="utf-8")
    import agents.system_judge as sj

    saved = _c.get_sys_root
    _c.get_sys_root = lambda: str(tmp_path)
    try:
        res = sj.judge({"stock_code": "600519", "industry": "消费"})
        assert res["verdict"] != "position_conflict"
    finally:
        _c.get_sys_root = saved


def test_factor_coverage_scan(tmp_path):
    af = tmp_path / "utils" / "alpha_factor"
    af.mkdir(parents=True)
    (af / "momentum.py").write_text(
        "class MomentumFactor:\n    pass\n", encoding="utf-8"
    )
    import agents.synthesizer as syn

    saved = _c.get_sys_root
    _c.get_sys_root = lambda: str(tmp_path)
    try:
        cov = syn.scan_factor_coverage("使用 Momentum 因子构造组合")
        assert "existing" in cov
        assert "momentumfactor" in cov or "momentum" in cov
    finally:
        _c.get_sys_root = saved


def test_retriever_dedup():
    import agents.retriever as r

    r.search_arxiv = lambda q, n: [
        {
            "paper_id": "arxiv:1",
            "source": "arxiv",
            "title": "Low Volatility Anomaly",
            "authors": ["X"],
            "year": 2024,
            "url": "",
            "citations": 10,
            "abstract": "abc",
        }
    ]
    r.search_s2 = lambda q, n: [
        {
            "paper_id": "s2:1",
            "source": "s2",
            "title": "Low Volatility Anomaly",
            "authors": ["Y"],
            "year": 2023,
            "url": "",
            "citations": 5,
            "abstract": "def",
        }
    ]
    papers = r.retrieve("x", 10)
    assert len(papers) == 1
    assert papers[0]["citations"] == 10
