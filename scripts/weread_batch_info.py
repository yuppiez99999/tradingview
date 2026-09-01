"""批量获取微信读书书籍详情和章节目录."""

import json
import os
import time
import urllib.request

API = "https://i.weread.qq.com/api/agent/gateway"
KEY = os.environ.get("WEREAD_API_KEY", "")
SKILL_VER = "1.0.4"

BOOKS = [
    ("3300192813", "Active Portfolio Management", "Grinold & Kahn"),
    ("33831337", "因子投资：方法与实践", "石川"),
    ("3300082503", "控制论", "Wiener"),
    ("3300081407", "系统之美", "Meadows"),
    ("934903", "复杂", "梅拉妮·米歇尔"),
    ("3300065639", "期权期货衍生品", "Hull"),
    ("635942", "反脆弱", "Taleb"),
    ("3300026806", "肥尾效应", "Taleb"),
    ("3300143092", "思考快与慢", "卡尼曼"),
    ("3300128060", "行为金融与投资心理学", "诺夫辛格"),
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


results = []
for book_id, short_name, author in BOOKS:
    info = call_api({"api_name": "/book/info", "bookId": book_id})
    chapters = call_api({"api_name": "/book/chapterinfo", "bookId": book_id})

    ch_list = []
    if "chapters" in chapters:
        for ch in chapters["chapters"][:30]:
            ch_list.append(
                {
                    "title": ch.get("title"),
                    "level": ch.get("level", 1),
                    "wordCount": ch.get("wordCount"),
                }
            )

    results.append(
        {
            "bookId": book_id,
            "short_name": short_name,
            "target_author": author,
            "title": info.get("title"),
            "author": info.get("author"),
            "translator": info.get("translator"),
            "intro": info.get("intro", "")[:500],
            "category": info.get("category"),
            "publisher": info.get("publisher"),
            "publishTime": info.get("publishTime"),
            "isbn": info.get("isbn"),
            "wordCount": info.get("wordCount"),
            "newRating": info.get("newRating"),
            "newRatingCount": info.get("newRatingCount"),
            "deepLink": info.get("deepLink"),
            "chapters": ch_list,
        }
    )
    time.sleep(0.5)

print(json.dumps(results, ensure_ascii=False, indent=2))
