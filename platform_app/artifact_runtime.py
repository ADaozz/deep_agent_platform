"""Format-neutral artifact transactions, snapshots, event journal and recovery.

The legacy blob column is named docx for on-disk compatibility; it stores bytes
of any supported artifact. This module never plans or executes Agent work.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\w.-]{1,80}',value) or value in ('.','..'):
        raise ValueError('Invalid stable identifier')
    return value


def publish(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)



class ArtifactStore:
    def __init__(self, path, suffix, state_dir):
        self.path=Path(path).absolute()
        if self.path.suffix.lower()!=suffix or self.path.is_symlink():
            raise ValueError('Invalid artifact path')
        self.directory=self.path.parent/state_dir/self.path.name

    def _publish(self,path,data):
        publish(path,data)

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            c = sqlite3.connect(self.directory / 'history.sqlite', timeout=30)
            c.row_factory = sqlite3.Row
            try:
                c.execute('PRAGMA synchronous=FULL')
                c.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, data TEXT)')
                c.execute('CREATE TABLE IF NOT EXISTS revisions (revision INTEGER PRIMARY KEY, operation_id TEXT UNIQUE, request TEXT, result TEXT, docx BLOB)')
                c.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT)')
                row = c.execute('SELECT data FROM state WHERE id=1').fetchone()
                state = json.loads(row[0]) if row else None
                if state and state.get('pending_publish'):
                    data = c.execute('SELECT docx FROM revisions WHERE revision=?', (state['revision'],)).fetchone()[0]
                    self._publish(self.path, data)
                    state['pending_publish'] = False
                    self.save_state(c, state)
                    c.commit()
                yield c, state
            finally:
                c.close()

    def save_state(self, c, state):
        c.execute('INSERT OR REPLACE INTO state VALUES (1,?)', (json.dumps(state, ensure_ascii=False),))

    def persist_revision(self,c,state,data,result,request,operation_id,kind):
        c.execute('INSERT INTO revisions VALUES (?,?,?,?,?)',(state['revision'],operation_id,json.dumps(request,sort_keys=True),json.dumps(result),data))
        c.execute('INSERT INTO events(data) VALUES (?)',(json.dumps(dict(type=kind,**result)),))
        self.save_state(c,state)
        c.commit()
        self._publish(self.path,data)
        state['pending_publish']=False
        self.save_state(c,state)
        c.commit()

    def restore(self, revision, base_revision, operation_id):
        identifier(operation_id)
        request = dict(restore=revision, base_revision=base_revision)
        with self.locked() as (c, state):
            previous = c.execute('SELECT request,result FROM revisions WHERE operation_id=?', (operation_id,)).fetchone()
            if previous:
                if json.loads(previous[0]) != request:
                    raise ValueError('Operation ID reused')
                return json.loads(previous[1])
            if state is None or type(base_revision) is not int or state['revision'] != base_revision:
                raise ValueError('Revision conflict')
            row = c.execute('SELECT docx FROM revisions WHERE revision=?', (revision,)).fetchone()
            if row is None:
                raise ValueError('Unknown historical revision')
            state=self.prepare_restore(c,state,revision)
            return self.commit(c, state, row[0], operation_id, request, ['*'])

    def events(self, after=0):
        with self.locked() as (c, state):
            return [dict(json.loads(r['data']), store_seq=r['seq']) for r in c.execute('SELECT * FROM events WHERE seq>? ORDER BY seq', (after,))]

    def snapshot(self, revision):
        with self.locked() as (c, state):
            row = c.execute('SELECT docx FROM revisions WHERE revision=?', (revision,)).fetchone()
            if row is None:
                raise ValueError('Unknown revision')
            return row[0]


    def prepare_restore(self,c,state,revision):
        return state
