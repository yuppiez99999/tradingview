import os
import tempfile
from pathlib import Path

from obs import ObsClient

AK = os.environ["HUAWEICLOUD_AK"]
SK = os.environ["HUAWEICLOUD_SK"]
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")
job_id = "24d8f801-71da-4836-8715-e3866f4d57d3"
key = f"output/modelarts-job-{job_id}-worker-0.log"
tmp = Path(tempfile.gettempdir()) / f"ma_log_{job_id}.txt"
r = obs.getObject("qt-models", key, downloadPath=str(tmp))
text = tmp.read_text(encoding="utf-8", errors="replace")

idx = text.find("run command")
if idx >= 0:
    end = text.find("\n", idx)
    print("=== RUN COMMAND ===")
    print(text[idx:end])

for kw in ["modelscope_train.py", "auto_train.py", "total", "drwx", "can't open", "No such"]:
    idx = text.rfind(kw)
    if idx >= 0:
        print(f'\n[{kw}] 最后出现位置 {idx}:')
        print(text[max(0, idx - 100) : idx + 200])
