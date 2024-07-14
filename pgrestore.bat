@echo off
setlocal enabledelayedexpansion

:: Set variables
set "BACKUP_DIR=D:\path\extracted_file"
set "DB_USER=restore_user"
set "DB_PASSWORD=password"
set "DB_HOST=localhost"

:: Verify the backup directory exists
if not exist "%BACKUP_DIR%" (
    echo Backup directory %BACKUP_DIR% does not exist.
    pause
    exit /b 1
)

:: Set the PGPASSWORD environment variable
set PGPASSWORD=%DB_PASSWORD%

:: Restore global objects
echo Restoring global objects...
psql -U %DB_USER% -h %DB_HOST% -f "%BACKUP_DIR%\global_objects.sql"
if %errorlevel% neq 0 (
    echo Failed to restore global objects
    pause
    exit /b 1
)

:: Restore each database
for %%f in ("%BACKUP_DIR%\*.backup") do (
    set "dbname=%%~nf"
    echo Restoring database: !dbname!
    psql -U %DB_USER% -h %DB_HOST% -c "DROP DATABASE IF EXISTS \"!dbname!\";"
    if %errorlevel% neq 0 (
        echo Failed to drop database: !dbname!
        pause
        exit /b 1
    )
    psql -U %DB_USER% -h %DB_HOST% -c "CREATE DATABASE \"!dbname!\";"
    if %errorlevel% neq 0 (
        echo Failed to create database: !dbname!
        pause
        exit /b 1
    )
    pg_restore --verbose -U %DB_USER% -h %DB_HOST% -d "!dbname!" "%%f"
    if %errorlevel% neq 0 (
        echo Failed to restore database: !dbname!
        pause
        exit /b 1
    )
)

echo Restoration complete.
pause
