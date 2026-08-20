"""Normalize any upload into a form we can cite precisely.

Everything visual becomes a PDF, because a PDF is the one format where we can
render a page and lay a highlight rectangle over the exact text. That is what
lets a citation into a .hwp or .pptx behave identically to one into a PDF.

Audio and video are the exception: their canonical form is a timestamped
transcript, and a citation into them is a time range rather than a rectangle.
"""
from __future__ import annotations

import logging
import mimetypes
import shutil
import subprocess
import tempfile
from pathlib import Path

from api.app.db.models import SourceKind

log = logging.getLogger("ingest.convert")

SOFFICE = "soffice"
LIBREOFFICE_TIMEOUT_S = 300
FFMPEG_TIMEOUT_S = 3600

OFFICE_EXT = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf"}
HWP_EXT = {".hwp", ".hwpx"}
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".json"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".opus"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"}


def classify(filename: str, mime: str | None = None) -> SourceKind:
    """Decide the ingest route from the extension, falling back to the MIME type.

    ``pdf`` here means "a PDF we have not looked inside yet"; the extractor
    re-labels it ``scanned`` if no page has a text layer.
    """
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return SourceKind.pdf
    if ext in HWP_EXT:
        return SourceKind.hwp
    if ext in OFFICE_EXT:
        return SourceKind.office
    if ext in TEXT_EXT:
        return SourceKind.text
    if ext in IMAGE_EXT:
        return SourceKind.image
    if ext in AUDIO_EXT:
        return SourceKind.audio
    if ext in VIDEO_EXT:
        return SourceKind.video

    mime = mime or mimetypes.guess_type(filename)[0] or ""
    if mime == "application/pdf":
        return SourceKind.pdf
    if mime.startswith("image/"):
        return SourceKind.image
    if mime.startswith("audio/"):
        return SourceKind.audio
    if mime.startswith("video/"):
        return SourceKind.video
    if mime.startswith("text/"):
        return SourceKind.text
    raise ValueError(f"unsupported file type: {filename!r} ({mime!r})")


def is_media(kind: SourceKind) -> bool:
    return kind in (SourceKind.audio, SourceKind.video)


def to_pdf(src: Path, out_dir: Path, kind: SourceKind) -> Path:
    """Produce a PDF beside ``src``. Returns the PDF path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == SourceKind.pdf:
        return src
    if kind == SourceKind.image:
        return _image_to_pdf(src, out_dir)
    if kind in (SourceKind.office, SourceKind.hwp, SourceKind.text):
        return _libreoffice_to_pdf(src, out_dir)
    raise ValueError(f"{kind} has no PDF representation")


def _libreoffice_to_pdf(src: Path, out_dir: Path) -> Path:
    """Convert via headless LibreOffice.

    HWP/HWPX go through the H2Orestart extension registered in the image. Each
    conversion gets a private user profile directory: LibreOffice keeps a lock on
    a shared profile, so two concurrent workers using the default one would
    deadlock and eventually time out.
    """
    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
        cmd = [
            SOFFICE,
            f"-env:UserInstallation=file://{profile}",
            "--headless", "--norestore", "--invisible", "--nolockcheck",
            "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", str(out_dir),
            str(src),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=LIBREOFFICE_TIMEOUT_S)

    produced = out_dir / (src.stem + ".pdf")
    if not produced.exists():
        # Spreadsheets and presentations sometimes land under a different stem.
        candidates = sorted(out_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime)
        if candidates:
            produced = candidates[-1]
    if not produced.exists():
        raise RuntimeError(
            f"LibreOffice produced no PDF for {src.name}\n"
            f"stdout: {proc.stdout[-800:]}\nstderr: {proc.stderr[-800:]}"
        )
    log.info("converted %s -> %s", src.name, produced.name)
    return produced


def _image_to_pdf(src: Path, out_dir: Path) -> Path:
    """Wrap an image as a single-page PDF at its natural size.

    Going through PDF rather than treating images specially means OCR word boxes
    and highlight rectangles use exactly the same coordinate space as every other
    document.
    """
    import pymupdf

    dst = out_dir / (src.stem + ".pdf")
    with pymupdf.open() as pdf:
        img = pymupdf.open(str(src))
        if img.is_pdf:
            pdf.insert_pdf(img)
        else:
            pix = pymupdf.Pixmap(str(src))
            page = pdf.new_page(width=pix.width, height=pix.height)
            page.insert_image(page.rect, filename=str(src))
        img.close()
        pdf.save(str(dst))
    return dst


def extract_audio(src: Path, out_dir: Path) -> tuple[Path, int]:
    """Pull a mono 16 kHz WAV out of any audio or video file for Whisper.

    Returns ``(wav_path, duration_ms)``. 16 kHz mono is what Whisper resamples to
    anyway, so doing it once here avoids ffmpeg running again inside the ASR
    service on every retry.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / (src.stem + ".16k.wav")
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S)
    if not wav.exists():
        raise RuntimeError(f"ffmpeg failed for {src.name}: {proc.stderr[-800:]}")
    return wav, probe_duration_ms(src)


def probe_duration_ms(src: Path) -> int:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return int(float(proc.stdout.strip()) * 1000)
    except (TypeError, ValueError):
        return 0


def normalize_media_for_playback(src: Path, out_dir: Path, kind: SourceKind) -> Path:
    """Make sure the browser can play the file the citation seeks into.

    An .avi or .wma will not play in a browser, so a citation into it could not
    be verified by ear. Anything already web-playable is passed through
    untouched rather than re-encoded.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    if (kind == SourceKind.audio and ext in {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"}) or \
       (kind == SourceKind.video and ext in {".mp4", ".webm", ".m4v"}):
        dst = out_dir / src.name
        if dst.resolve() != src.resolve():
            shutil.copyfile(src, dst)
        return dst

    if kind == SourceKind.audio:
        dst = out_dir / (src.stem + ".m4a")
        cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn",
               "-c:a", "aac", "-b:a", "128k", str(dst)]
    else:
        dst = out_dir / (src.stem + ".mp4")
        cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(src),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S)
    if not dst.exists():
        raise RuntimeError(f"ffmpeg transcode failed for {src.name}: {proc.stderr[-800:]}")
    return dst
