@echo off
title Iniciador do NutriIA
echo ========================================================
echo        Iniciando o Servidor NutriIA Local...
echo ========================================================
echo.
echo O servidor ficara ativo na porta 8000.
echo Acesse: http://localhost:8000
echo.

:: Garante que o terminal rode a partir da pasta correta
cd /d "%~dp0"

:: Inicia o servidor Python
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
