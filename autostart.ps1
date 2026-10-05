# Claude Code SessionStart hook: make sure the background service (data + web page) is running.
# It no longer splits the terminal; open the terminal view on demand with `cs`.
# Start-Process so the hook returns immediately; ticker.py --bg start is a no-op if the panel is already running.
Start-Process python -ArgumentList "`"$PSScriptRoot\ticker.py`" --bg start" -WindowStyle Hidden
exit 0
