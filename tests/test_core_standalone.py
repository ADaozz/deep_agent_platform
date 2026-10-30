"""The platform must run without the CLI distribution or its import namespace."""
from pathlib import Path
import subprocess
import sys


def test_platform_boots_with_external_cli_imports_blocked(tmp_path):
    source = r'''
import importlib.abc, os, sys
os.environ['PLATFORM_DATA_DIR'] = sys.argv[1]
class BlockCLI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'agent' or fullname.startswith('agent.'):
            raise AssertionError('Platform tried to import the external CLI: ' + fullname)
sys.meta_path.insert(0, BlockCLI())
from fastapi.testclient import TestClient
from platform_app.main import app
from platform_app.agent_core.config import default_config_dir, default_skills_dir
from pathlib import Path
assert default_config_dir() == Path(sys.argv[1]).resolve()
assert default_skills_dir() == Path(sys.argv[1]).resolve() / 'skills'
with TestClient(app) as client:
    assert client.get('/api/status').json()['core'] == 'platform_app.agent_core'
    assert client.get('/api/agent/capabilities').json()['source'] == 'in-tree'
    assert len(client.get('/api/features').json()) == 2
    assert client.get('/api/agent/sessions').json() == []
assert not any(name == 'agent' or name.startswith('agent.') for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', source, str(tmp_path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_dependency_manifests_do_not_install_cli():
    root = Path(__file__).resolve().parents[1]
    for name in ('requirements.txt', 'requirements.lock', 'requirements-features.lock'):
        text = (root / name).read_text()
        assert 'deep-agent-cli' not in text
        assert 'deep_agent_cli' not in text
        assert 'git+' not in text
    assert (root / 'platform_app/agent_core/LICENSE').is_file()
    assert not (root / 'platform_app/agent_core/cli').exists()
