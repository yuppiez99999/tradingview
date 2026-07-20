# -*- coding: utf-8 -*-
import os
import re
import sys
import requests

resp = requests.get("https://pypi.org/simple/iFinDAPI/", timeout=30, verify=False)
text = resp.text
urls = re.findall(r'href="(https://files\.pythonhosted\.org/packages/[^"]+)"', text)
urls = [u for u in urls if u.endswith('.tar.gz') or '.tar.gz#' in u]
print('found', len(urls))
for u in urls:
    print(u)
if not urls:
    sys.exit(1)

url = urls[-1]
# strip fragment for download
download_url = url.split('#')[0]
print('download_url=', download_url)

out = os.path.join(os.path.dirname(__file__), "ifindapi-latest.tar.gz")
with requests.get(download_url, stream=True, timeout=60, verify=False) as r:
    r.raise_for_status()
    total = 0
    with open(out, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                total += len(chunk)
                f.write(chunk)
                if total % (1024 * 1024) < (256 * 1024):
                    print(f"downloaded={total/1024/1024:.1f} MB")
print("saved=", out, "size=", os.path.getsize(out))
