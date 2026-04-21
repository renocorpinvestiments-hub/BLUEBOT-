# bootstrap.py
import os
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

ENV_FILE = BASE_DIR / ".env"

REQUIRED_ENV_VARS = [
    "MT5_LOGIN",
    "MT5_PASSWORD",
    "MT5_SERVER",
    "BRIDGE_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID"
]

# --------------------------------------------------
# UTIL
# --------------------------------------------------

def run(cmd, cwd=None):
    print(f"[RUN] {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")

# --------------------------------------------------
# INSTALL DEPENDENCIES
# --------------------------------------------------

def install_requirements():
    print("\n=== Installing dependencies ===")

    projects = [
        BASE_DIR / "MT5_bridge_server-main",
        BASE_DIR / "BLACKBOT-main",
        BASE_DIR / "orchestrator--main"
    ]

    for project in projects:
        req = project / "requirements.txt"
        if req.exists():
            run(f"{sys.executable} -m pip install -r requirements.txt", cwd=project)
        else:
            print(f"[WARN] No requirements.txt in {project}")

# --------------------------------------------------
# CREATE .ENV
# --------------------------------------------------

def create_env():
    print("\n=== Setting up .env ===")

    if ENV_FILE.exists():
        print("[OK] .env already exists")
        return

    values = {}

    for var in REQUIRED_ENV_VARS:
        val = input(f"Enter {var}: ").strip()
        values[var] = val

    with open(ENV_FILE, "w") as f:
        for k, v in values.items():
            f.write(f"{k}={v}\n")

    print("[OK] .env created")

# --------------------------------------------------
# VALIDATE ENV
# --------------------------------------------------

def validate_env():
    print("\n=== Validating .env ===")

    if not ENV_FILE.exists():
        raise RuntimeError(".env missing")

    content = ENV_FILE.read_text()

    for var in REQUIRED_ENV_VARS:
        if var not in content:
            raise RuntimeError(f"Missing {var} in .env")

    print("[OK] .env looks good")

# --------------------------------------------------
# RUN ORCHESTRATOR
# --------------------------------------------------

def start_orchestrator():
    print("\n=== Starting orchestrator ===")

    orchestrator_path = BASE_DIR / "orchestrator--main" / "orchestrator.py"

    if not orchestrator_path.exists():
        raise RuntimeError("orchestrator.py not found")

    run(f"{sys.executable} orchestrator.py", cwd=orchestrator_path.parent)

# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    print("=== SYSTEM BOOTSTRAP ===")

    install_requirements()
    create_env()
    validate_env()
    start_orchestrator()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL] {e}")
        sys.exit(1)
