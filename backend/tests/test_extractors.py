from __future__ import annotations

import io
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from generate_corpus import generate

from swipesort.pipeline.digest import build_digest
from swipesort.pipeline.extractors.registry import extract
from swipesort.pipeline.mime import DOCX, PDF, PPTX, XLSX, sniff_mime


def _pdf(path: Path, pages: list[str]) -> Path:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.setTitle("Acme Invoice")
    c.setAuthor("Jane Doe")
    for text in pages:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    return path


def _docx(path: Path) -> Path:
    from docx import Document

    d = Document()
    d.core_properties.title = "Quarterly Report"
    d.core_properties.author = "Sam"
    d.add_paragraph("Revenue grew strongly in the third quarter.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "Region"
    t.rows[0].cells[1].text = "Emea"
    d.save(str(path))
    return path


def _pptx(path: Path, n: int = 5) -> Path:
    from pptx import Presentation

    prs = Presentation()
    for i in range(n):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text_frame.text = f"Topic {i}"
        s.placeholders[1].text_frame.text = f"Bullet about subject{i}"
    prs.save(str(path))
    return path


def _xlsx(path: Path) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Budget"
    ws.append(["Item", "Cost"])
    for i in range(10):
        ws.append([f"thing{i}", i * 10])
    wb.create_sheet("Notes").append(["hello"])
    wb.save(str(path))
    return path


def _jpeg(path: Path) -> Path:
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 14"
    exif.get_ifd(0x8769)[0x9003] = "2024:05:01 12:30:00"
    Image.new("RGB", (64, 48), (200, 10, 10)).save(path, "JPEG", exif=exif)
    return path


# ------------------------------------------------------------------ mime


def test_sniff_mime(tmp_path: Path) -> None:
    assert sniff_mime(_pdf(tmp_path / "x.dat", ["hi"])) == PDF
    assert sniff_mime(_docx(tmp_path / "a.bin")) == DOCX
    assert sniff_mime(_pptx(tmp_path / "b.bin")) == PPTX
    assert sniff_mime(_xlsx(tmp_path / "c.bin")) == XLSX
    assert sniff_mime(_jpeg(tmp_path / "d.png")) == "image/jpeg"  # content beats extension
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "PNG")
    (tmp_path / "e").write_bytes(buf.getvalue())
    assert sniff_mime(tmp_path / "e") == "image/png"
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "GIF")
    (tmp_path / "g").write_bytes(buf.getvalue())
    assert sniff_mime(tmp_path / "g") == "image/gif"
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "WEBP")
    (tmp_path / "w").write_bytes(buf.getvalue())
    assert sniff_mime(tmp_path / "w") == "image/webp"


def test_sniff_mime_misc(tmp_path: Path) -> None:
    def w(name: str, data: bytes) -> Path:
        p = tmp_path / name
        p.write_bytes(data)
        return p

    assert sniff_mime(w("h", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00")) == "image/heic"
    assert sniff_mime(w("m", b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00")) == "video/mp4"
    assert sniff_mime(w("q", b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00")) == "video/quicktime"
    assert sniff_mime(w("a", b"ID3\x03\x00\x00\x00\x00\x00\x00")) == "audio/mpeg"
    assert sniff_mime(w("wav", b"RIFF\x24\x00\x00\x00WAVEfmt ")) == "audio/wav"
    assert sniff_mime(w("notes", "héllo wörld\n".encode())) == "text/plain"
    assert sniff_mime(w("blob", bytes(range(256)))) == "application/octet-stream"
    assert sniff_mime(w("s.py", b"print(1)\n")) == "text/x-python"
    assert sniff_mime(w("s.ts", b"let x: number = 1;\n")) == "text/x-typescript"
    assert sniff_mime(w("z.zip", b"PK\x03\x04junk")) == "application/zip"
    d = tmp_path / "proj"
    d.mkdir()
    assert sniff_mime(d) == "inode/directory"
    assert sniff_mime(tmp_path / "missing") == "application/octet-stream"


# ------------------------------------------------------------------ extractors


def test_pdf(tmp_path: Path) -> None:
    p = _pdf(tmp_path / "inv.pdf", ["Invoice number 42 from Acme", "second page text", "third"])
    r = extract(p, 2000, 2)
    assert r.mime == PDF
    assert r.page_count == 3
    assert "Invoice number 42" in r.text
    assert "second page text" in r.text
    assert "third" not in r.text  # only the first `pages` pages
    assert r.metadata["title"] == "Acme Invoice"
    assert r.metadata["author"] == "Jane Doe"
    assert str(r.metadata["created"]).startswith("20")
    assert len(extract(p, 10, 3).text) <= 10


def test_docx(tmp_path: Path) -> None:
    r = extract(_docx(tmp_path / "r.docx"), 2000, 2)
    assert r.mime == DOCX
    assert "Revenue grew" in r.text
    assert "Region | Emea" in r.text
    assert r.metadata["title"] == "Quarterly Report"
    assert r.metadata["author"] == "Sam"
    assert "created" in r.metadata


def test_pptx(tmp_path: Path) -> None:
    p = _pptx(tmp_path / "s.pptx", n=8)
    r = extract(p, 5000, 1)
    assert r.mime == PPTX
    assert r.page_count == 8
    assert "Topic 0" in r.text and "subject2" in r.text
    assert "Topic 3" not in r.text  # pages * 3 slides


def test_xlsx(tmp_path: Path) -> None:
    r = extract(_xlsx(tmp_path / "b.xlsx"), 2000, 2)
    assert r.mime == XLSX
    assert r.page_count == 2
    assert "Sheet: Budget" in r.text and "Item | Cost" in r.text
    assert "Sheet: Notes" in r.text
    assert "thing3" in r.text and "thing8" not in r.text  # first 5 rows only
    assert r.metadata["sheets"] == ["Budget", "Notes"]


def test_image_exif(tmp_path: Path) -> None:
    r = extract(_jpeg(tmp_path / "p.jpg"), 2000, 2)
    assert r.mime == "image/jpeg"
    assert r.is_image and r.text == ""
    assert (r.metadata["width"], r.metadata["height"]) == (64, 48)
    assert r.metadata["camera"] == "Apple iPhone 14"
    assert r.metadata["exif_date"] == "2024-05-01T12:30:00"
    assert r.metadata["has_gps"] is False


def test_image_no_exif(tmp_path: Path) -> None:
    p = tmp_path / "s.png"
    Image.new("RGB", (30, 20)).save(p, "PNG")
    r = extract(p, 2000, 2)
    assert r.mime == "image/png" and r.is_image
    assert "camera" not in r.metadata


def test_text_and_code(tmp_path: Path) -> None:
    t = tmp_path / "n.txt"
    t.write_bytes(b"hello \xff world\n" * 100)
    r = extract(t, 50, 2)
    assert r.mime == "text/plain" and len(r.text) == 50 and "hello" in r.text
    c = tmp_path / "m.py"
    c.write_text("def main():\n    pass\n", encoding="utf-8")
    r = extract(c, 2000, 2)
    assert r.mime == "text/x-python"
    assert r.metadata["language"] == "python"
    assert "def main" in r.text


def test_audio_and_video_never_raise(tmp_path: Path) -> None:
    wav = tmp_path / "a.wav"
    import wave

    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)
    r = extract(wav, 2000, 2)
    assert r.mime == "audio/wav"
    assert abs(float(r.metadata["duration"]) - 1.0) < 0.1
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
    r = extract(mp4, 2000, 2)
    assert r.mime == "video/mp4"


def test_bundle_node(tmp_path: Path) -> None:
    d = tmp_path / "myapp"
    d.mkdir()
    (d / "package.json").write_text('{"name": "cool-app", "description": "A cool app"}')
    (d / "README.md").write_text("# Cool App\nDoes cool things.\n")
    (d / "src").mkdir()
    r = extract(d, 2000, 2)
    assert r.mime == "inode/directory"
    assert r.metadata["bundle_kind"] == "node"
    assert "cool-app" in r.text and "A cool app" in r.text
    assert "Does cool things" in r.text
    assert "src/" in r.text and "package.json" in r.text


def test_bundle_git_and_python(tmp_path: Path) -> None:
    g = tmp_path / "g"
    (g / ".git").mkdir(parents=True)
    assert extract(g, 500, 1).metadata["bundle_kind"] == "git"
    p = tmp_path / "p"
    p.mkdir()
    (p / "pyproject.toml").write_text('[project]\nname = "pkg"\ndescription = "Py thing"\n')
    r = extract(p, 500, 1)
    assert r.metadata["bundle_kind"] == "python" and "pkg" in r.text
    a = tmp_path / "Thing.app"
    a.mkdir()
    assert extract(a, 500, 1).metadata["bundle_kind"] == "app"


@pytest.mark.parametrize("ext", ["pdf", "docx", "pptx", "xlsx", "jpg", "png", "mp3", "txt", "py"])
def test_garbage_never_raises(tmp_path: Path, ext: str) -> None:
    p = tmp_path / f"junk.{ext}"
    p.write_bytes(b"\x00\x01garbage\xff\xfe" * 50)
    r = extract(p, 2000, 2)
    assert r.mime
    if ext in ("pdf", "docx", "pptx", "xlsx", "jpg", "png"):
        assert "error" in r.metadata


def test_missing_file_never_raises(tmp_path: Path) -> None:
    r = extract(tmp_path / "nope.pdf", 100, 1)
    assert r.mime and r.text == ""


def test_process_pool_roundtrip(tmp_path: Path) -> None:
    p = _pdf(tmp_path / "pool.pdf", ["pool test page"])
    with ProcessPoolExecutor(max_workers=1) as ex:
        r = ex.submit(extract, p, 2000, 2).result(timeout=60)
    assert r.mime == PDF and "pool test page" in r.text and r.page_count == 1


# ------------------------------------------------------------------ corpus


def test_corpus_digests_have_content(tmp_path: Path) -> None:
    """>=90% of files get digest text beyond the filename. Images get their text later from
    caption/OCR (pipeline), so here they only need the metadata lines (kind, size)."""
    m = generate(tmp_path / "c", n=60, seed=1)
    inp = tmp_path / "c" / "input"
    good = 0
    for e in m.entries:
        path = inp / e.rel_path
        r = extract(path, 4000, 2)
        assert "error" not in r.metadata, (e.rel_path, r.metadata)
        digest = build_digest(path.name, e.rel_path, path.suffix.lstrip("."), r)
        if r.is_image:
            assert e.kind in ("photo", "screenshot")
            if e.kind == "screenshot":
                assert "image screenshot" in digest
            good += 1 if "size: " in digest else 0
        elif "\ntext: " in digest and digest.split("\ntext: ", 1)[1].strip():
            good += 1
    assert good / len(m.entries) >= 0.9
