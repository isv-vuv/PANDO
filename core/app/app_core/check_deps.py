"""Fast dependency checker for start.bat."""

import importlib.metadata
import os
import subprocess
import sys


def main() -> None:
    req_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "..", "..", "requirements.txt")
    req_file = os.path.abspath(req_file)
    if not os.path.isfile(req_file):
        print(f"[WARNING] requirements.txt not found at: {req_file}")
        return

    with open(req_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    print("Target dependencies from requirements.txt:")
    print("----------------------------------------------------------------------")

    missing = []
    for req in lines:
        name = req.split(">=")[0].split("==")[0].split("<")[0].strip()
        try:
            ver = importlib.metadata.version(name)
            print(f"  [OK] {name:<15} (v{ver})")
        except Exception:
            alt_name = "pyosmium" if name == "osmium" else name
            try:
                ver = importlib.metadata.version(alt_name)
                print(f"  [OK] {name:<15} (v{ver})")
            except Exception:
                missing.append(req)
                print(f"  [MISSING] {req:<15} -> install needed")

    print("----------------------------------------------------------------------")

    if missing:
        print(f"Installing missing package(s): {', '.join(missing)}...")
        cmd = [sys.executable, "-m", "pip", "install"] + missing + ["--quiet", "--disable-pip-version-check"]
        res = subprocess.run(cmd)
        if res.returncode == 0:
            print(f"[OK] Successfully installed missing package(s): {', '.join(missing)}")
        else:
            print("[ERROR] Failed to install missing packages.")
            sys.exit(res.returncode)
    else:
        print("[OK] All dependencies are already installed.")


if __name__ == "__main__":
    main()
