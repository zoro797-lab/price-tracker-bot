@echo off
chcp 65001 >nul
title تثبيت بوت متابعة الأسعار
echo ============================================
echo   تثبيت بوت متابعة أسعار المنتجات
echo ============================================
echo.

REM التحقق من وجود Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [خطأ] Python غير مثبت على الجهاز.
    echo حمّل Python 3.11 أو أحدث من: https://www.python.org/downloads/
    echo مهم: فعّل خيار "Add Python to PATH" أثناء التثبيت.
    pause
    exit /b 1
)

echo [1/3] إنشاء بيئة افتراضية...
if not exist venv (
    python -m venv venv
) else (
    echo     البيئة الافتراضية موجودة بالفعل.
)

echo [2/3] تفعيل البيئة الافتراضية وتحديث pip...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip

echo [3/3] تثبيت المكتبات المطلوبة...
pip install -r requirements.txt

echo.
echo إنشاء ملف .env إذا لم يكن موجودًا...
if not exist .env (
    copy .env.example .env >nul
    echo [مهم] تم إنشاء ملف .env — افتحه وضع فيه BOT_TOKEN و ADMIN_IDS.
) else (
    echo     ملف .env موجود بالفعل.
)

echo.
echo ============================================
echo   التثبيت اكتمل!
echo   الخطوة التالية: عدّل ملف .env ثم شغّل run.bat
echo ============================================
pause
