"""Platform adapter for the same deterministic engine used by the bundled skill."""
import importlib.util
import hashlib
import os
from pathlib import Path
from platform_app.artifact_runtime import validate_embedded_resources
import threading
import time
from concurrent.futures import ThreadPoolExecutor

SCRIPTS = Path(__file__).resolve().parent / 'scripts'
spec = importlib.util.spec_from_file_location('atomic_docx', SCRIPTS / 'atomic_docx.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
DocumentStore = engine.DocumentStore
PREVIEWS = ThreadPoolExecutor(max_workers=2, thread_name_prefix='docx-preview')
INSTRUCTIONS = '''DOCX tasks MUST use docx_init then docx_apply_one, one heading, paragraph, bounded table or image per call. Never create full DOCX via Python, write_file, execute or create_docx. Never batch atomic calls in one execute. Call docx_status to find stable block IDs and current revision. Every 3 mutations the engine requires docx_checkpoint. A failed checkpoint blocks new content: repair current pages, then checkpoint again. Strict mode also waits for visual review of completed pages. Normal mode can continue after automatic checks but visual review stays pending. Inspect rendered page images with actual vision capability, otherwise request human visual review; never certify unseen pages. At section end and before completion call docx_checkpoint(final=true), review every page, then docx_finalize. Receive steering at each tool boundary; repair affected existing blocks and re-check before adding content. A restore is a new revision, never a revision rewind. Do not announce completion before finalize passes. The browser auto-opens the document after init. PPTX/XLSX tools are unaffected.'''


def safe_path(root, document_id):
    engine.identifier(document_id)
    root = Path(root).resolve()
    path = root / (document_id + '.docx')
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Invalid document path')
    return path


class Documents:
    def __init__(self, root, emit=None, vision=False, live_preview=False):
        self.root = Path(root).resolve()
        self.emit = emit or (lambda event: None)
        self.vision = vision
        self.lock = threading.RLock()
        self.cursors = {}
        self.bound_paths = {}
        self.timings = []
        self.live_preview = live_preview
        self.preview_jobs = {}
        self.preview_active = set()
        self.closed = False

    def bind_state(self,record):
        self.bound_paths = {identity: item['path'] for identity, item in record.get('documents', {}).items()}
        self.cursors=dict(record.get('artifact_cursors',{}).get('docx',{}))
        for path in self.paths():
            relative=path.relative_to(self.root).as_posix()
            if relative not in self.cursors and not any(doc.get('path')==relative for doc in record.get('documents',{}).values()):
                if (path.parent/'.docx-state'/path.name/'history.sqlite').exists():
                    events=DocumentStore(path).events()
                    self.cursors[relative]=events[-1]['store_seq'] if events else 0

    def document_path(self, identity):
        path = self.root / self.bound_paths[identity] if identity in self.bound_paths else safe_path(self.root, identity)
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Invalid document path')
        return path

    def feedback(self,text,context):
        identity=context.get('document_id') or context.get('id')
        revision=context.get('revision')
        if type(revision)is not int:raise ValueError('Invalid artifact revision')
        state=DocumentStore(self.document_path(identity)).status(committed=True)
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
        store = DocumentStore(path)
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
                store = DocumentStore(path)
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
                    result = DocumentStore(path).track_external(identity)
                    if result:
                        self.schedule_preview(path)
                except Exception as exc:
                    # Native tools can leave incomplete ZIPs. Preserve snapshots,
                    # surface the failure, and retry at the next tool boundary.
                    context = {}
                    try:
                        committed = DocumentStore(path).status(committed=True)
                        context = dict(document_id=committed['document_id'], revision=committed['revision'], external_conflict=True)
                    except (ValueError, OSError):
                        pass
                    self.emit(dict(type='document_error', path=path.relative_to(self.root).as_posix(),
                        status='invalid_external_write', error=type(exc).__name__, **context))
            self.sync()

    def execute(self, action, arguments):
        result = self._execute(action, arguments)
        if isinstance(result, dict) and 'path' in result:
            result = {**result, 'path': self.document_path(arguments['document_id']).relative_to(self.root).as_posix()}
        return result

    def _execute(self, action, arguments):
        start = time.perf_counter()
        document_id = arguments['document_id']
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.document_path(document_id)
        store = DocumentStore(path)
        if path.is_file(): validate_embedded_resources(path)
        try:
            if action == 'init':
                template = arguments.get('template')
                if template:
                    template = (self.root / template).resolve()
                    if not template.is_relative_to(self.root.resolve()) or not template.is_file():
                        raise ValueError('Template must be a library file')
                    validate_embedded_resources(template)
                return store.init(document_id, template, arguments.get('mode', 'normal'))
            if action == 'apply_one':
                return store.apply_one({k: arguments[k] for k in ('document_id', 'operation_id', 'base_revision', 'operation')}, self.root)
            if action == 'status':
                return store.status()
            if action == 'checkpoint':
                return store.checkpoint(arguments.get('final', False))
            if action == 'review':
                if not self.vision:
                    raise ValueError('Text-only model cannot perform visual review; request human inspection')
                return store.visual_review(arguments['revision'], arguments['page'], arguments['passed'], 'vision_model', arguments['notes'])
            if action == 'restore':
                return store.restore(arguments['revision'], arguments['base_revision'], arguments['operation_id'])
            if action == 'finalize':
                return store.finalize(arguments['base_revision'])
            raise ValueError('Unknown document action')
        finally:
            self.sync()
            if action in ('init', 'apply_one', 'restore'):
                self.schedule_preview(path)
            self.timings.append(dict(action=action, seconds=time.perf_counter() - start))

    def tools(self):
        from langchain_core.tools import StructuredTool
        def schema(props, required=()):
            return dict(type='object', properties=dict(document_id=dict(type='string', pattern=r'^[\w.-]{1,80}$'), **props),
                required=['document_id', *required], additionalProperties=False)
        string = dict(type='string')
        number = dict(type='integer', minimum=0)
        definitions = {
            'init': (schema(dict(template=string, mode=dict(type='string', enum=['normal','strict']))), 'Initialize a blank DOCX immediately; optional template path relative to the file library. Default normal mode.'),
            'apply_one': (schema(dict(operation_id=string, base_revision=number, operation=dict(type='object')), ['operation_id','base_revision','operation']), 'Commit exactly one bounded atomic DOCX operation. Supported type: append_paragraph(text,style), append_table(rows), insert_image(path,width_cm,alt), insert_block(xml; one paragraph), replace_text(old,new), delete_block, set_paragraph_style(style), set_paragraph_format, set_table_format(keep_together), set_page. Every operation requires stable block_id; insertion may specify after_block_id. Idempotent operation_id; optimistic base_revision. Cannot add content past a checkpoint gate.'),
            'status': (schema({}), 'Inspect latest revision, block IDs, page checks, gate and history.'),
            'checkpoint': (schema(dict(final=dict(type='boolean'))), 'Render with LibreOffice and check page layout. final=true marks the last page complete. Failures block new content; visual review remains pending.'),
            'review': (schema(dict(revision=number, page=dict(type='integer', minimum=1), passed=dict(type='boolean'), notes=string), ['revision','page','passed','notes']), 'Record actual page screenshot inspection by a vision model. Text-only models cannot use this; ask human instead.'),
            'restore': (schema(dict(revision=number, base_revision=number, operation_id=string), ['revision','base_revision','operation_id']), 'Restore a historical snapshot as a NEW revision. Requires current base revision.'),
            'finalize': (schema(dict(base_revision=number), ['base_revision']), 'Finalize only after exact-version final checkpoint and visual review of every page.'),
        }
        result = []
        for name, (args, description) in definitions.items():
            def invoke(_action=name, **kwargs):
                try:
                    return self.execute(_action, kwargs)
                except (ValueError, OSError, KeyError) as exc:
                    return dict(status='rejected', error=str(exc))
            result.append(StructuredTool.from_function(func=invoke, name='docx_' + name, description=description, args_schema=args))
        return result


def create_service(root, emit, actor, vision=False):
    return Documents(root, emit, vision=vision, live_preview=True)


def create_router(host):
    import importlib.util
    path=Path(__file__).with_name('routes.py')
    spec=importlib.util.spec_from_file_location('docx_routes',path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.create_router(host,DocumentStore)
