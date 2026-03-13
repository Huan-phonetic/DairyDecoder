@echo off
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0transcriber.py"
) else if exist "%USERPROFILE%\miniconda3\python.exe" (
    "%USERPROFILE%\miniconda3\python.exe" "%~dp0transcriber.py"
) else if exist "%USERPROFILE%\anaconda3\python.exe" (
    "%USERPROFILE%\anaconda3\python.exe" "%~dp0transcriber.py"
) else (
    echo Python not found. Please install Python 3.10+.
    pause
    exit /b 1
)
if errorlevel 1 pause
