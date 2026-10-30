"""Build one documented skill CLI command for deterministic browser fixtures."""
import json
from pathlib import Path
import shlex


def command(feature, root, action, args):
    identity = args['document_id' if feature == 'docx' else 'presentation_id']
    target = identity + '.' + feature
    script = f'/agent-skills/{feature}-ooxml-author/scripts/office_{feature}.py'
    argv = ['python', script]
    if action == 'init':
        argv += ['init', '--document-id', identity, '--output', target] if feature == 'docx' else ['init', target, '--presentation-id', identity]
        if args.get('template'): argv += ['--template', args['template']]
    elif action == 'apply_one':
        argv += ['apply-one', target, '/dev/stdin'] if feature == 'docx' else ['apply-one', target, '--request', '/dev/stdin']
    elif action == 'checkpoint' and feature == 'docx':
        argv += ['checkpoint', target]
        if args.get('final'): argv.append('--final')
    elif action == 'check_slide' and feature == 'pptx':
        argv += ['check-slide', target, '--slide-id', args['slide_id'], '--notes', args['content_notes']]
    else:
        raise ValueError('Unsupported fixture command: ' + action)
    result = 'cd ' + shlex.quote(str(Path(root))) + ' && ' + shlex.join(argv)
    if action == 'apply_one':
        result += " <<'OFFICE_REQUEST'\n" + json.dumps(args, ensure_ascii=True) + '\nOFFICE_REQUEST'
    return result
