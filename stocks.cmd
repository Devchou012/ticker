@echo off
rem Top 45%: ticker panel, bottom: Claude Code (keep this file ASCII, cmd reads it as ANSI)
wt -d "%USERPROFILE%" python "%~dp0ticker.py" ; split-pane -H --size 0.55 -d "%USERPROFILE%" cmd /k claude
