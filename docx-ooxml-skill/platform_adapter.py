"""Platform adapter for the same deterministic engine used by the bundled skill."""
import importlib.util
import hashlib
import os
from pathlib import Path
from platform_app.artifact_runtime import validate_embedded_resources
import threading
from concurrent.futures import ThreadPoolExecutor

SCRIPTS = Path(__file__).resolve().parent / 'scripts'
spec = importlib.util.spec_from_file_location('atomic_docx', SCRIPTS / 'atomic_docx.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
DocumentStore = engine.DocumentStore
PREVIEWS = ThreadPoolExecutor(max_workers=2, thread_name_prefix='docx-preview')


class Documents:
    def __init__(self, root, emit=None, live_preview=False, temporary_root=None):
        self.root = Path(root).resolve()
        self.temporary_root = temporary_root
        self.emit = emit or (lambda event: None)
        self.lock = threading.RLock()
        self.cursors = {}
        self.bound_paths = {}
        self.live_preview = live_preview
        self.preview_jobs = {}
        self.preview_active = set()
        self.closed = False

    def store(self, path):
        return DocumentStore(path, temporary_root=self.temporary_root)

    def bind_state(self,record):
        self.bound_paths = {identity: item['path'] for identity, item in record.get('documents', {}).items()}
        self.cursors=dict(record.get('artifact_cursors',{}).get('docx',{}))
        for path in self.paths():
            relative=path.relative_to(self.root).as_posix()
            if relative not in self.cursors and not any(doc.get('path')==relative for doc in record.get('documents',{}).values()):
                if (path.parent/'.docx-state'/path.name/'history.sqlite').exists():
                    events=self.store(path).events()
                    self.cursors[relative]=events[-1]['store_seq'] if events else 0

    def document_path(self, identity):
        if identity not in self.bound_paths: raise ValueError('Unknown document')
        path = self.root / self.bound_paths[identity]
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Invalid document path')
        return path

    def feedback(self,text,context):
        identity=context.get('document_id') or context.get('id')
        revision=context.get('revision')
        if type(revision)is not int:raise ValueError('Invalid artifact revision')
        state=self.store(self.document_path(identity)).status(committed=True)
        if revision>state['revision']:raise ValueError('Invalid artifact revision')
        return text+f"\n[Document feedback: document_id={identity}, viewed_revision={revision}, current_revision={state['revision']}. Re-read status, repair affected blocks and checkpoint before continuing.]"

    def schedule_preview(self, path):
        if not self.live_preview or self.closed:
            return
        with self.lock:
            if str(path) in self.preview_active:
                return
            self.preview_active.add(str(path))
            self.preview_jobs[str(path)] = PREVIEWS.submit(self.render_latest, path)

    def render_latest(self, path):
        store = self.store(path)
        failed = False
        try:
            while not self.closed:
                try:
                    state = store.status(committed=True)
                    if state.get('preview_revision', -1) >= state['revision']:
                        break
                    validate_embedded_resources(path)
                    store.preview(state['revision'])
                    self.sync()
                except Exception as exc:
                    failed = True
                    store.preview_failed(type(exc).__name__ + ': Layout preview unavailable; check LibreOffice and fonts')
                    self.sync()
                    break
        finally:
            with self.lock:
                self.preview_active.discard(str(path))
                # Close the final-commit race: a request arriving while this job
                # was returning must still get a renderer, even without another tool.
                if not failed and not self.closed:
                    state = store.status(committed=True)
                    if state.get('preview_revision', -1) < state['revision']:
                        self.schedule_preview(path)

    def close(self):
        self.closed = True
        for job in list(self.preview_jobs.values()):
            job.result(timeout=130)

    def paths(self):
        paths = []
        for base, directories, filenames in os.walk(self.root, followlinks=False):
            directories[:] = [name for name in directories if name != '.docx-state' and not (Path(base)/name).is_symlink()]
            for name in filenames:
                path = Path(base)/name
                if path.suffix.lower() == '.docx' and not path.is_symlink() and path.resolve().is_relative_to(self.root.resolve()):
                    paths.append(path)
        return paths

    def sync(self):
        """Replay every durable store event, including intermediate CLI commits."""
        with self.lock:
            if not self.root.exists():
                return
            for path in self.paths():
                if path.is_symlink() or not (path.parent / '.docx-state' / path.name / 'history.sqlite').exists():
                    continue
                relative = path.relative_to(self.root).as_posix()
                store = self.store(path)
                for event in store.events(self.cursors.get(relative, 0)):
                    seq = event['store_seq']
                    event.update(path=relative)
                    identity = event.get('document_id')
                    if identity in self.bound_paths and self.bound_paths[identity] != relative:
                        self.cursors[relative] = seq
                        continue
                    if identity: self.bound_paths[identity] = relative
                    self.emit(event)
                    self.cursors[relative] = seq

    def detect_external(self):
        with self.lock:
            for path in self.paths():
                if path.is_symlink():
                    continue
                try:
                    relative = path.relative_to(self.root).as_posix()
                    identity = path.stem if len(Path(relative).parts) == 1 and engine.ID.fullmatch(path.stem) else 'external-' + hashlib.sha256(relative.encode()).hexdigest()[:20]
                    validate_embedded_resources(path)
                    result = self.store(path).track_external(identity)
                    if result:
                        self.schedule_preview(path)
                except Exception as exc:
                    # Native tools can leave incomplete ZIPs. Preserve snapshots,
                    # surface the failure, and retry at the next tool boundary.
                    context = {}
                    try:
                        committed = self.store(path).status(committed=True)
                        context = dict(document_id=committed['document_id'], revision=committed['revision'], external_conflict=True)
                    except (ValueError, OSError):
                        pass
                    self.emit(dict(type='document_error', path=path.relative_to(self.root).as_posix(),
                        status='invalid_external_write', error=type(exc).__name__, **context))
            self.sync()


def create_service(root, emit, temporary_root=None):
    return Documents(root, emit, live_preview=True, temporary_root=temporary_root)


def create_router(host):
    import importlib.util
    path=Path(__file__).with_name('routes.py')
    spec=importlib.util.spec_from_file_location('docx_routes',path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.create_router(host)
