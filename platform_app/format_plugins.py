"""Optional format extensions. No format engine is imported by the Agent core.

Delete either feature directory, disable it, or logically uninstall it through
/api/features. Discovery skips absent modules; feature dependency errors disable
only that feature. Existing artifact files/history are never removed.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel
from . import domain as d

ROOT=Path(__file__).resolve().parents[1]
CACHE={}


def catalog():
    result=[]
    for path in sorted(ROOT.glob('*-ooxml-skill/feature.json')):
        try:
            meta=json.loads(path.read_text()); meta['directory']=path.parent
            if not (path.parent/meta['adapter']).is_file():continue
            result.append(meta)
        except (OSError,ValueError,KeyError):continue
    return result


def config(feature_id):
    try:return d.get('feature',feature_id)
    except KeyError:return dict(id=feature_id,installed=True,enabled=True)


def enabled():
    return [meta for meta in catalog() if config(meta['id']).get('installed') and config(meta['id']).get('enabled')]


def load_plugin(feature_id):
    meta=next((m for m in enabled() if m['id']==feature_id),None)
    if meta is None:raise LookupError('Feature is not installed/enabled: '+feature_id)
    path=meta['directory']/meta['adapter']
    key=(str(path),path.stat().st_mtime_ns)
    if key not in CACHE:
        spec=importlib.util.spec_from_file_location('platform_feature_'+feature_id,path); module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module; spec.loader.exec_module(module); CACHE[key]=module
    return CACHE[key]


def build_services(root,actor,emit,vision=False):
    result={}
    for meta in enabled():
        try:
            module=load_plugin(meta['id'])
            def publish(event,_meta=meta):
                identity=event.get(_meta['identity_key'])
                if identity:event={**event,'artifact_extension':dict(feature=_meta['id'],id=identity,state_key=_meta['state_key'])}
                emit(event)
            result[meta['id']]=module.create_service(root,publish,actor,vision)
        except (ImportError,LookupError):
            continue
    return result


def instructions():
    result=[]
    for meta in enabled():
        try:result.append(load_plugin(meta['id']).INSTRUCTIONS)
        except (ImportError,LookupError):continue
    return '\n\n'.join(result)


def snapshot_skills(destination):
    active=enabled()
    active_ids={meta['id'] for meta in active}
    # Uninstall/disable is explicit authorization to retract this extension's
    # previous snapshot, even when the conversation itself is retained.
    for manifest in destination.glob('*/feature.json'):
        try:
            identity=json.loads(manifest.read_text())['id']
            if identity not in active_ids:shutil.rmtree(manifest.parent)
        except (OSError,ValueError,KeyError):continue
    for meta in active:
        try:load_plugin(meta['id'])
        except (ImportError,LookupError):continue
        folder=destination/meta['skill_name']
        if not folder.exists():
            shutil.copytree(meta['directory'],folder,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','tests','platform_adapter.py'))
            shutil.copy2(Path(__file__).with_name('artifact_runtime.py'),folder/'scripts'/'artifact_runtime.py')

router=APIRouter(prefix='/api/features')


def status():
    result=[]
    for meta in catalog():
        value=config(meta['id']); error=None
        if value.get('installed') and value.get('enabled'):
            try:load_plugin(meta['id'])
            except (ImportError,LookupError) as exc:error=type(exc).__name__
        result.append(dict(id=meta['id'],name=meta['name'],installed=value['installed'],enabled=value['enabled'] and not error,error=error))
    return result


@router.get('')
def list_features():return status()


class FeatureConfig(BaseModel):
    enabled:bool


def change(feature_id,installed,active):
    if feature_id not in [m['id'] for m in catalog()]:raise HTTPException(404,'功能模块文件不存在')
    if any(r['status'] in ('running','queued','waiting_confirmation','waiting_human','paused') for kind in ('session','task') for r in d.all_records(kind)):
        raise HTTPException(409,'请先完成或取消当前会话，再卸载或切换扩展功能')
    d.put('feature',dict(id=feature_id,installed=installed,enabled=active))
    # Drop idle format services; model/tool changes rebuild at the next invocation.
    from . import runtime
    for rt in runtime.RUNTIMES.values():
        for service in rt.artifact_services.values():service.close()
        rt.artifact_services={}
        if rt.runner:
            rt.runner.close(); rt.runner.session_store.close(); rt.runner=None
    return status()


@router.put('/{feature_id}')
def configure_feature(feature_id:str,value:FeatureConfig):return change(feature_id,True,value.enabled)


@router.delete('/{feature_id}')
def uninstall_feature(feature_id:str):return change(feature_id,False,False)


def approval_rules():
    rules={}
    for meta in enabled():
        try:
            for name in getattr(load_plugin(meta['id']),'APPROVAL_TOOLS',()):rules[name]=True
        except (ImportError,LookupError):continue
    return rules
