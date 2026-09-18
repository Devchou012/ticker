"""Set up the ticker panel on a new machine. Safe to run again: each step skips what is already there.

    git clone https://github.com/Devchou012/ticker.git %USERPROFILE%\\ticker
    python %USERPROFILE%\\ticker\\install.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()


def step(msg):
    print(f"- {msg}")


# 1. Python packages
subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--user", "rich", "yfinance"])
step("rich, yfinance installed")

# 2. Personal list: start from the example; portfolio.json is not in git
portfolio = HERE / "portfolio.json"
if not portfolio.exists():
    shutil.copy(HERE / "portfolio.example.json", portfolio)
    step("portfolio.json created from the example (edit holdings by hand)")

# 3. Claude Code SessionStart hook that opens the panel above Claude Code
settings = HOME / ".claude" / "settings.json"
cfg = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
autostart = (HERE / "autostart.ps1").as_posix()
hooks = cfg.setdefault("hooks", {}).setdefault("SessionStart", [])
if any("autostart.ps1" in h.get("command", "") for entry in hooks for h in entry.get("hooks", [])):
    step("SessionStart hook already present")
else:
    if settings.exists():
        shutil.copy(settings, settings.with_name("settings.json.bak-ticker"))
    hooks.append({"matcher": "startup", "hooks": [{
        "type": "command",
        "command": f"powershell -NoProfile -ExecutionPolicy Bypass -File {autostart} 2>/dev/null || true",
        "timeout": 15}]})
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    step(f"SessionStart hook added (backup: {settings.name}.bak-ticker)")

# 4. PowerShell command `cs` (alias 股票介面) that opens a new window: panel on top, Claude Code below
profile = Path(subprocess.check_output(
    ["powershell", "-NoProfile", "-Command", "$PROFILE"], text=True).strip())
text = profile.read_text(encoding="utf-8-sig") if profile.exists() else ""
if "function cs" in text:
    step("PowerShell cs command already present")
else:
    profile.parent.mkdir(parents=True, exist_ok=True)
    block = ("\n# 股票介面：開新視窗，上方行情面板、下方 Claude Code\n"
             f'function cs {{ & "{HERE / "stocks.cmd"}" }}\n'
             "Set-Alias 股票介面 cs\n")
    profile.write_text(text + block, encoding="utf-8-sig")  # PowerShell 5.1 needs the BOM for Chinese
    step(f"cs / 股票介面 added to {profile}")

# 5. Desktop shortcut
ps = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut('
      '[Environment]::GetFolderPath("Desktop")+"\\行情面板.lnk");'
      f'$s.TargetPath="{HERE / "stocks.cmd"}";$s.WorkingDirectory="{HOME}";$s.Save()')
subprocess.check_call(["powershell", "-NoProfile", "-Command", ps])
step("desktop shortcut 行情面板 created")

print("Done. Restart Claude Code inside Windows Terminal, or run `cs`.")
