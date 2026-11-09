# Windows Terminal Alt+Q runs this in a new pane split above the current one.
# Screen open: knock on its lock port so it quits like pressing q (it hands back to the background service); this pane exits too.
# Background service or nothing running: this pane becomes the screen (ticker.py takes over from the service), focus goes back to Claude Code.
$lock = @(Get-NetTCPConnection -LocalPort 47653 -State Listen -ErrorAction SilentlyContinue)
if ($lock.Count) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($lock[0].OwningProcess)"
    if ($owner.CommandLine -notlike '*--headless*') {
        $c = New-Object Net.Sockets.TcpClient
        try { $c.Connect('127.0.0.1', 47653) } catch {}
        $c.Close()
        exit 0
    }
}
wt -w 0 move-focus down
python "$PSScriptRoot	icker.py"
exit $LASTEXITCODE
