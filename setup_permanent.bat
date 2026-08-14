@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   ตั้งค่าลิงก์ถาวร (ทำครั้งเดียว)
echo ============================================
echo.
python -c "from server import ensure_tunnel_key, tunnel_public_key; ensure_tunnel_key(); print(tunnel_public_key())"
echo.
echo 1. คัดลอกคีย์ SSH ด้านบน
echo 2. เปิด https://admin.localhost.run/ แล้ววางคีย์
echo 3. ลงทะเบียนคีย์ที่เว็บ แล้วรีสตาร์ท start.bat
echo 4. ในแอป กด "ลงทะเบียนแล้ว · ยืนยันลิงก์ถาวร"
echo.
start https://admin.localhost.run/
pause
