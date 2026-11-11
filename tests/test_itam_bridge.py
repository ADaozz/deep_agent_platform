"""ITAM bridge regression: signature integrity, RBAC and owner isolation."""
import hashlib
import hmac
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_app import itam_bridge


SECRET = 'regression-test-shared-secret-0123456789abcdef'


def signed(method, path, user='1001', role='user', query='', timestamp=None):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    message = '\n'.join((timestamp, user, role, method, path, query))
    signature = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        'X-Agent-User-Id': user,
        'X-Agent-Role': role,
        'X-Agent-Timestamp': timestamp,
        'X-Agent-Signature': signature,
    }


def test_verify_rejects_tamper_and_stale_headers(monkeypatch):
    monkeypatch.setenv('ITAM_RUNTIME_SHARED_SECRET', SECRET)
    from starlette.requests import Request
    from starlette.datastructures import Headers

    def request(method, path, headers, query=''):
        scope = {'type': 'http', 'method': method,
                 'path': path, 'raw_path': path.encode(), 'query_string': query.encode(),
                 'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
        return Request(scope)

    path = '/api/agent/sessions/abc'
    good = signed('GET', path, query='after=1')
    assert itam_bridge.verify(request('GET', path, good, 'after=1')) == ('1001', 'user')
    assert itam_bridge.verify(request('POST', path, good, 'after=1')) is None
    assert itam_bridge.verify(request('GET', path, good, 'after=2')) is None
    assert itam_bridge.verify(request('GET', path, signed('GET', path, timestamp=int(time.time())-40))) is None


def test_middleware_enforces_owner_and_admin_config(monkeypatch):
    monkeypatch.setenv('ITAM_RUNTIME_SHARED_SECRET', SECRET)
    def find(kind, key):
        if kind == 'session' and key == 'own':
            return {'id': key, 'owner_user_id': '1001'}
        if kind == 'session' and key == 'other':
            return {'id': key, 'owner_user_id': '1002'}
        raise KeyError(key)

    monkeypatch.setattr(itam_bridge.d, 'get', find)
    app = FastAPI()
    itam_bridge.install(app)

    @app.get('/api/agent/sessions/{session_id}')
    def get_session(session_id: str):
        return {'id': session_id, 'owner': itam_bridge.owner()}

    @app.put('/api/models')
    def save_models():
        return {'ok': True}

    @app.put('/api/models/personal')
    def save_personal_models():
        return {'owner': itam_bridge.owner()}

    @app.put('/api/settings')
    def save_settings():
        return {'ok': True}

    @app.put('/api/features/{feature_id}')
    def save_feature(feature_id: str):
        return {'ok': True}

    with TestClient(app) as client:
        path = '/api/agent/sessions/own'
        ok = client.get(path, headers=signed('GET', path))
        assert ok.status_code == 200 and ok.json()['owner'] == '1001'
        path = '/api/agent/sessions/other'
        assert client.get(path, headers=signed('GET', path)).status_code == 404
        path = '/api/models'
        assert client.put(path, headers=signed('PUT', path, role='user')).status_code == 403
        assert client.put(path, headers=signed('PUT', path, role='admin')).status_code == 200
        assert client.get('/api/agent/sessions/own').status_code == 401
        path = '/api/models/personal'
        assert client.put(path, headers=signed('PUT', path, role='user')).json() == {'owner': '1001'}
        for path in ('/api/settings', '/api/features/docx'):
            assert client.put(path, headers=signed('PUT', path, role='admin')).status_code == 403
