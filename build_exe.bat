@echo off
setlocal

cd /d "%~dp0"

set "APP_NAME=PhyloTreeViewer"
set "ENTRY=app\main.py"
set "DIST_DIR=dist"
set "BUILD_DIR=build"
set "RELEASE_DIR=release"
set "RELEASE_APP_DIR=%RELEASE_DIR%\%APP_NAME%"

echo [1/5] Check Python...
where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found in PATH.
    exit /b 1
)

echo [2/5] Check PyInstaller...
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo PyInstaller not found. Installing from requirements-dev.txt...
    python -m pip install -r requirements-dev.txt
    if errorlevel 1 (
        echo Failed to install PyInstaller.
        exit /b 1
    )
)

echo [3/5] Clean old build output...
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"
if exist "%RELEASE_APP_DIR%" rmdir /s /q "%RELEASE_APP_DIR%"
if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"

echo [4/5] Build app folder...
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name "%APP_NAME%" ^
    --paths "%cd%" ^
    "%ENTRY%"

if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo [5/5] Copy app folder to release...
xcopy "%DIST_DIR%\%APP_NAME%" "%RELEASE_APP_DIR%\" /E /I /Y >nul
if errorlevel 1 (
    echo Build finished, but copy to release folder failed.
    exit /b 1
)

echo.
echo Build complete:
echo   %cd%\%RELEASE_APP_DIR%\%APP_NAME%.exe

endlocal
