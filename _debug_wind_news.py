# -*- coding: utf-8 -*-
"""调试 Wind MCP 新闻 - 关键词匹配诊断"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(__file__))

from wind_mcp_fetcher import wind_search_news

# 原始关键词
keywords_positive = ["预增", "增长", "中标", "订单", "扩产", "出海", "份额提升", "超预期", "盈利", "放量", "景气"]
keywords_negative = ["预减", "下滑", "亏损", "处罚", "减持", "质押", "暴雷", "下调", "断供", "降价", "过剩"]

# 扩展关键词 (覆盖 Wind MCP 返回的常见新闻类型)
extended_positive = keywords_positive + [
    "涨停", "回购", "增持", "推荐", "业绩", "突破", "新高", "上调",
    "入选", "合作", "研发", "投产", "启动", "上线", "推出", "成长",
    "提升", "向好", "强势", "爆买", "加仓", "登顶", "首破",
]
extended_negative = keywords_negative + [
    "跌停", "下跌", "回落", "减持", "问询", "风险", "警告",
    "暴跌", "萎缩", "下降", "破发", "破净", "违规", "诉讼",
    "退市", "停牌", "调整", "走弱", "承压", "利空",
]

test_cases = [
    ("688041", "海光信息"),
    ("601088", "中国神华"),
    ("000333", "美的集团"),
]

all_results = []
for code, name in test_cases:
    query = f"{name} {code}".strip()
    items = wind_search_news(query, top_k=5)
    result = {
        "code": code,
        "name": name,
        "items_count": len(items),
        "items": [],
    }
    for it in items:
        title = it.get("title", "") or ""
        snippet = it.get("snippet", "") or ""
        pub_time = it.get("publish_time", "") or ""
        text = f"{title} {snippet}".lower()

        # 用扩展关键词匹配
        pos_hits_orig = [k for k in keywords_positive if k in text]
        neg_hits_orig = [k for k in keywords_negative if k in text]
        pos_hits_ext = [k for k in extended_positive if k in text]
        neg_hits_ext = [k for k in extended_negative if k in text]

        result["items"].append({
            "title": title,
            "snippet": snippet[:300],
            "publish_time": pub_time,
            "pos_hits_orig": pos_hits_orig,
            "neg_hits_orig": neg_hits_orig,
            "pos_hits_ext": pos_hits_ext,
            "neg_hits_ext": neg_hits_ext,
        })
    all_results.append(result)

# 保存到 JSON (避免控制台编码问题)
out_file = "_debug_wind_news_result.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)

print(f"结果已保存到 {out_file}")
print(f"共测试 {len(all_results)} 个标的")
for r in all_results:
    print(f"\n{r['name']} ({r['code']}): {r['items_count']} 条新闻")
    for i, it in enumerate(r["items"][:3]):
        print(f"  [{i+1}] {it['publish_time']} | {it['title'][:60]}")
        print(f"      原关键词命中: 正={it['pos_hits_orig']}, 负={it['neg_hits_orig']}")
        print(f"      扩展关键词命中: 正={it['pos_hits_ext']}, 负={it['neg_hits_ext']}")
