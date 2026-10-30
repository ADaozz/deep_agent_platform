import copy
import json
import os
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4
import yaml

from jsonschema import Draft202012Validator

DATA = Path(os.environ.get('PLATFORM_DATA_DIR', '.platform')).resolve()
LOCK = threading.RLock()
CORE_TOOLS = ('ls', 'read_file', 'write_file', 'edit_file', 'delete', 'glob', 'grep', 'execute', 'task', 'read_subagent_log', 'request_human_input', 'web_search', 'web_fetch')
REMOVED_TOOLS = ('write_todos',)


def hidden_tools(config):
    return tuple(dict.fromkeys((*REMOVED_TOOLS, *(name for name, enabled in config.get('core_tools', {}).items() if not enabled))))


def schema(properties, required):
    return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1}
TOOL_SCHEMAS = {
    'create_xlsx': schema({'title': TEXT, 'rows': {'type': 'array', 'items': {'type': 'array', 'items': {'type': ['string', 'number', 'boolean', 'null']}}}}, ['title', 'rows']),
}


def defaults():
    return dict(id='global', system_prompt='你是一个通用智能助理，根据任务选择可用工具和技能，完成工作并验证结果。',
        rules=[], skills=[], core_tools={}, skill_bindings={}, mcp={'enabled': False, 'servers': {}},
        tools=[{'name': name, 'description': f'创建真实的 {name[7:].upper()} 文件并返回文件名。', 'schema': copy.deepcopy(value), 'enabled': True} for name, value in TOOL_SCHEMAS.items()], revision=1)


def files_root(thread_id):
    from .workspace import resolve
    return resolve(thread_id).files_root


def artifact_root(record_id):
    from .workspace import resolve
    return resolve(record_id).artifacts_root


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / 'platform.sqlite')
    connection.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))')
    return connection


def put(kind, value):
    with LOCK, db() as c:
        c.execute('INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data', (kind, value['id'], json.dumps(value, ensure_ascii=False)))
    return value


def get(kind, key):
    with db() as c:
        row = c.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
    if row is None:
        raise KeyError(key)
    return json.loads(row[0])


def all_records(kind):
    with db() as c:
        return [json.loads(r[0]) for r in c.execute('SELECT data FROM records WHERE kind=? ORDER BY rowid', (kind,))]


def init():
    from .workspace import PlatformPolicy
    PlatformPolicy.load()
    with LOCK:
        try:
            layout = get('platform', 'storage-layout')
        except KeyError:
            if any(all_records(kind) for kind in ('workspace', 'session', 'task', 'event', 'upload', 'thread_workspace')) or any((DATA / name).exists() for name in ('files','runtime','artifacts')):
                raise RuntimeError('旧数据布局不受支持：请使用新的 PLATFORM_DATA_DIR；旧数据不会自动迁移或删除。')
            layout = put('platform', dict(id='storage-layout', version='thread-v1'))
        if layout.get('version') != 'thread-v1':
            raise RuntimeError('不支持此数据布局：请使用新的 PLATFORM_DATA_DIR。')
        if not all_records('settings'):
            put('settings', defaults())


def native_skills():
    from platform_app.agent_core.config import default_skills_dir
    from .format_plugins import bundled_skills
    root = default_skills_dir()
    result = bundled_skills()
    bundled_ids = {skill['id'] for skill in result}
    if root.is_dir():
        for path in sorted(root.glob('*/SKILL.md')):
            if path.parent.name in bundled_ids:
                continue
            try:
                text = path.read_text(encoding='utf-8')
                front = yaml.safe_load(text.split('---', 2)[1]) if text.startswith('---') else {}
                front = front if isinstance(front, dict) else {}
                result.append({'id': path.parent.name, 'name': str(front.get('name') or path.parent.name), 'description': str(front.get('description') or ''), 'source': 'platform'})
            except (OSError, ValueError, yaml.YAMLError):
                continue
    return result


def effective_config():
    value = get('settings', 'global')
    bindings = value.get('skill_bindings', {})
    value['native_skills'] = [{**skill, 'enabled': skill.get('available', True) and bindings.get(skill['id'], True)} for skill in native_skills()]
    return value


def validate_config(value):
    bindings = value.get('skill_bindings', {})
    if not isinstance(bindings, dict) or any(not isinstance(k, str) or not k or '/' in k or '\\' in k or k in ('.', '..') or type(v) is not bool for k, v in bindings.items()):
        raise ValueError('Skill 绑定必须使用已安装技能的标识和布尔启用状态')
    if any(k not in {s['id'] for s in native_skills()} for k in bindings):
        raise ValueError('Skill 未安装，请刷新技能列表后重试')
    if not isinstance(value.get('system_prompt'), str):
        raise ValueError('system_prompt 必须是文本')
    if not isinstance(value.get('rules'), list) or not all(isinstance(x, str) for x in value['rules']):
        raise ValueError('rules 必须是文本列表')
    if not isinstance(value.get('skills'), list) or not all(isinstance(x, dict) and isinstance(x.get('name'), str) and isinstance(x.get('content'), str) for x in value['skills']):
        raise ValueError('skills 必须包含 name 和 content')
    core = value.get('core_tools', {})
    if not isinstance(core, dict) or any(k not in CORE_TOOLS or type(v) is not bool for k, v in core.items()):
        raise ValueError('内核工具配置无效')
    for skill in value['skills']:
        if not skill['name'].strip() or type(skill.get('enabled', True)) is not bool or not isinstance(skill.get('description', ''), str):
            raise ValueError('Skill 需要非空名称、文本描述和有效的启用状态')
    mcp = value.get('mcp', {'enabled': False, 'servers': {}})
    if not isinstance(mcp, dict) or type(mcp.get('enabled', False)) is not bool or not isinstance(mcp.get('servers', {}), dict):
        raise ValueError('MCP 配置需要 enabled 和 servers')
    from platform_app.agent_core.mcp.config import parse_server
    for name, config in mcp.get('servers', {}).items():
        parse_server(name, config)
    seen = set()
    for tool in value.get('tools', []):
        name = tool.get('name')
        if name not in TOOL_SCHEMAS or name in seen:
            raise ValueError('工具必须绑定唯一的已注册处理器')
        seen.add(name)
        if not isinstance(tool.get('description'), str) or not tool['description'].strip() or type(tool.get('enabled')) is not bool:
            raise ValueError('工具需要 description 和 enabled')
        s = tool.get('schema', {})
        Draft202012Validator.check_schema(s)
        # Descriptions and constraints are editable; handler arguments stay compatible.
        original = TOOL_SCHEMAS[name]
        if s.get('type') != 'object' or s.get('properties', {}).keys() != original['properties'].keys() or set(s.get('required', [])) != set(original['required']) or s.get('additionalProperties') is not False:
            raise ValueError('schema 必须保留处理器参数与 required；可修改描述和约束')


class ToolAdapter:
    def __init__(self, config, thread_id):
        self.config = copy.deepcopy(config)
        self.root = files_root(thread_id)
        self.root.mkdir(parents=True, exist_ok=True)

    def execute(self, name, arguments):
        tool = next((t for t in self.config['tools'] if t['name'] == name and t['enabled']), None)
        if tool is None:
            raise ValueError('尚未启用该工具')
        Draft202012Validator(tool['schema']).validate(arguments)
        Draft202012Validator(TOOL_SCHEMAS[name]).validate(arguments)
        extension = name[7:]
        path = self.root / f'{uuid4().hex[:12]}.{extension}'
        if extension == 'xlsx':
            from openpyxl import Workbook
            doc = Workbook()
            for row in arguments['rows']:
                # Treat untrusted strings as text, never spreadsheet formulas.
                doc.active.append(["'" + x if isinstance(x, str) and x.startswith(('=', '+', '-', '@')) else x for x in row])
            doc.save(path)
        return {'filename': path.name, 'type': extension}

    def tools(self):
        from langchain_core.tools import StructuredTool
        tools = []
        for tool in self.config['tools']:
            if tool['enabled']:
                def invoke(_name=tool['name'], **kwargs):
                    return self.execute(_name, kwargs)
                tools.append(StructuredTool.from_function(func=invoke, name=tool['name'], description=tool['description'], args_schema=tool['schema']))
        return tools
