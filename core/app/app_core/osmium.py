"""Platform-independent discovery and execution of the osmium-tool CLI."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import Callable, Mapping, Sequence


REQUIRED_COMMANDS = ("cat", "merge", "time-filter", "extract", "tags-filter")
MINIMUM_VERSION = (1, 14, 0)


class OsmiumError(RuntimeError):
    """Base error for osmium discovery and execution."""


class OsmiumNotFoundError(OsmiumError):
    """Raised when no usable osmium-tool executable can be found."""


class OsmiumValidationError(OsmiumError):
    """Raised when an executable is not a compatible osmium-tool CLI."""


class OsmiumCancelledError(OsmiumError):
    """Raised after a running osmium process has been cancelled."""


@dataclass(frozen=True)
class OsmiumRuntime:
    executable: Path
    version: str
    platform: str
    architecture: str
    bundled: bool
    environment: Mapping[str, str]

    @property
    def creationflags(self) -> int:
        if self.platform != "windows":
            return 0
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def normalize_platform(system: str | None = None) -> str:
    value = (system or platform.system()).strip().lower()
    aliases = {"windows": "windows", "darwin": "macos", "linux": "linux"}
    try:
        return aliases[value]
    except KeyError as exc:
        raise OsmiumNotFoundError(f"Nicht unterstütztes Betriebssystem: {value or 'unbekannt'}") from exc


def normalize_architecture(machine: str | None = None) -> str:
    value = (machine or platform.machine()).strip().lower()
    aliases = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise OsmiumNotFoundError(f"Nicht unterstützte Prozessorarchitektur: {value or 'unbekannt'}") from exc


def _runtime_environment(executable: Path, os_name: str) -> dict[str, str]:
    env = os.environ.copy()
    runtime_dir = executable.parent
    if os_name == "windows":
        env["PATH"] = str(runtime_dir) + os.pathsep + env.get("PATH", "")
    else:
        lib_dir = runtime_dir / "lib"
        if lib_dir.is_dir():
            variable = "DYLD_LIBRARY_PATH" if os_name == "macos" else "LD_LIBRARY_PATH"
            env[variable] = str(lib_dir) + os.pathsep + env.get(variable, "")
    return env


def _version_tuple(text: str) -> tuple[int, ...]:
    match = re.search(r"(?:osmium(?: tool)?\s+version\s+)?(\d+)\.(\d+)(?:\.(\d+))?", text, re.I)
    if not match:
        raise OsmiumValidationError(f"Osmium-Version konnte nicht erkannt werden: {text.strip()!r}")
    return tuple(int(part or 0) for part in match.groups())


def _install_hint(os_name: str) -> str:
    if os_name == "windows":
        return "Installieren Sie osmium-tool z. B. über Conda-forge und tragen Sie osmium.exe im PATH ein."
    if os_name == "macos":
        return "Installieren Sie osmium-tool z. B. mit Homebrew (`brew install osmium-tool`)."
    return "Installieren Sie osmium-tool über den Paketmanager Ihrer Linux-Distribution oder Conda-forge."


def resolve_osmium(
    explicit_path: str | os.PathLike[str] | None = None,
    *,
    tool_root: str | os.PathLike[str] | None = None,
    system: str | None = None,
    machine: str | None = None,
    minimum_version: tuple[int, ...] = MINIMUM_VERSION,
    maximum_version: tuple[int, ...] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> OsmiumRuntime:
    """Resolve and validate osmium: explicit path, bundled runtime, then PATH."""

    os_name = normalize_platform(system)
    architecture = normalize_architecture(machine)
    executable_name = "osmium.exe" if os_name == "windows" else "osmium"
    root = Path(tool_root) if tool_root else Path(__file__).resolve().parents[3]
    bundled_path = root / "core" / "scripts" / "osmium" / executable_name
    platform_bundled_path = (
        root / "core" / "scripts" / "osmium" / f"{os_name}-{architecture}" / executable_name
    )

    candidates: list[tuple[Path, bool]] = []
    if explicit_path:
        candidates.append((Path(explicit_path).expanduser(), False))
    candidates.append((bundled_path, True))
    candidates.append((platform_bundled_path, True))
    path_candidate = which(executable_name)
    if path_candidate:
        candidates.append((Path(path_candidate), False))

    errors: list[str] = []
    for candidate, bundled in candidates:
        candidate = candidate.resolve()
        if not candidate.is_file():
            errors.append(f"{candidate}: Datei fehlt")
            continue
        if os_name != "windows" and not os.access(candidate, os.X_OK):
            if bundled:
                try:
                    candidate.chmod(candidate.stat().st_mode | 0o111)
                except OSError as exc:
                    errors.append(f"{candidate}: nicht ausführbar ({exc})")
                    continue
            else:
                errors.append(f"{candidate}: nicht ausführbar")
                continue

        env = _runtime_environment(candidate, os_name)
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)) if os_name == "windows" else None
        kwargs = {"env": env, "capture_output": True, "text": True, "check": False}
        if flags is not None:
            kwargs["creationflags"] = flags
        try:
            version_result = run([str(candidate), "--version"], **kwargs)
        except OSError as exc:
            errors.append(f"{candidate}: Start fehlgeschlagen ({exc})")
            continue
        if version_result.returncode:
            errors.append(f"{candidate}: --version fehlgeschlagen ({version_result.stderr.strip()})")
            continue
        try:
            parsed_version = _version_tuple(version_result.stdout or version_result.stderr)
        except OsmiumValidationError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        if parsed_version < minimum_version or (maximum_version and parsed_version > maximum_version):
            errors.append(
                f"{candidate}: Version {'.'.join(map(str, parsed_version))} außerhalb des freigegebenen Bereichs"
            )
            continue

        missing = []
        for command in REQUIRED_COMMANDS:
            result = run([str(candidate), "help", command], **kwargs)
            if result.returncode:
                missing.append(command)
        if missing:
            errors.append(f"{candidate}: benötigte Unterbefehle fehlen: {', '.join(missing)}")
            continue

        return OsmiumRuntime(
            executable=candidate,
            version=".".join(map(str, parsed_version)),
            platform=os_name,
            architecture=architecture,
            bundled=bundled,
            environment=env,
        )

    details = "; ".join(errors) if errors else "keine Kandidaten"
    raise OsmiumNotFoundError(
        f"Keine kompatible osmium-tool-CLI für {os_name}/{architecture} gefunden ({details}). "
        f"{_install_hint(os_name)} `pip install osmium` installiert nur Pyosmium, nicht diese CLI."
    )


def run_osmium(
    runtime: OsmiumRuntime,
    arguments: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    stop_event=None,
    log: Callable[[str], None] | None = None,
    poll_interval: float = 0.05,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
) -> subprocess.CompletedProcess[str]:
    """Run osmium without a shell and terminate it when ``stop_event`` is set."""

    normalized_arguments = [str(arg) for arg in arguments]
    if runtime.platform == "windows":
        # osmium-tool 1.14 on Windows prepends "./" to absolute polygon
        # paths passed to `extract -p`, producing invalid paths such as
        # `./C:\project\bound.poly`. A relative path avoids that upstream
        # parser bug while keeping all other command arguments unchanged.
        working_directory = Path(cwd).resolve() if cwd is not None else Path.cwd()
        for index, argument in enumerate(normalized_arguments[:-1]):
            if argument != "-p":
                continue
            polygon_path = Path(normalized_arguments[index + 1])
            if polygon_path.is_absolute():
                try:
                    normalized_arguments[index + 1] = os.path.relpath(
                        polygon_path,
                        working_directory,
                    )
                except ValueError:
                    # Cross-drive on Windows: set cwd to polygon parent directory
                    poly_parent = polygon_path.parent
                    updated_args = []
                    for a_idx, arg in enumerate(normalized_arguments):
                        if a_idx == index + 1:
                            updated_args.append(polygon_path.name)
                        elif arg != "-p" and not arg.startswith("-"):
                            p = Path(arg)
                            if not p.is_absolute() and (working_directory / p).exists():
                                updated_args.append(str((working_directory / p).resolve()))
                            else:
                                updated_args.append(arg)
                        else:
                            updated_args.append(arg)
                    normalized_arguments = updated_args
                    cwd = poly_parent
                    working_directory = poly_parent
                    break

    command = [str(runtime.executable), *normalized_arguments]
    kwargs = {
        "cwd": str(cwd) if cwd is not None else None,
        "env": dict(runtime.environment),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "shell": False,
    }
    if runtime.platform == "windows":
        kwargs["creationflags"] = runtime.creationflags
    process = popen(command, **kwargs)
    while True:
        if stop_event is not None and stop_event.is_set():
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            if log:
                for line in (stdout + "\n" + stderr).splitlines():
                    log(line)
            raise OsmiumCancelledError("Osmium-Ausführung wurde abgebrochen.")
        try:
            stdout, stderr = process.communicate(timeout=poll_interval)
            break
        except subprocess.TimeoutExpired:
            continue
    if log:
        for line in (stdout + "\n" + stderr).splitlines():
            if line:
                log(line)
        sub_cmd = normalized_arguments[0] if normalized_arguments else "befehl"
        log(f"Osmium: Befehl '{sub_cmd}' erfolgreich abgeschlossen.")
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if result.returncode:
        raise OsmiumError(
            f"Osmium-Befehl fehlgeschlagen ({result.returncode}): {' '.join(command)}\n{stderr.strip()}"
        )
    return result
