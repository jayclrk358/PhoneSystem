"""Run a complete local dev stack: a private Asterisk plus PhoneSystem.

    cd backend && .venv/bin/python scripts/devstack.py

Everything lives under backend/var/dev (delete it to start over). Needs the
asterisk package installed. Ports: web UI/API http://127.0.0.1:8000, phone
provisioning :8081, phone syslog :5514/udp, SIP :5060 (after the first apply).
For UI work, also run "npm run dev" in frontend/ (it proxies /api to :8000).
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from tests.integration.harness import AMI_SECRET, AsteriskInstance  # noqa: E402


def main() -> int:
    root = BACKEND / "var" / "dev"
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    ast_root = root / "asterisk"
    if ast_root.exists():
        import shutil

        shutil.rmtree(ast_root)  # Asterisk's runtime dirs; the data dir is kept
    asterisk = AsteriskInstance(ast_root, data / "asterisk-current")
    asterisk.start()
    dist = BACKEND.parent / "frontend" / "dist"
    env = dict(
        os.environ,
        PHONESYSTEM_DATA_DIR=str(data),
        PHONESYSTEM_AMI_PORT=str(asterisk.ami_port),
        PHONESYSTEM_AMI_SECRET=AMI_SECRET,
        PHONESYSTEM_ADMIN_HOST="127.0.0.1",
        PHONESYSTEM_ADMIN_PORT="8000",
        PHONESYSTEM_SECURE_COOKIES="false",
        PHONESYSTEM_PROVISIONING_PORT="8081",
        PHONESYSTEM_SYSLOG_PORT="5514",
        PHONESYSTEM_FRONTEND_DIST=str(dist),
        PHONESYSTEM_CDR_FILE=str(asterisk.cdr_file),
    )
    app = subprocess.Popen([sys.executable, "-m", "phonesystem.cli", "serve"], env=env)  # noqa: S603

    def stop(*_):
        app.terminate()
        try:
            app.wait(10)
        finally:
            asterisk.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("PhoneSystem dev stack: http://127.0.0.1:8000  (Ctrl+C to stop)")
    app.wait()
    asterisk.stop()
    return app.returncode


if __name__ == "__main__":
    sys.exit(main())
