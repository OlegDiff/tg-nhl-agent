@echo off
cd /d C:\Projects\tg-nhl-agent
C:\Projects\tg-nhl-agent\.venv\Scripts\python.exe -m tg_nhl_agent.main
if errorlevel 1 pause
