"""Signed identity bridge from the Spring Boot ITAM backend.

The Python service must be bound to loopback/private networking. In ITAM mode
all API calls require a short-lived signed identity from the Spring backend;
unowned pre-migration sessions are intentionally inaccessible.
"""
import hashlib
import hmac
import os
import re
import time
from contextvars import ContextVar

from fastapi.responses import JSONResponse

from . import domain as d

USER = ContextVar('itam_user_id', default=None)
ROLE = ContextVar('itam_role', default='user')
SESSION_PATH = re.compile(r'^/api/agent/sessions/([a-zA-Z0-9_-]{1,80})(?:/|$)')
SENSITIVE = {'/api/models'}
# Agent tools, skills and feature plugins are server-managed in ITAM mode, even for admins.
LOCKED = {'/api/settings', '/api/features'}
CONFIG_WRITE = {'POST', 'PUT', 'PATCH', 'DELETE'}


def is_enabled():
    return os.getenv('ITAM_RUNTIME_SHARED_SECRET', '') != ''


def owner():
    return USER.get()


def verify(request):
    secret = os.getenv('ITAM_RUNTIME_SHARED_SECRET', '')
    if len(secret.encode('utf-8')) < 32:
        raise ValueError('ITAM_RUNTIME_SHARED_SECRET must have at least 32 bytes')
    headers = request.headers
    user = headers.get('x-agent-user-id', '')
    role = headers.get('x-agent-role', '')
    timestamp = headers.get('x-agent-timestamp', '')
    signature = headers.get('x-agent-signature', '')
    if not re.fullmatch(r'[1-9][0-9]{0,18}', user) or role not in ('admin', 'user'):
        return None
    try:
        if abs(int(time.time()) - int(timestamp)) > 30:
            return None
    except (TypeError, ValueError):
        return None
    path = request.scope.get('raw_path', b'').decode('ascii', 'surrogateescape')
    query = request.scope.get('query_string', b'').decode('ascii', 'surrogateescape')
    canonical = '\n'.join((timestamp, user, role, request.method, path, query))
    expected = hmac.new(secret.encode(), canonical.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return user, role


def install(app):
    if not is_enabled():
        # The original single-user standalone product keeps its previous behavior.
        return
    if len(os.environ['ITAM_RUNTIME_SHARED_SECRET'].encode('utf-8')) < 32:
        raise RuntimeError('ITAM_RUNTIME_SHARED_SECRET must have at least 32 bytes')

    @app.middleware('http')
    async def itam_authorization(request, call_next):
        if not request.url.path.startswith('/api/'):
            return await call_next(request)
        identity = verify(request)
        if identity is None:
            return JSONResponse({'detail': 'Invalid ITAM runtime identity'}, status_code=401)
        user, role = identity
        path = request.url.path
        if request.method in CONFIG_WRITE and (path in LOCKED or path.startswith('/api/features/')):
            return JSONResponse({'detail': 'ITAM 模式下暂不开放 Agent 工具与 Skill 配置'}, status_code=403)
        # Shiro RBAC continues to determine admin/user, this is a defense in depth check.
        if request.method in CONFIG_WRITE and path in SENSITIVE and role != 'admin':
            return JSONResponse({'detail': 'Admin permission required'}, status_code=403)
        # Full host access can bypass Thread isolation, so it is disabled in multi-user mode.
        if request.method == 'PUT' and re.fullmatch(r'/api/agent/sessions/[^/]+/sandbox', path):
            return JSONResponse({'detail': 'Sandbox mode changes are disabled in ITAM mode'}, status_code=403)
        if request.method == 'POST' and path == '/api/agent/sessions':
            # Existing NewSession still validates its body; the endpoint separately
            # rejects full-access when the bridge is active.
            pass
        match = SESSION_PATH.match(path)
        if match:
            try:
                record = d.get('session', match.group(1))
            except KeyError:
                return JSONResponse({'detail': 'Session not found'}, status_code=404)
            if record.get('owner_user_id') != user:
                return JSONResponse({'detail': 'Session not found'}, status_code=404)
        owner_token = USER.set(user)
        role_token = ROLE.set(role)
        try:
            return await call_next(request)
        finally:
            USER.reset(owner_token)
            ROLE.reset(role_token)
