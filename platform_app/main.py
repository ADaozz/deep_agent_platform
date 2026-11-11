from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import domain as d
from . import models as m
from . import runtime
from . import format_plugins
from . import itam_bridge

app = FastAPI(title='Deep Agent Platform')
itam_bridge.install(app)
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

WEB = Path(__file__).parent / 'web'
if WEB.exists():
    app.mount('/assets', StaticFiles(directory=WEB / 'assets'), name='web-assets')
    app.mount('/fonts', StaticFiles(directory=WEB / 'fonts'), name='web-fonts')


@app.get('/favicon.svg')
def favicon():
    return FileResponse(WEB / 'favicon.svg')


@app.get('/')
def index():
    if not (WEB / 'index.html').is_file():
        raise HTTPException(503, '前端资源未就绪')
    return FileResponse(WEB / 'index.html')


@app.get('/api/status')
def status():
    return {'model_configured': any(p['configured'] for p in m.options(itam_bridge.owner())['profiles']), 'mode': 'itam-multi-user' if itam_bridge.is_enabled() else 'single-user', 'core': 'platform_app.agent_core'}


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
        raise HTTPException(422, '模型配置无效：请检查来源、默认模型、API 协议和 llm 参数。')


@app.get('/api/models/options')
def model_options():
    return m.options(itam_bridge.owner())


def personal_owner():
    owner = itam_bridge.owner()
    if owner is None:
        raise HTTPException(404, '个人模型配置仅在 ITAM 多用户模式下可用')
    return owner


@app.get('/api/models/personal')
def personal_models():
    return m.public_personal(personal_owner())


@app.put('/api/models/personal')
def update_personal_models(value: ModelConfig):
    owner = personal_owner()
    try:
        return m.save_personal(owner, value.llm, value.revision)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f'个人模型配置无效：{exc}')


@app.get('/api/settings')
def settings():
    return d.effective_config()


@app.get('/api/tool-catalog')
def tool_catalog():
    return [{'name': name, 'description': f'创建真实的 {name[7:].upper()} 文件并返回文件名。', 'schema': schema, 'enabled': True} for name, schema in d.TOOL_SCHEMAS.items()]


@app.put('/api/settings')
def update_settings(value: dict):
    with d.LOCK:
        previous = d.get('settings', 'global')
        if value.get('revision') != previous['revision']:
            raise HTTPException(409, '配置已更新，请刷新后重试')
        edits = {k: v for k, v in value.items() if k in ('system_prompt', 'rules', 'skills', 'tools', 'core_tools', 'mcp', 'skill_bindings')}
        value = {**previous, **edits}
        try:
            d.validate_config(value)
        except Exception as exc:
            raise HTTPException(422, str(exc))
        value['revision'] += 1
        d.put('settings', value)
        return d.effective_config()
