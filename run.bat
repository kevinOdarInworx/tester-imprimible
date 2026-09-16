@echo off
REM Inicia la app web de testeo de imprimibles.
cd /d "%~dp0"
if not exist ".env" (
  echo No existe .env. Copialo desde .env.example y completa los valores.
  copy ".env.example" ".env" >nul
  echo Se creo .env a partir de .env.example. Editalo y vuelve a correr.
  pause
  exit /b 1
)
echo Instalando dependencias (solo la primera vez)...
py -m pip install -r requirements.txt --quiet
echo.
echo Abriendo http://127.0.0.1:5003 en tu navegador...
start "" http://127.0.0.1:5003
py app.py
pause
