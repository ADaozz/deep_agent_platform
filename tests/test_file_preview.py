import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from platform_app import runtime


@pytest.fixture
def preview(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'find', lambda session_id: {})
    monkeypatch.setattr(runtime, 'resolve', lambda session_id: SimpleNamespace(files_root=tmp_path))
    app = FastAPI()
    app.include_router(runtime.router)

    def get(path, headers=None):
        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                return await client.get('/api/agent/sessions/test/file-preview/' + path, headers=headers)
        return asyncio.run(request())
    return tmp_path, get


@pytest.mark.parametrize('ext,encoding', [('md', 'utf-8-sig'), ('txt', 'gb18030'), ('yaml', 'utf-16'), ('yml', 'utf-8'), ('json', 'utf-8')])
def test_text_preview_decodes_without_executing(preview, ext, encoding):
    root, get = preview
    content = '# 中文\nkey: <script>alert(1)</script>'
    (root / ('report.' + ext)).write_bytes(content.encode(encoding))
    response = get('report.' + ext)
    assert response.status_code == 200
    assert response.json()['content'] == content
    assert response.headers['content-type'].startswith('application/json')


@pytest.mark.parametrize('ext,mime', [('pdf', 'application/pdf'), ('png', 'image/png'), ('svg', 'image/svg+xml')])
def test_media_is_inline_and_pdf_supports_ranges(preview, ext, mime):
    root, get = preview
    (root / ('report.' + ext)).write_bytes(b'0123456789')
    response = get('report.' + ext)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith(mime)
    assert response.headers['content-disposition'].startswith('inline;')
    assert response.headers['x-content-type-options'] == 'nosniff'
    if ext == 'svg':
        assert 'sandbox' in response.headers['content-security-policy']
    if ext == 'pdf':
        ranged = get('report.pdf', {'Range': 'bytes=0-3'})
        assert ranged.status_code == 206 and ranged.content == b'0123'


def test_oversize_unsupported_missing_and_path_escape_are_rejected(preview, tmp_path):
    root, get = preview
    (root / 'large.txt').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
    (root / 'page.html').write_text('<script>bad()</script>')
    outside = tmp_path.parent / (tmp_path.name + '-outside.txt')
    outside.write_text('private')
    (root / 'linked.txt').symlink_to(outside)
    assert get('large.txt').status_code == 413
    assert get('page.html').status_code == 415
    assert get('missing.txt').status_code == 404
    assert get('linked.txt').status_code == 404
    assert get('%2e%2e/' + outside.name).status_code == 404
