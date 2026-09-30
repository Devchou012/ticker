# Windows Terminal Alt+Q runs this in a new pane split above the current one.
# Panel already running: knock on its lock port so it quits like pressing q; this pane exits too.
# Otherwise this pane becomes the panel and focus goes back to Claude Code below.
if (Get-NetTCPConnection -LocalPort 47653 -State Listen -ErrorAction SilentlyContinue) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', 47653) } catch {}
    $c.Close()
    exit 0
}
wt -w 0 move-focus down
python "$PSScriptRoot\ticker.py"
exit $LASTEXITCODE
