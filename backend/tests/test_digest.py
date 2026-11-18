from __future__ import annotations

import pytest

from swipesort.pipeline.digest import build_digest, tokenize_name
from swipesort.pipeline.extractors.base import ExtractResult


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("IMG_2231.pdf", ["img", "2231"]),
        ("Q3-report_finalV2.docx", ["q3", "report", "final", "v2"]),
        ("invoiceAcme2025", ["invoice", "acme", "2025"]),
        ("XMLHttpRequest.js", ["xml", "http", "request"]),
        ("Screenshot 2024-01-05 at 10.30.12 AM.png", [
            "screenshot", "2024", "01", "05", "at", "10", "30", "12", "am"]),
        ("my  file (copy).txt", ["my", "file", "copy"]),
        ("README", ["readme"]),
        ("", []),
    ],
)
def test_tokenize_name(name: str, expected: list[str]) -> None:
    assert tokenize_name(name) == expected


def _result(**kw: object) -> ExtractResult:
    return ExtractResult(mime="application/pdf", text="Invoice   total\n due  $40", **kw)


def test_digest_content() -> None:
    r = _result(page_count=3, metadata={"title": "Acme Invoice", "author": "Jane"})
    d = build_digest("invoiceAcme2025.pdf", "Finance/Taxes 2025/invoiceAcme2025.pdf", "pdf", r)
    assert "name: invoice acme 2025" in d
    assert "folder: finance taxes 2025" in d
    assert "pdf document" in d
    assert "pages: 3" in d
    assert "title: Acme Invoice" in d and "author: Jane" in d
    assert "text: Invoice total due $40" in d  # whitespace normalized


def test_digest_deterministic_and_capped() -> None:
    r = ExtractResult(mime="text/plain", text="word " * 5000)
    a = build_digest("n.txt", "a/n.txt", "txt", r, caption="c", ocr_text="o", max_chars=500)
    b = build_digest("n.txt", "a/n.txt", "txt", r, caption="c", ocr_text="o", max_chars=500)
    assert a == b
    assert len(a) <= 500
    assert len(build_digest("n.txt", "n.txt", "txt", r)) <= 2000
    tiny = build_digest("n.txt", "n.txt", "txt", r, max_chars=30)
    assert len(tiny) <= 30


def test_digest_caption_and_ocr() -> None:
    r = ExtractResult(mime="image/jpeg", is_image=True, metadata={"width": 64, "height": 48})
    d = build_digest("a.jpg", "a.jpg", "jpg", r, caption="a dog on a beach", ocr_text="OPEN  24h")
    assert "caption: a dog on a beach" in d
    assert "ocr: OPEN 24h" in d
    assert "image photo" in d


def test_digest_screenshot_heuristic() -> None:
    png = ExtractResult(mime="image/png", is_image=True, metadata={"width": 800, "height": 500})
    assert "screenshot" in build_digest("Screenshot 2024-01-01.png", "x.png", "png", png)
    assert "screenshot" in build_digest(
        "x.png", "x.png", "png",
        ExtractResult(mime="image/png", is_image=True, metadata={"width": 1920, "height": 1080}),
    )
    photo = ExtractResult(
        mime="image/png", is_image=True,
        metadata={"width": 800, "height": 500, "camera": "Apple iPhone 14"},
    )
    d = build_digest("Screenshot.png", "x.png", "png", photo)
    assert "image screenshot" not in d and "image photo" in d
    jpg = ExtractResult(mime="image/jpeg", is_image=True, metadata={"width": 1920, "height": 1080})
    assert "screenshot" not in build_digest("x.jpg", "x.jpg", "jpg", jpg)


def test_digest_bundle_and_code() -> None:
    r = ExtractResult(
        mime="inode/directory", text="Project: cool", metadata={"bundle_kind": "node"}
    )
    d = build_digest("cool-app", "Desktop/cool-app", "", r)
    assert "folder bundle project" in d and "project type: node" in d
    c = ExtractResult(mime="text/x-python", text="def f(): pass", metadata={"language": "python"})
    assert "source code python" in build_digest("m.py", "m.py", "py", c)
