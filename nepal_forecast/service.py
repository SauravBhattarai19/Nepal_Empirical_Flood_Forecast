# -*- coding: utf-8 -*-
"""Keep ``nepal-forecast serve`` running on a Linux or macOS computer: a
systemd user service where there is systemd, otherwise a cron line that
starts one update every N hours."""

import os
import shutil
import subprocess
import sys

UNIT = "nepal-forecast.service"


def _unit_text(root, exe):
    return f"""[Unit]
Description=Nepal empirical flood forecast (updates every few hours)
After=network-online.target

[Service]
WorkingDirectory={root}
ExecStart={exe} serve
Restart=always
RestartSec=120

[Install]
WantedBy=default.target
"""


def install(root):
    exe = shutil.which("nepal-forecast") or f"{sys.executable} -m nepal_forecast"
    if shutil.which("systemctl"):
        d = os.path.expanduser("~/.config/systemd/user")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, UNIT), "w") as f:
            f.write(_unit_text(root, exe))
        for args in (["daemon-reload"], ["enable", "--now", UNIT]):
            subprocess.run(["systemctl", "--user"] + args, check=False)
        user = os.environ.get("USER", "")
        subprocess.run(["loginctl", "enable-linger", user], check=False)
        print(f"Installed the systemd user service {UNIT}.\n"
              f"  Logs:   journalctl --user -u {UNIT} -f\n"
              f"  Stop:   systemctl --user disable --now {UNIT}")
        return 0
    line = f"10 */2 * * * cd {root} && {exe} run >> {root}/forecast.log 2>&1  # nepal-forecast"
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if "# nepal-forecast" not in cur:
        subprocess.run(["crontab", "-"], input=cur + line + "\n", text=True, check=True)
    print("Installed a cron line (every 2 hours at minute 10):\n  " + line)
    return 0


def uninstall():
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", UNIT], check=False)
        p = os.path.expanduser(f"~/.config/systemd/user/{UNIT}")
        if os.path.exists(p):
            os.remove(p)
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if "# nepal-forecast" in cur:
        keep = "\n".join(l for l in cur.splitlines() if "# nepal-forecast" not in l) + "\n"
        subprocess.run(["crontab", "-"], input=keep, text=True, check=False)
    print("Removed the service.")
    return 0
