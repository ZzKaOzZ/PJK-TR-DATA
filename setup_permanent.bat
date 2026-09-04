@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   ตั้งค่าลิงก์เดิมทุกครั้ง (ทำครั้งเดียว)
echo ============================================
echo.
echo หลังตั้งค่านี้ ปิดแล้วเปิด start.bat จะได้ลิงก์เดิม
echo แต่ยังต้องเปิดโน้ตบุ๊คค้างไว้ และห้ามปิดฝา
echo.
python -c "from server import ensure_tunnel_key, tunnel_public_key; ensure_tunnel_key(); print(tunnel_public_key())"
echo.
echo 1. คัดลอกคีย์ SSH ทั้งบรรทัดด้านบน
echo 2. เปิด https://admin.localhost.run/ สมัครฟรี แล้ววางคีย์
echo 3. บันทึกคีย์บนเว็บ แล้วเปิด start.bat
echo 4. ในแอป กด "ลงทะเบียนแล้ว · ใช้ลิงก์เดิมทุกครั้ง"
echo 5. รอจนลิงก์ขึ้นว่าใช้ได้ แล้วส่งอันนั้นในไลน์ (อาจเปลี่ยนครั้งนี้ครั้งเดียว)
echo.
start https://admin.localhost.run/
pause
