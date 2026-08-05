import io
from typing import Any

import httpx
from docx import Document
from pypdf import PdfReader

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
    return text[:8000]


def fetch_attachment_bytes(url: str, client: httpx.Client | None = None) -> bytes:
    """Download an attachment previously uploaded to R2 (public URL) so a
    generator can read it as context. Public bucket URL, no auth header
    needed -- same pattern video_generator.py uses to re-download from
    Google's own signed video URI."""
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
