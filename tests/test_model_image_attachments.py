"""Model image limits apply to request copies, never the original attachments."""
import base64
import io

from PIL import Image
import pytest
from langchain_core.messages import HumanMessage

from platform_app.agent_core.attachments import (
    ATTACHMENT_META_KEY, AttachmentStore, ImageAttachment,
    image_attachment_from_bytes, model_image_attachment,
)
from platform_app.agent_core.llm import materialize_attachment_refs
from platform_app.runtime import public_run_error


def picture(size, mode='RGB'):
    out = io.BytesIO()
    Image.new(mode, size, (0, 40, 80, 100) if mode == 'RGBA' else (0, 40, 80)).save(out, 'PNG')
    return image_attachment_from_bytes(out.getvalue(), filename='reference.png')


def test_long_png_is_resized_even_when_below_ten_megabytes():
    original = picture((1920, 8640))
    before = original.data
    assert original.size < 10 * 1024 * 1024
    result = model_image_attachment(original)
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.size == (1820, 8192)
        assert image.mode == 'RGB' and image.format == 'JPEG'
    assert original.data == before and original.mime_type == 'image/png'


def test_valid_smaller_image_bytes_are_retained():
    original = picture((100, 200))
    assert model_image_attachment(original) is original


def test_transparency_is_preserved_when_resizing():
    original = picture((200, 9000), 'RGBA')
    result = model_image_attachment(original)
    assert result.mime_type == 'image/png'
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.height == 8192 and image.mode == 'RGBA'
        assert image.getpixel((0, 0))[3] == 100


def test_magic_header_alone_does_not_make_a_valid_image():
    raw = b'\x89PNG\r\n\x1a\n' + b'broken'
    with pytest.raises(ValueError, match='cannot be decoded'):
        model_image_attachment(ImageAttachment('bad.png', 'image/png', raw, len(raw)))


def test_request_materialization_keeps_checkpoint_and_original_unchanged(tmp_path):
    original = picture((200, 9000))
    store = AttachmentStore(tmp_path / 'attachments')
    ref = store.put(original)
    message = HumanMessage(content='Reference', additional_kwargs={ATTACHMENT_META_KEY:[ref.to_dict()]})
    result = materialize_attachment_refs([message], store)[0]
    assert message.content == 'Reference' and message.additional_kwargs[ATTACHMENT_META_KEY]
    assert ATTACHMENT_META_KEY not in result.additional_kwargs
    assert store.read(ref).data == original.data
    block = next(b for b in result.content if b.get('type') == 'image')
    with Image.open(io.BytesIO(base64.b64decode(block['base64']))) as image:
        assert image.height == 8192
    store.close()


def test_many_images_use_the_lower_dimension_limit(tmp_path, monkeypatch):
    original = picture((10, 10)); store = AttachmentStore(tmp_path / 'attachments')
    ref = store.put(original)
    message = HumanMessage(content='Reference', additional_kwargs={ATTACHMENT_META_KEY:[ref.to_dict()]})
    dimensions = []
    def prepare(image, *, max_edge):
        dimensions.append(max_edge)
        return image
    monkeypatch.setattr('platform_app.agent_core.llm.model_image_attachment', prepare)
    materialize_attachment_refs([message]*15, store)
    assert dimensions == [4096]*15
    store.close()


def test_upstream_image_rejection_has_an_actionable_public_error():
    message = public_run_error('Error code: 400 - You have uploaded an unsupported image. request_id: private')
    assert '图片' in message and '像素尺寸' in message and 'private' not in message
