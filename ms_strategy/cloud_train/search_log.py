import tempfile
from pathlib import Path

tmp_path = Path(tempfile.gettempdir()) / "ma_log_950bc2d7-f3f5-49e7-ac09-9237ea430144.txt"
text = tmp_path.read_text(encoding="utf-8", errors="replace")
for keyword in ["drwx", "modelscope_train", "auto_train", "cloud_train", "upload_code", ".py\n", "DONE"]:
    idx = 0
    count = 0
    while True:
        idx = text.find(keyword, idx)
        if idx < 0:
            break
        count += 1
        if count <= 3:
            print(f'[{keyword}] 位置 {idx}: {text[max(0,idx-50):idx+100].strip()}')
        idx += 1
    print(f'"{keyword}" 出现 {count} 次\n')
