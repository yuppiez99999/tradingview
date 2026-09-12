import os

from obs import ObsClient

AK = os.environ["HUAWEICLOUD_AK"]
SK = os.environ["HUAWEICLOUD_SK"]
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")

resp = obs.listObjects("qt-models", prefix="models/")
print(f"status: {resp.status}")
if resp.status < 300 and resp.body and resp.body.contents:
    for obj in resp.body.contents:
        print(f"  key={obj.key}, size={obj.size}")
else:
    print("models/ 目录为空或不存在")

resp2 = obs.listObjects("qt-models", prefix="output/")
if resp2.status < 300 and resp2.body and resp2.body.contents:
    pkl_files = [o for o in resp2.body.contents if o.key.endswith(('.pkl', '.lgb.txt', '.csv'))]
    if pkl_files:
        print("\noutput/ 下的模型文件:")
        for obj in pkl_files:
            print(f"  key={obj.key}, size={obj.size}")
    else:
        print("\noutput/ 下也没有 .pkl/.csv 文件")
