"""批量搜索微信读书书籍并获取详情."""

import json
import os
import time
import urllib.request

API = "https://i.weread.qq.com/api/agent/gateway"
KEY = os.environ.get("WEREAD_API_KEY", "")
SKILL_VER = "1.0.4"

BOOKS = [
    ("金融机器学习", "López de Prado"),
    ("Machine Learning for Asset Managers", "López de Prado"),
    ("寻找Alpha", "Igor Tulchinsky"),
    ("统计学习要素", "Hastie"),
    ("主动投资组合管理", "Grinold & Kahn"),
    ("因子投资", "石川"),
    ("机器学习与资产定价", "石川"),
    ("鲁棒投资组合优化", "Fabozzi"),
    ("控制论", "Wiener"),
    ("阿什比 控制论", "Ashby"),
    ("系统之美", "Meadows"),
    ("复杂", "梅拉妮"),
    ("交易策略评估", "Pardo"),
    ("主动投资组合管理进展", "Sorensen"),
    ("量化风险管理", "McNeil"),
    ("动态对冲", "Taleb"),
    ("期权期货衍生品", "Hull"),
    ("量化投资 Python", "蔡立耑"),
    ("行为金融学", "饶育蕾"),
]


def call_api(payload):
    payload["skill_version"] = SKILL_VER
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)  # nosec B310  # 固定 HTTPS 官方端点 i.weread.qq.com, 非用户可控 scheme
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def search_book(keyword):
    return call_api(
        {"api_name": "/store/search", "keyword": keyword, "scope": 10, "count": 5}
    )


def get_book_info(book_id):
    return call_api({"api_name": "/book/info", "bookId": book_id})


results = []
for keyword, author in BOOKS:
    data = search_book(keyword)
    books = []
    if "results" in data:
        for group in data["results"]:
            for b in group.get("books", []):
                info = b.get("bookInfo", {})
                books.append(
                    {
                        "bookId": info.get("bookId"),
                        "title": info.get("title"),
                        "author": info.get("author"),
                        "intro": info.get("intro", "")[:200],
                        "category": info.get("category"),
                        "newRating": b.get("newRating"),
                        "readingCount": b.get("readingCount"),
                        "deepLink": info.get("deepLink"),
                    }
                )
    results.append({"keyword": keyword, "target_author": author, "matches": books[:3]})
    time.sleep(0.5)

print(json.dumps(results, ensure_ascii=False, indent=2))
