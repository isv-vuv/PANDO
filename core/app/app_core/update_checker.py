"""Update checking utilities for PANDO (Git remote sync / local file verification)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from typing import Optional

from core.locales import localizer
from core.app.app_core.project import tool_root

GITHUB_REPO = "isv-vuv/PANDO"
GITHUB_COMMITS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"

ESSENTIAL_FILES = [
    os.path.join("core", "scripts", "qgis", "models", "Model1_DataPrep.model3"),
    os.path.join("core", "scripts", "qgis", "models", "Model2_ZoneClass.model3"),
    os.path.join("core", "scripts", "qgis", "models", "Model3_GridGen.model3"),
    os.path.join("core", "scripts", "qgis", "models", "Model3-4_GridAssign.model3"),
    os.path.join("core", "scripts", "qgis", "models", "Model4_TierAssign.model3"),
    os.path.join("core", "scripts", "qgis", "models", "Model5_UrbanCentrality.model3"),
    os.path.join("core", "scripts", "qgis", "models", "Model6_ZoneAssembler.model3"),
    os.path.join("core", "scripts", "qgis", "styles", "adm0.qml"),
    os.path.join("core", "scripts", "qgis", "styles", "adm1.qml"),
    os.path.join("core", "scripts", "qgis", "styles", "adm2.qml"),
    os.path.join("core", "scripts", "qgis", "styles", "adm3.qml"),
    os.path.join("core", "scripts", "qgis", "styles", "centrality_points.qml"),
    os.path.join("core", "scripts", "qgis", "styles", "centrality_polygons.qml"),
    os.path.join("core", "scripts", "qgis", "styles", "intensity.qml"),
    os.path.join("core", "scripts", "qgis", "styles", "poi_points.qml"),
    os.path.join("core", "scripts", "qgis", "styles", "population_raster.qml"),
    os.path.join("core", "locales", "de.json"),
    os.path.join("core", "locales", "en.json"),
]


@dataclass
class UpdateCheckResult:
    has_update: bool
    status_code: str  # "UP_TO_DATE", "UPDATE_AVAILABLE", "LOCAL_FILES_MISSING", "OFFLINE", "NOT_GIT_REPO"
    message: str
    button_text: str = ""
    local_commit: str = ""
    remote_commit: str = ""
    behind_count: int = 0
    missing_files_count: int = 0


def find_git_executable() -> Optional[str]:
    """Finds the path to the git executable, checking PATH and common Windows locations."""
    found = shutil.which("git")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
        os.path.expandvars(r"%ProgramW6432%\Git\cmd\git.exe"),
        os.path.expandvars(r"%ProgramFiles%\Git\cmd\git.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Git\cmd\git.exe"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def check_essential_files() -> list[str]:
    """Checks for missing essential files in the application directory (works without Git)."""
    root = tool_root()
    missing = []
    for rel_path in ESSENTIAL_FILES:
        full_path = os.path.join(root, rel_path)
        if not os.path.exists(full_path):
            missing.append(rel_path)
    return missing


def get_local_commit_info() -> tuple[str, str]:
    """Returns (commit_hash, iso_date_string) of local HEAD using git log."""
    root = tool_root()
    git_bin = find_git_executable()
    if not git_bin:
        return "", ""
    git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        res = subprocess.run(
            [git_bin, "log", "-1", "--format=%H|%cI"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            env=git_env,
        )
        if res.returncode == 0 and "|" in res.stdout:
            parts = res.stdout.strip().split("|", 1)
            return parts[0], parts[1]
    except Exception:
        pass
    return "", ""


def get_git_status() -> dict:
    """Checks git repository state: branch, upstream, behind count, deleted/modified files."""
    root = tool_root()
    git_bin = find_git_executable()

    info = {
        "is_git": False,
        "branch": "",
        "upstream": "",
        "behind_count": 0,
        "ahead_count": 0,
        "deleted_files": [],
        "modified_files": [],
        "fetch_ok": False,
        "local_commit": "",
        "remote_commit": "",
    }

    git_dir = os.path.join(root, ".git")
    if not os.path.exists(git_dir) or not git_bin:
        return info

    info["is_git"] = True
    git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    # 1. Get current branch & local commit
    try:
        res = subprocess.run(
            [git_bin, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=4,
            env=git_env,
        )
        if res.returncode == 0:
            info["branch"] = res.stdout.strip()
    except Exception:
        pass

    try:
        res = subprocess.run(
            [git_bin, "log", "-1", "--format=%H"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=4,
            env=git_env,
        )
        if res.returncode == 0:
            info["local_commit"] = res.stdout.strip()
    except Exception:
        pass

    # 2. Check working directory status for deleted or missing tracked files (instant, local only)
    try:
        res = subprocess.run(
            [git_bin, "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=4,
            env=git_env,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if not line.strip():
                    continue
                status_code = line[:2]
                filename = line[3:].strip()
                if "D" in status_code:
                    info["deleted_files"].append(filename)
                elif "M" in status_code:
                    info["modified_files"].append(filename)
    except Exception:
        pass

    # 3. Check upstream and commit diff using local tracking branch first
    try:
        res = subprocess.run(
            [git_bin, "rev-parse", "--abbrev-ref", "@{u}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=4,
            env=git_env,
        )
        if res.returncode == 0 and res.stdout.strip():
            info["upstream"] = res.stdout.strip()
    except Exception:
        pass

    if not info["upstream"]:
        try:
            res_origin = subprocess.run(
                [git_bin, "rev-parse", "--verify", "origin/main"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=4,
                env=git_env,
            )
            if res_origin.returncode == 0:
                info["upstream"] = "origin/main"
        except Exception:
            pass

    target_ref = info["upstream"] or "origin/main"

    if info["upstream"]:
        try:
            res = subprocess.run(
                [git_bin, "rev-list", "--count", f"HEAD..{target_ref}"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=4,
                env=git_env,
            )
            if res.returncode == 0:
                info["behind_count"] = int(res.stdout.strip() or 0)
        except Exception:
            pass

        try:
            res_ahead = subprocess.run(
                [git_bin, "rev-list", "--count", f"{target_ref}..HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=4,
                env=git_env,
            )
            if res_ahead.returncode == 0:
                info["ahead_count"] = int(res_ahead.stdout.strip() or 0)
        except Exception:
            pass

        try:
            res = subprocess.run(
                [git_bin, "log", "-1", "--format=%H", target_ref],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=4,
                env=git_env,
            )
            if res.returncode == 0:
                info["remote_commit"] = res.stdout.strip()
        except Exception:
            pass

    # 4. Try fast git fetch to check remote updates
    try:
        res = subprocess.run(
            [git_bin, "fetch", "--no-tags", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            env=git_env,
        )
        if res.returncode == 0:
            info["fetch_ok"] = True
            try:
                res_sha = subprocess.run(
                    [git_bin, "log", "-1", "--format=%H", target_ref],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=4,
                    env=git_env,
                )
                if res_sha.returncode == 0:
                    info["remote_commit"] = res_sha.stdout.strip()
            except Exception:
                pass
            try:
                res_cnt = subprocess.run(
                    [git_bin, "rev-list", "--count", f"HEAD..{target_ref}"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=4,
                    env=git_env,
                )
                if res_cnt.returncode == 0:
                    info["behind_count"] = int(res_cnt.stdout.strip() or 0)
            except Exception:
                pass
            try:
                res_ahead = subprocess.run(
                    [git_bin, "rev-list", "--count", f"{target_ref}..HEAD"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=4,
                    env=git_env,
                )
                if res_ahead.returncode == 0:
                    info["ahead_count"] = int(res_ahead.stdout.strip() or 0)
            except Exception:
                pass
    except Exception:
        info["fetch_ok"] = False

    return info


def parse_iso_datetime(dt_str: str) -> Optional[datetime]:
    """Parses ISO timestamp string (supporting 'Z' and offset) to timezone-aware datetime."""
    if not dt_str:
        return None
    try:
        from datetime import datetime
        s = dt_str.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def fetch_github_remote_info() -> tuple[str, str, str]:
    """Fetches (sha, iso_date_string, message) of the latest commit on main from GitHub API."""
    try:
        req = urllib.request.Request(
            GITHUB_COMMITS_URL,
            headers={"User-Agent": "PANDO-App/1.0", "Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                remote_sha = data.get("sha", "")
                remote_date = data.get("commit", {}).get("committer", {}).get("date", "")
                remote_msg = data.get("commit", {}).get("message", "").splitlines()[0] if data.get("commit", {}).get("message") else ""
                return remote_sha, remote_date, remote_msg
    except Exception:
        pass
    return "", "", ""


def check_for_updates() -> UpdateCheckResult:
    """Checks whether GitHub has a newer commit, or if local files were deleted/modified."""
    missing_essentials = check_essential_files()
    info = get_git_status()
    all_deleted = list(set(info.get("deleted_files", []) + missing_essentials))

    # 1. Deleted / missing files have highest priority (restore prompt)
    if all_deleted:
        msg = localizer.get_string(
            "update_status_files_missing",
            count=len(all_deleted),
            default=f"Fehlende/gelöschte Dateien erkannt ({len(all_deleted)} Datei(en) können wiederhergestellt werden)."
        )
        btn = localizer.get_string("main_button_git_restore", default="Dateien wiederherstellen")
        return UpdateCheckResult(
            has_update=True,
            status_code="LOCAL_FILES_MISSING",
            message=msg,
            button_text=btn,
            missing_files_count=len(all_deleted),
            local_commit=info.get("local_commit", "")[:7],
        )

    # 2. If running inside a git repository, determine status from real Git state
    if info.get("is_git"):
        behind_count = info.get("behind_count", 0)
        ahead_count = info.get("ahead_count", 0)
        modified_count = len(info.get("modified_files", []))
        local_sha = info.get("local_commit", "")[:7]
        remote_sha = info.get("remote_commit", "")[:7]

        # Case A: Remote has new commits to pull
        if behind_count > 0:
            count_display = behind_count
            msg = localizer.get_string(
                "update_status_available",
                count=count_display,
                default=f"Neues Update verfügbar ({count_display} neue(r) Commit(s) auf Server)."
            )
            btn = localizer.get_string("main_button_git_pull", default="Update durchführen (git pull)")
            return UpdateCheckResult(
                has_update=True,
                status_code="UPDATE_AVAILABLE",
                message=msg,
                button_text=btn,
                local_commit=local_sha,
                remote_commit=remote_sha,
                behind_count=count_display,
            )

        # Case B: Local modifications (uncommitted changes)
        if modified_count > 0:
            if ahead_count > 0:
                mod_msg = f"Lokal angepasst ({ahead_count} Commit(s) voraus, {modified_count} Datei(en) geändert)."
            else:
                mod_msg = f"Sie verwenden die aktuellste Version ({modified_count} Datei(en) lokal angepasst)."
            return UpdateCheckResult(
                has_update=False,
                status_code="UP_TO_DATE",
                message=mod_msg,
                local_commit=local_sha,
                remote_commit=remote_sha,
            )

        # Case C: Ahead of remote (commits ready to push)
        if ahead_count > 0:
            msg = f"Lokale Version ist aktuell ({ahead_count} Commit(s) voraus)."
            return UpdateCheckResult(
                has_update=False,
                status_code="UP_TO_DATE",
                message=msg,
                local_commit=local_sha,
                remote_commit=remote_sha,
            )

        # Case D: Offline
        if not info.get("fetch_ok", True) and not info.get("remote_commit"):
            return UpdateCheckResult(
                has_update=False,
                status_code="OFFLINE",
                message=localizer.get_string("update_status_offline", default="Keine Internetverbindung oder Git-Server nicht erreichbar."),
            )

        # Case E: Fully in sync
        return UpdateCheckResult(
            has_update=False,
            status_code="UP_TO_DATE",
            message=localizer.get_string("update_status_up_to_date", default="Sie verwenden die aktuellste Version (alles synchron)."),
            local_commit=local_sha,
            remote_commit=remote_sha,
        )

    # 3. Non-git distribution (ZIP fallback using GitHub API)
    remote_sha, remote_date_str, remote_msg = fetch_github_remote_info()
    local_sha, local_date_str = get_local_commit_info()
    remote_dt = parse_iso_datetime(remote_date_str)
    local_dt = parse_iso_datetime(local_date_str)

    if remote_sha:
        remote_short_sha = remote_sha[:7]
        local_short_sha = local_sha[:7] if local_sha else ""
        is_newer = False
        if remote_dt and local_dt:
            is_newer = remote_dt > local_dt
        elif remote_sha != local_sha:
            is_newer = True

        if is_newer:
            formatted_date = remote_dt.strftime("%d.%m.%Y %H:%M") if remote_dt else remote_date_str
            msg_detail = f' ("{remote_msg}")' if remote_msg else ""
            msg = localizer.get_string(
                "update_status_available",
                count=1,
                default=f"Neues Update verfügbar auf GitHub: Stand {formatted_date}{msg_detail}."
            )
            btn = localizer.get_string("main_button_git_pull", default="Update durchführen (git pull)")
            return UpdateCheckResult(
                has_update=True,
                status_code="UPDATE_AVAILABLE",
                message=msg,
                button_text=btn,
                local_commit=local_short_sha,
                remote_commit=remote_short_sha,
                behind_count=1,
            )
        else:
            return UpdateCheckResult(
                has_update=False,
                status_code="UP_TO_DATE",
                message=localizer.get_string("update_status_up_to_date", default="Sie verwenden die aktuellste Version (alles synchron)."),
                local_commit=local_short_sha,
                remote_commit=remote_short_sha,
            )

    return UpdateCheckResult(
        has_update=False,
        status_code="OFFLINE",
        message=localizer.get_string("update_status_offline", default="Keine Internetverbindung oder Git-Server nicht erreichbar."),
    )


def perform_git_pull() -> tuple[bool, str]:
    """Runs git fetch and reset to sync cleanly with isv-vuv/PANDO (supporting force-pushed histories)."""
    root = tool_root()
    git_bin = find_git_executable()
    if not git_bin:
        return False, "Git ist auf diesem System nicht installiert."

    git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    output_msgs = []
    try:
        # Check for deleted tracked files and restore them first if needed
        status_res = subprocess.run(
            [git_bin, "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            env=git_env,
        )
        if status_res.returncode == 0:
            deleted_files = [
                line[3:].strip()
                for line in status_res.stdout.splitlines()
                if "D" in line[:2]
            ]
            if deleted_files:
                subprocess.run(
                    [git_bin, "checkout", "--"] + deleted_files,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=git_env,
                )
                output_msgs.append(f"{len(deleted_files)} fehlende Datei(en) wiederhergestellt.")

        # Try fetching from public GitHub repo directly
        target_remote_url = f"https://github.com/{GITHUB_REPO}.git"
        fetch_res = subprocess.run(
            [git_bin, "fetch", target_remote_url, "main"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            env=git_env,
        )
        if fetch_res.returncode == 0:
            reset_res = subprocess.run(
                [git_bin, "reset", "--hard", "FETCH_HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=20,
                env=git_env,
            )
            if reset_res.returncode == 0:
                output_msgs.append("PANDO erfolgreich auf den neuesten Stand aktualisiert.")
                return True, "\n".join(output_msgs)

        # Fallback to standard git pull
        pull_res = subprocess.run(
            [git_bin, "pull"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            env=git_env,
        )
        if pull_res.returncode == 0:
            stdout_txt = pull_res.stdout.strip()
            if stdout_txt:
                output_msgs.append(stdout_txt)
            return True, "\n".join(output_msgs) or "Repository ist aktuell."

        if output_msgs:
            return True, "\n".join(output_msgs)

        stderr_txt = pull_res.stderr.strip() or fetch_res.stderr.strip()
        return False, stderr_txt or "Fehler beim Ausführen des Updates."
    except Exception as exc:
        return False, str(exc)
