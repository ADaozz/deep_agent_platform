"""CLI-compatible grouped LLM configuration; credentials never leave read APIs."""
import copy
import json
import os
import re
from pathlib import Path
import yaml
from cryptography.fernet import Fernet
from platform_app.agent_core.config import Settings
from . import domain as d

MASK = '__UNCHANGED_SECRET__'
ENV = re.compile(r'^\$\{[A-Za-z_][A-Za-z0-9_]*\}$')
UNSET = object()


def cipher():
    d.DATA.mkdir(parents=True, exist_ok=True)
    path = d.DATA / 'model-config.key'
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def seal(config):
    return cipher().encrypt(json.dumps(config).encode()).decode()


def unseal(value):
    return json.loads(cipher().decrypt(value.encode()))


def current():
    try:
        row = d.get('model_config', 'llm')
        return unseal(row['encrypted']), row['revision']
    except KeyError:
        path = Path(os.environ.get('PLATFORM_CONFIG', d.DATA / 'config.yaml')).expanduser()
        if path.is_file():
            config = yaml.safe_load(path.read_text(encoding='utf-8'))
            if not isinstance(config, dict) or not isinstance(config.get('llm'), dict):
                raise ValueError('配置文件必须包含 llm 配置')
            validate(config['llm'])
            return config['llm'], 0
        if os.environ.get('PLATFORM_CONFIG'):
            raise FileNotFoundError('PLATFORM_CONFIG 指向的配置文件不存在')
        if all(os.environ.get(n) for n in ('PLATFORM_MODEL', 'PLATFORM_API_KEY', 'PLATFORM_BASE_URL')):
            return {'default':'environment/model','models':{'environment':{'base_url':os.environ['PLATFORM_BASE_URL'],'api_key':'${PLATFORM_API_KEY}','api':'chat_completions','models':{'model':{'model':os.environ['PLATFORM_MODEL']}}}}}, 0
        return {'default':'','timeout':60,'models':{}}, 0


def transform(value, fn):
    if isinstance(value, dict):
        return {k: fn(v) if k == 'api_key' else transform(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [transform(v, fn) for v in value]
    return value


def validate(config):
    # References may be configured before a server environment variable is supplied.
    structural = transform(config, lambda v: 'sk-validation' if isinstance(v, str) and ENV.fullmatch(v) else v)
    return Settings.from_mapping({'llm':structural})


def restore(value, previous):
    if isinstance(value, dict):
        return {k: (previous[k] if k == 'api_key' and v == MASK and k in previous else restore(v, previous.get(k, {}))) for k, v in value.items()}
    if value == MASK:
        raise ValueError('密钥占位符没有对应的已保存密钥')
    return value


def public():
    config, revision = current()
    profiles = []
    if config['models']:
        parsed = validate(config)
        for profile in parsed.list_profiles():
            try:
                real = Settings.from_mapping({'llm':config}).get_profile(profile.id)
                ready = bool(real.api_key)
            except ValueError:
                ready = False
            profiles.append({'id':profile.id, 'model':profile.model, 'api':profile.api, 'reasoning_efforts':list(profile.reasoning_efforts), 'default_reasoning_effort':profile.reasoning_efforts[0] if profile.reasoning_efforts else None, 'configured':ready})
    default_effort = next((p['default_reasoning_effort'] for p in profiles if p['id'] == config['default']), None)
    return {'llm':transform(config, lambda v: v if isinstance(v,str) and ENV.fullmatch(v) else MASK), 'revision':revision, 'profiles':profiles, 'default_reasoning_effort':default_effort}


def save(config, revision):
    with d.LOCK:
        previous, old_revision = current()
        if old_revision != revision:
            raise RuntimeError('模型配置已更新，请重新打开后编辑')
        config = restore(copy.deepcopy(config), previous)
        validate(config)
        d.put('model_config', {'id':'llm','revision':revision+1,'encrypted':seal(config)})
    return public()


def selection(model_id=None, effort=UNSET):
    config, _ = current()
    if not config['models']:
        raise LookupError('请在网页的模型配置中新增模型来源和模型')
    parsed = validate(config)
    profile = parsed.get_profile(model_id or config['default'])
    if effort is UNSET:
        effort = profile.reasoning_efforts[0] if profile.reasoning_efforts else None
    if effort is not None and effort not in profile.reasoning_efforts:
        raise ValueError('该模型不支持所选思考强度')
    # Fail before queueing if a referenced credential is absent.
    Settings.from_mapping({'llm':config})
    return {'encrypted':seal(config), 'id':profile.id, 'reasoning_effort':effort}
