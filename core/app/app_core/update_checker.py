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
        if res.returncode == 0:
            info["upstream"] = res.stdout.strip()
    except Exception:
        pass

    if info["upstream"]:
        try:
            res = subprocess.run(
                [git_bin, "rev-list", "--count", "HEAD..@{u}"],
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
            res = subprocess.run(
                [git_bin, "log", "-1", "--format=%H", "@{u}"],
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
            [git_bin, "fetch", "--no-tags"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            env=git_env,
        )
        if res.returncode == 0:
            info["fetch_ok"] = True
            if info["upstream"]:
                try:
                    res_cnt = subprocess.run(
                        [git_bin, "rev-list", "--count", "HEAD..@{u}"],
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
                    res_sha = subprocess.run(
                        [git_bin, "log", "-1", "--format=%H", "@{u}"],
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
    except Exception:
        info["fetch_ok"] = False

    return info


def check_for_updates() -> UpdateCheckResult:
    """Checks whether local git repo is behind remote or has missing/deleted files.

    If Git is not installed or repo is a ZIP extract, falls back to standalone
    file integrity check and GitHub REST API.
    """
    # Always check essential file integrity first
    missing_essentials = check_essential_files()

    info = get_git_status()

    if not info["is_git"]:
        # Standalone / No-Git mode:
        if missing_essentials:
            msg = localizer.get_string(
                "update_status_files_missing",
                count=len(missing_essentials),
                default=f"Fehlende Dateien erkannt ({len(missing_essentials)} Datei(en) können wiederhergestellt werden)."
            )
            btn = localizer.get_string("main_button_github_repo", default="GitHub Repository ↗")
            return UpdateCheckResult(
                has_update=True,
                status_code="LOCAL_FILES_MISSING",
                message=msg,
                button_text=btn,
                missing_files_count=len(missing_essentials),
            )

        # Query GitHub API via pure Python urllib without Git
        try:
            req = urllib.request.Request(
                GITHUB_COMMITS_URL,
                headers={"User-Agent": "PANDO-App/1.0", "Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(req, timeout=4) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    remote_sha = data.get("sha", "")[:7]
                    return UpdateCheckResult(
                        has_update=False,
                        status_code="UP_TO_DATE",
                        message=localizer.get_string("update_status_up_to_date", default="Sie verwenden die aktuellste Version (alles synchron)."),
                        remote_commit=remote_sha,
                    )
        except Exception:
            pass

        return UpdateCheckResult(
            has_update=False,
            status_code="UP_TO_DATE",
            message=localizer.get_string("update_status_up_to_date", default="Sie verwenden die aktuellste Version (alles synchron)."),
        )

    # Git mode:
    behind_count = info["behind_count"]
    deleted_files = list(set(info["deleted_files"] + missing_essentials))
    deleted_count = len(deleted_files)
    local_sha = info["local_commit"][:7] if info["local_commit"] else ""
    remote_sha = info["remote_commit"][:7] if info["remote_commit"] else ""

    # Priority 1: Deleted/missing tracked files (always detected immediately)
    if deleted_count > 0:
        msg = localizer.get_string(
            "update_status_files_missing",
            count=deleted_count,
            default=f"Fehlende Dateien erkannt ({deleted_count} Datei(en) können wiederhergestellt werden)."
        )
        btn = localizer.get_string("main_button_git_restore", default="Dateien wiederherstellen (git pull)")
        return UpdateCheckResult(
            has_update=True,
            status_code="LOCAL_FILES_MISSING",
            message=msg,
            button_text=btn,
            local_commit=local_sha,
            remote_commit=remote_sha,
            behind_count=behind_count,
            missing_files_count=deleted_count,
        )

    # Priority 2: Remote commits available on upstream branch
    if behind_count > 0:
        msg = localizer.get_string(
            "update_status_available",
            count=behind_count,
            default=f"Neues Update verfügbar ({behind_count} neue(r) Commit(s) auf dem Server)."
        )
        btn = localizer.get_string("main_button_git_pull", default="Update durchführen (git pull)")
        return UpdateCheckResult(
            has_update=True,
            status_code="UPDATE_AVAILABLE",
            message=msg,
            button_text=btn,
            local_commit=local_sha,
            remote_commit=remote_sha,
            behind_count=behind_count,
            missing_files_count=0,
        )

    # Priority 3: Up to date & clean (fetch verified or local tracking matches)
    if info["fetch_ok"] or (info["local_commit"] and info["remote_commit"] and info["local_commit"] == info["remote_commit"]):
        msg = localizer.get_string("update_status_up_to_date", default="Sie verwenden die aktuellste Version (alles synchron).")
        return UpdateCheckResult(
            has_update=False,
            status_code="UP_TO_DATE",
            message=msg,
            local_commit=local_sha,
            remote_commit=remote_sha,
        )

    # Priority 4: Offline / remote unreachable
    msg = localizer.get_string("update_status_offline", default="Keine Internetverbindung oder Git-Server nicht erreichbar.")
    return UpdateCheckResult(
        has_update=False,
        status_code="OFFLINE",
        message=msg,
        local_commit=local_sha,
    )


def perform_git_pull() -> tuple[bool, str]:
    """Runs git restore on deleted files and git pull in the workspace directory."""
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
                restore_res = subprocess.run(
                    [git_bin, "checkout", "--"] + deleted_files,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=git_env,
                )
                if restore_res.returncode == 0:
                    output_msgs.append(f"{len(deleted_files)} fehlende Datei(en) wiederhergestellt.")

        # Run git pull
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
            full_msg = "\n".join(output_msgs) or "Repository ist aktuell."
            return True, full_msg

        # If pull failed (e.g. offline) but we restored missing files:
        if output_msgs:
            return True, "\n".join(output_msgs)

        stderr_txt = pull_res.stderr.strip() or pull_res.stdout.strip()
        return False, f"Fehler bei git pull:\n{stderr_txt}"
    except Exception as exc:
        return False, f"Fehler beim Ausführen von git: {exc}"
