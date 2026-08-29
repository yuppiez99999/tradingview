"""
上传 cloud_train 代码到 OBS，让 ModelArts 训练作业从 OBS 拉最新代码
用法: python upload_code.py
"""
import os
from pathlib import Path

from obs import ObsClient

AK = os.environ.get("HUAWEICLOUD_AK", "")
SK = os.environ.get("HUAWEICLOUD_SK", "")
BUCKET = "qt-data"
PREFIX = "code/ms_strategy/cloud_train/"

LOCAL_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\ms_strategy\cloud_train")


def main():
    if not AK or not SK:
        print("错误: 请设置 HUAWEICLOUD_AK 和 HUAWEICLOUD_SK")
        return
    obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")
    uploaded = 0
    for fp in LOCAL_DIR.glob("*.py"):
        key = PREFIX + fp.name
        resp = obs.putFile(BUCKET, key, file_path=str(fp))
        if resp.status < 300:
            print(f"已上传: {fp.name} -> obs://{BUCKET}/{key}")
            uploaded += 1
        else:
            print(f"上传失败: {fp.name} ({resp.errorMessage})")
    print(f"\n完成: {uploaded} 个文件已上传到 obs://{BUCKET}/{PREFIX}")


if __name__ == "__main__":
    main()
