import copy
import json
import os
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator

DATA = Path(os.environ.get('PLATFORM_DATA_DIR', '.platform')).resolve()
LOCK = threading.RLock()


def schema(properties, required):
    return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1}
TOOL_SCHEMAS = {
    'create_docx': schema({'title': TEXT, 'paragraphs': {'type': 'array', 'items': TEXT}}, ['title', 'paragraphs']),
    'create_pptx': schema({'title': TEXT, 'slides': {'type': 'array', 'items': schema({'title': TEXT, 'body': TEXT}, ['title', 'body'])}}, ['title', 'slides']),
    'create_xlsx': schema({'title': TEXT, 'rows': {'type': 'array', 'items': {'type': 'array', 'items': {'type': ['string', 'number', 'boolean', 'null']}}}}, ['title', 'rows']),
}


def defaults():
    return [
        {'id': 'office', 'name': 'Office', 'icon': '◈', 'description': '把想法变成出色的文档', 'color': 'orange', 'system_prompt': '你是 Office 工作空间的文档助理。使用当前空间工具创建真实的 Office 文件，回复中说明结果，不虚构下载地址。', 'rules': ['仅使用当前工作空间提供的能力。', '文档语言默认跟随用户。'], 'skills': [{'name': '办公文档', 'content': '演示文稿先确定叙事结构；文档使用清晰标题；表格保留结构化数据。'}], 'tools': [{'name': name, 'description': f'创建真实的 {name[7:].upper()} 文件并返回文件名。', 'schema': s, 'enabled': True} for name, s in TOOL_SCHEMAS.items()], 'revision': 1},
        {'id': 'research', 'name': 'Research', 'icon': '⌘', 'description': '理清问题，构建新的洞察', 'color': 'blue', 'system_prompt': '你是研究助理。根据用户提供的材料分析问题。当前没有网络检索工具，不虚构引用或声称已经联网。', 'rules': ['明确区分事实、假设与建议。'], 'skills': [], 'tools': [], 'revision': 1},
        {'id': 'writing', 'name': 'Writing', 'icon': '✳', 'description': '让每一个想法找到表达', 'color': 'green', 'system_prompt': '你是写作助理，帮助用户构思、撰写和改进内容。', 'rules': ['保持用户的语气与意图。'], 'skills': [], 'tools': [], 'revision': 1},
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
    for task in all_records('task'):
        if task['status'] in ('queued', 'running'):
            task.update(status='failed', error='服务已重启，请创建新任务重试。')
            put('task', task)


def validate_config(value):
    if not isinstance(value.get('name'), str) or not value['name'].strip():
        raise ValueError('工作空间名称不能为空')
    if not isinstance(value.get('system_prompt'), str):
        raise ValueError('system_prompt 必须是文本')
    if not isinstance(value.get('rules'), list) or not all(isinstance(x, str) for x in value['rules']):
        raise ValueError('rules 必须是文本列表')
    if not isinstance(value.get('skills'), list) or not all(isinstance(x, dict) and isinstance(x.get('name'), str) and isinstance(x.get('content'), str) for x in value['skills']):
        raise ValueError('skills 必须包含 name 和 content')
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
        self.root = DATA / 'artifacts' / workspace['id'] / task_id
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def instructions(self):
        return '\n\n'.join([self.config['system_prompt'], '\n'.join(self.config['rules']), *[f"技能：{s['name']}\n{s['content']}" for s in self.config['skills']]])

    def execute(self, name, arguments):
        tool = next((t for t in self.config['tools'] if t['name'] == name and t['enabled']), None)
        if tool is None:
            raise ValueError('当前工作空间未启用该工具')
        Draft202012Validator(tool['schema']).validate(arguments)
        Draft202012Validator(TOOL_SCHEMAS[name]).validate(arguments)
        extension = name[7:]
        path = self.root / f'{uuid4().hex[:12]}.{extension}'
        if extension == 'docx':
            from docx import Document
            doc = Document()
            doc.add_heading(arguments['title'], 0)
            for paragraph in arguments['paragraphs']:
                doc.add_paragraph(paragraph)
            doc.save(path)
        elif extension == 'pptx':
            from pptx import Presentation
            doc = Presentation()
            slide = doc.slides.add_slide(doc.slide_layouts[0])
            slide.shapes.title.text = arguments['title']
            for item in arguments['slides']:
                slide = doc.slides.add_slide(doc.slide_layouts[1])
                slide.shapes.title.text = item['title']
                slide.placeholders[1].text = item['body']
            doc.save(path)
        else:
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
