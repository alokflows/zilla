@echo off
echo Building AGY Desktop App...
pyinstaller --noconsole --onefile --windowed --name AGY_Desktop_Manager gui_main.py
echo Build complete! Check the dist folder.
