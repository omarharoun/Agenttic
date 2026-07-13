"""Document text extraction for the guided Business Requirement step.

A user may upload a requirement as a file (pdf / docx / txt / md) instead of
pasting text. Extraction is in-memory (we never persist the upload to disk, so
there is no path-traversal surface), gated by an extension whitelist + a byte
ceiling, and the heavy parsers (pypdf / python-docx) are lazy-imported so the
base install stays slim and a missing parser degrades to a clear error.
"""

from __future__ import annotations

from pathlib import Path

# 10 MB is plenty for a requirements doc and bounds in-memory parse cost.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

#: lower-cased file extensions we accept. Text-like ones decode directly; pdf /
#: docx go through the respective parser.
TEXT_EXTS = {".txt", ".md", ".markdown", ".text", ""}
ALLOWED_EXTS = TEXT_EXTS | {".pdf", ".docx"}


class DocumentError(ValueError):
    """A user-facing problem with an uploaded document (bad type, too big,
    unreadable). Routes map this to a 422 with the message shown verbatim."""


def extract_text(filename: str, data: bytes, *,
                 max_bytes: int = MAX_DOCUMENT_BYTES) -> str:
    """Extract plain text from an uploaded document.

    ``filename`` is used only for its extension (the type gate); ``data`` is the
    raw bytes. Raises :class:`DocumentError` (never a bare/opaque error) for an
    oversized file, an unsupported type, or an unreadable document.
    """
    if not data:
        raise DocumentError("the uploaded file is empty")
    if len(data) > max_bytes:
        raise DocumentError(
            f"file too large ({len(data) // 1024} KB); "
            f"limit is {max_bytes // (1024 * 1024)} MB")

    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise DocumentError(
            f"unsupported document type {ext or '(none)'!r}; "
            "upload a pdf, docx, txt, or md file")

    if ext in TEXT_EXTS:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            # tolerate stray non-UTF-8 bytes rather than failing the upload
            return data.decode("utf-8", "replace")
    if ext == ".pdf":
        return _extract_pdf(data)
    return _extract_docx(data)  # ext == ".docx"


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency present in image
        raise DocumentError("PDF support is not available on this server") from exc
    import io
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - any parse failure is user-facing
        raise DocumentError(f"could not read the PDF: {exc}") from exc
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text.strip():
        raise DocumentError(
            "no extractable text in the PDF (it may be scanned images)")
    return text


def _extract_docx(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover - dependency present in image
        raise DocumentError("DOCX support is not available on this server") from exc
    import io
    try:
        document = docx.Document(io.BytesIO(data))
        paras = [p.text for p in document.paragraphs]
    except Exception as exc:  # noqa: BLE001 - any parse failure is user-facing
        raise DocumentError(f"could not read the DOCX: {exc}") from exc
    text = "\n".join(p for p in paras if p.strip())
    if not text.strip():
        raise DocumentError("no extractable text in the DOCX")
    return text
