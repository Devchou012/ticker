# Claude Code SessionStart hook: split the current Windows Terminal window, ticker on top.

# Only inside Windows Terminal (skip VS Code, plain conhost, etc.)
if (-not $env:WT_SESSION) { exit 0 }

# ponytail: one panel machine-wide; per-window tracking if multiple windows need their own
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object CommandLine -like '*ticker.py*'
if ($running) { exit 0 }  # ticker.py 另有單一實例鎖，多開會自己關窗格

# New pane opens below and takes focus; swap it up, then give focus back to Claude Code.
# swap-pane swaps contents, not sizes: new pane must be 0.75 so Claude lands in the 75% slot.
wt -w 0 split-pane -H --size 0.75 python "$PSScriptRoot\ticker.py" `; swap-pane up `; move-focus down
exit 0
