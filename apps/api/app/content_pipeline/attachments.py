import io
import re
from typing import Any

import httpx
from docx import Document
from PIL import Image
from pypdf import PdfReader

from app.social_integrations.media_validation import is_own_media_url

# Gemini's tile count is aspect-ratio-driven, not size-driven, above
# this threshold (crop_unit = floor(min(w,h)/1.5), tiles = ceil(w/
# crop_unit) * ceil(h/crop_unit)) -- verified by hand: a 4000x3000
# photo and the *same photo resized to 768x576* both come out to 4
# tiles (1032 tokens), because the crop unit scales right along with
# the image. The one documented flat rate is images with BOTH
# dimensions <= 384px: exactly 258 tokens regardless of aspect ratio
# (ai.google.dev/gemini-api/docs/image-understanding). That's the only
# resize target that reliably lowers cost, so that's what this uses --
# an unresized phone photo (often 3000-4000px, 3-6+ tiles) drops to a
# guaranteed single tile, with no loss to what the model actually
# needs for style/content understanding at this level.
_MAX_IMAGE_DIMENSION = 384
_IMAGE_JPEG_QUALITY = 85

# File-size caps (CIN-97) -- enforced on the raw upload, not on duration,
# since no video/audio duration probing (ffprobe or similar) is wired up
# in this codebase. Sized so that, at typical bitrates, a video/audio
# attachment stays roughly in the "short reference clip" range the
# unit-economics model assumed, not so it strictly guarantees it.
_MAX_SIZE_BYTES = {
    "image": 10 * 1024 * 1024,
    "video": 20 * 1024 * 1024,
    "audio": 8 * 1024 * 1024,
    "document": 5 * 1024 * 1024,
}

# Legacy .doc (binary OLE) isn't supported -- python-docx only reads the
# OOXML .docx format. Users are pointed at .docx in the upload error and
# the frontend's file picker only offers .docx.
_MIME_TO_ATTACHMENT_TYPE = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "image/gif": "image",
    "video/mp4": "video",
    "video/quicktime": "video",
    "video/webm": "video",
    "audio/mpeg": "audio",
    "audio/mp3": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/ogg": "audio",
    "audio/mp4": "audio",
    "text/plain": "document",
    "text/markdown": "document",
    "application/pdf": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
}


class UnsupportedAttachmentError(Exception):
    """Unknown mime type -- not one of the formats CIN-97 supports."""


class AttachmentTooLargeError(Exception):
    """Raw upload exceeds the per-type size cap."""


class TooManyAttachmentsError(Exception):
    """The attachment set for one generation exceeds CIN-107's caps."""


# CIN-107: up to 5 attachments total, any mix of document/image, but
# video and audio are capped at 1 each below that total -- one
# reference clip/track is enough context, and Gemini's multimodal
# input isn't meant to take several.
MAX_TOTAL_ATTACHMENTS = 5
_PER_TYPE_CAPS = {"video": 1, "audio": 1}


def validate_attachment_set(attachment_types: list[str]) -> None:
    if len(attachment_types) > MAX_TOTAL_ATTACHMENTS:
        raise TooManyAttachmentsError(
            f"Слишком много вложений: {len(attachment_types)}, максимум {MAX_TOTAL_ATTACHMENTS}"
        )
    for attachment_type, cap in _PER_TYPE_CAPS.items():
        count = attachment_types.count(attachment_type)
        if count > cap:
            raise TooManyAttachmentsError(
                f"Слишком много вложений типа {attachment_type}: {count}, максимум {cap}"
            )


def downscale_image_for_context(data: bytes) -> tuple[bytes, str]:
    """Resize an uploaded image attachment down to _MAX_IMAGE_DIMENSION
    on its longest side before it's stored/sent to Gemini as context
    (CIN-98). Images already at or under the bound pass through
    untouched -- this only ever shrinks, never upscales. Always
    re-encoded to JPEG: this attachment is read-only model context
    (never redisplayed to the user, unlike Post.image_url), so
    transparency/format fidelity doesn't matter, and JPEG compresses
    better than the source formats we accept (PNG/WEBP/GIF).
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()  # Image.open is lazy -- force decoding now to catch truncated/corrupt data here
    except Exception as exc:
        # mime_type is client-supplied and unverified against actual
        # file content (same gap as the other attachment types) -- a
        # spoofed/corrupt "image/*" upload should fail as a clean 400,
        # not an unhandled 500 from deep inside PIL.
        raise UnsupportedAttachmentError(f"Повреждённый или неподдерживаемый файл изображения: {exc}") from exc
    width, height = image.size
    if max(width, height) > _MAX_IMAGE_DIMENSION:
        scale = _MAX_IMAGE_DIMENSION / max(width, height)
        image = image.resize(
            (round(width * scale), round(height * scale)), Image.Resampling.LANCZOS
        )
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_IMAGE_JPEG_QUALITY)
    return buffer.getvalue(), "image/jpeg"


# CIN-151: backgrounds for the card renderer are resized for a canvas,
# not for a model's tile budget -- 384px (the attachment bound above)
# would be visibly soft behind 1080x1920. This bound covers every
# canvas format with room for the cover-crop.
_MAX_BACKGROUND_DIMENSION = 2160


def downscale_image_for_background(data: bytes) -> tuple[bytes, str]:
    """Resize an uploaded card background down to a canvas-appropriate
    bound. Same validation and JPEG re-encode as the context path
    (downscale_image_for_context), only a much larger target: this
    image is displayed to people, not read by a model."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise UnsupportedAttachmentError(
            f"Повреждённый или неподдерживаемый файл изображения: {exc}"
        ) from exc
    width, height = image.size
    if max(width, height) > _MAX_BACKGROUND_DIMENSION:
        scale = _MAX_BACKGROUND_DIMENSION / max(width, height)
        image = image.resize(
            (round(width * scale), round(height * scale)), Image.Resampling.LANCZOS
        )
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_IMAGE_JPEG_QUALITY)
    return buffer.getvalue(), "image/jpeg"


def classify_attachment(mime_type: str, size_bytes: int) -> str:
    """Validate an uploaded file and return its attachment_type
    (image/video/audio/document), or raise if the type is unsupported
    or the file exceeds its type's size cap."""
    attachment_type = _MIME_TO_ATTACHMENT_TYPE.get(mime_type)
    if attachment_type is None:
        raise UnsupportedAttachmentError(f"Неподдерживаемый формат файла: {mime_type}")
    if size_bytes > _MAX_SIZE_BYTES[attachment_type]:
        limit_mb = _MAX_SIZE_BYTES[attachment_type] / (1024 * 1024)
        raise AttachmentTooLargeError(
            f"Файл больше {limit_mb:.0f}MB -- максимум для типа {attachment_type}"
        )
    return attachment_type


def extract_document_text(data: bytes, mime_type: str) -> str:
    """Pull plain text out of an uploaded document attachment, capped so
    a large file doesn't blow up prompt size/cost unpredictably (~2000
    tokens worth of context, matching the CIN-97 cost model)."""
    if mime_type in ("text/plain", "text/markdown"):
        text = data.decode("utf-8", errors="replace")
    elif mime_type == "application/pdf":
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        document = Document(io.BytesIO(data))
        text = "\n".join(p.text for p in document.paragraphs)
    else:
        raise UnsupportedAttachmentError(f"Не текстовый документ: {mime_type}")
    return _collapse_whitespace(text)[:8000]


def _collapse_whitespace(text: str) -> str:
    """PDF extraction in particular tends to leave runs of blank lines
    and repeated spaces from page layout -- collapsing them before the
    8000-char cap spends that budget on actual content, not formatting
    artifacts (CIN-98)."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_attachment_bytes(url: str, client: httpx.Client | None = None) -> bytes:
    """Download an attachment previously uploaded to R2 (public URL) so a
    generator can read it as context. Public bucket URL, no auth header
    needed -- same pattern video_generator.py uses to re-download from
    Google's own signed video URI.

    CIN-161: GenerationRequest.attachments[].url is a plain client-
    supplied string, not verified against anything POST /content/
    attachment actually returned -- without this check, any
    authenticated user can point it at an internal address and have
    the response (as "document" text, it decodes straight into the
    generation prompt -- see extract_document_text's text/plain
    fallback) come back through the generated post. Same SSRF class
    already closed for TikTok/Telegram (CIN-134/CIN-156), reused here
    via is_own_media_url rather than duplicated.
    """
    if not is_own_media_url(url):
        raise UnsupportedAttachmentError(
            "Вложение должно ссылаться на файл, загруженный через "
            "POST /content/attachment"
        )
    response = (
        client.get(url, timeout=30.0) if client is not None else httpx.get(url, timeout=30.0)
    )
    response.raise_for_status()
    return response.content


def build_attachment_context(
    attachment_url: str, attachment_type: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Resolve an attachment reference into either extracted text
    (documents) or raw bytes + mime type (image/video/audio, for
    multimodal inline_data) -- one HTTP round-trip, shared by every
    generator that wants attachment context."""
    data = fetch_attachment_bytes(attachment_url, client=client)
    if attachment_type == "document":
        mime_type = _guess_document_mime(attachment_url)
        return {"kind": "text", "text": extract_document_text(data, mime_type)}
    mime_type = _guess_media_mime(attachment_url, attachment_type)
    return {"kind": "media", "mime_type": mime_type, "data": data}


def _guess_document_mime(url: str) -> str:
    lower = url.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "text/plain"


def _guess_media_mime(url: str, attachment_type: str) -> str:
    lower = url.lower()
    ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
    guesses = {
        "image": {"png": "image/png", "webp": "image/webp", "gif": "image/gif"},
        "video": {"mov": "video/quicktime", "webm": "video/webm"},
        "audio": {"wav": "audio/wav", "ogg": "audio/ogg", "m4a": "audio/mp4"},
    }
    defaults = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/mpeg"}
    return guesses.get(attachment_type, {}).get(ext, defaults[attachment_type])
