@echo off
set OLLAMA_NUM_GPUS=0
start "" "C:\Users\Administrator\AppData\Local\Programs\Ollama\ollama.exe" serve
echo Ollama started with CPU mode