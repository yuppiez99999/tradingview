@echo off
chcp 65001 >nul
echo ================================================================
echo  QLib 云端训练打包工具
echo ================================================================
echo.

cd /d "e:\各种PY程序\28-终极量化交易系统7.1"

echo [1/3] 打包 QLib 数据 (qlib_data.zip)...
if exist "qlib_data.zip" del "qlib_data.zip"
powershell -Command "Compress-Archive -Path 'qlib_data\cn_data' -DestinationPath 'qlib_data.zip' -CompressionLevel Optimal"
if exist "qlib_data.zip" (
    echo       完成: qlib_data.zip
    for %%A in (qlib_data.zip) do echo       大小: %%~zA bytes
) else (
    echo       [失败] 打包失败
    pause
    exit /b 1
)

echo.
echo [2/3] 复制训练脚本...
if not exist "cloud_train" mkdir "cloud_train"
copy /Y "cloud_train\modelscope_train.py" "cloud_train\modelscope_train.py" >nul
copy /Y "cloud_train\requirements.txt" "cloud_train\requirements.txt" >nul
echo       完成

echo.
echo [3/3] 打包完成!
echo.
echo 上传到云端:
echo   1. qlib_data.zip        → 云端存储/数据集
echo   2. cloud_train/ 文件夹   → 云端代码
echo.
echo 云端运行:
echo   unzip qlib_data.zip
echo   pip install -r requirements.txt
echo   python modelscope_train.py --data-dir ./qlib_data/cn_data --market csi300
echo.
pause
