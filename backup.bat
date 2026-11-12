@echo off
chcp 65001 >nul
title النسخ الاحتياطي لقاعدة البيانات
echo ============================================
echo   النسخ الاحتياطي لقاعدة البيانات
echo ============================================

if not exist venv (
    echo [خطأ] البيئة الافتراضية غير موجودة — شغّل install.bat أولًا.
    pause
    exit /b 1
)

if not exist data (
    echo [خطأ] لا توجد قاعدة بيانات بعد — شغّل البوت أول مرة.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python -c "from app.config import settings; from app.database.database import backup_database; from datetime import datetime; from pathlib import Path; p = Path(settings.database_path).parent / 'backups' / ('manual_backup_' + datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S') + '.db'); p.parent.mkdir(exist_ok=True); print('OK' if backup_database(p) else 'FAILED'); print(p)"

echo.
echo ============================================
echo   اكتملت عملية النسخ الاحتياطي
echo ============================================
pause
