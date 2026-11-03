"""Transactional, bounded DOCX edits. SQLite snapshots are the recovery authority.

The visible DOCX is published atomically after a durable commit. pending_publish
repairs a process crash between the database commit and os.replace. flock covers
CLI and HTTP processes; revisions never decrease, including restore.
"""
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
import zipfile

_spec = importlib.util.spec_from_file_location('ooxml_engine', Path(__file__).with_name('office_docx.py'))
o = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o)
_shared_spec = importlib.util.spec_from_file_location('artifact_runtime', Path(__file__).with_name('artifact_runtime.py'))
_shared = importlib.util.module_from_spec(_shared_spec)
_shared_spec.loader.exec_module(_shared)
ID = re.compile(r'^[\w.-]{1,80}$')
BLOCK_NS = 'urn:deep-agent:docx:blocks:v1'
BLOCK_ID = '{%s}id' % BLOCK_NS


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or value in ('.', '..'):
        raise ValueError('Invalid stable identifier')
    return value


def block_signatures(data):
    parts = o.read_parts(io.BytesIO(data))
    root = o.parse_xml(parts[o.MAIN])
    return {block.get(BLOCK_ID): hashlib.sha256(o.xml_bytes(block)).hexdigest()
        for block in root.iter() if block.get(BLOCK_ID)}


def packed(parts):
    result = o.validate_parts(parts)
    if not result['ok']:
        raise ValueError('OOXML validation failed: ' + str(result['errors']))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return stream.getvalue()


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


def mark_blocks(parts):
    root = o.parse_xml(parts[o.MAIN])
    mc = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
    if 'dab' not in root.nsmap:
        replacement = o.etree.Element(root.tag, nsmap={**root.nsmap, 'dab': BLOCK_NS, 'mc': mc})
        replacement.attrib.update(root.attrib)
        replacement.text = root.text
        replacement.extend(list(root))
        root = replacement
    ignorable = root.get('{%s}Ignorable' % mc, '').split()
    if 'dab' not in ignorable:
        root.set('{%s}Ignorable' % mc, ' '.join([*ignorable, 'dab']))
    ids = [b.get(BLOCK_ID) for b in root.iter() if b.get(BLOCK_ID)]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate stable block IDs')
    used = set(ids)
    # Existing template paragraphs, table cells, tables and image paragraphs get
    # deterministic IDs once; future edits preserve these attributes. No index API.
    for i, block in enumerate(root.iter()):
        if block.tag in (o.q(o.W, 'p'), o.q(o.W, 'tbl')) and not block.get(BLOCK_ID):
            name = 'template-%04d' % i
            while name in used:
                name += '-x'
            block.set(BLOCK_ID, name)
            used.add(name)
    parts[o.MAIN] = o.xml_bytes(root)
    return parts


class DocumentStore(_shared.ArtifactStore):
    def __init__(self, path, temporary_root=None):
        super().__init__(path, '.docx', '.docx-state', temporary_root)

    def _publish(self, path, data):
        publish(path, data)

    def commit(self, c, state, data, operation_id, request, changed, kind='document_revision', extra=None, publish=True):
        revision = state['revision'] + 1
        digest = hashlib.sha256(data).hexdigest()
        previous_notes = state.get('content_notes') or {}
        previous_signatures = state.get('block_signatures') or {}
        signatures = block_signatures(data)
        notes = {key: value for key, value in previous_notes.items() if previous_signatures.get(key) == signatures.get(key)}
        result = dict(status='committed', document_id=state['document_id'], revision=revision,
            sha256=digest, operation_id=operation_id, changed_blocks=changed, needs_preview=True,
            path=self.path.name, preview_ready=False, committed_at=time.time())
        if extra:
            result.update(extra)
        state.update(revision=revision, sha256=digest, pending_publish=bool(publish), finalized=False,
            pages=[], preview_ready=False, check=None, block_signatures=signatures, content_notes=notes)
        self.persist_revision(c, state, data, result, request, operation_id, kind, publish=publish)
        return result

    def init(self, document_id, template=None, style_id=None):
        identifier(document_id)
        with self.locked() as (c, state):
            if state:
                if state['document_id'] != document_id:
                    raise ValueError('Document identity mismatch')
                return state
            if self.path.exists():
                raise ValueError('Document exists; use track_external or a different output path')
            if style_id and template:
                raise ValueError('A new styled document does not start from a DOCX template')
            if style_id:
                parts = _document_build().blank(_style_library().parameters(style_id), style_id)
            else:
                parts = o.read_parts(template) if template else o.default_package()
            parts = mark_blocks(parts)
            manifest = _template_manifest(parts, template) if template else None
            data = packed(parts)
            state = dict(document_id=document_id, revision=-1, pages=[], template_manifest=manifest,
                style_id=style_id, content_notes={}, block_signatures={})
            self.commit(c, state, data, '__init__', {'template': str(template) if template else None, 'style_id': style_id}, [], 'document_initialized')
            return state

    def assert_current(self, state, base_revision=None):
        if state is None:
            raise ValueError('Document not initialized')
        if base_revision is not None and (type(base_revision) is not int or state['revision'] != base_revision):
            raise ValueError('Revision conflict; inspect status and rebase')
        if not self.path.exists() or o.sha(self.path) != state['sha256']:
            raise ValueError('External modification detected; track before editing')

    def status(self, committed=False):
        with self.locked() as (c, state):
            if committed:
                if state is None:
                    raise ValueError('Document not initialized')
                row = c.execute('SELECT docx FROM revisions WHERE revision=?', (state['revision'],)).fetchone()
                parts = o.read_parts(io.BytesIO(row[0]))
            else:
                self.assert_current(state)
                parts = o.read_parts(self.path)
            root = o.parse_xml(parts[o.MAIN])
            result = dict(state)
            if committed:
                try:
                    result['external_conflict'] = o.sha(self.path) != state['sha256']
                except OSError:
                    result['external_conflict'] = True
            result['blocks'] = [dict(block_id=b.get(BLOCK_ID), type=o.etree.QName(b).localname, text=o.ptext(b)[:500])
                for b in root.iter() if b.get(BLOCK_ID)]
            result['styles'] = [node.get(o.q(o.W, 'styleId')) for node in o.parse_xml(parts.get('word/styles.xml', ('<w:styles xmlns:w="'+o.W+'"/>').encode())).iter(o.q(o.W, 'style'))]
            result['history'] = [json.loads(r[0]) for r in c.execute('SELECT result FROM revisions ORDER BY revision')]
            return result

    def apply_one(self, request, asset_root=None):
        identifier(request['operation_id'])
        with self.locked() as (c, state):
            if state is None or request['document_id'] != state['document_id']:
                raise ValueError('Document identity mismatch')
            previous = c.execute('SELECT request,result FROM revisions WHERE operation_id=?', (request['operation_id'],)).fetchone()
            if previous:
                if json.loads(previous[0]) != request:
                    raise ValueError('Operation ID reused with different payload')
                return json.loads(previous[1])
            self.assert_current(state, request['base_revision'])
            op = request['operation']
            parts = o.read_parts(self.path)
            try:
                parts, changed, extra = mutate(parts, op, Path(asset_root or self.path.parent))
            except (o.etree.XMLSyntaxError, KeyError, TypeError) as exc:
                raise ValueError('Invalid atomic operation: ' + str(exc)) from exc
            return self.commit(c, state, packed(parts), request['operation_id'], request, changed, extra=extra)

    def track_external(self, document_id=None):
        """Snapshot a complete external write without publishing over its writer."""
        with self.locked() as (c, state):
            if not self.path.is_file() or self.path.is_symlink():
                return None
            data = self.path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if state is not None and state.get('sha256') == digest:
                return None
            parts = o.read_parts(io.BytesIO(data))
            if not o.validate_parts(parts)['ok']:
                raise ValueError('External DOCX is invalid')
            _shared.validate_embedded_resources(io.BytesIO(data))
            if hashlib.sha256(self.path.read_bytes()).hexdigest() != digest:
                return None
            initial = state is None
            if initial:
                state = dict(document_id=identifier(document_id or self.path.stem), revision=-1, pages=[], content_notes={}, block_signatures={})
            operation = '__external__' if initial else 'external-%d-%s' % (state['revision'] + 1, digest[:12])
            return self.commit(c, state, data, operation, {'external': True}, ['*'],
                'document_initialized' if initial else 'document_revision', extra={'external': True}, publish=False)

    def render_snapshot(self, revision):
        """One renderer per immutable revision, shared by live preview and QA."""
        data = self.snapshot(revision)
        digest = hashlib.sha256(data).hexdigest()
        output = self.render_directory(revision)
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'render.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest_file = output / 'render.json'
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())
                if manifest.get('sha256') == digest and (output / 'preview.pdf').is_file() and all((output / image).is_file() for image in manifest.get('images', [])):
                    return output, manifest
            source = output / self.path.name
            publish(source, data)
            manifest = o.run_render(source, output)
            if manifest is None:  # deterministic renderer used by storage tests
                import pymupdf as fitz
                with fitz.open(output / 'preview.pdf') as pdf:
                    manifest = dict(sha256=digest, page_count=len(pdf), images=[f'page-{i+1:03d}.png' for i in range(len(pdf))])
                publish(manifest_file, json.dumps(manifest).encode())
            return output, manifest

    def preview_failed(self, error):
        with self.locked() as (c, state):
            if state is None:
                return
            state['preview_error'] = error
            self.save_state(c, state)
            event = dict(type='document_preview_error', document_id=state['document_id'], revision=state['revision'],
                path=self.path.name, preview_error=error)
            c.execute('INSERT INTO events(data) VALUES (?)', (json.dumps(event),))
            c.commit()
            return event

    def preview(self, revision):
        output, manifest = self.render_snapshot(revision)
        with self.locked() as (c, state):
            if state is None or revision <= state.get('preview_revision', -1):
                return None
            state.update(preview_revision=revision, preview_pages=manifest['page_count'], preview_error=None, preview_ready=True)
            self.save_state(c, state)
            event = dict(type='document_preview', document_id=state['document_id'], revision=state['revision'],
                preview_revision=revision, preview_pages=manifest['page_count'], path=self.path.name, preview_ready=True)
            c.execute('INSERT INTO events(data) VALUES (?)', (json.dumps(event),))
            c.commit()
            return event

    def retain_recent_history(self, limit=2):
        """Keep two file snapshots. Operation ids and check summaries stay for retries."""
        with self.locked() as (c, state):
            if not state:
                return []
            kept = [row[0] for row in c.execute('SELECT revision FROM revisions WHERE docx IS NOT NULL ORDER BY revision DESC LIMIT ?', (limit,))]
            if not kept:
                return []
            c.execute('UPDATE revisions SET docx=NULL WHERE revision<? AND docx IS NOT NULL', (min(kept),))
            if state.get('preview_revision') not in kept:
                state['preview_revision'] = max(kept) if state.get('preview_revision', -1) >= 0 else state.get('preview_revision', -1)
            self.save_state(c, state)
            c.commit()
        self.prune_render_cache(kept)
        return kept

    def prune_render_cache(self, kept):
        import shutil
        if not self.render_root.exists():
            return
        for directory in self.render_root.glob('render-*'):
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                revision = int(directory.name.removeprefix('render-'))
            except ValueError:
                continue
            if revision in kept:
                continue
            try:
                with (directory / 'render.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    shutil.rmtree(directory)
            except (BlockingIOError, FileNotFoundError):
                continue

    def check_doc(self, content_notes=None):
        review = _page_review()
        current = self.status()
        revision = current['revision']
        notes = content_notes if isinstance(content_notes, dict) else {}
        try:
            output, _manifest = self.render_snapshot(revision)
            source = output / self.path.name
            parts = o.read_parts(source)
            design = _style_library().read_design(parts)
            expected = None
            if design and design.get('margins'):
                expected = _document_build().twips(design['margins'])
            pages = review.review_pages(source, output, o, expected_margins=expected, render=False)
            failure = None
        except (OSError, ValueError, RuntimeError, ImportError, TimeoutError) as exc:
            pages, failure, output = [], type(exc).__name__ + ': ' + str(exc)[:300], None
        targets = repair_targets(pages)
        automatic = not failure and bool(pages) and all(page['checks']['automatic_passed'] for page in pages) and not targets
        with self.locked() as (c, state):
            self.assert_current(state, revision)
            known = set(state.get('block_signatures') or {})
            state['content_notes'] = {key: value for key, value in {**(state.get('content_notes') or {}), **notes}.items() if key in known}
            aesthetic = dict(status='pending' if automatic else 'unavailable', reason=None if automatic else 'automatic_checks_failed')
            state.update(pages=pages, render_revision=revision, render_error=failure,
                check=dict(revision=revision, automatic_passed=automatic, repair_targets=targets, aesthetic_review=aesthetic))
            if not failure:
                state.update(preview_revision=revision, preview_pages=len(pages), preview_error=None, preview_ready=True)
            self.save_state(c, state)
            event = dict(type='document_review', document_id=state['document_id'], revision=revision, path=self.path.name,
                pages=pages, automatic_passed=automatic, repair_targets=targets, render_error=failure, aesthetic_review=aesthetic,
                **({} if failure else dict(preview_revision=revision, preview_pages=len(pages), preview_ready=True)))
            c.execute('INSERT INTO events(data) VALUES (?)', (json.dumps(event),))
            c.commit()
            return event

    def record_aesthetic(self, revision, payload):
        """Store a host visual suggestion. This never changes the DOCX bytes."""
        with self.locked() as (c, state):
            check = (state or {}).get('check') or {}
            if not state or check.get('revision') != revision:
                return dict(applied=False)
            check['aesthetic_review'] = payload
            state['check'] = check
            self.save_state(c, state)
            event = dict(type='document_aesthetic', document_id=state['document_id'], revision=revision, path=self.path.name,
                aesthetic_review=payload, automatic_passed=check.get('automatic_passed'), repair_targets=check.get('repair_targets') or [])
            c.execute('INSERT INTO events(data) VALUES (?)', (json.dumps(event),))
            c.commit()
            return dict(applied=True, **event)

    def finalize(self, base_revision):
        with self.locked() as (c, state):
            self.assert_current(state, base_revision)
            check = state.get('check') or {}
            if check.get('revision') != base_revision or not check.get('automatic_passed'):
                raise ValueError('check-doc must pass for this revision before finalize')
            state['finalized'] = True
            self.save_state(c, state)
            aesthetic = (check.get('aesthetic_review') or {}).get('status', 'pending')
            event = dict(type='document_finalized', document_id=state['document_id'], revision=base_revision,
                path=self.path.name, finalized=True, aesthetic_status=aesthetic, sha256=state['sha256'])
            c.execute('INSERT INTO events(data) VALUES (?)', (json.dumps(event),))
            c.commit()
            return event


def mutate(parts, op, asset_root):
    if not isinstance(op, dict) or len(json.dumps(op, ensure_ascii=False)) > 24000:
        raise ValueError('Operation exceeds bounded payload')
    parts = mark_blocks(parts)
    kind = op.get('type')
    if kind in ('block.clone', 'section.clone', 'slot.fill', 'part.replace_text'):
        updated, changed, extra = _template_blocks().apply(parts, op, o, BLOCK_ID)
        return mark_blocks(updated), changed, extra
    if kind == 'document.recolor':
        return _template_styles().recolor(parts, op, o)
    root = o.parse_xml(parts[o.MAIN])
    body = root.find(o.q(o.W, 'body'))
    block_id = identifier(op.get('block_id', 'page-layout'))
    matches = root.xpath('//*[@b:id=$id]', namespaces={'b': BLOCK_NS}, id=block_id)
    target = matches[0] if len(matches) == 1 else None
    adding = kind in ('append_paragraph', 'append_table', 'insert_image', 'insert_block')
    if adding:
        if matches:
            raise ValueError('Duplicate block_id')
        if kind == 'append_paragraph':
            text = op['text']
            if not isinstance(text, str) or len(text) > 3000 or '\n' in text or '\r' in text:
                raise ValueError('One paragraph, maximum 3000 characters')
            style = op.get('style', 'Normal')
            o.require_style(parts, style)
            node = o.etree.Element(o.q(o.W, 'p'))
            prop = o.etree.SubElement(node, o.q(o.W, 'pPr'))
            o.etree.SubElement(prop, o.q(o.W, 'pStyle')).set(o.q(o.W, 'val'), style)
            run = o.etree.SubElement(node, o.q(o.W, 'r'))
            t = o.etree.SubElement(run, o.q(o.W, 't'))
            t.text = text
            t.set(o.q(o.XMLNS, 'space'), 'preserve')
        elif kind == 'append_table':
            rows = op['rows']
            if not isinstance(rows, list) or not 1 <= len(rows) <= 10 or any(not isinstance(r, list) or not 1 <= len(r) <= 8 for r in rows) or len({len(r) for r in rows}) != 1:
                raise ValueError('A bounded table requires 1-10 rows and 1-8 uniform columns')
            if any(not isinstance(t, str) or len(t) > 300 for row in rows for t in row):
                raise ValueError('Table cells must be strings of at most 300 characters')
            widths = op.get('column_widths_twips', [9000 // len(rows[0])] * len(rows[0]))
            if not isinstance(widths, list) or len(widths) != len(rows[0]) or any(type(w) is not int or not 300 <= w <= 15000 for w in widths) or sum(widths) > 15000:
                raise ValueError('Invalid bounded table column widths')
            node = o.etree.Element(o.q(o.W, 'tbl'))
            prop = o.etree.SubElement(node, o.q(o.W, 'tblPr'))
            o.etree.SubElement(prop, o.q(o.W, 'tblW'), attrib={o.q(o.W, 'w'): str(sum(widths)), o.q(o.W, 'type'): 'dxa'})
            borders = o.etree.SubElement(prop, o.q(o.W, 'tblBorders'))
            for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
                o.etree.SubElement(borders, o.q(o.W, edge), attrib={o.q(o.W, 'val'): 'single', o.q(o.W, 'sz'): '4', o.q(o.W, 'color'): 'BCC4CC'})
            o.etree.SubElement(prop, o.q(o.W, 'tblLayout'), attrib={o.q(o.W, 'type'): 'fixed'})
            grid = o.etree.SubElement(node, o.q(o.W, 'tblGrid'))
            for width in widths:
                o.etree.SubElement(grid, o.q(o.W, 'gridCol'), attrib={o.q(o.W, 'w'): str(width)})
            for row_index, row in enumerate(rows):
                tr = o.etree.SubElement(node, o.q(o.W, 'tr'))
                rp = o.etree.SubElement(tr, o.q(o.W, 'trPr'))
                o.etree.SubElement(rp, o.q(o.W, 'cantSplit'))
                if row_index == 0 and op.get('header', True):
                    o.etree.SubElement(rp, o.q(o.W, 'tblHeader'))
                for column, text in enumerate(row):
                    cell = o.etree.SubElement(tr, o.q(o.W, 'tc'))
                    cp = o.etree.SubElement(cell, o.q(o.W, 'tcPr'))
                    o.etree.SubElement(cp, o.q(o.W, 'tcW'), attrib={o.q(o.W, 'w'): str(widths[column]), o.q(o.W, 'type'): 'dxa'})
                    if row_index == 0 and op.get('header', True):
                        o.etree.SubElement(cp, o.q(o.W, 'shd'), attrib={o.q(o.W, 'fill'): 'F2F4F7'})
                    p = o.etree.SubElement(cell, o.q(o.W, 'p'))
                    r = o.etree.SubElement(p, o.q(o.W, 'r'))
                    if row_index == 0 and op.get('header', True):
                        rpr = o.etree.SubElement(r, o.q(o.W, 'rPr'))
                        o.etree.SubElement(rpr, o.q(o.W, 'b'))
                    o.etree.SubElement(r, o.q(o.W, 't')).text = text
            if op.get('keep_together', True):
                paragraphs = list(node.iter(o.q(o.W, 'p')))
                for index, paragraph in enumerate(paragraphs):
                    o.set_para_format(paragraph, {'keep_next': index < len(paragraphs) - 1})
        elif kind == 'insert_image':
            node = o.add_image(parts, root, op, asset_root)
            o.set_para_format(node, {'alignment': 'center', 'keep_next': bool(op.get('keep_with_next', True))})
        else:
            nodes = o.fragment_nodes([op['xml']], parts)
            node = nodes[0]
            if node.tag != o.q(o.W, 'p') or len(o.ptext(node)) > 3000 or len(list(node.iter(o.q(o.W, 'p')))) != 1:
                raise ValueError('insert_block supports one bounded paragraph; use append_table for tables')
        node.set(BLOCK_ID, block_id)
        after_id = op.get('after_block_id')
        if after_id:
            after = root.xpath('//*[@b:id=$id]', namespaces={'b': BLOCK_NS}, id=after_id)
            if len(after) != 1 or after[0].getparent() != body:
                raise ValueError('Insertion anchor must be one top-level block')
            body.insert(list(body).index(after[0]) + 1, node)
        else:
            ix = next((i for i, b in enumerate(body) if b.tag == o.q(o.W, 'sectPr')), len(body))
            body.insert(ix, node)
    elif kind == 'set_page':
        # Reuse the conservative existing layout implementation.
        updated = o.apply_plan(parts, {'operations': [dict(op, op='set_page')]}, asset_root)
        return updated, ['page-layout'], None
    else:
        if target is None:
            raise ValueError('Unknown or ambiguous block_id')
        if kind == 'delete_block':
            target.getparent().remove(target)
        elif kind == 'set_table_format':
            if target.tag != o.q(o.W, 'tbl'):
                raise ValueError('Target must be a table')
            paragraphs = list(target.iter(o.q(o.W, 'p')))
            for index, paragraph in enumerate(paragraphs):
                o.set_para_format(paragraph, {'keep_next': bool(op.get('keep_together', True)) and index < len(paragraphs) - 1})
        elif kind == 'replace_text':
            if target.tag != o.q(o.W, 'p') or not isinstance(op['new'], str) or len(op['new']) > 3000 or '\n' in op['new']:
                raise ValueError('replace_text requires one bounded paragraph')
            o.replace_text_in_paragraph(target, op['old'], op['new'])
        elif kind in ('set_paragraph_style', 'set_paragraph_format'):
            if target.tag != o.q(o.W, 'p'):
                raise ValueError('Target must be a paragraph')
            if kind == 'set_paragraph_style':
                o.require_style(parts, op['style'])
                prop = target.find(o.q(o.W, 'pPr'))
                if prop is None:
                    prop = o.etree.Element(o.q(o.W, 'pPr')); target.insert(0, prop)
                style = prop.find(o.q(o.W, 'pStyle'))
                if style is None:
                    style = o.etree.Element(o.q(o.W, 'pStyle')); prop.insert(0, style)
                style.set(o.q(o.W, 'val'), op['style'])
            else:
                o.set_para_format(target, op)
        else:
            raise ValueError('Unsupported atomic operation: ' + str(kind))
    parts[o.MAIN] = o.xml_bytes(root)
    return mark_blocks(parts), [block_id], None


def _sibling(name):
    spec = importlib.util.spec_from_file_location('docx_skill_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _template_blocks():
    return _sibling('template_blocks')


def _template_styles():
    return _sibling('template_styles')


def repair_targets(pages):
    targets = []
    labels = (('content_overflow', 'overflow'), ('heading_orphan', 'heading_orphan'), ('kept_table_split', 'kept_table_split'),
        ('margins_abnormal', 'margins'), ('margins_off_plan', 'margins_off_plan'))
    for page in pages:
        checks = page.get('checks') or {}
        for key, kind in labels:
            if checks.get(key):
                targets.append(dict(page=page['page'], kind=kind))
        for font in checks.get('fonts_missing') or []:
            targets.append(dict(page=page['page'], kind='missing_font', font=font))
    return targets


def _page_review():
    return _sibling('page_review')


def _style_library():
    return _sibling('style_library')


def _document_build():
    return _sibling('document_build')


def _template_manifest(parts, template):
    manifest = _sibling('template_importer').analyze(parts, o, source=str(template))
    sidecar = Path(template).parent / 'manifest.json'
    if sidecar.is_file():
        recorded = json.loads(sidecar.read_text(encoding='utf-8'))
        if isinstance(recorded, dict):
            known = {block['block_id'] for block in manifest.get('blocks') or []}
            for key in ('sections', 'tables'):
                for item in recorded.get(key) or []:
                    block_id = item.get('block_id')
                    if block_id and block_id not in known:
                        raise ValueError(f'Sidecar {key} block_id {block_id} is not in the template')
            manifest['template_id'] = recorded.get('template_id') or manifest['template_id']
            if recorded.get('license'):
                manifest['license'] = recorded['license']
            for key in ('sections', 'tables', 'content_controls'):
                if recorded.get(key):
                    manifest[key] = recorded[key]
    if not manifest.get('blocks'):
        raise ValueError('Template has no paragraphs or tables to index')
    return manifest
