@echo off
chcp 65001 >nul
REM TelePost Windows 一键安装（原生 Python，无需 WSL / Docker）
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (
  echo [X] 未找到 Python。请到 https://www.python.org/downloads/ 安装 3.10+，
  echo     安装时务必勾选 "Add python.exe to PATH"。
  pause & exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" || (
  echo [X] Python 版本过低，需要 3.10 或更高。
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
.venv\Scripts\python -m pip install -q --upgrade pip || (
  echo [X] pip 升级失败，请检查网络后重试。
  pause & exit /b 1
)
.venv\Scripts\python -m pip install -q -r requirements.txt || (
  echo [X] 依赖安装失败，请检查网络后重试。
  pause & exit /b 1
)

.venv\Scripts\python -c "import pyvips" >nul 2>nul && (
  echo [OK] pyvips 可用（超大图片流式缩放已启用）
) || (
  echo [!] 未检测到 libvips/pyvips（可选，不影响正常使用）。
  echo     仅影响超大图片的流式缩放；需要时见 https://www.libvips.org/install.html
  echo     安装系统库后再执行 .venv\Scripts\python -m pip install pyvips
)

echo [*] 检查配置文件 ...
.venv\Scripts\python check_config.py || (
  echo [X] 配置检查未通过。请按上面的提示补齐 config.ini 后重试。
  pause & exit /b 1
)

echo.
echo [OK] 安装完成。以后双击 run.bat 即可启动；关闭窗口即停止。
pause
