"""Persistent Thread resource ownership and translation to native sandbox policy."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import yaml
from . import domain as d

ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid Thread or Workspace identifier')
    return value


@dataclass(frozen=True)
class PlatformPolicy:
    workspaces_root: Path
    sessions_root: Path
    state_root: Path
    skills_root: Path
    default_mode: str = 'restricted'

    @classmethod
    def load(cls):
        path = Path(os.environ.get('PLATFORM_POLICY', d.DATA / 'configuration' / 'policy.yaml'))
        raw = yaml.safe_load(path.read_text()) if path.is_file() else {}
        raw = raw or {}
        if not isinstance(raw, dict) or set(raw) - {'workspace', 'sandbox'}:
            raise ValueError('Invalid platform isolation policy')
        workspace, sandbox = raw.get('workspace', {}), raw.get('sandbox', {})
        if not isinstance(workspace, dict) or not isinstance(sandbox, dict):
            raise ValueError('Invalid platform isolation policy')
        if set(workspace) - {'isolation', 'root'} or set(sandbox) - {'default_mode', 'state_scope', 'state_root', 'mounts', 'full_access'}:
            raise ValueError('Unknown isolation policy fields')
        if workspace.get('isolation', 'thread') != 'thread' or sandbox.get('state_scope', 'thread') != 'thread':
            raise ValueError('Sandbox state and ordinary workspace ownership must be Thread scoped')
        if sandbox.get('default_mode', 'restricted') != 'restricted':
            raise ValueError('The default sandbox mode must be restricted')
        mounts = sandbox.get('mounts', {})
        full = sandbox.get('full_access', {})
        if not isinstance(mounts, dict) or set(mounts) - {'allow_custom', 'read_only', 'read_write'} or mounts.get('allow_custom', False) is not False or mounts.get('read_only', []) != [] or mounts.get('read_write', []) != []:
            raise ValueError('Custom host mounts are not authorized by this platform policy')
        if not isinstance(full, dict) or set(full) - {'require_confirmation'} or full.get('require_confirmation', True) is not True:
            raise ValueError('Full access requires explicit confirmation')
        def root(value, default):
            p = Path(value).expanduser() if value is not None else default
            if not p.is_absolute(): p = d.DATA.parent / p
            return p.absolute()
        policy = cls(root(workspace.get('root'), d.DATA / 'workspaces'), d.DATA / 'sessions',
            root(sandbox.get('state_root'), d.DATA / 'sandbox-states'), d.DATA / 'skill-snapshots')
        roots = (policy.workspaces_root, policy.sessions_root, policy.state_root, policy.skills_root)
        for index, p in enumerate(roots):
            if any(part.is_symlink() for part in (p,*p.parents)):
                raise ValueError('Storage roots cannot use symlink aliases')
            real = p.resolve()
            if real == d.DATA or d.DATA.is_relative_to(real) or any(real.is_relative_to(Path(r)) for r in ('/usr','/bin','/lib','/lib64')):
                raise ValueError('Storage root would expose platform control or system runtime files')
            if any(real.is_relative_to(q.resolve()) or q.resolve().is_relative_to(real) for q in roots[index+1:]):
                raise ValueError('Workspace, session, skills and sandbox state roots must be disjoint')
        return policy


@dataclass(frozen=True)
class WorkspaceContext:
    thread_id: str
    workspace_id: str
    workspace_root: Path
    files_root: Path
    artifacts_root: Path
    session_root: Path
    sandbox_state_root: Path
    skills_root: Path


def real_directory(path):
    # Refuse storage aliases rather than silently assigning another Thread's tree.
    for part in (path, *path.parents):
        if part.is_symlink(): raise ValueError('Thread storage cannot contain symlinks')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir(): raise ValueError('Thread storage must be a directory')
    return path.resolve()


class WorkspaceResolver:
    def __init__(self, policy=None):
        self.policy = policy or PlatformPolicy.load()

    def resolve(self, thread_id):
        identifier(thread_id)
        with d.LOCK, d.db() as connection:
            candidate = dict(id=thread_id, workspace_id='ws_' + uuid4().hex)
            connection.execute('INSERT OR IGNORE INTO records VALUES (?,?,?)', ('thread_workspace', thread_id, json.dumps(candidate)))
            value = json.loads(connection.execute('SELECT data FROM records WHERE kind=? AND id=?', ('thread_workspace', thread_id)).fetchone()[0])
        workspace_id = identifier(value['workspace_id'])
        root = real_directory(self.policy.workspaces_root / workspace_id)
        files = real_directory(root / 'files')
        artifacts = real_directory(root / 'artifacts')
        real_directory(root / 'tmp')
        session = real_directory(self.policy.sessions_root / thread_id)
        state = real_directory(self.policy.state_root / thread_id)
        skills = real_directory(self.policy.skills_root / thread_id)
        return WorkspaceContext(thread_id, workspace_id, root, files, artifacts, session, state, skills)


class SandboxPolicyBuilder:
    def __init__(self, resolver=None):
        self.resolver = resolver or WorkspaceResolver()

    def build(self, context, mode='restricted', *, read_only=(), read_write=(), env_set=None):
        from agent.config import BindMount, SandboxConfig
        from agent.sandbox import _validate_config
        if context != self.resolver.resolve(context.thread_id):
            raise ValueError('WorkspaceContext does not match its persistent Thread association')
        if mode not in ('restricted', 'full-access'):
            raise ValueError('Invalid sandbox mode')
        if read_only or read_write or env_set:
            raise ValueError('Unapproved host mounts or environment overrides')
        policy = self.resolver.policy
        protected = (d.DATA / 'platform.sqlite', d.DATA / 'platform.sqlite-wal', d.DATA / 'platform.sqlite-shm', d.DATA / 'platform.sqlite-journal',
            d.DATA / 'model-config.key', d.DATA / 'configuration', policy.sessions_root, policy.state_root,
            Path.home() / '.deep-agent')
        for path in protected:
            if context.workspace_root.is_relative_to(path.resolve()):
                raise ValueError('A protected control directory cannot contain the workspace')
        config = SandboxConfig(workspace=context.workspace_root, mode=mode,
            protected_config_dir=context.sandbox_state_root, protected_files=protected,
            mount_skills=False, network_modes=('full-access',), extra_read_only_mounts=(
                BindMount(source=context.skills_root, destination='/agent-skills'),
                BindMount(source=context.skills_root, destination=context.skills_root.as_posix())),
            extra_read_write_mounts=(), env_set={})
        _validate_config(config)  # Native validation also runs on each mode rebuild.
        return config


def resolve(thread_id):
    return WorkspaceResolver().resolve(thread_id)
