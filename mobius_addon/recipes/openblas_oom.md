# OpenBLAS 内存分配失败（线程栈 OOM）

## 症状
EOD 全流程或大模型矩阵运算中途崩溃：`Memory allocation still failed after 10 retries,
giving up`（OpenBLAS 分配线程栈内存失败），多个阶段（phase0/1/2/4...）集体失败。

## 根因
系统可用内存不足（其他进程如 IDE / node / 浏览器占用大量内存），OpenBLAS 默认多线程
各自占用线程栈，峰值内存需求远超物理内存，分配重试 10 次仍失败即放弃。

## 修复
减少线程栈内存需求，限制为单线程：
```powershell
$env:OPENBLAS_NUM_THREADS="1"
$env:OMP_NUM_THREADS="1"
$env:MKL_NUM_THREADS="1"
python run_eod.py
```

## 验证命令
设置上述三变量后重跑 EOD，原失败阶段应转为成功（非阻断性问题除外）。

## 防复发
外挂启动器 / 定时任务统一预置 `OPENBLAS_NUM_THREADS=1` 等三变量；
监控机器可用内存，避免在低内存宿主机跑大矩阵运算。
