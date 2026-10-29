"""HTTP presentation adapter for the upstream AgentRunner, never an agent loop."""
import asyncio
import base64
import json
import logging
import mimetypes
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import domain as d, models as m
from .usage import runner_usage, saved_usage
from . import format_plugins

router = APIRouter(prefix='/api/agent')
POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='agent-core')
RUNTIMES = {}
LOCK = threading.RLock()
LOG = logging.getLogger(__name__)
ACTIVE = ('queued', 'running')
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def upload_records(workspace_id, ids):
    root = d.DATA / 'workspaces' / workspace_id / 'files'
    result = []
    for upload_id in ids:
        try:
            item = d.get('upload', upload_id)
        except KeyError:
            raise HTTPException(404, '上传文件不存在')
        path = root / item['path']
        if item['workspace_id'] != workspace_id or not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise HTTPException(404, '上传文件不存在')
        result.append(item)
    return result


@router.post('/workspaces/{workspace_id}/uploads', status_code=201)
async def upload_file(workspace_id: str, file: UploadFile = File(...)):
    try:
        d.get('workspace', workspace_id)
    except KeyError:
        raise HTTPException(404, '工作空间不存在')
    name = Path((file.filename or 'file').replace('\\', '/')).name
    name = ''.join(c for c in name if c.isprintable()).strip().encode('utf-8')[:180].decode('utf-8', errors='ignore') or 'file'
    if name in ('.', '..'):
        raise HTTPException(422, '文件名无效')
    upload_id = uuid4().hex
    relative = Path('uploads') / upload_id / name
    root = d.DATA / 'workspaces' / workspace_id / 'files'
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.resolve().is_relative_to(root.resolve()):
        raise HTTPException(422, '上传目录无效')
    size = 0
    try:
        with path.open('xb') as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, '每个文件不能超过 20MB')
                target.write(chunk)
        if not size:
            raise HTTPException(422, '不能上传空文件')
        record = dict(id=upload_id, workspace_id=workspace_id, name=name, size=size,
            type=mimetypes.guess_type(name)[0] or 'application/octet-stream', path=relative.as_posix(), created_at=now())
        return d.put('upload', record)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def internal_path(path):
    return any(part=='.deepagents' or (part.startswith('.') and part.endswith('-state')) for part in Path(path).parts)


def public_event(event):
    if event.get('type')=='output_files':
        return {**event,'files':[item for item in event.get('files',[]) if not internal_path(item.get('path',''))]}
    return event


def journal(session_id, after):
    prefix = f'{session_id}:'
    with d.db() as connection:
        rows = connection.execute(
            'SELECT data FROM records WHERE kind=? AND id>? AND id<? ORDER BY id LIMIT 512',
            ('event', f'{prefix}{after:010d}', prefix + '~')).fetchall()
    return [public_event(json.loads(row[0])) for row in rows]


def workspace_snapshot(root):
    snapshot = {}
    if root.exists():
        for path in root.rglob('*'):
            if not path.is_file() or path.is_symlink() or internal_path(path.relative_to(root)):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[path.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def changed_workspace_files(root, before):
    after = workspace_snapshot(root)
    changed = ({'path': name, 'size': size} for name, (mtime, size) in after.items() if before.get(name) != (mtime, size))
    return sorted(changed, key=lambda item: item['path'])[:100]


def find(session_id):
    try:
        return d.get('session', session_id)
    except KeyError:
        raise HTTPException(404, '会话不存在')


def public(record):
    value = {k: v for k, v in record.items() if k not in ('model_snapshot', 'config')}
    root = d.DATA / 'artifacts' / record['workspace_id'] / record['id']
    value['artifacts'] = [p.name for p in root.iterdir() if p.is_file() and not p.is_symlink()] if root.exists() else []
    return value


def initialize():
    for record in d.all_records('session'):
        if record['status'] in ACTIVE:
            record.update(status='interrupted', usage={}, error='服务重启，本轮已中断；可发送新消息恢复会话。')
            d.put('session', record)
        if 'cache_hit_rate' not in record.get('usage', {}) and record['status'] in ('completed', 'waiting_confirmation', 'waiting_human', 'paused'):
            record['usage'] = saved_usage(record, d.DATA / 'runtime' / record['id'] / 'session.sqlite')
            d.put('session', record)


class Runtime:
    def __init__(self, session_id):
        self.id = session_id
        self.runner = None
        self.artifact_services = {}
        self.lock = threading.RLock()

    def event(self, event):
        with self.lock:
            record = find(self.id)
            event = json.loads(json.dumps(event, default=str))
            event['occurred_at'] = now()
            event['seq'] = record.get('event_seq', 0) + 1
            record['event_seq'] = event['seq']
            if event['type'] == 'runtime_config_applied':
                record['permission_mode'] = event['result']['permission_mode']
                record['pending_permission_mode'] = None
                record['sandbox_mode'] = event['result']['sandbox_mode']
                if event['result'].get('model_changed') or event['result'].get('reasoning_changed'):
                    record['usage'] = {}
            if event['type'] == 'assistant_started':
                record['usage'] = {}
            elif event['type'] == 'usage':
                record['usage'] = event.get('result') or {}
            artifact=event.get('artifact_extension')
            if artifact:
                items=record.setdefault(artifact['state_key'],{})
                previous=items.get(artifact['id'],{})
                if event.get('revision',-1)>=previous.get('revision',-1):
                    newer=event.get('revision',-1)>previous.get('revision',-1)
                    items[artifact['id']]={**previous,**({'pages':[],'finalized':False,'render_error':None} if newer else {}),**event}
                if event.get('store_seq') and event.get('path'):
                    record.setdefault('artifact_cursors',{}).setdefault(artifact['feature'],{})[event['path']]=event['store_seq']
            # Event journal is durable; reconnecting clients resume by sequence.
            record['updated_at'] = now()
            with d.LOCK, d.db() as connection:
                for kind, value in [('event', {'id': f'{self.id}:{event["seq"]:010d}', 'session_id': self.id, **event}), ('session', record)]:
                    connection.execute('INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data', (kind, value['id'], json.dumps(value, ensure_ascii=False)))

    def ensure_artifact_services(self):
        with self.lock:
            if self.artifact_services:
                return self.artifact_services
            record=find(self.id)
            workspace=d.DATA/'workspaces'/record['workspace_id']/'files'
            workspace.mkdir(parents=True,exist_ok=True)
            self.artifact_services=format_plugins.build_services(workspace,record['workspace_id'],self.id,self.event)
            for feature,service in self.artifact_services.items():
                service.bind_state(record)
            return self.artifact_services

    def core_event(self,event):
        data=asdict(event)
        self.event(data)
        for service in self.artifact_services.values():
            if self.runner:service.vision=self.runner.supports_next_input('image')
            if data['type'] in ('tool_completed','tool_failed'):service.detect_external()

    def build(self):
        if self.runner is not None:
            return self.runner
        from agent import AgentRunner
        from .agent_factory import prepare_agent
        from agent.config import Settings, SandboxConfig, SubAgentConfig, BindMount
        from agent.llm import build_chat_model
        from agent.session import SessionStore
        from agent.mcp.manager import MCPManager

        record = find(self.id)
        config = record['config']
        workspace = d.DATA / 'workspaces' / record['workspace_id'] / 'files'
        workspace.mkdir(parents=True, exist_ok=True)
        # Immutable skill snapshot outside the writable workspace, mounted read-only.
        root = d.DATA / 'runtime' / self.id
        root.mkdir(parents=True, exist_ok=True)
        skills = d.DATA / 'skill-snapshots' / self.id
        skills.mkdir(parents=True, exist_ok=True)
        for index, skill in enumerate(config['skills']):
            if skill.get('enabled', True):
                path = skills / f'skill-{index + 1}'
                path.mkdir(exist_ok=True)
                title = skill['name'].replace('\n', ' ').replace('\r', ' ')
                slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or f'skill-{index + 1}'
                (path / 'SKILL.md').write_text(f'---\nname: {slug}\ndescription: {json.dumps(skill.get("description") or title, ensure_ascii=False)}\n---\n\n# {title}\n\n{skill["content"]}', encoding='utf-8')
        # Copy complete installed skills, including scripts/references, into
        # the session's read-only snapshot. Only enabled bindings are discovered.
        from agent.config import default_skills_dir
        installed = skills / 'installed'
        installed.mkdir(exist_ok=True)
        for skill in config.get('native_skills', []):
            if not skill.get('enabled'):
                continue
            identity = skill['id']
            if Path(identity).name != identity or identity in ('.', '..'):
                raise ValueError('无效的 Skill 标识')
            destination = installed / identity
            if destination.exists():
                continue
            source = (default_skills_dir() / identity).resolve()
            if not source.is_relative_to(default_skills_dir().resolve()) or not (source / 'SKILL.md').is_file():
                raise ValueError('已绑定的 Skill 不存在，请重新配置工作区')
            temporary = installed / f'.{identity}-copy'
            if temporary.exists():
                shutil.rmtree(temporary)
            shutil.copytree(source, temporary, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '*.pyc'))
            temporary.rename(destination)
        format_plugins.snapshot_skills(skills)
        snapshot = record['model_snapshot']
        settings = Settings.from_mapping({'llm': m.unseal(snapshot['encrypted'])})
        hidden = d.hidden_tools(config)
        settings = replace(settings, trust_env=True, llm_default=record['model_id'],
            subagents=SubAgentConfig(enabled='task' not in hidden), hidden_tools=hidden,
            sandbox=SandboxConfig(workspace=workspace, mode='full-access', protected_config_dir=d.DATA / 'configuration',
                protected_files=(d.DATA / 'platform.sqlite', d.DATA / 'model-config.key', d.DATA / 'runtime'),
                extra_read_only_mounts=(BindMount(source=skills, destination='/workspace-skills'),
                    BindMount(source=skills, destination=skills.as_posix()))),
            config_dir=d.DATA / 'configuration', agent_instructions=config['system_prompt'])
        model = build_chat_model(settings.active_profile, reasoning_effort=record['reasoning_effort'], trust_env=True, timeout=settings.request_timeout)
        store = SessionStore.for_workspace(workspace, override=root / 'session.sqlite')
        (root / 'mcp.json').write_text(json.dumps(config.get('mcp', {'enabled': False, 'servers': {}})), encoding='utf-8')
        manager = MCPManager(root / 'mcp.json', trust_env=True)
        manager.load()
        adapter = d.WorkspaceAdapter(config, self.id)
        self.ensure_artifact_services()
        for service in self.artifact_services.values():service.vision='image' in settings.active_profile.input
        try:
            prepared = prepare_agent(settings=settings, model=model, checkpointer=store.checkpointer,
                instructions=format_plugins.instructions() + '\n\n' + '\n\n'.join([config['system_prompt'], '\n'.join(config['rules'])]), skills=['/workspace-skills/', '/workspace-skills/installed/'],
                extra_tools=[*adapter.tools(include_artifacts=False), *[tool for service in self.artifact_services.values() for tool in service.tools()]], interrupt_on={**{t['name']: True for t in config['tools'] if t['enabled'] and t['name'] in d.TOOL_SCHEMAS}, **{name: True for service in self.artifact_services.values() for name in getattr(service,'approval_tools',())}} or None, mcp=manager)
            runner = AgentRunner(prepared=prepared, settings=settings, session_store=store,
                thread_id=self.id, workspace=workspace, mcp_manager=manager,
                on_event=self.core_event)
            if record['reasoning_effort']:
                runner.request_model_change(record['model_id'], reasoning_effort=record['reasoning_effort'])
            runner.request_permission_change(record.get('pending_permission_mode') or record.get('permission_mode', 'ask'))
            runner.apply_pending_config_if_idle()
            self.runner = runner
            with self.lock:
                current = find(self.id)
                current.update(permission_mode=runner.permission_mode().value,
                    pending_permission_mode=runner.pending_permission_mode().value if runner.pending_permission_mode() else None,
                    sandbox_mode=runner.sandbox_mode())
                d.put('session', current)
            self.event({'type': 'capabilities', 'tools': list(runner.prepared.model_tool_names), 'mode': runner.prepared.execution_mode.value, 'sandbox_mode': runner.sandbox_mode()})
            return runner
        except BaseException:
            manager.close()
            store.close()
            raise

    def execute(self, operation, payload):
        try:
            with self.lock:
                record = find(self.id)
                record.update(status='running', error=None)
                d.put('session', record)
            self.event({'type': 'status', 'status': 'running'})
            runner = self.build()
            workspace_root = d.DATA / 'workspaces' / record['workspace_id'] / 'files'
            before = workspace_snapshot(workspace_root)
            if operation == 'message':
                from agent import ImageAttachment
                images = []
                for item in payload.get('images', []):
                    data = base64.b64decode(item['data'], validate=True)
                    images.append(ImageAttachment(filename=item['name'], mime_type=item['type'], data=data, size=len(data)))
                uploads = upload_records(record['workspace_id'], payload.get('upload_ids', []))
                context = []
                for item in uploads:
                    path = d.DATA / 'workspaces' / record['workspace_id'] / 'files' / item['path']
                    context.append(f'- {json.dumps(item["name"], ensure_ascii=False)}: {path}')
                    if runner.supports_next_input('image') and item['type'] in ('image/png', 'image/jpeg', 'image/webp', 'image/gif') and item['size'] <= 10 * 1024 * 1024 and len(images) < 4:
                        data = path.read_bytes()
                        images.append(ImageAttachment(filename=item['name'], mime_type=item['type'], data=data, size=len(data)))
                text = payload['text'] + ('\n\n[Uploaded files in this workspace; use tools to read these paths]\n' + '\n'.join(context) if context else '')
                result = runner.invoke(text, images=images)
            elif operation == 'approve':
                result = runner.decide_tools(payload['decisions'])
            elif operation == 'answer':
                result = runner.submit_human_input(payload['values'])
            elif operation == 'resume':
                result = runner.resume_runtime_config() if str(runner.current_interrupt().kind) == 'runtime_config_boundary' else runner.continue_run()
            elif operation == 'compact':
                result = runner.compact_context()
                self.event({'type': 'compacted', **asdict(result)})
                with self.lock:
                    record = find(self.id)
                    record.update(status='completed', usage={})
                    d.put('session', record)
                return
            else:
                raise ValueError('未知操作')
            with self.lock:
                record = find(self.id)
                record.update(status=result.status, output=result.output or '',
                    error='内核执行失败，请检查服务端日志及模型配置。' if result.error else None,
                    pending_tool_calls=result.pending_tool_calls, human_input=result.human_input,
                    usage=runner_usage(runner), model_id=runner.current_model().id,
                    permission_mode=runner.permission_mode().value,
                    pending_permission_mode=runner.pending_permission_mode().value if runner.pending_permission_mode() else None,
                    sandbox_mode=runner.sandbox_mode(),
                    reasoning_effort=None if runner.reasoning_effort() == 'default' else runner.reasoning_effort())
                d.put('session', record)
            for service in self.artifact_services.values():service.detect_external()
            changed = changed_workspace_files(workspace_root, before)
            if changed:
                self.event({'type': 'output_files', 'files': changed})
            self.event({'type': 'result', **{**asdict(result), 'error': record['error'] or ''}})
        except Exception as exc:
            # Provider errors may contain credentials; only publish known safe diagnoses.
            from agent.sandbox import SandboxUnavailableError
            message = 'Bubblewrap 沙箱不可用，请检查主机 user namespace 与 bwrap 配置。' if isinstance(exc, SandboxUnavailableError) else 'Agent 执行失败，请检查模型配置与服务端日志。'
            LOG.error('Agent operation failed: session=%s exception=%s', self.id, type(exc).__name__)
            with self.lock:
                record = find(self.id)
                record.update(status='failed', error=message)
                d.put('session', record)
            self.event({'type': 'error', 'content': message})


def runtime(session_id):
    find(session_id)
    with LOCK:
        return RUNTIMES.setdefault(session_id, Runtime(session_id))


def schedule(session_id, operation, payload):
    rt = runtime(session_id)
    with rt.lock:
        record = find(session_id)
        if record['status'] in ACTIVE:
            raise HTTPException(409, '当前会话正在执行')
        if sum(r['status'] in ACTIVE for r in d.all_records('session')) >= 8:
            raise HTTPException(429, '任务队列已满')
        previous = record['status']
        if operation == 'compact' and previous in ('waiting_confirmation', 'waiting_human', 'paused', 'runtime_config_boundary'):
            raise HTTPException(409, '请先处理当前中断再压缩上下文')
        if operation == 'message' and previous in ('waiting_confirmation', 'waiting_human', 'paused', 'runtime_config_boundary'):
            raise HTTPException(409, '请先处理当前审批、提问或暂停')
        if operation == 'approve' and previous != 'waiting_confirmation':
            raise HTTPException(409, '当前没有待审批工具')
        if operation == 'answer' and previous != 'waiting_human':
            raise HTTPException(409, '当前没有待回答问题')
        if operation == 'resume' and previous not in ('paused', 'runtime_config_boundary'):
            raise HTTPException(409, '当前会话未暂停')
        if operation == 'approve':
            decisions = payload.get('decisions', [])
            expected = {p['toolCallId'] for p in record.get('pending_tool_calls', [])}
            if len(decisions) != len(expected) or {v.get('tool_call_id') for v in decisions} != expected or any(v.get('decision') not in ('approve', 'reject') for v in decisions):
                raise HTTPException(422, '请对每一个待审批工具作出决定')
        if operation == 'answer':
            from agent.tools.human_interaction import validate_human_input_values
            try:
                validate_human_input_values(payload.get('values'), record['human_input'])
            except ValueError as exc:
                raise HTTPException(422, str(exc))
        record.update(status='queued', updated_at=now(), error=None)
        if operation == 'message':
            record['messages'].append({'role': 'user', 'content': payload['text'], 'created_at': now(), 'images': [i['name'] for i in payload.get('images', [])], 'attachments': payload.get('attachments', [])})
        d.put('session', record)
        rt.event({'type': 'operation', 'operation': operation, 'text': payload.get('text', ''), 'attachments': payload.get('attachments', [])})
    POOL.submit(rt.execute, operation, payload)
    return public(find(session_id))


class NewSession(BaseModel):
    workspace_id: str
    model_id: str | None = None
    reasoning_effort: str | None = None
    permission_mode: str = Field(default='ask', pattern='^(ask|allow)$')
    permission_confirmation: str | None = None


@router.get('/capabilities')
def capabilities():
    return {'core': 'ADaozz/deep_agent_cli', 'commit': 'f190baa2e08a160f1cf8e2c1575cf3261e0bd826',
        'tools': list(d.CORE_TOOLS), 'streaming': True, 'sessions': True, 'approvals': True,
        'subagents': True, 'images': True, 'mcp': True}


@router.get('/sessions')
def sessions(workspace_id: str | None = None):
    return [public(r) for r in reversed(d.all_records('session')) if workspace_id is None or r['workspace_id'] == workspace_id]


@router.post('/sessions', status_code=201)
def create_session(value: NewSession):
    if value.permission_mode == 'allow' and value.permission_confirmation != 'ALLOW':
        raise HTTPException(422, '请输入 ALLOW 确认自动批准模式')
    try:
        config = d.effective_workspace(value.workspace_id)
    except KeyError:
        raise HTTPException(404, '工作空间不存在')
    try:
        snapshot = m.selection(value.model_id, value.reasoning_effort if 'reasoning_effort' in value.model_fields_set else m.UNSET)
    except LookupError as exc:
        raise HTTPException(503, str(exc))
    except ValueError:
        raise HTTPException(422, '模型、思考强度或密钥配置无效')
    record = dict(id=uuid4().hex, workspace_id=value.workspace_id, config=config, model_snapshot=snapshot,
        model_id=snapshot['id'], reasoning_effort=snapshot['reasoning_effort'], status='idle', messages=[],
        output='', error=None, created_at=now(), updated_at=now(), event_seq=0,
        pending_tool_calls=[], human_input={}, permission_mode=value.permission_mode,
        pending_permission_mode=None, sandbox_mode='full-access')
    return public(d.put('session', record))


@router.get('/sessions/{session_id}')
def session(session_id: str):
    return public(find(session_id))


@router.get('/sessions/{session_id}/artifacts/{filename}')
def artifact(session_id: str, filename: str):
    record = find(session_id)
    root = d.DATA / 'artifacts' / record['workspace_id'] / session_id
    path = root / filename
    if path.parent != root or not path.is_file() or path.is_symlink():
        raise HTTPException(404, '文件不存在')
    return FileResponse(path, filename=filename)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=100000)
    images: list[dict[str, str]] = Field(default_factory=list, max_length=4)
    upload_ids: list[str] = Field(default_factory=list, max_length=10)


@router.post('/sessions/{session_id}/messages', status_code=202)
def message(session_id: str, value: Message):
    record = find(session_id)
    if len(set(value.upload_ids)) != len(value.upload_ids):
        raise HTTPException(422, '上传文件不能重复')
    uploads = upload_records(record['workspace_id'], value.upload_ids)
    for image in value.images:
        try:
            if image['type'] not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif') or len(base64.b64decode(image['data'], validate=True)) > 10 * 1024 * 1024:
                raise ValueError()
        except (KeyError, ValueError):
            raise HTTPException(422, '图片格式无效或超过 10MB')
    return schedule(session_id, 'message', {**value.model_dump(), 'attachments': [{k: item[k] for k in ('id', 'name', 'size', 'type', 'path')} for item in uploads]})


@router.post('/sessions/{session_id}/actions/{action}', status_code=202)
def action(session_id: str, action: str, value: dict):
    if action in ('approve', 'answer', 'resume', 'compact'):
        return schedule(session_id, action, value)
    rt = runtime(session_id)
    with rt.lock:
        record = find(session_id)
        if rt.runner is None or record['status'] != 'running':
            raise HTTPException(409, 'Agent 尚未运行')
        if action in ('cancel', 'pause'):
            getattr(rt.runner, 'request_' + action)()
        elif action in ('steer', 'follow_up'):
            text = value.get('text', '').strip()
            if not text:
                raise HTTPException(422, '指令不能为空')
            context=value.get('artifact')
            instruction=text
            if context is not None:
                if not isinstance(context,dict):raise HTTPException(422,'Artifact feedback context invalid')
                feature=context.get('feature')
                service=rt.ensure_artifact_services().get(feature)
                if service is None:raise HTTPException(404,'Artifact feature is unavailable')
                try:instruction=service.feedback(text,context)
                except ValueError as exc:raise HTTPException(409,str(exc))
            getattr(rt.runner, action)(instruction)
            rt.event({'type': action, 'content': text, 'document': context, 'artifact_context': context, 'status': 'accepted_pending_boundary'})
        else:
            raise HTTPException(404, '操作不存在')
    return {'accepted': True}


@router.put('/sessions/{session_id}/model')
def change_model(session_id: str, value: NewSession):
    rt = runtime(session_id)
    with rt.lock:
        record = find(session_id)
        try:
            selected = m.selection(value.model_id, value.reasoning_effort)
        except (LookupError, ValueError):
            raise HTTPException(422, '模型或思考强度配置无效')
        if rt.runner is not None:
            available = {p.id for p in rt.runner.list_models()}
            if selected['id'] not in available:
                raise HTTPException(409, '此模型在会话创建后新增，请新建会话使用')
            rt.runner.request_model_change(selected['id'], reasoning_effort=value.reasoning_effort or 'default')
            if record['status'] not in ACTIVE:
                rt.runner.apply_pending_config_if_idle()
        record.update(model_id=selected['id'], reasoning_effort=value.reasoning_effort)
        if rt.runner is None:
            record['model_snapshot'] = selected
        d.put('session', record)
        rt.event({'type': 'model_selected', 'model_id': selected['id'], 'reasoning_effort': value.reasoning_effort})
    return public(record)


class Permission(BaseModel):
    mode: str = Field(pattern='^(ask|allow)$')
    confirmation: str | None = None


@router.put('/sessions/{session_id}/permission')
def permission(session_id: str, value: Permission):
    rt = runtime(session_id)
    with rt.lock:
        record = find(session_id)
        if record['status'] in ('waiting_confirmation', 'waiting_human', 'paused', 'runtime_config_boundary'):
            raise HTTPException(409, '请先处理当前交互再切换权限模式')
        previous = record.get('pending_permission_mode') or record.get('permission_mode', 'ask')
        if value.mode == 'allow' and previous != 'allow' and value.confirmation != 'ALLOW':
            raise HTTPException(422, '请输入 ALLOW 确认自动批准模式')
        if rt.runner is None:
            if record['status'] in ACTIVE:
                raise HTTPException(409, '请在 Agent 初始化完成后切换权限模式')
            record.update(permission_mode=value.mode, pending_permission_mode=None)
            d.put('session', record)
            rt.event({'type': 'permission_selected', 'mode': value.mode})
            return public(record)
        try:
            rt.runner.request_permission_change(value.mode)
            if find(session_id)['status'] not in ACTIVE:
                rt.runner.apply_pending_config_if_idle()
        except ValueError:
            raise HTTPException(422, '当前运行环境不支持此权限模式')
        record = find(session_id)
        record['permission_mode'] = rt.runner.permission_mode().value
        pending = rt.runner.pending_permission_mode()
        record['pending_permission_mode'] = pending.value if pending else None
        d.put('session', record)
        rt.event({'type': 'permission_selected', 'mode': value.mode})
        return public(record)


@router.get('/sessions/{session_id}/journal')
def session_journal(session_id: str):
    record = find(session_id)
    prefix = f'{session_id}:'
    with d.db() as connection:
        rows = connection.execute(
            'SELECT data FROM records WHERE kind=? AND id>? AND id<? ORDER BY id',
            ('event', f'{prefix}{0:010d}', prefix + '~')).fetchall()
    events = [public_event(json.loads(row[0])) for row in rows]
    # Keep every reasoning/tool-output chunk available for full history replay.
    return {'events': events}


@router.get('/sessions/{session_id}/events')
async def events(session_id: str, request: Request, after: int = 0):
    find(session_id)
    async def stream():
        try:
            cursor = max(0, after, int(request.headers.get('last-event-id', '0') or 0))
        except ValueError:
            cursor = max(0, after)
        while not await request.is_disconnected():
            batch = await asyncio.to_thread(journal, session_id, cursor)
            for event in batch:
                cursor = event['seq']
                yield f'id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n'
            yield ': heartbeat\n\n'
            await asyncio.sleep(0.1)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.get('/workspaces/{workspace_id}/files')
def files(workspace_id: str):
    try:
        d.get('workspace', workspace_id)
    except KeyError:
        raise HTTPException(404, '工作空间不存在')
    root = d.DATA / 'workspaces' / workspace_id / 'files'
    if not root.exists():
        return []
    return [{'path': str(p.relative_to(root)), 'size': p.stat().st_size} for p in root.rglob('*') if p.is_file() and not p.is_symlink() and not internal_path(p.relative_to(root)) and p.resolve().is_relative_to(root.resolve())][:1000]


@router.get('/workspaces/{workspace_id}/files/{filename:path}')
def download(workspace_id: str, filename: str):
    root, path = workspace_file(workspace_id, filename)
    return FileResponse(path, filename=path.name)


def workspace_file(workspace_id, filename):
    try:
        d.get('workspace', workspace_id)
    except KeyError:
        raise HTTPException(404, '工作空间不存在')
    root = d.DATA / 'workspaces' / workspace_id / 'files'
    path = root / filename
    if internal_path(filename) or not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink():
        raise HTTPException(404, '文件不存在')
    return root, path
