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
_shared_path = Path(__file__).resolve().parents[2] / 'platform_app' / 'artifact_runtime.py'
if not _shared_path.is_file():
    _shared_path = Path(__file__).with_name('artifact_runtime.py')
_shared_spec = importlib.util.spec_from_file_location('artifact_runtime', _shared_path)
_shared = importlib.util.module_from_spec(_shared_spec)
_shared_spec.loader.exec_module(_shared)
ID = re.compile(r'^[\w.-]{1,80}$')
BLOCK_NS = 'urn:deep-agent:docx:blocks:v1'
BLOCK_ID = '{%s}id' % BLOCK_NS
MAX_DIRTY = 3


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or value in ('.', '..'):
        raise ValueError('Invalid stable identifier')
    return value


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

    def commit(self, c, state, data, operation_id, request, changed, kind='document_revision'):
        revision = state['revision'] + 1
        digest = hashlib.sha256(data).hexdigest()
        result = dict(status='committed', document_id=state['document_id'], revision=revision,
            sha256=digest, operation_id=operation_id, changed_blocks=changed, needs_preview=True,
            path=self.path.name, preview_ready=False, committed_at=time.time())
        dirty = state.get('dirty', 0) + 1
        gate = state.get('gate', 'open')
        if gate == 'open' and dirty >= MAX_DIRTY:
            gate = 'checkpoint'
        if 'external' in request:
            result.update(external_unreviewed=True, gate='checkpoint')
        state.update(revision=revision, sha256=digest, pending_publish=True, finalized=False,
            dirty=dirty, pages=[], preview_ready=False, gate=gate)
        self.persist_revision(c, state, data, result, request, operation_id, kind)
        return result

    def init(self, document_id, template=None, mode='normal'):
        identifier(document_id)
        if mode not in ('normal', 'strict'):
            raise ValueError('Unknown review mode')
        with self.locked() as (c, state):
            if state:
                if state['document_id'] != document_id:
                    raise ValueError('Document identity mismatch')
                return state
            if self.path.exists():
                raise ValueError('Document exists; use track_external or a different output path')
            parts = o.read_parts(template) if template else o.default_package()
            data = packed(mark_blocks(parts))
            state = dict(document_id=document_id, revision=-1, mode=mode, dirty=-1, pages=[], gate='open')
            self.commit(c, state, data, '__init__', {'template': str(template)}, [], 'document_initialized')
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
            kind = op.get('type')
            repairs = {'replace_text', 'set_paragraph_style', 'set_paragraph_format', 'delete_block', 'set_table_format', 'set_page'}
            if state['gate'] != 'open' and kind not in repairs:
                raise ValueError('Page checkpoint required before adding content: ' + state['gate'])
            if state['dirty'] >= MAX_DIRTY * 2:
                raise ValueError('Repair budget exhausted; checkpoint before further changes')
            parts = o.read_parts(self.path)
            try:
                parts, changed = mutate(parts, op, Path(asset_root or self.path.parent))
            except (o.etree.XMLSyntaxError, KeyError, TypeError) as exc:
                raise ValueError('Invalid atomic operation: ' + str(exc)) from exc
            return self.commit(c, state, packed(parts), request['operation_id'], request, changed)

    def track_external(self, document_id=None):
        """Observe native write/execute changes without calling them reviewed/atomic."""
        with self.locked() as (c, state):
            if not self.path.is_file() or self.path.is_symlink():
                return None
            digest = o.sha(self.path)
            if state and digest == state['sha256']:
                return None
            # Invalid writes never replace the last committed snapshot.
            parts = o.read_parts(self.path)
            data = self.path.read_bytes()
            if not o.validate_parts(parts)['ok']:
                raise ValueError('External DOCX is invalid')
            if state is None:
                state = dict(document_id=identifier(document_id or self.path.stem), revision=-1, mode='normal', dirty=0)
            result = self.commit(c, state, data, 'external-' + str(state['revision'] + 1), {'external': digest}, ['*'])
            state.update(gate='checkpoint', external_unreviewed=True)
            self.save_state(c, state)
            c.commit()
            return result

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

    def checkpoint(self, final=False):
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location('page_review', Path(__file__).with_name('page_review.py'))
        review = module_from_spec(spec)
        spec.loader.exec_module(review)
        # Render an immutable snapshot outside the commit lock; stale results are rejected.
        current = self.status()
        revision = current['revision']
        try:
            output, manifest = self.render_snapshot(revision)
            source = output / self.path.name
            pages = review.review_pages(source, output, o, final=final, render=False)
            failure = None
        except (OSError, ValueError, RuntimeError, ImportError, TimeoutError) as exc:
            pages, failure = [], type(exc).__name__ + ': ' + str(exc)[:300]
        with self.locked() as (c, state):
            self.assert_current(state, revision)
            old = {p['fingerprint']: p for p in state.get('last_pages', []) if p.get('checks', {}).get('visual_review') in ('passed', 'failed')}
            for page in pages:
                if page['fingerprint'] in old and page['checks']['automatic_passed']:
                    page['checks']['visual_review'] = old[page['fingerprint']]['checks']['visual_review']
                    page['evidence'] = old[page['fingerprint']].get('evidence')
                    if page['checks']['visual_review'] == 'failed':
                        page['status'] = 'failed'
                    elif page['complete']:
                        page['status'] = 'passed'
            state.update(pages=pages, last_pages=pages, render_revision=revision, render_error=failure, dirty=0)
            if not failure:
                state.update(preview_revision=revision, preview_pages=len(pages), preview_error=None, preview_ready=True)
            complete = [p for p in pages if p['complete']]
            if failure or any(not p['checks']['automatic_passed'] or p['checks']['visual_review'] == 'failed' for p in pages):
                state['gate'] = 'failed'
            elif state['mode'] == 'strict' and any(p['status'] != 'passed' for p in complete):
                state['gate'] = 'visual_pending'
            else:
                state['gate'] = 'open'
            self.save_state(c, state)
            event = dict(type='document_review', document_id=state['document_id'], revision=revision,
                pages=pages, gate=state['gate'], render_error=failure, path=self.path.name,
                **({} if failure else dict(preview_revision=revision, preview_pages=len(pages), preview_ready=True, render_directory=str(output))))
            c.execute('INSERT INTO events(data) VALUES (?)', (json.dumps(event),))
            c.commit()
            return event

    def visual_review(self, revision, page, passed, reviewer, notes):
        if type(passed) is not bool:
            raise ValueError('Visual verdict must be a boolean')
        if reviewer not in ('human', 'vision_model') or not isinstance(notes, str) or not notes.strip():
            raise ValueError('Actual visual review requires reviewer and evidence notes')
        with self.locked() as (c, state):
            self.assert_current(state, revision)
            if state.get('render_revision') != revision:
                raise ValueError('Render checkpoint is stale')
            target = next((p for p in state['pages'] if p['page'] == page), None)
            if target is None or not target['checks']['automatic_passed']:
                raise ValueError('Page must pass automatic checks first')
            target['checks']['visual_review'] = 'passed' if passed else 'failed'
            target['evidence'] = dict(reviewer=reviewer, notes=notes, reviewed_at=time.time())
            target['status'] = ('passed' if target['complete'] else 'writing') if passed else 'failed'
            state['last_pages'] = state['pages']
            if not passed:
                state['gate'] = 'failed'
            elif all(p['checks']['automatic_passed'] and (not p['complete'] or p['status'] == 'passed') for p in state['pages']):
                state['gate'] = 'open'
            self.save_state(c, state)
            event = dict(type='document_review', document_id=state['document_id'], revision=revision, path=self.path.name,
                pages=state['pages'], gate=state['gate'])
            c.execute('INSERT INTO events(data) VALUES (?)', (json.dumps(event),))
            c.commit()
            return event

    def finalize(self, base_revision):
        with self.locked() as (c, state):
            self.assert_current(state, base_revision)
            if state.get('render_revision') != base_revision or not state['pages'] or state['gate'] != 'open' or any(p['status'] != 'passed' for p in state['pages']):
                raise ValueError('Final checkpoint and actual visual review of every page required')
            render_dir = self.render_directory(base_revision)
            if not (render_dir / 'render.json').is_file() or any(not (render_dir / p['image']).is_file() for p in state['pages']):
                raise ValueError('Reviewed render artifacts are missing; render and review again')
            state['finalized'] = True
            state['external_unreviewed'] = False
            self.save_state(c, state)
            c.execute('INSERT INTO events(data) VALUES (?)', (json.dumps(dict(type='document_finalized', document_id=state['document_id'], revision=base_revision, path=self.path.name, finalized=True)),))
            c.commit()
            return dict(status='finalized', document_id=state['document_id'], revision=base_revision, sha256=state['sha256'])


def mutate(parts, op, asset_root):
    if not isinstance(op, dict) or len(json.dumps(op, ensure_ascii=False)) > 24000:
        raise ValueError('Operation exceeds bounded payload')
    parts = mark_blocks(parts)
    root = o.parse_xml(parts[o.MAIN])
    body = root.find(o.q(o.W, 'body'))
    kind = op.get('type')
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
        return updated, ['page-layout']
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
    return mark_blocks(parts), [block_id]
