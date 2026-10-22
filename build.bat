@echo off
REM Génère un .exe autonome (libmpv-2.dll doit être dans ce dossier)
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name LecteurProLogic --add-binary "libmpv-2.dll;." lecteur_prologic.py
echo.
echo Terminé : voir le dossier dist\
pause
