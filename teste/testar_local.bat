@echo off
if exist "%~dp0..\..\iniciar_teste_local.bat" (
    call "%~dp0..\..\iniciar_teste_local.bat"
) else (
    call "%~dp0..\iniciar_teste_local.bat"
)
