import os
import tempfile
from pathlib import Path

from obs import ObsClient

AK = os.environ["HUAWEICLOUD_AK"]
SK = os.environ["HUAWEICLOUD_SK"]
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")
job_id = "fcbf60b2-b9a9-4064-8bed-c0eb14ee2050"
key = f"output/modelarts-job-{job_id}-worker-0.log"
tmp = Path(tempfile.gettempdir()) / f"ma_log_{job_id}.txt"
r = obs.getObject("qt-models", key, downloadPath=str(tmp))
text = tmp.read_text(encoding="utf-8", errors="replace")

for kw in ["模型已保存", "所有文件", "pip install", "esdk-obs", "已上传", "上传失败", "错误", "Error", "Traceback", "Terminated", "Killed", "OutOfMemory", "exit with"]:
    idx = text.rfind(kw)
    if idx >= 0:
        print(f'[{kw}] 位置 {idx}:')
        print(text[max(0, idx - 100) : idx + 300])
        print("---")

print("\n=== 日志最后3000字符 ===")
print(text[-3000:])
