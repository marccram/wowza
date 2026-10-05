# Builds a single WoWZA.exe (with the addon bundled) for sharing with people who don't have Python.
# Output: companion\dist\WoWZA.exe
Set-Location $PSScriptRoot
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name WoWZA `
    --icon wowza.ico `
    --add-data "wowza.ico;." `
    --add-data "..\WoWZA;WoWZA" `
    --collect-all lupa `
    app.py
