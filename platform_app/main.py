import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import domain as d
from . import models as m
from . import runtime
from . import format_plugins

app = FastAPI(title='Deep Agent Platform')
POOL = ThreadPoolExecutor(max_workers=4)
d.init()
runtime.initialize()
app.include_router(runtime.router)
app.include_router(format_plugins.router)
class ArtifactHost:
    def session(self,session_id):return runtime.find(session_id)
    def service(self,session_id,feature):
        if feature not in [m['id'] for m in format_plugins.enabled()]:raise HTTPException(404,'Feature is uninstalled or disabled')
        value=runtime.runtime(session_id).ensure_artifact_services().get(feature)
        if value is None:raise HTTPException(503,'Feature dependencies unavailable')
        return value
    def emit(self,session_id,event):runtime.runtime(session_id).event(event)
artifact_host=ArtifactHost()
for feature in format_plugins.enabled():
    try:
        plugin=format_plugins.load_plugin(feature['id'])
        if hasattr(plugin,'create_router'):app.include_router(plugin.create_router(artifact_host))
    except (ImportError,LookupError):pass

STATIC = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC), name='static')
WEB = Path(__file__).parent / 'web'
if WEB.exists():
    app.mount('/assets', StaticFiles(directory=WEB / 'assets'), name='web-assets')
    app.mount('/fonts', StaticFiles(directory=WEB / 'fonts'), name='web-fonts')


@app.get('/favicon.svg')
def favicon():
    return FileResponse(WEB / 'favicon.svg')


def find(kind, key):
    try:
        return d.get(kind, key)
    except KeyError:
        raise HTTPException(404, '记录不存在')


@app.get('/')
def index():
    return FileResponse((WEB if (WEB / 'index.html').exists() else STATIC) / 'index.html')


@app.get('/api/status')
def status():
    return {'model_configured': any(p['configured'] for p in m.public()['profiles']), 'mode': 'single-user', 'core': 'deep-agent-cli@f190baa'}


class ModelConfig(BaseModel):
    llm: dict
    revision: int


@app.get('/api/models')
def models():
    return m.public()


@app.put('/api/models')
def update_models(value: ModelConfig):
    try:
        return m.save(value.llm, value.revision)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except Exception:
        raise HTTPException(422, '模型配置无效：请检查来源、默认模型、API 协议和参数；格式与 deep_agent_cli 的 llm 配置一致。')


@app.get('/api/workspaces')
def workspaces():
    return [d.effective_workspace(w['id']) for w in d.all_records('workspace')]


@app.get('/api/tool-catalog')
def tool_catalog():
    return [{'name': name, 'description': f'创建真实的 {name[7:].upper()} 文件并返回文件名。', 'schema': schema, 'enabled': True} for name, schema in d.TOOL_SCHEMAS.items()]


@app.put('/api/workspaces/{workspace_id}')
def update_workspace(workspace_id: str, value: dict):
    with d.LOCK:
        previous = find('workspace', workspace_id)
        if value.get('revision') != previous['revision']:
            raise HTTPException(409, '配置已更新，请刷新后重试')
        edits = {k: v for k, v in value.items() if k in ('name', 'description', 'system_prompt', 'rules', 'skills', 'tools', 'core_tools', 'mcp', 'bind_all', 'skill_bindings')}
        if workspace_id == 'everything' and edits.get('bind_all', previous.get('bind_all', True)):
            for field in ('skills', 'tools', 'core_tools', 'mcp', 'skill_bindings'):
                edits.pop(field, None)
        value = {**previous, **edits}
        try:
            d.validate_config(value)
        except Exception as exc:
            raise HTTPException(422, str(exc))
        value['revision'] += 1
        d.put('workspace', value)
        return d.effective_workspace(workspace_id)


class NewWorkspace(BaseModel):
    name: str = Field(min_length=1, max_length=50)


@app.post('/api/workspaces', status_code=201)
def create_workspace(value: NewWorkspace):
    return d.put('workspace', dict(id=uuid4().hex, name=value.name, description='你的专属工作空间', icon='◇', color='blue', system_prompt='你是一个可靠的智能助理。', rules=[], skills=[], tools=[], revision=1))


@app.get('/api/tasks')
def tasks(workspace_id: str | None = None):
    return [{k: v for k, v in t.items() if k not in ('config', 'model_snapshot')} for t in reversed(d.all_records('task')) if workspace_id is None or t['workspace_id'] == workspace_id]


@app.get('/api/tasks/{task_id}')
def task(task_id: str):
    value = find('task', task_id)
    root = d.DATA / 'artifacts' / value['workspace_id'] / value['id']
    value['artifacts'] = [p.name for p in root.iterdir() if p.is_file()] if root.exists() else []
    return {k: v for k, v in value.items() if k not in ('config', 'model_snapshot')}


class Prompt(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    model_id: str | None = None
    reasoning_effort: str | None = None


@app.post('/api/workspaces/{workspace_id}/tasks', status_code=202)
def create_task(workspace_id: str, value: Prompt):
    workspace = find('workspace', workspace_id)
    try:
        selected = m.selection(value.model_id, value.reasoning_effort if 'reasoning_effort' in value.model_fields_set else m.UNSET)
    except LookupError as exc:
        raise HTTPException(503, str(exc))
    except ValueError:
        raise HTTPException(422, '模型、思考强度或密钥环境变量无效，请检查模型配置。')
    pending = sum(t['status'] in ('running', 'queued') for t in d.all_records('task'))
    if pending >= 8:
        raise HTTPException(429, '当前任务已满，请稍后再试')
    record = dict(id=uuid4().hex, workspace_id=workspace_id, prompt=value.prompt, config=workspace, status='queued', output='', error=None, created_at=datetime.now(timezone.utc).isoformat(), revision=workspace['revision'])
    record.update(model_snapshot=selected, model_id=selected['id'], reasoning_effort=selected['reasoning_effort'])
    d.put('task', record)
    POOL.submit(run_task, record)
    return {k: v for k, v in record.items() if k not in ('config', 'model_snapshot')}


def run_task(record, model=None, raise_errors=False):
    runner = None
    try:
        from agent import AgentRunner
        from .agent_factory import prepare_agent
        from agent.config import Settings, SubAgentConfig, SandboxConfig
        from agent.llm import build_chat_model
        from dataclasses import replace
        record['status'] = 'running'
        d.put('task', record)
        adapter = d.WorkspaceAdapter(record['config'], record['id'])
        hidden = d.hidden_tools(record['config'])
        settings = Settings(trust_env=True, subagents=SubAgentConfig(enabled='task' not in hidden),
            sandbox=SandboxConfig(workspace=adapter.root, mode='full-access', protected_config_dir=d.DATA / 'configuration',
                protected_files=(d.DATA / 'platform.sqlite', d.DATA / 'model-config.key', d.DATA / 'runtime')),
            hidden_tools=hidden)
        # Compatibility tasks use the same native sandbox and delegation capabilities.
        if model is None:
            snapshot = record['model_snapshot']
            parsed = Settings.from_mapping({'llm':m.unseal(snapshot['encrypted'])})
            profile = parsed.get_profile(snapshot['id'])
            settings = replace(settings, llm_profiles=parsed.llm_profiles, llm_default=profile.id, request_timeout=parsed.request_timeout)
            model = build_chat_model(profile, reasoning_effort=snapshot['reasoning_effort'], trust_env=True, timeout=parsed.request_timeout)
        prepared = prepare_agent(settings=settings, model=model, instructions=adapter.instructions, skills=[], extra_tools=adapter.tools(), interrupt_on=format_plugins.approval_rules())
        runner = AgentRunner(prepared=prepared, settings=settings)
        result = runner.invoke(record['prompt'])
        record.update(status=result.status, output=result.output or '')
        if result.status != 'completed':
            record['error'] = '内核未完成此任务；请检查服务端日志与模型配置。'
    except Exception:
        if raise_errors:
            raise
        # Do not expose provider exceptions, which can contain credential-bearing URLs.
        record.update(status='failed', error='Agent 执行失败，请检查模型连接及服务端运行环境。')
    finally:
        if runner:
            runner.close()
        d.put('task', record)


@app.get('/api/tasks/{task_id}/artifacts/{filename}')
def artifact(task_id: str, filename: str):
    value = find('task', task_id)
    root = d.DATA / 'artifacts' / value['workspace_id'] / value['id']
    path = root / filename
    if path.parent != root or not path.is_file() or path.is_symlink():
        raise HTTPException(404, '文件不存在')
    return FileResponse(path, filename=filename)
