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


def hidden_tools(workspace):
    return tuple(dict.fromkeys((*REMOVED_TOOLS, *(name for name, enabled in workspace.get('core_tools', {}).items() if not enabled))))


def schema(properties, required):
    return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1}
TOOL_SCHEMAS = {
    'create_xlsx': schema({'title': TEXT, 'rows': {'type': 'array', 'items': {'type': 'array', 'items': {'type': ['string', 'number', 'boolean', 'null']}}}}, ['title', 'rows']),
}


def defaults():
    return [
        {'id': 'office', 'name': 'Office', 'icon': '◈', 'description': '把想法变成出色的文档', 'color': 'orange', 'system_prompt': '你是 Office 工作空间的文档助理。使用当前空间工具创建真实的 Office 文件，回复中说明结果，不虚构下载地址。', 'rules': ['仅使用当前工作空间提供的能力。', '文档语言默认跟随用户。'], 'skills': [{'name': '办公文档', 'content': '演示文稿先确定叙事结构；文档使用清晰标题；表格保留结构化数据。'}], 'tools': [{'name': name, 'description': f'创建真实的 {name[7:].upper()} 文件并返回文件名。', 'schema': s, 'enabled': True} for name, s in TOOL_SCHEMAS.items()], 'revision': 1},
        {'id': 'research', 'name': 'Research', 'icon': '⌘', 'description': '理清问题，构建新的洞察', 'color': 'blue', 'system_prompt': '你是研究助理。根据用户提供的材料和已启用工具分析问题，核实信息并准确说明来源；不虚构引用或检索结果。', 'rules': ['明确区分事实、假设与建议。'], 'skills': [], 'tools': [], 'revision': 1},
        {'id': 'writing', 'name': 'Writing', 'icon': '✳', 'description': '让每一个想法找到表达', 'color': 'green', 'system_prompt': '你是写作助理，帮助用户构思、撰写和改进内容。', 'rules': ['保持用户的语气与意图。'], 'skills': [], 'tools': [], 'revision': 1},
        {'id': 'everything', 'name': 'Everything', 'icon': '✳', 'description': '全部工具与技能，一个工作区', 'color': 'blue', 'system_prompt': '你是一个通用智能助理，根据任务选择可用工具和技能，完成工作并验证结果。', 'rules': [], 'skills': [], 'tools': [], 'core_tools': {}, 'bind_all': True, 'revision': 1},
    ]


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
    if not all_records('workspace'):
        for value in defaults():
            put('workspace', value)
    if not any(w['id'] == 'everything' for w in all_records('workspace')):
        put('workspace', defaults()[-1])
    for workspace in all_records('workspace'):
        if any(name in workspace.get('core_tools', {}) for name in REMOVED_TOOLS):
            workspace['core_tools'] = {name: enabled for name, enabled in workspace['core_tools'].items() if name not in REMOVED_TOOLS}
            workspace['revision'] += 1
            put('workspace', workspace)
    for workspace in all_records('workspace'):
        if any(t['name'] in ('create_docx','create_pptx') for t in workspace.get('tools', [])):
            workspace['tools'] = [t for t in workspace['tools'] if t['name'] not in ('create_docx','create_pptx')]
            workspace['revision'] += 1
            put('workspace', workspace)
    research = get('workspace', 'research') if any(w['id'] == 'research' for w in all_records('workspace')) else None
    if research and research['revision'] == 1 and research['system_prompt'] == '你是研究助理。根据用户提供的材料分析问题。当前没有网络检索工具，不虚构引用或声称已经联网。':
        research['system_prompt'] = defaults()[1]['system_prompt']
        research['revision'] += 1
        put('workspace', research)
    for task in all_records('task'):
        if task['status'] in ('queued', 'running'):
            task.update(status='failed', error='服务已重启，请创建新任务重试。')
            put('task', task)


def native_skills():
    from agent.config import default_skills_dir
    root = default_skills_dir()
    result = []
    if root.is_dir():
        for path in sorted(root.glob('*/SKILL.md')):
            try:
                text = path.read_text(encoding='utf-8')
                front = yaml.safe_load(text.split('---', 2)[1]) if text.startswith('---') else {}
                front = front if isinstance(front, dict) else {}
                result.append({'id': path.parent.name, 'name': str(front.get('name') or path.parent.name), 'description': str(front.get('description') or ''), 'source': 'deep_agent_cli'})
            except (OSError, ValueError, yaml.YAMLError):
                continue
    return result


def effective_workspace(workspace_id):
    """Resolve Everything at session creation, keeping existing session snapshots stable."""
    value = get('workspace', workspace_id)
    auto = workspace_id == 'everything' and value.get('bind_all', True)
    bindings = value.get('skill_bindings', {})
    value['skill_bindings'] = bindings
    value['native_skills'] = [{**skill, 'enabled': auto or bindings.get(skill['id'], False)} for skill in native_skills()]
    if workspace_id != 'everything' or not value.get('bind_all', True):
        return value
    value['bind_all'] = True
    value['core_tools'] = {name: True for name in CORE_TOOLS}
    value['tools'] = [{'name': name, 'description': f'创建真实的 {name[7:].upper()} 文件并返回文件名。', 'schema': copy.deepcopy(s), 'enabled': True} for name, s in TOOL_SCHEMAS.items()]
    sources = [w for w in all_records('workspace') if w['id'] != 'everything']
    sources.append(get('workspace', 'everything'))
    skills, seen = [], set()
    mcp = {'enabled': False, 'servers': {}}
    for index, source in enumerate(sources):
        for skill in source.get('skills', []):
            identity = (skill['name'], skill['content'])
            if identity not in seen:
                skills.append({**copy.deepcopy(skill), 'enabled': True, 'source_workspace': source['name']})
                seen.add(identity)
        catalog = source.get('mcp', {})
        if catalog.get('enabled'):
            for name, server in catalog.get('servers', {}).items():
                if server.get('enabled', True):
                    mcp['servers'][f'ws{index}-{name}'[:32]] = copy.deepcopy(server)
                    mcp['enabled'] = True
    value.update(skills=skills, mcp=mcp)
    return value


def validate_config(value):
    bindings = value.get('skill_bindings', {})
    if not isinstance(bindings, dict) or any(not isinstance(k, str) or not k or '/' in k or '\\' in k or k in ('.', '..') or type(v) is not bool for k, v in bindings.items()):
        raise ValueError('Skill 绑定必须使用已安装技能的标识和布尔启用状态')
    if any(k not in {s['id'] for s in native_skills()} for k in bindings):
        raise ValueError('Skill 未安装，请刷新技能列表后重试')
    if type(value.get('bind_all', False)) is not bool:
        raise ValueError('bind_all 必须是布尔值')
    if not isinstance(value.get('name'), str) or not value['name'].strip():
        raise ValueError('工作空间名称不能为空')
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
    from agent.mcp.config import parse_server
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


class WorkspaceAdapter:
    def __init__(self, workspace, task_id):
        self.config = copy.deepcopy(workspace)
        self.config['tools'] = [t for t in self.config.get('tools', []) if t['name'] not in ('create_docx','create_pptx')]
        self.root = DATA / 'artifacts' / workspace['id'] / task_id
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def instructions(self):
        from .format_plugins import instructions
        return instructions() + '\n\n' + '\n\n'.join([self.config['system_prompt'], '\n'.join(self.config['rules']), *[f"技能：{s['name']}\n{s['content']}" for s in self.config['skills']]])

    def execute(self, name, arguments):
        tool = next((t for t in self.config['tools'] if t['name'] == name and t['enabled']), None)
        if tool is None:
            raise ValueError('当前工作空间未启用该工具')
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

    def tools(self, include_artifacts=True):
        from langchain_core.tools import StructuredTool
        tools = []
        for tool in self.config['tools']:
            if tool['enabled']:
                def invoke(_name=tool['name'], **kwargs):
                    return self.execute(_name, kwargs)
                tools.append(StructuredTool.from_function(func=invoke, name=tool['name'], description=tool['description'], args_schema=tool['schema']))
        if include_artifacts:
            from .format_plugins import build_services
            tools.extend(tool for service in build_services(self.root,self.config['id'],self.root.name,lambda event:None).values() for tool in service.tools())
        return tools
