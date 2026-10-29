"""One-time imports for data written by older platform releases."""
import copy
import json
import os
import tempfile
import shutil
from pathlib import Path


def _copy_missing(source, destination):
    if source.is_symlink():
        return
    if destination.is_symlink():
        raise ValueError('Import destination cannot be a symlink')
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy_missing(child, destination / child.name)
    elif source.is_file() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.import-', delete=False) as stream:
            temporary = Path(stream.name)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def remove_workspaces():
    from . import domain as d
    with d.LOCK:
        old = d.all_records('workspace')
        if not old:
            return
        settings = next((copy.deepcopy(w) for w in old if w['id'] == 'everything'), d.defaults())
        auto = settings.get('bind_all', True)
        allowed = set(d.defaults())
        settings = {k: v for k, v in settings.items() if k in allowed}
        settings = {**d.defaults(), **settings, 'id': 'global'}
        if auto:
            settings['core_tools'] = {}
            settings['skill_bindings'] = {}
            settings['tools'] = d.defaults()['tools']
            skills, servers = [], {}
            for index, source in enumerate(old):
                for skill in source.get('skills', []):
                    if not any((s['name'], s['content']) == (skill['name'], skill['content']) for s in skills):
                        skills.append({**skill, 'enabled': True})
                if source.get('mcp', {}).get('enabled'):
                    for name, server in source['mcp'].get('servers', {}).items():
                        if server.get('enabled', True):
                            key = name if name not in servers else f"import{index}-{name}"[:32]
                            servers[key] = server
            settings.update(skills=skills, mcp=dict(enabled=bool(servers), servers=servers))
        settings['tools'] = [t for t in settings.get('tools', []) if t['name'] in d.TOOL_SCHEMAS]
        settings['core_tools'] = {k: v for k, v in settings.get('core_tools', {}).items() if k in d.CORE_TOOLS}
        # Each legacy tree has a fixed import location. Retrying a startup import
        # never overwrites files or mixes histories of identically named reports.
        prefixes = {}
        for source in old:
            identity = source['id']
            if Path(identity).name != identity or identity in ('.', '..'):
                continue
            prefixes[identity] = f'imported/{identity}/'
            destination = d.files_root() / 'imported' / identity
            if not destination.resolve().is_relative_to(d.files_root().resolve()):
                raise ValueError('Invalid import destination')
            _copy_missing(d.DATA / 'workspaces' / identity / 'files', destination)
        rows = []
        owners = {}
        for kind in ('session', 'task', 'upload'):
            for record in d.all_records(kind):
                identity = record.pop('workspace_id', None)
                prefix = prefixes.get(identity, '')
                if kind in ('session', 'task'):
                    owners[record['id']] = prefix
                    if identity:
                        _copy_missing(d.DATA / 'artifacts' / identity / record['id'], d.artifact_root(record['id']))
                    if 'config' in record:
                        record['config'] = {k: v for k, v in record['config'].items() if k in allowed or k == 'native_skills'}
                        record['config']['id'] = 'global'
                        record['config']['tools'] = [t for t in record['config'].get('tools', []) if t['name'] in d.TOOL_SCHEMAS]
                    for key in ('documents', 'presentations'):
                        for item in record.get(key, {}).values():
                            item.pop('workspace_id', None)
                            if item.get('path'): item['path'] = prefix + item['path']
                    for key in ('document_cursors',):
                        if key in record:record[key] = {prefix + k: v for k, v in record[key].items()}
                    for feature, cursors in record.get('artifact_cursors', {}).items():
                        record['artifact_cursors'][feature] = {prefix + k: v for k, v in cursors.items()}
                    for message in record.get('messages', []):
                        for item in message.get('attachments', []):
                            if item.get('path'):item['path'] = prefix + item['path']
                elif record.get('path'):
                    record['path'] = prefix + record['path']
                rows.append((kind, record))
        for grant in d.all_records('artifact_grant'):
            path = Path(grant.get('path', ''))
            for identity, prefix in prefixes.items():
                source = d.DATA / 'workspaces' / identity / 'files'
                if path.is_relative_to(source):
                    grant['path'] = str(d.files_root() / prefix / path.relative_to(source))
                    break
            rows.append(('artifact_grant', grant))
        for event in d.all_records('event'):
            event.pop('workspace_id', None)
            prefix = owners.get(event.get('session_id'), '')
            if event.get('artifact_extension') and event.get('path'):
                event['path'] = prefix + event['path']
            for item in event.get('files', []) + event.get('attachments', []):
                if item.get('path'):item['path'] = prefix + item['path']
            rows.append(('event', event))
        # Commit all reference changes and the migration marker together. Legacy
        # directories remain untouched as a backup, never used by new sessions.
        with d.db() as connection:
            for kind, record in rows:
                connection.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, record['id'], json.dumps(record, ensure_ascii=False)))
            if not d.all_records('settings'):
                connection.execute('INSERT INTO records VALUES (?,?,?)', ('settings', 'global', json.dumps(settings, ensure_ascii=False)))
            connection.execute("DELETE FROM records WHERE kind='workspace'")
