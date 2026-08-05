import io
from unittest.mock import MagicMock, patch

import httpx
import pytest
from docx import Document

from app.content_pipeline.attachments import (
    AttachmentTooLargeError,
    UnsupportedAttachmentError,
    build_attachment_context,
    classify_attachment,
    extract_document_text,
    fetch_attachment_bytes,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_classify_attachment_image_ok() -> None:
    assert classify_attachment("image/jpeg", 1024) == "image"


def test_classify_attachment_document_ok() -> None:
    assert classify_attachment("application/pdf", 1024) == "document"


def test_classify_attachment_unsupported_mime_raises() -> None:
    with pytest.raises(UnsupportedAttachmentError):
        classify_attachment("application/x-msdownload", 1024)


def test_classify_attachment_too_large_raises() -> None:
    with pytest.raises(AttachmentTooLargeError):
        classify_attachment("image/jpeg", 11 * 1024 * 1024)


def test_classify_attachment_video_size_cap_is_independent_of_image() -> None:
    # 15MB is over the image cap (10MB) but under the video cap (20MB)
    assert classify_attachment("video/mp4", 15 * 1024 * 1024) == "video"


def test_extract_document_text_plain() -> None:
    text = extract_document_text("Привет, мир".encode(), "text/plain")
    assert text == "Привет, мир"


def test_extract_document_text_markdown() -> None:
    text = extract_document_text(b"# Title\n\nBody text", "text/markdown")
    assert "Title" in text and "Body text" in text


def test_extract_document_text_docx() -> None:
    buf = io.BytesIO()
    document = Document()
    document.add_paragraph("Первый абзац.")
    document.add_paragraph("Второй абзац.")
    document.save(buf)

    text = extract_document_text(
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "Первый абзац." in text
    assert "Второй абзац." in text


def test_extract_document_text_pdf() -> None:
    fake_page = MagicMock()
    fake_page.extract_text.return_value = "Текст со страницы PDF"
    with patch("app.content_pipeline.attachments.PdfReader") as MockReader:
        MockReader.return_value.pages = [fake_page]
        text = extract_document_text(b"%PDF-fake-bytes", "application/pdf")
    assert text == "Текст со страницы PDF"


def test_extract_document_text_caps_length() -> None:
    text = extract_document_text(("x" * 20000).encode(), "text/plain")
    assert len(text) == 8000


def test_extract_document_text_unsupported_mime_raises() -> None:
    with pytest.raises(UnsupportedAttachmentError):
        extract_document_text(b"data", "image/jpeg")


def test_fetch_attachment_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"raw-bytes")

    assert fetch_attachment_bytes("https://r2.example/x.jpg", client=_client(handler)) == b"raw-bytes"


def test_build_attachment_context_document_extracts_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"Some plain text content")

    context = build_attachment_context(
        "https://r2.example/notes.txt", "document", client=_client(handler)
    )
    assert context == {"kind": "text", "text": "Some plain text content"}


def test_build_attachment_context_image_returns_raw_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-image-bytes")

    context = build_attachment_context(
        "https://r2.example/photo.png", "image", client=_client(handler)
    )
    assert context == {"kind": "media", "mime_type": "image/png", "data": b"fake-image-bytes"}
