@echo off
rem Top 25%: ticker panel, bottom: Claude Code (keep this file ASCII, cmd reads it as ANSI)
wt -d "%USERPROFILE%" python "%~dp0ticker.py" ; split-pane -H --size 0.75 -d "%USERPROFILE%" cmd /k claude
