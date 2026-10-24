@echo off
title Resenha Scanner
cd /d "%~dp0"
echo.
echo Iniciando scanner...
echo.
python scanner.py
echo.
echo Pressione qualquer tecla para fechar...
pause >nul
