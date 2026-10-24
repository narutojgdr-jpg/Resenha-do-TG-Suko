@echo off
title Atualizar Resenha
cd /d "%~dp0"
echo.
echo Atualizando do GitHub...
echo.
git pull
echo.
echo Pronto. Pode fechar.
pause
