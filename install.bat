@echo off
chcp 65001 >nul
REM TelePost Windows 一键安装（原生 Python，无需 WSL / Docker）
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (
  echo [X] 未找到 Python。请到 https://www.python.org/downloads/ 安装 3.9+，
  echo     安装时务必勾选 "Add python.exe to PATH"。
  pause & exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)" || (
  echo [X] Python 版本过低，需要 3.9 或更高。
  pause & exit /b 1
)

if not exist .venv (
  echo [*] 创建虚拟环境 ...
  python -m venv .venv || (
    echo [X] venv 创建失败，请重装 Python 并保留默认的 pip/venv 组件。
    pause & exit /b 1
  )
)

echo [*] 安装依赖（首次需要几分钟）...
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt || (
  echo [X] 依赖安装失败，请检查网络后重试。
  pause & exit /b 1
)

echo [*] 生成配置文件 ...
.venv\Scripts\python check_config.py

echo.
echo [OK] 安装完成。以后双击 run.bat 即可启动；关闭窗口即停止。
pause
