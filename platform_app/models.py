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
# Personal sources are exposed under this prefix so they never shadow global sources.
PERSONAL_PREFIX = 'my-'
EMPTY = {'default': '', 'models': {}}


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


def profiles_of(config):
    profiles = []
    if config['models']:
        parsed = validate(config)
        for profile in parsed.list_profiles():
            try:
                real = Settings.from_mapping({'llm':config}).get_profile(profile.id)
                ready = bool(real.api_key)
            except ValueError:
                ready = False
            profiles.append({'id':profile.id, 'model':profile.model, 'api':profile.api, 'context_window':profile.context_window, 'reasoning_efforts':list(profile.reasoning_efforts), 'default_reasoning_effort':profile.reasoning_efforts[0] if profile.reasoning_efforts else None, 'configured':ready})
    return profiles


def public():
    config, revision = current()
    profiles = profiles_of(config)
    default_effort = next((p['default_reasoning_effort'] for p in profiles if p['id'] == config['default']), None)
    return {'llm':transform(config, lambda v: v if isinstance(v,str) and ENV.fullmatch(v) else MASK), 'revision':revision, 'profiles':profiles, 'default_reasoning_effort':default_effort}


def personal_current(owner):
    try:
        row = d.get('user_model_config', owner)
        return unseal(row['encrypted']), row['revision']
    except KeyError:
        return copy.deepcopy(EMPTY), 0


def check_personal(config):
    """Personal sources may not borrow server credentials or reach non-HTTP endpoints."""
    if not isinstance(config, dict) or set(config) - {'default', 'models'} or not isinstance(config.get('models', {}), dict):
        raise ValueError('个人模型配置只支持 default 和 models 两个字段')

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'api_key' and isinstance(item, str) and '${' in item:
                    raise ValueError('个人模型不能引用服务器环境变量，请直接填写 API Key')
                if key == 'base_url' and not (isinstance(item, str) and re.match(r'^https?://', item.strip())):
                    raise ValueError('base_url 必须以 http:// 或 https:// 开头')
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(config)


def public_personal(owner):
    config, revision = personal_current(owner)
    return {'llm':transform(config, lambda v: MASK if v else v), 'revision':revision, 'profiles':profiles_of(config), 'prefix':PERSONAL_PREFIX}


def save_personal(owner, config, revision):
    with d.LOCK:
        previous, old_revision = personal_current(owner)
        if old_revision != revision:
            raise RuntimeError('个人模型配置已更新，请重新打开后编辑')
        config = restore(copy.deepcopy(config), previous)
        check_personal(config)
        if config.get('models'):
            validate(config)
            shared = current()[0].get('models') or {}
            clash = sorted(source for source in config['models'] if PERSONAL_PREFIX + source in shared)
            if clash:
                raise ValueError(f'个人模型来源与全局来源重名：{", ".join(clash)}')
        else:
            config = copy.deepcopy(EMPTY)
        d.put('user_model_config', {'id':owner, 'revision':revision + 1, 'encrypted':seal(config)})
    return public_personal(owner)


def merged(owner=None):
    """Global models plus the owner's personal sources; personal default wins."""
    config, _ = current()
    if owner is None:
        return config, set()
    personal, _ = personal_current(owner)
    if not personal['models']:
        return config, set()
    result = copy.deepcopy(config)
    result['models'] = dict(result.get('models') or {})
    added = set()
    for source, group in personal['models'].items():
        key = PERSONAL_PREFIX + source
        if key not in result['models']:
            result['models'][key] = copy.deepcopy(group)
            added.add(key)
    preferred = PERSONAL_PREFIX + personal['default'] if personal.get('default') else ''
    if preferred.split('/', 1)[0] in added:
        result['default'] = preferred
    elif not result.get('default') and added:
        source = sorted(added)[0]
        result['default'] = f'{source}/{next(iter(result["models"][source]["models"]))}'
    return result, added


def options(owner=None):
    config, added = merged(owner)
    profiles = profiles_of(config)
    for profile in profiles:
        profile['scope'] = 'personal' if profile['id'].split('/', 1)[0] in added else 'global'
    default_effort = next((p['default_reasoning_effort'] for p in profiles if p['id'] == config['default']), None)
    return {'default':config['default'], 'profiles':profiles, 'default_reasoning_effort':default_effort}


def save(config, revision):
    with d.LOCK:
        previous, old_revision = current()
        if old_revision != revision:
            raise RuntimeError('模型配置已更新，请重新打开后编辑')
        config = restore(copy.deepcopy(config), previous)
        validate(config)
        d.put('model_config', {'id':'llm','revision':revision+1,'encrypted':seal(config)})
    return public()


def selection(model_id=None, effort=UNSET, owner=None):
    config, _ = merged(owner)
    if not config['models']:
        raise LookupError('请在设置的模型配置中新增模型来源和模型')
    parsed = validate(config)
    profile = parsed.get_profile(model_id or config['default'])
    if effort is UNSET:
        effort = profile.reasoning_efforts[0] if profile.reasoning_efforts else None
    if effort is not None and effort not in profile.reasoning_efforts:
        raise ValueError('该模型不支持所选思考强度')
    # Fail before queueing if a referenced credential is absent.
    Settings.from_mapping({'llm':config})
    return {'encrypted':seal(config), 'id':profile.id, 'reasoning_effort':effort}
