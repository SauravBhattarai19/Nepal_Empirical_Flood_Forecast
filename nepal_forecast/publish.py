# -*- coding: utf-8 -*-
"""
Put the site online after an update.

``publish: git: {remote: ..., branch: gh-pages}`` pushes ``site/`` as one fresh
orphan commit (``--force``), so the repository does not grow with every
update; GitHub Pages serves the branch. The remote can also come from the
NEPAL_FORECAST_PUBLISH_REMOTE environment variable (GitHub Actions sets it with
its token). ``publish: command: "rsync -a {site}/ host:/var/www/flood/"`` runs
any command instead.
"""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime


def _git(args, cwd, check=True):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        msg = (r.stderr or r.stdout).strip()
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            msg = msg.replace(token, "***")
        raise RuntimeError(f"git {args[0]} failed: {msg}")
    return r


def remote_and_branch(cfg):
    git = (cfg.get("publish") or {}).get("git") or {}
    remote = os.environ.get("NEPAL_FORECAST_PUBLISH_REMOTE") or git.get("remote")
    return remote, git.get("branch", "gh-pages")


def push_site(site_dir, remote, branch="gh-pages", message="forecast"):
    work = tempfile.mkdtemp(prefix="nff-pages-")
    try:
        dst = os.path.join(work, "site")
        shutil.copytree(site_dir, dst)
        _git(["init", "-q"], dst)
        _git(["checkout", "-q", "-b", branch], dst)
        _git(["add", "-A"], dst)
        _git(["-c", "user.name=nepal-forecast", "-c", "user.email=forecast@localhost",
              "commit", "-q", "-m", message], dst)
        _git(["push", "-q", "--force", remote, f"{branch}:{branch}"], dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def publish(cfg, site_dir, log=print):
    pub = cfg.get("publish") or {}
    latest = os.path.join(site_dir, "data", "latest.json")
    issued = json.load(open(latest))["issued_utc"] if os.path.exists(latest) else "?"
    if pub.get("command"):
        cmd = pub["command"].format(site=shlex.quote(site_dir))
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"publish command failed: {(r.stderr or r.stdout).strip()}")
        log("  published with the command")
        return
    remote, branch = remote_and_branch(cfg)
    if not remote:
        log("  (no publish target set: the site stays in site/)")
        return
    push_site(site_dir, remote, branch, f"forecast {issued}")
    shown = remote.split("@")[-1]
    log(f"  published to {shown} ({branch})")


def published_age_minutes(cfg):
    """Minutes since the forecast on the publish branch was issued (None if unknown).
    Lets the GitHub Actions backup skip when the main server is up to date."""
    remote, branch = remote_and_branch(cfg)
    if not remote:
        return None
    work = tempfile.mkdtemp(prefix="nff-check-")
    try:
        _git(["init", "-q"], work)
        r = _git(["fetch", "-q", "--depth", "1", remote, branch], work, check=False)
        if r.returncode:
            return None
        r = _git(["show", "FETCH_HEAD:data/latest.json"], work, check=False)
        if r.returncode:
            return None
        issued = datetime.strptime(json.loads(r.stdout)["issued_utc"], "%Y-%m-%dT%H:%MZ")
        return (datetime.utcnow() - issued).total_seconds() / 60.0
    finally:
        shutil.rmtree(work, ignore_errors=True)
