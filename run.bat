@echo off
chcp 65001 >nul
REM TelePost Windows 启动（保持窗口开着即运行；开机自启用任务计划程序）
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo [X] 未找到虚拟环境，请先双击 install.bat 完成安装。
  pause & exit /b 1
)

.venv\Scripts\python main.py
pause
