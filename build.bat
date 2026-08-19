@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM MMY-FrameQuickView 一键打包脚本
REM 双击此文件即可生成单文件便携包 exe
REM ============================================================

cd /d "%~dp0%"

REM 检查 venv
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到 .venv\Scripts\python.exe
    echo 请先创建虚拟环境：python -m venv .venv
    pause
    exit /b 1
)

echo ============================================================
echo  MMY-FrameQuickView 一键打包
echo  Python: .venv\Scripts\python.exe
echo  模式:   onefile（单文件便携包）
echo ============================================================
echo.

REM 调用 Python 打包脚本
".venv\Scripts\python.exe" "scripts\build.py" onefile
set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE% equ 0 (
    echo [成功] 打包完成！
    echo.
    set "LATEST="
    for /f "delims=" %%i in ('dir /b /ad /od "releases\v*_*" 2^>nul') do set "LATEST=%%i"
    if defined LATEST (
        echo 产物目录：releases\!LATEST!\
        echo   - MMY-FrameQuickView.exe
        echo   - README.txt
        echo.
        echo 3 秒后打开产物目录...
        timeout /t 3 >nul
        explorer "releases\!LATEST!"
    )
) else (
    echo [失败] 打包过程出错，退出码 %EXITCODE%
    echo.
    pause
)

endlocal
