"""Collection must never initialize or inspect the user's platform data directory."""
import os
import tempfile
from pathlib import Path
import shutil

BOOTSTRAP = Path(tempfile.mkdtemp(prefix='platform-test-bootstrap-'))
os.environ['PLATFORM_DATA_DIR'] = str(BOOTSTRAP)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(BOOTSTRAP, ignore_errors=True)
