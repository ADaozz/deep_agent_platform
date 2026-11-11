"""Guidance must keep the document under files/, the only place the platform versions and previews."""
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
DOCS = (SKILL / 'SKILL.md', SKILL / 'README.md')


def test_documented_commands_target_files_directory():
    for path in DOCS:
        text = path.read_text(encoding='utf-8')
        assert '../tmp' not in text and 'work/' not in text, path
        for command in re.findall(r'^python "\$S" (?:init|apply-one|status|checkpoint|finalize) .*$', text, re.M):
            document = re.search(r'(?:--output |apply-one |status |checkpoint |finalize )(\S+\.docx)', command)
            assert document and document.group(1).startswith('files/'), (path.name, command)
            if 'apply-one' in command:
                assert ' tmp/' in command and '--asset-root files' in command, (path.name, command)
