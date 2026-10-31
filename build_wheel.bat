@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 构建 wheel 到 dist\ ...
python -m pip wheel . --no-deps --no-build-isolation -w dist
if errorlevel 1 (
    echo 构建失败: 请先 pip install setuptools wheel
    pause
    exit /b 1
)
echo.
echo 完成! dist\ 下的 .whl 可拷贝到其他电脑:
echo    pip install dist\netease_lyric_api-*.whl
pause
