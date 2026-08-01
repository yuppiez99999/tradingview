"""修复 B303: MD5 → SHA256"""
import os

fixes = [
    (r"v8.3_institutional\src\data\data_pipeline.py",
     'hashlib.md5(f"{timestamp}{source}".encode()).hexdigest()[:8]',
     'hashlib.sha256(f"{timestamp}{source}".encode()).hexdigest()[:8]'),
    (r"v8.3_institutional\src\data\data_pipeline.py",
     "hashlib.md5(data_str.encode()).hexdigest()",
     "hashlib.sha256(data_str.encode()).hexdigest()"),
    (r"utils\alpha\ab_testing.py",
     'hashlib.md5(symbol.encode("utf-8")).hexdigest()',
     'hashlib.sha256(symbol.encode("utf-8")).hexdigest()'),
]

for filepath, old, new in fixes:
    if not os.path.exists(filepath):
        print(f"NOT FOUND: {filepath}")
        continue
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    if old in content:
        content = content.replace(old, new, 1)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Fixed: {filepath}")
    else:
        print(f"NOT FOUND in {filepath}: {old[:50]}")

# 单独处理 media_crawler_adapter.py
filepath = r"utils\media_crawler_adapter.py"
if os.path.exists(filepath):
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    if "hashlib.md5(" in content:
        content = content.replace("hashlib.md5(", "hashlib.sha256(")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Fixed: {filepath}")
    else:
        print(f"NOT FOUND in {filepath}: hashlib.md5(")
