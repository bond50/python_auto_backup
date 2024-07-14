@echo off
setlocal

:: Check for administrative privileges
openfiles >nul 2>&1
if %errorlevel% neq 0 (
    echo This script must be run as an administrator.
    echo Please right-click on this batch file and select "Run as administrator".
    pause
    exit /b 1
)

:: Detect the current user
set "current_user=%username%"
echo Current user detected: %current_user%

:: Set paths
set "backup_script_dir=%APPDATA%\BackupScript"
set "backup_script_path=%backup_script_dir%\autoBackup.py"
set "venv_path=%backup_script_dir%\venv"
set "venv_python=%venv_path%\Scripts\python.exe"
set "python_installer=python-3.12.4-amd64.exe"
set "python_install_dir=C:\Users\%current_user%\AppData\Local\Programs\Python\Python312"
set "task_name=BackupScript"
set "log_file=%backup_script_dir%\setup.log"
set "env_file=%backup_script_dir%\.env"
set "vbs_file=%backup_script_dir%\run_backup.vbs"

:: Create the BackupScript directory if it doesn't exist
if not exist "%backup_script_dir%" (
    mkdir "%backup_script_dir%"
    attrib +h "%backup_script_dir%"
)

:: Copy necessary files
copy /Y "%~dp0autoBackup.py" "%backup_script_path%" >nul
copy /Y "%~dp0requirements.txt" "%backup_script_dir%" >nul
copy /Y "%~dp0.env" "%env_file%" >nul
echo Files copied to %backup_script_dir%

:: Remove existing VBScript file
if exist "%vbs_file%" (
    del "%vbs_file%"
)

:: Create a VBScript to run the backup script hidden
echo Creating VBScript to run backup script silently...
echo Set WshShell = CreateObject("WScript.Shell") > "%vbs_file%"
echo WshShell.Run """"^& "%venv_python%" ^& """" ^& " """ ^& "%backup_script_path%" ^& """", 0, False >> "%vbs_file%"

:: Check if Python is installed
echo Attempting to find Python installation...
set "python_path="
for %%p in (
    "C:\Users\%current_user%\AppData\Local\Programs\Python\Python3*"
    "C:\Program Files\Python3*"
    "C:\Program Files (x86)\Python3*"
) do (
    for %%v in (%%p\python.exe) do (
        if exist "%%v" set "python_path=%%v"
    )
)

:: Manual check in the user's specific path
if not defined python_path (
    for /d %%v in ("C:\Users\%current_user%\AppData\Local\Programs\Python\Python3*") do (
        if exist "%%v\python.exe" set "python_path=%%v\python.exe"
    )
)

:: If Python is not found, attempt to install it
if not defined python_path (
    if exist "%~dp0%python_installer%" (
        echo Python not found. Installing Python...
        start /wait "" "%~dp0%python_installer%" /quiet InstallAllUsers=0 PrependPath=1 TargetDir=%python_install_dir%
        if exist "%python_install_dir%\python.exe" (
            set "python_path=%python_install_dir%\python.exe"
        ) else (
            echo Failed to install Python.
            pause
            exit /b 1
        )
    ) else (
        echo Python is not installed or not found in common paths, and installer is not available. Please install Python 3.12 or later.
        pause
        exit /b 1
    )
)

echo Found Python at %python_path%
echo Verifying Python installation...
"%python_path%" --version

:: Create a virtual environment if it doesn't exist
if not exist "%venv_path%" (
    echo Creating virtual environment...
    "%python_path%" -m venv "%venv_path%"
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate the virtual environment and install dependencies
echo Activating virtual environment...
call "%venv_path%\Scripts\activate"
echo Installing dependencies...
pip install --disable-pip-version-check -r "%backup_script_dir%\requirements.txt"
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

:: Remove any existing scheduled task
echo Removing any existing scheduled task...
schtasks /Delete /F /TN "%task_name%" >nul 2>&1

:: Schedule the task to run the VBScript at startup
echo Scheduling the backup script to run at startup...
schtasks /Create /TN "%task_name%" /TR "wscript.exe \"%vbs_file%\"" /SC ONSTART /RL HIGHEST /F

if errorlevel 1 (
    echo Failed to schedule the backup script at startup.
    pause
    exit /b 1
)

:: Run the VBScript immediately to verify setup
echo Running the backup script...
start "" "wscript.exe" "%vbs_file%"

echo Backup script setup complete.
pause
exit /b 0
