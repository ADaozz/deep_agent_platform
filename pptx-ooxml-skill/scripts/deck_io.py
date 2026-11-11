"""The single supported way for generation/repair scripts to write a PPTX.

The deliverable in ``files/`` is the only working copy: every save lands there
atomically, so the platform can version and preview it. A deck kept under
``tmp/`` is invisible to versioning and preview, hence rejected here.
"""
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_runtime import publish  # noqa: E402


def save_deck(prs, path='files/deck.pptx'):
    target = Path(path)
    if target.suffix.lower() != '.pptx':
        raise ValueError(f'save_deck 只写 .pptx 交付文件：{path}')
    if target.resolve().is_relative_to((Path.cwd() / 'tmp').resolve()):
        raise ValueError(f'不要把整稿保存到 tmp/（平台不会登记版本或预览）：{path}；请直接保存到 files/ 下的交付路径')
    target.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    prs.save(buffer)
    publish(target, buffer.getvalue())
    return target
