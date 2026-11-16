@echo off
rem Publica en Vercel el snapshot CIFRADO de la BVC (cada 2 semanas por tarea programada).
rem Requiere clave.txt con tu contraseña (nunca se sube a git).
cd /d "%~dp0"
if not exist clave.txt (
  echo [%date% %time%] Falta clave.txt: no se publica. >> publicar.log
  exit /b 1
)
echo [%date% %time%] Recolectando datos de la BVC... >> publicar.log
py -3 bvc.py >> publicar.log 2>&1
echo [%date% %time%] Exportando snapshot cifrado... >> publicar.log
.venv\Scripts\python exportar.py >> publicar.log 2>&1 || exit /b 1
git add datos.enc >> publicar.log 2>&1
git commit -m "datos: snapshot cifrado %date%" >> publicar.log 2>&1
git push origin main >> publicar.log 2>&1
echo [%date% %time%] Publicado. >> publicar.log
