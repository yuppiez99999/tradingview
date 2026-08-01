"""
导入/拉取 Qwen2.5 到 Ollama，并设置为默认模型。
优先尝试本地 GGUF，若不可用则直接 ollama pull 官方模型。
用法：
    py -3.8 scripts/setup_ollama_qwen.py
    OLLAMA_MODEL=qwen2.5:7b py -3.8 scripts/setup_ollama_qwen.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_CANDIDATES = [
    Path(r"D:\models\Qwen\Qwen2.5-7B-Instruct"),
    Path(r"D:\models\Qwen\Qwen2.5-1.5B-Instruct"),
    Path(r"D:\models\pretrained\Qwen\Qwen2.5-0.5B-Instruct"),
]
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")


def find_ollama_bin() -> str:
    bin_name = "ollama.exe" if sys.platform == "win32" else "ollama"
    path = shutil.which(bin_name)
    if path:
        return path
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
        Path("D:/Program Files/Ollama/ollama.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    raise FileNotFoundError("未找到 ollama，请先安装 Ollama 并确保在 PATH 中")


def is_complete_gguf(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if path.suffix.lower() != ".gguf":
        return False
    if ".incomplete" in path.name.lower() or path.stat().st_size < 100_000_000:
        return False
    return True


def find_gguf(model_dir: Path):
    if not model_dir.exists():
        return None
    candidates = list(model_dir.rglob("*.gguf"))
    if not candidates:
        return None
    valid = [p for p in candidates if is_complete_gguf(p)]
    if not valid:
        return None
    for kw in ["q5_k_m", "q4_k_m", "q4_0", "q5_0"]:
        for p in valid:
            if kw in p.name.lower():
                return p
    return valid[0]


def _run(cmd, cwd=None, env=None):
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        print("run_exception=", repr(e))
        return None
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stdout:
        print(stdout)
    if stderr:
        print("stderr=", stderr)
    return proc


def main():
    print("ollama_bin_check=start")
    ollama = find_ollama_bin()
    print(f"ollama_bin={ollama}")

    # 先检查是否已存在
    proc = _run([ollama, "list"])
    if proc and proc.returncode == 0:
        for line in (proc.stdout or "").splitlines():
            if OLLAMA_MODEL_NAME in line:
                print(f"already_exists={OLLAMA_MODEL_NAME}")
                print("next=you can now use it in llm_client.py or .env")
                return

    # 尝试本地 GGUF 导入
    print("gguf_search=start")
    gguf = None
    for model_dir in MODEL_CANDIDATES:
        print(f"candidate={model_dir}")
        gguf = find_gguf(model_dir)
        if gguf:
            break

    if gguf:
        print(f"gguf_found={gguf}")
        cmd = [ollama, "create", OLLAMA_MODEL_NAME, "-f", "Modelfile"]
        env = os.environ.copy()
        env["OLLAMA_MODELS"] = str(gguf.parent)
        proc = _run(cmd, cwd=str(gguf.parent), env=env)
    else:
        print("gguf_not_found=fallback to ollama pull")
        # 根据模型名推断官方 tag
        tag = OLLAMA_MODEL_NAME.split(":")[-1] if ":" in OLLAMA_MODEL_NAME else "1.5b"
        official = f"qwen2.5:{tag}"
        proc = _run([ollama, "pull", official])

    if proc and proc.returncode == 0:
        print(f"done=ollama model '{OLLAMA_MODEL_NAME}' ready")
    else:
        print("failed=see above logs")
        sys.exit(1)

    print("ollama_list=start")
    _run([ollama, "list"])
    print(f"next=set OLLAMA_MODEL={OLLAMA_MODEL_NAME} in .env or llm_client.py")


if __name__ == "__main__":
    main()
