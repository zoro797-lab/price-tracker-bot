@echo off
chcp 65001 >nul
title بوت متابعة الأسعار
echo ============================================
echo   تشغيل بوت متابعة أسعار المنتجات
echo ============================================
echo.

REM التحقق من وجود ملف .env
if not exist .env (
    echo [خطأ] ملف .env غير موجود.
    echo شغّل install.bat أولًا ثم عدّل ملف .env وضع فيه التوكن.
    pause
    exit /b 1
)

if not exist venv (
    echo [خطأ] البيئة الافتراضية غير موجودة — شغّل install.bat أولًا.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python main.py
pause
