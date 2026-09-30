"""Set up the ticker panel on a new machine. Safe to run again: each step skips what is already there.

    git clone https://github.com/Devchou012/ticker.git %USERPROFILE%\\ticker
    python %USERPROFILE%\\ticker\\install.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()


def step(msg):
    print(f"- {msg}")


# 1. Python packages
# --user is rejected inside a virtualenv; there it already installs into the venv
user_flag = [] if sys.prefix != sys.base_prefix else ["--user"]
subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *user_flag, "rich", "yfinance"])
step("rich, yfinance installed")

# 2. Personal list: start from the example; portfolio.json is not in git
portfolio = HERE / "portfolio.json"
if not portfolio.exists():
    shutil.copy(HERE / "portfolio.example.json", portfolio)
    step("portfolio.json created from the example (edit holdings by hand)")

# 3. Windows Terminal Alt+Q toggles the panel above the current pane (nothing opens on its own)
settings = HOME / ".claude" / "settings.json"
if settings.exists():  # older installs opened the panel from a SessionStart hook: remove it
    cfg = json.loads(settings.read_text(encoding="utf-8"))
    hooks = cfg.get("hooks", {}).get("SessionStart", [])
    kept = [e for e in hooks if not any("autostart.ps1" in h.get("command", "") for h in e.get("hooks", []))]
    if len(kept) != len(hooks):
        shutil.copy(settings, settings.with_name("settings.json.bak-ticker"))
        if kept:
            cfg["hooks"]["SessionStart"] = kept
        else:
            del cfg["hooks"]["SessionStart"]
            if not cfg["hooks"]:
                del cfg["hooks"]
        settings.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        step(f"old SessionStart hook removed (backup: {settings.name}.bak-ticker)")

wt = Path(os.environ["LOCALAPPDATA"]) / "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json"
if wt.exists():
    cfg = json.loads(wt.read_text(encoding="utf-8"))
    toggle = (HERE / "toggle.ps1").as_posix()
    action = {"command": {"action": "splitPane", "split": "up", "size": 0.45,
                          "commandline": f"powershell -NoProfile -ExecutionPolicy Bypass -File {toggle}"},
              "id": "User.tickerToggle"}
    cfg["actions"] = [a for a in cfg.get("actions", []) if a.get("id") != "User.tickerToggle"] + [action]
    binds = cfg.setdefault("keybindings", [])
    if not any(k.get("id") == "User.tickerToggle" for k in binds):
        binds.append({"id": "User.tickerToggle", "keys": "alt+q"})
    shutil.copy(wt, wt.with_name("settings.json.bak-ticker"))
    wt.write_text(json.dumps(cfg, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    step("Windows Terminal Alt+Q toggles the panel")
else:
    step("Windows Terminal not found: skipped Alt+Q")

# 4. PowerShell command `cs` (alias 股票介面) that opens a new window: panel on top, Claude Code below
# powershell writes the path in the console codepage, not UTF-8 (CJK home dirs break text=True)
profile = Path(subprocess.check_output(
    ["powershell", "-NoProfile", "-Command", "$PROFILE"]).decode("mbcs").strip())
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

print("Done. Press Alt+Q in Windows Terminal to open or close the panel, or run `cs`.")
