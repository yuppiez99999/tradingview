import os
import tempfile
from pathlib import Path

from obs import ObsClient

AK = os.environ["HUAWEICLOUD_AK"]
SK = os.environ["HUAWEICLOUD_SK"]
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")
job_id = "1922f7c0-6f83-40df-b7cf-c7b0677ad46b"
key = f"output/modelarts-job-{job_id}-worker-0.log"
tmp = Path(tempfile.gettempdir()) / f"ma_log_{job_id}.txt"
r = obs.getObject("qt-models", key, downloadPath=str(tmp))
text = tmp.read_text(encoding="utf-8", errors="replace")

idx = text.find("can't open")
if idx >= 0:
    print("=== 找到错误 ===")
    print(text[max(0, idx - 200) : idx + 300])
else:
    idx = text.find("Error")
    if idx >= 0:
        print(text[max(0, idx - 200) : idx + 300])

idx = text.find("code is now in")
if idx >= 0:
    print("\n=== 代码下载位置 ===")
    print(text[idx : idx + 200])

idx = text.find("code_url")
if idx >= 0:
    print("\n=== code_url ===")
    print(text[idx : idx + 200])
