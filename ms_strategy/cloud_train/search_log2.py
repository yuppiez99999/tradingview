import os
import tempfile
from pathlib import Path

from obs import ObsClient

AK = os.environ["HUAWEICLOUD_AK"]
SK = os.environ["HUAWEICLOUD_SK"]
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")
job_id = "69a25692-4627-4c6e-bb11-e65067a0a1d7"
key = f"output/modelarts-job-{job_id}-worker-0.log"
tmp = Path(tempfile.gettempdir()) / f"ma_log_{job_id}.txt"
r = obs.getObject("qt-models", key, downloadPath=str(tmp))
text = tmp.read_text(encoding="utf-8", errors="replace")

for kw in ["Error", "error", "Traceback", "No such", "cannot", "Failed", "exit with"]:
    idx = text.find(kw)
    if idx >= 0:
        print(f'[{kw}] 位置 {idx}:')
        print(text[max(0, idx - 200) : idx + 300])
        print("---")

print("\n=== 日志最后2000字符 ===")
print(text[-2000:])
