"""Optional DOCX HTTP API. Loaded only while the DOCX feature is installed."""
import re
from pathlib import Path
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from platform_app import domain as d

def create_router(host, DocumentStore):
    router=APIRouter(prefix='/api/agent')
    @router.get('/sessions/{session_id}/documents')
    def session_documents(session_id: str):
        record = host.session(session_id)
        documents = host.service(session_id, 'docx')
        documents.sync()
        result = []
        for document_id in record.get('documents', {}):
            try:
                store = document_store(session_id, document_id)
                state = store.status(committed=True)
                if state.get('preview_revision', -1) < state['revision']:
                    documents.schedule_preview(store.path)
                result.append(dict(document_id=document_id, path=record['documents'][document_id]['path'], **{k: state.get(k) for k in ('revision','sha256','pages','gate','render_revision','render_error','finalized','preview_revision','preview_pages','preview_error','external_conflict')}))
            except (ValueError, OSError):
                continue
        return result


    def document_store(session_id, document_id):
        host.service(session_id,'docx')
        record = host.session(session_id)
        if document_id not in record.get('documents', {}):
            raise HTTPException(404, '文档不属于当前会话')
        try:
            root = d.DATA / 'workspaces' / record['workspace_id'] / 'files'
            path = root / record['documents'][document_id]['path']
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or '.docx-state' in path.relative_to(root).parts:
                raise ValueError('Invalid document path')
        except ValueError:
            raise HTTPException(422, '文档标识无效')
        return DocumentStore(path)


    @router.get('/sessions/{session_id}/documents/{document_id}/revisions/{revision}')
    def document_snapshot(session_id: str, document_id: str, revision: int):
        from fastapi.responses import Response
        from urllib.parse import quote
        store = document_store(session_id, document_id)
        try:
            data = store.snapshot(revision)
        except (ValueError, OSError):
            raise HTTPException(404, '文档版本不存在')
        return Response(data, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={'Content-Disposition': f"attachment; filename=\"document-r{revision}.docx\"; filename*=UTF-8''{quote(store.path.name)}", 'Cache-Control': 'private, max-age=31536000, immutable'})


    @router.get('/sessions/{session_id}/documents/{document_id}/renders/{revision}/{filename}')
    def document_page(session_id: str, document_id: str, revision: int, filename: str):
        store = document_store(session_id, document_id)
        if not re.fullmatch(r'page-\d{3}\.png|preview\.pdf', filename) or revision < 0:
            raise HTTPException(404, '页面不存在')
        path = store.directory / ('render-%08d' % revision) / filename
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, '页面不存在')
        return FileResponse(path, headers={'Cache-Control': 'private, max-age=31536000, immutable'})


    class VisualReview(BaseModel):
        revision: int = Field(ge=0)
        page: int = Field(ge=1)
        passed: bool
        notes: str = Field(min_length=1, max_length=2000)


    @router.post('/sessions/{session_id}/documents/{document_id}/review')
    def human_document_review(session_id: str, document_id: str, value: VisualReview):
        store = document_store(session_id, document_id)
        try:
            result = store.visual_review(value.revision, value.page, value.passed, 'human', value.notes)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        host.service(session_id,'docx').sync()
        return result
    return router
