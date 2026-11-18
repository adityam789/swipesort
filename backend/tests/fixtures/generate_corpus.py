"""Synthetic messy-folder corpus with ground truth (SPEC §15).

    uv run python tests/fixtures/generate_corpus.py OUT_DIR --n 300 --seed 0 [--nested] [--hard]

Writes OUT_DIR/input (the mess), OUT_DIR/output (pre-seeded category tree with a few
examples per category) and OUT_DIR/manifest.json (ground truth). Fully deterministic
for a given (seed, n, nested, hard) on a given set of installed libraries.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import random
import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

try:  # optional: real .docx / .pptx when the libs happen to be installed
    import docx as _docx  # type: ignore[import-not-found,unused-ignore]
except ImportError:
    _docx = None
try:
    import pptx as _pptx  # type: ignore[import-not-found,unused-ignore]
except ImportError:
    _pptx = None

# --------------------------------------------------------------------------- manifest


class Entry(BaseModel):
    rel_path: str  # relative to input/ (or output/ for examples), posix
    category: str  # output-relative folder path, posix
    kind: str
    dup_of: str | None = None
    near_dup_of: str | None = None
    version_of: str | None = None
    misleading_name: bool = False
    generic_name: bool = False  # hard mode: "document (3).pdf", "scan0001.pdf", "IMG_0001.png"
    misleading_dir: bool = False  # hard mode: lives in an input folder themed for another category
    # nested mode only: the deepest ancestor folder a careful human is certain about. `category`
    # still holds one plausible leaf below it. None means the full `category` path is determinable.
    ancestor_only: str | None = None


class Manifest(BaseModel):
    seed: int
    n: int
    nested: bool
    hard: bool = False
    categories: list[str] = Field(default_factory=list)
    entries: list[Entry] = Field(default_factory=list)  # input files
    examples: list[Entry] = Field(default_factory=list)  # pre-seeded output/ files


# --------------------------------------------------------------------------- categories

BASE_CATEGORIES = [
    "Finance/Invoices",
    "Finance/Receipts",
    "Finance/Taxes/2025",
    "Work/Resumes",
    "School/Lectures",
    "Photos/2024",
    "Photos/Screenshots",
    "Code",
]

# kind -> (category, weight)
KINDS: dict[str, tuple[str, float]] = {
    "invoice": ("Finance/Invoices", 17),
    "receipt": ("Finance/Receipts", 12),
    "tax_form": ("Finance/Taxes/2025", 6),
    "resume": ("Work/Resumes", 5),
    "lecture_notes": ("School/Lectures", 9),
    "lecture_slides": ("School/Lectures", 5),
    "photo": ("Photos/2024", 22),
    "screenshot": ("Photos/Screenshots", 10),
    "code_project": ("Code", 6),
}
DOC_KINDS = {
    "invoice", "receipt", "tax_form", "resume", "lecture_notes", "lecture_slides",
    "onboarding_doc", "meeting_notes",
}
IMAGE_KINDS = {"photo", "screenshot"}

# --------------------------------------------------------------------------- content pools

VENDORS = [
    "Acme Corp", "Globex Ltd", "Initech", "Umbrella Supplies", "Hooli Cloud", "Stark Industries",
    "Wayne Logistics", "Soylent Foods", "Vandelay Imports", "Cyberdyne Systems",
    "Wonka Confections", "Pied Piper Hosting", "Oscorp Labs", "Massive Dynamic",
]
SERVICES = [
    "Consulting services", "Website design", "Cloud hosting (monthly)", "Software license",
    "Data migration", "Logo design", "Technical support", "Copywriting", "Photography session",
    "API integration", "Training workshop", "Security audit", "Equipment rental",
]
STORES = [
    ("Trader Joe's", ["Bananas", "Almond milk", "Sourdough bread", "Dark chocolate", "Eggs"]),
    ("Whole Foods Market", ["Organic apples", "Kale", "Oat milk", "Salmon fillet", "Hummus"]),
    ("Target", ["Notebook", "Paper towels", "Phone charger", "Shampoo", "Desk lamp"]),
    ("Walgreens", ["Ibuprofen", "Toothpaste", "Vitamin D", "Bandages", "Sunscreen"]),
    ("Home Depot", ["Wood screws", "Paint roller", "LED bulbs", "Duct tape", "Extension cord"]),
    ("Blue Bottle Coffee", ["Latte", "Cortado", "Croissant", "Cold brew", "Beans 12oz"]),
    ("Best Buy", ["HDMI cable", "USB-C hub", "Mouse", "Keyboard", "SD card"]),
    ("Costco Wholesale", ["Rotisserie chicken", "Olive oil", "Paper towels", "Coffee beans"]),
]
STREETS = ["Main St", "Oak Ave", "Market St", "Elm St", "Broadway", "Mission St", "Pine Rd"]
CITIES = ["Springfield", "Riverton", "Fairview", "Georgetown", "Madison", "Clinton"]
FIRST = ["Alex", "Jordan", "Sam", "Taylor", "Priya", "Maria", "Chen", "Omar", "Elena", "Noah"]
LAST = ["Johnson", "Patel", "Nguyen", "Garcia", "Kim", "Okafor", "Silva", "Brown", "Cohen"]
EMPLOYERS = ["Northwind Traders", "Contoso", "Fabrikam", "Tailspin Toys", "Litware", "Adventure Works"]
JOBS = [
    "Software Engineer", "Data Analyst", "Product Manager", "Backend Developer",
    "Machine Learning Engineer", "UX Designer", "DevOps Engineer",
]
SKILLS = [
    "Python", "TypeScript", "SQL", "React", "Docker", "Kubernetes", "AWS", "PostgreSQL",
    "Go", "Rust", "Figma", "Pandas", "FastAPI", "Terraform", "Git",
]
COURSES = {
    "CS 101": ("Algorithms", ["binary search", "big-O notation", "merge sort", "hash tables",
                              "recursion", "graph traversal", "dynamic programming"]),
    "PHYS 201": ("Thermodynamics", ["entropy", "heat engines", "the second law", "ideal gas",
                                    "Carnot cycle", "enthalpy", "phase transitions"]),
    "ECON 110": ("Macroeconomics", ["inflation", "GDP", "fiscal policy", "monetary policy",
                                    "unemployment", "aggregate demand", "the Phillips curve"]),
    "BIO 150": ("Cell Biology", ["mitochondria", "the cell cycle", "DNA replication",
                                 "protein folding", "membrane transport", "meiosis"]),
    "HIST 220": ("Modern History", ["the industrial revolution", "world war I", "the cold war",
                                    "decolonization", "the printing press", "urbanization"]),
}
ONBOARDING = [
    "Benefits enrollment checklist", "Laptop and access request form", "Security training guide",
    "Direct deposit authorization", "Team handbook", "Payments platform overview",
    "Compliance acknowledgment", "First week schedule",
]
MEETINGS = [
    "Sprint planning", "Payments roadmap sync", "Incident review", "1:1 with manager",
    "Quarterly business review", "Design critique",
]
SCREEN_TEXT = [
    "Settings", "Preferences", "Error: connection refused", "Build succeeded", "Inbox (12)",
    "npm install", "Compose", "Dashboard", "Sign in", "Update available", "Search results",
    "Terminal - zsh", "pytest: 42 passed", "Order confirmation", "Calendar",
]
PHOTO_CAMERAS = [
    ("Canon", "Canon EOS R6"), ("Apple", "iPhone 14 Pro"), ("SONY", "ILCE-7M3"),
    ("Google", "Pixel 8"), ("NIKON CORPORATION", "NIKON Z 6"),
]
PROJECT_NAMES = [
    "todo-app", "weather-cli", "portfolio-site", "chat-server", "budget-tracker", "blog-engine",
    "image-resizer", "recipe-api", "snake-game", "markdown-notes", "url-shortener", "log-parser",
]
GENERIC_STEMS = [
    "IMG_2231", "scan0042", "document", "Untitled", "New Document", "download", "file (3)",
    "Scan 2025-02-11", "attachment", "doc_final_REAL",
]
CROSS_STEMS = {
    "invoice": ["Receipt", "notes", "lecture_week3"],
    "receipt": ["Invoice_scan", "tax_stuff", "Resume_old"],
    "tax_form": ["bank statement", "random", "Invoice_final"],
    "resume": ["lecture_notes_week3", "cover_letter?", "taxes_2025"],
    "lecture_notes": ["Resume_draft", "invoice", "todo"],
    "lecture_slides": ["meeting_deck", "quarterly_report", "presentation1"],
    "photo": ["Taxes_2025", "lecture_slides", "invoice_scan", "Resume"],
    "screenshot": ["vacation_photo", "tax_return", "resume_draft"],
    "onboarding_doc": ["lecture_notes", "invoice", "Untitled"],
    "meeting_notes": ["recipe", "todo", "random_thoughts"],
}

# --------------------------------------------------------------------------- helpers


def _date(rng: random.Random, year: int) -> dt.date:
    return dt.date(year, 1, 1) + dt.timedelta(days=rng.randrange(365))


def _money(cents: int) -> str:
    return f"${cents // 100:,}.{cents % 100:02d}"


def _person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def _address(rng: random.Random) -> str:
    return f"{rng.randint(10, 9999)} {rng.choice(STREETS)}, {rng.choice(CITIES)}"


FIXED_TS = dt.datetime(2025, 1, 1, 12, 0, 0)

# --------------------------------------------------------------------------- renderers


def render_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1, pageCompression=0)
    c.setTitle(lines[0] if lines else "")
    _, height = letter
    y = height - 60
    for i, line in enumerate(lines):
        if y < 60:
            c.showPage()
            y = height - 60
        if i == 0:
            c.setFont("Helvetica-Bold", 16)
            y -= 6
        else:
            c.setFont("Helvetica", 10)
        c.drawString(56, y, line)
        y -= 22 if i == 0 else 14
    c.save()
    return buf.getvalue()


def render_txt(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def render_md(lines: list[str]) -> bytes:
    body = "\n\n".join(lines[1:])
    return f"# {lines[0]}\n\n{body}\n".encode()


def _stable_zip(data: bytes) -> bytes:
    """Rewrite an OOXML zip with fixed member timestamps (the libs stamp wall-clock time)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=(2025, 1, 1, 12, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            dst.writestr(zi, src.read(info.filename))
    return out.getvalue()


def render_docx(lines: list[str]) -> bytes:  # only used when python-docx is importable
    assert _docx is not None
    d = _docx.Document()
    d.core_properties.created = FIXED_TS
    d.core_properties.modified = FIXED_TS
    d.core_properties.author = "corpus"
    d.add_heading(lines[0], level=1)
    for line in lines[1:]:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return _stable_zip(buf.getvalue())


def render_pptx(lines: list[str]) -> bytes:  # only used when python-pptx is importable
    assert _pptx is not None
    p = _pptx.Presentation()
    p.core_properties.created = FIXED_TS
    p.core_properties.modified = FIXED_TS
    p.core_properties.author = "corpus"
    s = p.slides.add_slide(p.slide_layouts[0])
    s.shapes.title.text = lines[0]
    body = lines[1:]
    for i in range(0, len(body), 4):
        chunk = body[i : i + 4]
        sl = p.slides.add_slide(p.slide_layouts[1])
        sl.shapes.title.text = chunk[0]
        sl.placeholders[1].text = "\n".join(chunk[1:]) or chunk[0]
    buf = io.BytesIO()
    p.save(buf)
    return _stable_zip(buf.getvalue())


RENDERERS: dict[str, Callable[[list[str]], bytes]] = {
    "pdf": render_pdf, "txt": render_txt, "md": render_md,
    "docx": render_docx, "pptx": render_pptx,
}

# --------------------------------------------------------------------------- generators


@dataclass
class Made:
    kind: str
    stem: str
    ext: str
    data: bytes = b""
    files: dict[str, bytes] | None = None  # bundle folders (ext == "")
    lines: list[str] | None = None  # editable text, for making versions
    sparse: bool = False  # hard mode: near-empty / image-only document
    # nested mode: ground-truth leaf, the ancestor-only marker, and a leaf-aware one-line version
    category: str = ""
    ancestor_only: str = ""
    one_liner: str = ""

    @property
    def is_bundle(self) -> bool:
        return self.files is not None


def _doc(kind: str, stem: str, ext: str, lines: list[str]) -> Made:
    return Made(kind, stem, ext, RENDERERS[ext](lines), lines=lines)


def gen_invoice(rng: random.Random) -> Made:
    vendor, client = rng.choice(VENDORS), rng.choice(VENDORS)
    num = f"INV-2025-{rng.randint(100, 9999):04d}"
    d = _date(rng, 2025)
    lines = [
        vendor, "INVOICE", f"Invoice number: {num}", f"Invoice date: {d.isoformat()}",
        f"Due date: {(d + dt.timedelta(days=30)).isoformat()}", f"Bill to: {client}",
        _address(rng), "Description | Qty | Unit price | Amount",
    ]
    sub = 0
    for svc in rng.sample(SERVICES, rng.randint(3, 6)):
        qty, unit = rng.randint(1, 20), rng.randint(2000, 45000)
        sub += qty * unit
        lines.append(f"{svc} | {qty} | {_money(unit)} | {_money(qty * unit)}")
    tax = sub * 8 // 100
    lines += [
        f"Subtotal: {_money(sub)}", f"Sales tax (8%): {_money(tax)}",
        f"Total due: {_money(sub + tax)}", "Payment terms: Net 30",
        f"Please remit payment to {vendor}, {_address(rng)}",
        "Thank you for your business.",
    ]
    stem = f"Invoice_{vendor.split()[0]}_{num[-4:]}"
    return _doc("invoice", stem, "pdf", lines)


def gen_receipt(rng: random.Random) -> Made:
    store, items = rng.choice(STORES)
    d = _date(rng, 2025)
    lines = [
        store, _address(rng), f"Date: {d.isoformat()} {rng.randint(8, 21):02d}:{rng.randint(0, 59):02d}",
        f"Receipt #{rng.randint(10000, 99999)}", "-" * 32,
    ]
    sub = 0
    for it in rng.sample(items, rng.randint(2, min(5, len(items)))):
        p = rng.randint(199, 4999)
        sub += p
        lines.append(f"{it}   {_money(p)}")
    tax = sub * 7 // 100
    lines += [
        "-" * 32, f"Subtotal {_money(sub)}", f"Tax {_money(tax)}", f"TOTAL {_money(sub + tax)}",
        f"Paid by VISA ending {rng.randint(1000, 9999)}", "Thank you for shopping with us!",
    ]
    return _doc("receipt", f"Receipt_{store.split()[0]}_{d.strftime('%b%d')}", "pdf", lines)


def gen_tax_form(rng: random.Random) -> Made:
    employer, who = rng.choice(EMPLOYERS), _person(rng)
    form = rng.choice(["W-2", "1099-INT", "1099-NEC", "1040"])
    ssn = f"XXX-XX-{rng.randint(1000, 9999)}"
    lines = [f"Form {form} {'Wage and Tax Statement' if form == 'W-2' else 'Tax Year'} 2025",
             "Department of the Treasury - Internal Revenue Service", "Tax year: 2025",
             f"Recipient: {who}", f"SSN: {ssn}", _address(rng)]
    if form == "W-2":
        lines += [f"Employer: {employer}", f"Employer EIN: {rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",
                  f"Box 1 Wages, tips, other compensation: {_money(rng.randint(4_000_000, 14_000_000))}",
                  f"Box 2 Federal income tax withheld: {_money(rng.randint(400_000, 2_000_000))}",
                  f"Box 3 Social security wages: {_money(rng.randint(4_000_000, 14_000_000))}",
                  f"Box 17 State income tax: {_money(rng.randint(100_000, 800_000))}"]
    elif form.startswith("1099"):
        lines += [f"Payer: {employer}", f"Box 1 Income: {_money(rng.randint(50_000, 9_000_000))}",
                  f"Federal income tax withheld: {_money(rng.randint(0, 200_000))}"]
    else:
        lines += ["Filing status: Single", f"Line 1a Wages: {_money(rng.randint(4_000_000, 14_000_000))}",
                  f"Line 11 Adjusted gross income: {_money(rng.randint(4_000_000, 14_000_000))}",
                  f"Line 24 Total tax: {_money(rng.randint(400_000, 2_000_000))}",
                  "Standard deduction claimed", "Sign here: ____________"]
    lines.append("Keep this copy for your records.")
    return _doc("tax_form", f"{form}_2025_{employer.split()[0]}", "pdf", lines)


def gen_resume(rng: random.Random) -> Made:
    who = _person(rng)
    lines = [who, f"{who.split()[0].lower()}@example.com | {_address(rng)}",
             (f"Professional summary: {rng.choice(JOBS)} with {rng.randint(2, 12)} years of experience "
             f"building reliable products.")]
    lines.append("Experience")
    for _ in range(rng.randint(2, 3)):
        lines += [f"{rng.choice(JOBS)}, {rng.choice(EMPLOYERS)} ({rng.randint(2012, 2021)}-{rng.randint(2022, 2025)})",
                  f"- Led development of {rng.choice(SERVICES).lower()} used by {rng.randint(2, 90)}k users",
                  f"- Improved {rng.choice(['latency', 'reliability', 'onboarding', 'throughput'])} by {rng.randint(10, 70)}%"]
    lines += ["Education", f"B.S. Computer Science, University of {rng.choice(CITIES)}, {rng.randint(2008, 2022)}",
              "Skills: " + ", ".join(rng.sample(SKILLS, 6))]
    ext = rng.choice(["pdf", "pdf", "docx" if _docx else "md"])
    return _doc("resume", f"{who.replace(' ', '_')}_Resume", ext, lines)


def _sentences(rng: random.Random, topic: str, terms: list[str], n: int) -> list[str]:
    tpl = [
        "{a} is a central idea in {t}; it connects directly to {b}.",
        "Remember: {a} matters because it explains {b}.",
        "Example: applying {a} to a small case shows how {b} behaves.",
        "The exam will likely ask you to compare {a} and {b}.",
        "Definition: {a} describes how a system relates to {b}.",
        "Reading for next week: chapter on {a}, skim the section on {b}.",
    ]
    out = []
    for _ in range(n):
        a, b = rng.sample(terms, 2)
        out.append(rng.choice(tpl).format(a=a.capitalize() if False else a, b=b, t=topic))
    return out


def gen_lecture_notes(rng: random.Random) -> Made:
    course = rng.choice(sorted(COURSES))
    topic, terms = COURSES[course]
    wk = rng.randint(1, 14)
    lines = [f"{course} - {topic} - Lecture {wk} notes", f"Date: {_date(rng, 2025).isoformat()}",
             f"Topics today: {', '.join(rng.sample(terms, 3))}"]
    lines += _sentences(rng, topic, terms, rng.randint(10, 16))
    ext = rng.choice(["md", "txt", "md"])
    return _doc("lecture_notes", f"{course.replace(' ', '')}_week{wk}_notes", ext, lines)


def gen_lecture_slides(rng: random.Random) -> Made:
    course = rng.choice(sorted(COURSES))
    topic, terms = COURSES[course]
    wk = rng.randint(1, 14)
    lines = [f"{course}: {topic} - Lecture {wk}"]
    for t in rng.sample(terms, 4):
        lines += [f"Slide: {t.capitalize()}", f"- Why {t} matters in {topic}",
                  f"- Worked example involving {t}", f"- Common mistakes with {t}"]
    ext = "pptx" if _pptx else "md"
    return _doc("lecture_slides", f"{course.replace(' ', '')}_L{wk}_slides", ext, lines)


def gen_onboarding(rng: random.Random) -> Made:
    title = rng.choice(ONBOARDING)
    lines = [f"PayPal onboarding: {title}", f"Employee: {_person(rng)}", f"Start date: {_date(rng, 2025).isoformat()}",
             "Welcome to the Payments team."]
    for i in range(rng.randint(6, 10)):
        lines.append(f"Step {i + 1}: {rng.choice(['Complete', 'Review', 'Sign', 'Submit', 'Schedule'])} "
                     f"{rng.choice(['your I-9', 'the security module', 'benefits selection', 'manager intro', 'tool access'])} "
                     f"by {_date(rng, 2025).isoformat()}")
    return _doc("onboarding_doc", "PayPal_" + title.replace(" ", "_"), rng.choice(["pdf", "md"]), lines)


def gen_meeting(rng: random.Random) -> Made:
    title = rng.choice(MEETINGS)
    lines = [f"{title} - meeting notes", f"Date: {_date(rng, 2025).isoformat()}",
             f"Attendees: {_person(rng)}, {_person(rng)}, {_person(rng)}"]
    for _ in range(rng.randint(5, 9)):
        lines.append(f"- {rng.choice(['Action', 'Decision', 'Risk', 'Follow-up'])}: "
                     f"{rng.choice(['ship', 'review', 'defer', 'escalate'])} the "
                     f"{rng.choice(['ledger migration', 'fraud rules', 'checkout latency fix', 'API v3'])} "
                     f"({rng.choice(FIRST)} owns)")
    return _doc("meeting_notes", title.replace(" ", "_").replace(":", ""), rng.choice(["md", "txt"]), lines)


def _base_image(rng: random.Random, w: int, h: int) -> Image.Image:
    tiny = Image.new("RGB", (3, 3))
    for x in range(3):
        for y in range(3):
            tiny.putpixel((x, y), (rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    return tiny.resize((w, h), Image.Resampling.BICUBIC)


def gen_photo(rng: random.Random) -> Made:
    img = _base_image(rng, 640, 480)
    d = ImageDraw.Draw(img)
    for _ in range(rng.randint(4, 9)):
        x, y = rng.randrange(600), rng.randrange(440)
        r = rng.randint(20, 120)
        col = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        d.ellipse((x, y, x + r, y + r), fill=col)
    make, model = rng.choice(PHOTO_CAMERAS)
    when = dt.datetime(2024, 1, 1) + dt.timedelta(seconds=rng.randrange(366 * 86400))
    stamp = when.strftime("%Y:%m:%d %H:%M:%S")
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0110] = model
    exif[0x0132] = stamp
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = stamp
    sub[0x9004] = stamp
    sub[0xA434] = f"{model} lens {rng.choice([24, 35, 50, 85])}mm"
    buf = io.BytesIO()
    if rng.random() < 0.85:
        img.save(buf, "JPEG", quality=85, exif=exif)
        ext = rng.choice(["jpg", "jpg", "JPG", "jpeg"])
    else:
        img.save(buf, "PNG", exif=exif)
        ext = "png"
    if make == "Apple":
        stem = f"IMG_{rng.randint(1000, 9999)}"
    elif make == "Google":
        stem = f"PXL_{when.strftime('%Y%m%d_%H%M%S')}{rng.randint(100, 999)}"
    else:
        stem = f"DSC_{rng.randint(1000, 9999)}"
    return Made("photo", stem, ext, buf.getvalue())


def gen_screenshot(rng: random.Random) -> Made:
    w, h = 800, 500
    img = Image.new("RGB", (w, h), (rng.randint(225, 255),) * 3)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 28), fill=(60, 60, 70))
    d.text((12, 8), rng.choice(SCREEN_TEXT), fill=(240, 240, 240))
    d.rectangle((0, 28, 160, h), fill=(rng.randint(180, 230),) * 3)
    for i in range(rng.randint(4, 8)):
        d.text((14, 44 + i * 26), rng.choice(SCREEN_TEXT), fill=(30, 30, 30))
    for _ in range(rng.randint(2, 5)):
        x, y = rng.randint(180, 520), rng.randint(50, 380)
        bw, bh = rng.randint(80, 240), rng.randint(30, 90)
        d.rectangle((x, y, x + bw, y + bh), outline=(40, 90, 200), width=2,
                    fill=(rng.randint(200, 255), rng.randint(200, 255), 255))
        d.text((x + 8, y + 8), rng.choice(SCREEN_TEXT), fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    t = dt.datetime(2024, 1, 1) + dt.timedelta(seconds=rng.randrange(366 * 86400))
    if rng.random() < 0.7:
        stem = f"Screenshot {t:%Y-%m-%d} at {t:%I.%M.%S %p}"
    else:
        stem = f"Screenshot {t:%Y-%m-%d %H%M%S}"
    return Made("screenshot", stem, "png", buf.getvalue())


def gen_code_project(rng: random.Random) -> Made:
    name = rng.choice(PROJECT_NAMES)
    token = rng.randint(100, 999)
    files: dict[str, bytes] = {}
    if rng.random() < 0.5:  # node project
        files["package.json"] = (
            f'{{\n  "name": "{name}",\n  "version": "1.{rng.randint(0, 9)}.0",\n  "main": "index.js",\n'
            f'  "scripts": {{"start": "node index.js"}}\n}}\n'
        ).encode()
        files["index.js"] = (
            f"const {{ run }} = require('./src/{name.split('-')[0]}');\n"
            f"// {name}: entry point {token}\nrun(process.argv.slice(2));\n"
        ).encode()
        files[f"src/{name.split('-')[0]}.js"] = (
            f"function run(args) {{\n  console.log('{name}', args.length, {token});\n}}\n"
            f"module.exports = {{ run }};\n"
        ).encode()
    else:  # git-tracked python project
        files[".git/HEAD"] = b"ref: refs/heads/main\n"
        files[".git/config"] = f"[core]\n\trepositoryformatversion = 0\n[remote \"origin\"]\n\turl = https://example.com/{name}.git\n".encode()
        files["main.py"] = (
            f'"""{name} entry point."""\n\n\ndef main() -> None:\n    print("{name} {token}")\n\n\n'
            f'if __name__ == "__main__":\n    main()\n'
        ).encode()
        files["utils.py"] = (
            f"def helper_{token}(x: int) -> int:\n    return x * {rng.randint(2, 9)}\n"
        ).encode()
    files["README.md"] = f"# {name}\n\nA small side project ({token}).\n".encode()
    suffix = rng.choice(["", "", "-main", "-master", " copy"])
    return Made("code_project", f"{name}{suffix}", "", files=files)


GENERATORS: dict[str, Callable[[random.Random], Made]] = {
    "invoice": gen_invoice, "receipt": gen_receipt, "tax_form": gen_tax_form,
    "resume": gen_resume, "lecture_notes": gen_lecture_notes, "lecture_slides": gen_lecture_slides,
    "photo": gen_photo, "screenshot": gen_screenshot, "code_project": gen_code_project,
    "onboarding_doc": gen_onboarding, "meeting_notes": gen_meeting,
}

# --------------------------------------------------------------------------- hard mode (--hard)
# Messier, more realistic content: several layouts per kind, shared vocabulary across categories,
# sparse/scanned files and image-only PDFs. Every file stays classifiable by a careful human.

HARD_EMPLOYERS = [*EMPLOYERS, "PayPal", "Stripe Labs"]
RESTAURANTS = [
    ("Luigi's Trattoria", ["Margherita pizza", "Linguine", "Tiramisu", "House salad", "Espresso"]),
    ("Golden Dragon", ["Kung pao chicken", "Fried rice", "Spring rolls", "Hot and sour soup"]),
    ("Blue Fin Sushi", ["Salmon nigiri", "Dragon roll", "Miso soup", "Edamame", "Green tea"]),
    ("Maple Diner", ["Pancake stack", "Club sandwich", "Milkshake", "Fries", "Coffee"]),
]
# Input sub-folders people really use, per kind (truthful), reused as misleading for other kinds.
DIR_THEMES: dict[str, list[str]] = {
    "invoice": ["Downloads/invoices", "Bills and invoices"],
    "receipt": ["Downloads/receipts", "Receipts 2025"],
    "tax_form": ["Downloads/taxes", "Tax docs"],
    "resume": ["Job hunt", "Downloads/resume stuff"],
    "lecture_notes": ["Downloads/school", "Classes"],
    "lecture_slides": ["Downloads/school", "Classes"],
    "photo": ["Camera roll", "Trip pics"],
    "screenshot": ["Screenshots", "Desktop pics"],
    "onboarding_doc": ["New job", "Downloads/work"],
    "meeting_notes": ["Downloads/work", "Work stuff"],
}


def _generic_stem(rng: random.Random, kind: str) -> str:
    if kind in IMAGE_KINDS:
        return rng.choice([f"IMG_{rng.randint(1, 300):04d}", f"image ({rng.randint(1, 9)})",
                           "download", f"IMG_{rng.randint(1, 300):04d}"])
    return rng.choice([f"document ({rng.randint(1, 9)})", f"scan{rng.randint(1, 60):04d}",
                       "untitled", "Untitled document", "New Document", "download",
                       f"file ({rng.randint(1, 9)})", "attachment",
                       f"Scan {_date(rng, 2025).isoformat()}"])


def _line_items(rng: random.Random, lo: int = 3, hi: int = 6) -> list[tuple[str, int, int]]:
    return [(s, rng.randint(1, 20), rng.randint(2000, 45000))
            for s in rng.sample(SERVICES, rng.randint(lo, hi))]


def gen_invoice_hard(rng: random.Random) -> Made:
    v = rng.randrange(6)
    if v == 0:
        return gen_invoice(rng)
    vendor, client = rng.sample(VENDORS, 2)
    vtag = vendor.split()[0]
    d = _date(rng, 2025)
    due = d + dt.timedelta(days=rng.choice([14, 21, 30, 45]))
    items = _line_items(rng)
    sub = sum(q * u for _, q, u in items)
    tax = sub * rng.choice([0, 5, 8]) // 100
    total = sub + tax
    ref = rng.randint(1000, 99999)
    mon = d.strftime("%b%Y")
    if v == 1:
        lines = [f"{vendor} - Statement of charges", f"Account holder: {client}",
                 f"Statement no. ST-{ref}", f"Period ending: {d.isoformat()}",
                 "Item | Qty | Rate | Charge"]
        lines += [f"{s} | {q} | {_money(u)} | {_money(q * u)}" for s, q, u in items]
        lines += [f"Tax: {_money(tax)}", f"Amount due: {_money(total)}", f"Pay by {due.isoformat()}",
                  "Balances unpaid after the due date accrue 1.5% interest per month."]
        return _doc("invoice", rng.choice([f"{vtag}_statement_{mon}", f"{vtag}_{mon}"]), "pdf", lines)
    if v == 2:
        lines = ["BILL", vendor, f"Bill no. B{ref}", f"Customer: {client}", _address(rng),
                 f"Issued: {d.isoformat()}"]
        lines += [f"{s} - {_money(q * u)}" for s, q, u in items]
        lines += [f"Balance owed: {_money(total)}",
                  f"Please pay within {(due - d).days} days of the issue date.",
                  "Remit by ACH or cheque payable to " + vendor + "."]
        return _doc("invoice", rng.choice([f"{vtag}_bill_{ref}", f"{vtag}_{mon}"]), "pdf", lines)
    if v == 3:
        first = rng.choice(FIRST)
        lines = [f"Subject: Payment due for work completed - {vendor}", f"Hi {first},",
                 (f"Here is what you owe for {items[0][0].lower()} and {items[1][0].lower()} "
                 f"delivered this month.")]
        lines += [f"  {s}: {_money(q * u)}" for s, q, u in items]
        lines += [f"Total owed: {_money(total)}", f"Please wire the money by {due.isoformat()}.",
                  f"Bank details: {vendor} operating account, routing 02100{rng.randint(1000, 9999)}",
                  "Thanks, accounts team"]
        return _doc("invoice", f"{vtag}_email_{mon}", rng.choice(["txt", "md"]), lines)
    if v == 4:
        lines = [f"{vendor} - Request for payment", f"To: {client}", f"Reference: PR-{ref}",
                 f"Date: {d.isoformat()}", "Work completed to date:"]
        lines += [f"{s} x{q} @ {_money(u)}" for s, q, u in items]
        lines += [f"Amount payable: {_money(total)}", f"Payable on or before {due.isoformat()}",
                  "Overdue amounts may be referred to collections."]
        ext = "docx" if _docx and rng.random() < 0.4 else "pdf"
        return _doc("invoice", f"{vtag}_payment_request", ext, lines)
    lines = [f"{vendor} - Services rendered", f"Client: {client}", f"Period: {d:%B %Y}",
             "Task | Hours | Rate"]
    rate = rng.choice([85, 95, 120, 150])
    hours = [rng.randint(2, 30) for _ in items]
    lines += [f"{s} | {h} h | ${rate}/h" for (s, _, _), h in zip(items, hours, strict=True)]
    lines += [f"Hours billed: {sum(hours)}", f"Balance due: {_money(sum(hours) * rate * 100)}",
              f"Net {(due - d).days} terms. Make payment to {vendor}."]
    return _doc("invoice", f"{vtag}_hours_{mon}", "pdf", lines)


def gen_receipt_hard(rng: random.Random) -> Made:
    v = rng.randrange(6)
    if v == 0:
        return gen_receipt(rng)
    d = _date(rng, 2025)
    card = f"VISA ending {rng.randint(1000, 9999)}"
    if v == 1:  # service-vendor payment receipt, mentions the invoice it settles
        vendor = rng.choice(VENDORS)
        items = _line_items(rng, 1, 3)
        total = sum(q * u for _, q, u in items)
        lines = [vendor, "PAYMENT RECEIPT", f"Payment received: {d.isoformat()}",
                 f"Re: invoice #INV-2025-{rng.randint(100, 9999):04d}"]
        lines += [f"{s}: {_money(q * u)}" for s, q, u in items]
        lines += [f"Amount paid: {_money(total)}", f"Paid via {rng.choice(['ACH transfer', card, 'wire'])}",
                  "Balance remaining: $0.00", "Thank you for your payment."]
        return _doc("receipt", f"{vendor.split()[0]}_{d:%b%Y}", "pdf", lines)
    if v == 2:  # online order
        store, goods = rng.choice(STORES)
        picked = rng.sample(goods, rng.randint(2, min(4, len(goods))))
        prices = [rng.randint(299, 6999) for _ in picked]
        lines = [f"{store} - Order confirmation", f"Order #{rng.randint(100000, 999999)}",
                 f"Placed on {d.isoformat()}"]
        lines += [f"{g} (qty {rng.randint(1, 3)}): {_money(p)}" for g, p in zip(picked, prices, strict=True)]
        lines += [f"Shipping: {_money(rng.choice([0, 499, 799]))}", f"Total charged to {card}: {_money(sum(prices))}",
                  "Payment status: Paid", "Keep this email as your receipt."]
        return _doc("receipt", f"{store.split()[0]}_order_{d:%b%d}", rng.choice(["pdf", "txt"]), lines)
    if v == 3:  # restaurant
        place, dishes = rng.choice(RESTAURANTS)
        picked = rng.sample(dishes, rng.randint(2, min(4, len(dishes))))
        prices = [rng.randint(450, 3200) for _ in picked]
        sub = sum(prices)
        tip = sub * rng.choice([15, 18, 20]) // 100
        lines = [place, f"Table {rng.randint(1, 30)}   Server: {rng.choice(FIRST)}",
                 f"{d.isoformat()} {rng.randint(11, 22)}:{rng.randint(0, 59):02d}"]
        lines += [f"{g}   {_money(p)}" for g, p in zip(picked, prices, strict=True)]
        lines += [f"Subtotal {_money(sub)}", f"Tip {_money(tip)}", f"Total {_money(sub + tip)}",
                  f"Paid {card}", "Thanks for dining with us"]
        return _doc("receipt", f"{place.split()[0]}_{d:%b%d}", "pdf", lines)
    if v == 4:  # retail receipt that carries an "invoice #" like real ones do
        store, goods = rng.choice(STORES)
        picked = rng.sample(goods, rng.randint(2, min(4, len(goods))))
        prices = [rng.randint(199, 4999) for _ in picked]
        sub = sum(prices)
        lines = [store.upper(), _address(rng), (f"Invoice #: {rng.randint(100000, 999999)}   "
                 f"Cashier: {rng.choice(FIRST)}"), f"{d.isoformat()}"]
        lines += [f"{g} {_money(p)}" for g, p in zip(picked, prices, strict=True)]
        lines += [f"Subtotal {_money(sub)}", f"Total {_money(sub * 107 // 100)}", f"Tendered: {card}",
                  "Customer copy - returns accepted within 30 days with this receipt"]
        return _doc("receipt", f"{store.split()[0]}_{d:%b%d}", "pdf", lines)
    fare = rng.randint(800, 6500)  # ride receipt
    lines = ["CityCab - Trip receipt", f"Trip date: {d.isoformat()}", f"Pickup: {_address(rng)}",
             f"Drop-off: {_address(rng)}", f"Fare: {_money(fare)}", f"Tip: {_money(fare // 6)}",
             f"Total charged: {_money(fare + fare // 6)} to {card}", "Thanks for riding."]
    return _doc("receipt", f"trip_{d:%b%d}", "pdf", lines)


def gen_tax_hard(rng: random.Random) -> Made:
    v = rng.randrange(6)
    if v == 0:
        return gen_tax_form(rng)
    who, d = _person(rng), _date(rng, 2025)
    if v == 1:
        lender = rng.choice(VENDORS)
        lines = ["Form 1098 Mortgage Interest Statement 2025", f"Lender: {lender} Bank",
                 f"Borrower: {who}", f"Box 1 Mortgage interest received: {_money(rng.randint(500_000, 2_200_000))}",
                 f"Box 5 Mortgage insurance premiums: {_money(rng.randint(20_000, 150_000))}",
                 f"Box 6 Points paid on purchase: {_money(rng.randint(0, 300_000))}",
                 "Tax year: 2025", "This is important tax information and is being furnished to the IRS."]
        return _doc("tax_form", rng.choice(["1098_2025", f"{lender.split()[0]}_mortgage_2025"]), "pdf", lines)
    if v == 2:
        lines = [f"2025 tax prep worksheet - {who}", "Deductible expenses paid this year:"]
        for vendor, svc in zip(rng.sample(VENDORS, 5), rng.sample(SERVICES, 5), strict=True):
            lines.append(f"{vendor} - {svc.lower()}: {_money(rng.randint(15_000, 480_000))}")
        lines += [f"Total deductions: {_money(rng.randint(600_000, 2_400_000))}",
                  f"Estimated federal tax owed: {_money(rng.randint(100_000, 1_800_000))}",
                  "Hand to accountant before the filing deadline."]
        return _doc("tax_form", f"tax_prep_{rng.randint(1, 9)}", rng.choice(["md", "txt", "pdf"]), lines)
    if v == 3:
        lines = ["Form 1040-ES Estimated Tax Payment Voucher 2025", "Department of the Treasury - Internal Revenue Service",
                 f"Name: {who}", f"Payment due: {rng.choice(['April 15', 'June 16', 'September 15', 'January 15'])}",
                 f"Amount of estimated tax: {_money(rng.randint(80_000, 900_000))}",
                 "Make check payable to United States Treasury"]
        return _doc("tax_form", f"1040ES_voucher_{rng.randint(1, 4)}", "pdf", lines)
    if v == 4:
        lines = [f"Dear {who.split()[0]},", "Enclosed is a summary of your 2025 federal and state tax returns.",
                 f"Adjusted gross income: {_money(rng.randint(4_000_000, 14_000_000))}",
                 f"Total tax: {_money(rng.randint(400_000, 2_000_000))}",
                 f"Refund expected: {_money(rng.randint(10_000, 400_000))}",
                 "Please review, sign Form 8879 and return it so we can e-file with the IRS.",
                 f"Regards, {rng.choice(LAST)} & Co. CPAs"]
        return _doc("tax_form", f"tax_return_summary_{d:%b}", rng.choice(["pdf", "txt"]), lines)
    lines = ["Schedule C - Profit or Loss From Business (2025)", f"Proprietor: {who}", "Principal business: consulting"]
    for client in rng.sample(VENDORS, 3):
        lines.append(f"Gross receipts from {client}: {_money(rng.randint(200_000, 3_500_000))}")
    lines += [f"Line 28 Total expenses: {_money(rng.randint(100_000, 1_500_000))}",
              f"Line 31 Net profit: {_money(rng.randint(200_000, 5_000_000))}"]
    return _doc("tax_form", f"ScheduleC_{who.split()[1]}", "pdf", lines)


def gen_resume_hard(rng: random.Random) -> Made:
    v = rng.randrange(5)
    if v == 0:
        return gen_resume(rng)
    who = _person(rng)
    first = who.split()[0].lower()
    ext = rng.choice(["pdf", "pdf", "docx" if _docx else "md", "md"])
    if v == 1:
        lines = [f"Curriculum Vitae - {who}", f"Contact: {first}@example.com", "Employment history"]
        for _ in range(rng.randint(2, 4)):
            lines.append(f"{rng.randint(2010, 2020)}-{rng.randint(2021, 2025)}  {rng.choice(HARD_EMPLOYERS)}, "
                         f"{rng.choice(JOBS)}")
        lines += ["Education", f"M.S., University of {rng.choice(CITIES)}",
                  "Certifications: " + ", ".join(rng.sample(SKILLS, 3))]
        return _doc("resume", f"CV_{who.split()[1]}", ext, lines)
    if v == 2:
        lines = [who, f"{rng.choice(JOBS)} at {rng.choice(HARD_EMPLOYERS)}", "About",
                 (f"I build things. {rng.randint(3, 15)} years across {rng.choice(HARD_EMPLOYERS)} and "
                 f"{rng.choice(HARD_EMPLOYERS)}."), "Experience"]
        for _ in range(rng.randint(2, 3)):
            lines += [f"{rng.choice(JOBS)} - {rng.choice(HARD_EMPLOYERS)}",
                      f"  Worked with {', '.join(rng.sample(SKILLS, 3))}"]
        lines += ["Skills: " + " | ".join(rng.sample(SKILLS, 5)), "Open to new roles"]
        return _doc("resume", f"{who.replace(' ', '')}_profile", ext, lines)
    if v == 3:
        lines = [f"{who} - {rng.choice(JOBS)}", f"{first}@example.com", "Work experience"]
        for _ in range(rng.randint(2, 3)):
            lines.append(f"{rng.choice(HARD_EMPLOYERS)} ({rng.randint(2014, 2020)}-{rng.randint(2021, 2025)}): "
                         f"{rng.choice(JOBS)}, {rng.choice(['promoted twice', 'led a team of 5', 'shipped v2'])}")
        lines += ["Languages: English, " + rng.choice(["Spanish", "Hindi", "Mandarin"]), "References available upon request"]
        return _doc("resume", rng.choice([f"{who.split()[1]}_{rng.choice(JOBS).split()[0]}", "my_resume_2025"]), ext, lines)
    lines = [f"RESUME OF {who.upper()}", "Objective: seeking a challenging role where I can grow",
             f"Most recent employer: {rng.choice(HARD_EMPLOYERS)}", "Skills: " + ", ".join(rng.sample(SKILLS, 7)),
             f"Education: B.Sc. {rng.randint(2005, 2022)}", "Hobbies: climbing, chess"]
    return _doc("resume", f"{who.split()[1]}_application", ext, lines)


def gen_lecture_notes_hard(rng: random.Random) -> Made:
    v = rng.randrange(5)
    if v == 0:
        return gen_lecture_notes(rng)
    course = rng.choice(sorted(COURSES))
    topic, terms = COURSES[course]
    wk = rng.randint(1, 14)
    a, b, c = rng.sample(terms, 3)
    ext = rng.choice(["md", "txt", "md", "docx" if _docx else "txt"])
    stem = rng.choice([f"{course.replace(' ', '')}_wk{wk}", f"notes_{wk}", f"{topic.split()[0]}_{wk}"])
    if v == 1:  # Cornell style
        lines = [f"{course} week {wk}", "Cues | Notes", f"{a} | what it is, why it matters in {topic}",
                 f"{b} | contrast with {c}; prof gave two examples", f"{c} | likely on the exam",
                 f"Summary: {a} and {b} both feed into {c}."]
    elif v == 2:  # scrappy outline
        lines = [f"{topic.lower()} - {_date(rng, 2025):%b %d}", f"* {a}: prof spent 20 min on this",
                 f"* {b} (see textbook ch. {rng.randint(2, 12)})", f"  - ties back to {c}",
                 f"TODO before next class: read the section on {a}, do problem set {rng.randint(1, 9)}",
                 f"Q for office hours: why does {b} not apply to {c}?"]
    elif v == 3:  # study-group meeting-shaped
        lines = [f"Study group - {course} {topic}", f"Date: {_date(rng, 2025).isoformat()}",
                 f"Present: {_person(rng)}, {_person(rng)}", "Agenda: go over this week's lecture",
                 f"- Discussed {a} and how it relates to {b}", f"- Still confused about {c}",
                 f"Action items: everyone reviews {a} before the {rng.choice(['quiz', 'midterm'])}"]
    else:  # textbook reading notes
        lines = [f"{topic} - chapter {rng.randint(1, 12)} reading notes", f"Key term: {a}",
                 f"Definition: {a} describes how a system relates to {b}.", f"Key term: {c}",
                 f"Example in the text uses {b}; the lecture added {c}.", "Review questions at end of chapter unanswered"]
    return _doc("lecture_notes", stem, ext, lines)


def gen_lecture_slides_hard(rng: random.Random) -> Made:
    v = rng.randrange(3)
    if v == 0:
        return gen_lecture_slides(rng)
    course = rng.choice(sorted(COURSES))
    topic, terms = COURSES[course]
    wk = rng.randint(1, 14)
    ext = "pptx" if _pptx else "md"
    stem = rng.choice([f"{course.replace(' ', '')}_L{wk}", f"week{wk}", f"{topic.split()[0]}_slides"])
    if v == 1:
        lines = [f"{topic}: week {wk}", "Learning objectives", f"- Explain {terms[0]}",
                 f"- Apply {terms[1]} to a worked problem", "Agenda", "- Recap", f"- {terms[2].capitalize()}", "- Q&A"]
        for t in rng.sample(terms, 3):
            lines += [f"{t.capitalize()}", f"- Key idea behind {t}", f"- Where {t} shows up in {topic}"]
        lines += ["Summary", "Questions?"]
    else:
        lines = [f"{course} midterm review"]
        for t in rng.sample(terms, 4):
            lines += [f"Review: {t}", f"- Be able to define {t}", f"- Practice problem on {t}"]
    return _doc("lecture_slides", stem, ext, lines)


def gen_onboarding_hard(rng: random.Random) -> Made:
    v = rng.randrange(4)
    if v == 0:
        return gen_onboarding(rng)
    who = _person(rng)
    ext = rng.choice(["pdf", "md"])
    if v == 1:
        lines = [f"Welcome to PayPal, {who.split()[0]}!", f"Your manager: {_person(rng)}",
                 "Day one: pick up your laptop and badge from the front desk", "Week one: shadow the Payments team",
                 f"Questions? Ask your buddy, {rng.choice(FIRST)}", "Your first sprint starts the following Monday"]
        stem = "welcome_to_PayPal"
    elif v == 2:
        lines = ["New hire checklist", f"Name: {who}", f"Start date: {_date(rng, 2025).isoformat()}"]
        lines += [f"[ ] {t}" for t in rng.sample(["Set up 2FA", "Sign the NDA", "Enroll in benefits",
                                                     "Complete security training", "Request repo access",
                                                     "Submit direct deposit form"], 5)]
        stem = rng.choice(["checklist", "new_hire_checklist"])
    else:
        lines = ["Policy acknowledgment", (f"I, {who}, confirm that I have read the PayPal code of conduct and "
                 "the acceptable use policy for company systems."), "Signature: ____________",
                 f"Date: {_date(rng, 2025).isoformat()}", "Return to HR within your first five days."]
        stem = "acknowledgment"
    return _doc("onboarding_doc", stem, ext, lines)


def gen_meeting_hard(rng: random.Random) -> Made:
    v = rng.randrange(4)
    if v == 0:
        return gen_meeting(rng)
    title = rng.choice(MEETINGS)
    thing = rng.choice(["ledger migration", "fraud rules", "checkout latency fix", "API v3"])
    ext = rng.choice(["md", "txt"])
    if v == 1:
        lines = [f"Notes - {title}", f"Agenda: status of the {thing}, blockers, next steps",
                 f"Attendees: {_person(rng)}, {_person(rng)}", f"Discussion: {rng.choice(FIRST)} raised concerns about the {thing}",
                 f"Action items: {rng.choice(FIRST)} to update the {thing} plan by Friday", "Next sync: Thursday 10:00"]
    elif v == 2:
        lines = [f"{title} recap", f"- Key takeaway: the {thing} slipped one week",
                 f"- Decision: keep the {thing} in the current quarter", f"- Owner: {rng.choice(FIRST)}",
                 "- Customers affected: merchants on the payments platform", "- Follow-up scheduled"]
    else:
        a, b = _person(rng), _person(rng)
        lines = [f"1:1 {a.split()[0]} / {b.split()[0]}", f"{a.split()[0]}: wants more ownership of the {thing}",
                 f"{b.split()[0]}: feedback on last sprint, promotion timeline", "Goals for next quarter: ship, mentor, document"]
    return _doc("meeting_notes", title.replace(" ", "_").replace(":", "") + rng.choice(["", "_notes", "_recap"]), ext, lines)


def gen_photo_hard(rng: random.Random) -> Made:
    m = gen_photo(rng)
    if rng.random() < 0.4:  # re-encode without EXIF (exported / messaged / edited photos)
        img = Image.open(io.BytesIO(m.data))
        buf = io.BytesIO()
        if m.ext.lower() == "png":
            img.save(buf, "PNG")
        else:
            img.convert("RGB").save(buf, "JPEG", quality=85)
        m = Made("photo", m.stem, m.ext, buf.getvalue())
    return m


def _text_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):
        return ImageFont.load_default()


def gen_screenshot_hard(rng: random.Random) -> Made:
    if rng.random() < 0.4:
        return gen_screenshot(rng)
    # screenshot of a document (invoice, receipt, ...): the text is pixels, not a file
    kind = rng.choice(["invoice", "invoice", "receipt", "resume", "lecture_notes", "tax_form"])
    src = HARD_GENERATORS[kind](rng)
    lines = (src.lines or [src.stem])[:16]
    w, h = 900, 560
    img = Image.new("RGB", (w, h), (rng.randint(225, 250),) * 3)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 30), fill=(60, 60, 70))
    d.text((12, 8), f"{src.stem}.{src.ext} - Preview", fill=(240, 240, 240), font=_text_font(14))
    d.rectangle((120, 44, w - 120, h - 12), fill=(255, 255, 255), outline=(180, 180, 180))
    font = _text_font(15)
    for i, line in enumerate(lines):
        d.text((140, 56 + i * 29), line[:70], fill=(20, 20, 20), font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    t = dt.datetime(2024, 1, 1) + dt.timedelta(seconds=rng.randrange(366 * 86400))
    return Made("screenshot", f"Screenshot {t:%Y-%m-%d} at {t:%I.%M.%S %p}", "png", buf.getvalue())


HARD_GENERATORS: dict[str, Callable[[random.Random], Made]] = {
    "invoice": gen_invoice_hard, "receipt": gen_receipt_hard, "tax_form": gen_tax_hard,
    "resume": gen_resume_hard, "lecture_notes": gen_lecture_notes_hard,
    "lecture_slides": gen_lecture_slides_hard, "photo": gen_photo_hard,
    "screenshot": gen_screenshot_hard, "code_project": gen_code_project,
    "onboarding_doc": gen_onboarding_hard, "meeting_notes": gen_meeting_hard,
}


def _one_liner(rng: random.Random, kind: str) -> str:
    d = _date(rng, 2025).isoformat()
    course = rng.choice(sorted(COURSES))
    topic, terms = COURSES[course]
    return {
        "invoice": f"{rng.choice(VENDORS)} - payment due {d} - {_money(rng.randint(20000, 900000))} owed",
        "receipt": f"{rng.choice(STORES)[0]} - paid {_money(rng.randint(300, 20000))} on {d} - thank you",
        "tax_form": f"Form {rng.choice(['W-2', '1099-NEC', '1040'])} tax year 2025 - {_money(rng.randint(500_000, 9_000_000))}",
        "resume": f"{_person(rng)} - {rng.choice(JOBS)} - {rng.choice(FIRST).lower()}@example.com - resume",
        "lecture_notes": f"{course} {topic}: {', '.join(rng.sample(terms, 3))} - lecture notes",
        "lecture_slides": f"{course} {topic} - lecture slides",
        "onboarding_doc": "PayPal new hire: laptop pickup Monday, badge at front desk",
        "meeting_notes": f"Team meeting notes {d} - action: {rng.choice(['ship', 'review', 'defer'])} the API v3",
    }[kind]


def make_sparse(rng: random.Random, m: Made) -> Made:
    """Replace a document by a near-empty version: one line of text, or an image-only scan."""
    assert m.lines is not None
    if rng.random() < 0.5:
        sm = _doc(m.kind, m.stem, m.ext, [m.one_liner or _one_liner(rng, m.kind)])
    else:
        sm = Made(m.kind, m.stem, "pdf", render_scan_pdf(rng, m.lines))
    sm.lines = None  # sparse docs are not re-versioned
    sm.category, sm.ancestor_only = m.category, m.ancestor_only
    sm.sparse = True
    return sm


def render_scan_pdf(rng: random.Random, lines: list[str]) -> bytes:
    """A PDF whose only content is a (slightly skewed) raster page: no text layer."""
    w, h = 850, 1100
    page = Image.new("L", (w, h), 250)
    d = ImageDraw.Draw(page)
    f_title, f_body = _text_font(26), _text_font(18)
    y = 70
    for i, line in enumerate(lines[:28]):
        d.text((60, y), line[:80], fill=20, font=f_title if i == 0 else f_body)
        y += 44 if i == 0 else 30
    page = page.rotate(rng.uniform(-1.2, 1.2), resample=Image.Resampling.BICUBIC, fillcolor=245)
    png = io.BytesIO()
    page.save(png, "PNG")
    png.seek(0)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1, pageCompression=0)
    pw, ph = letter
    c.drawImage(ImageReader(png), 0, 0, width=pw, height=ph)
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- nested mode (--nested)
# A depth-4 tree where siblings differ only by content at each level (year, vendor, project name,
# course code, trip vs. family photo). ~15% of input files are only determinable down to an
# ancestor (`ancestor_only`); their `category` is one plausible leaf under that ancestor.

NESTED_LEAVES = [
    "Finance/Taxes/2024", "Finance/Taxes/2025",
    "Finance/Invoices/Acme", "Finance/Invoices/Initech", "Finance/Receipts",
    "Work/PayPal/Onboarding/Docs", "Work/PayPal/Onboarding/Forms", "Work/PayPal/Meetings",
    "Work/Hooli/Projects/Atlas", "Work/Hooli/Projects/Beacon", "Work/Resumes",
    "School/CS101/Lectures", "School/CS101/Assignments", "School/BIO150/Lectures",
    "Photos/2024/Trips", "Photos/2024/Family", "Photos/Screenshots",
    "Code",
]
NESTED_DOC_KINDS = {"form", "project_doc", "assignment", "work_doc", "school_doc", "finance_doc"}

NESTED_VENDORS: dict[str, tuple[str, list[str]]] = {
    "Acme": ("Acme Corp", ["Anvil fabrication", "Rocket skates", "Dynamite safety review",
                           "Giant magnet rental", "Portable hole supply", "Bird seed bulk order"]),
    "Initech": ("Initech", ["TPS report automation", "COBOL maintenance", "Printer firmware support",
                            "Mainframe consulting", "Y2K remediation", "Stapler supply contract"]),
}
CLIENTS = [v for v in VENDORS if v not in ("Acme Corp", "Initech")]
ONBOARDING_TOPICS = [
    ("Team handbook", ["how the Payments team plans its quarters", "on-call expectations and escalation",
                       "how we write and review design docs"]),
    ("Security training guide", ["phishing and social engineering", "handling cardholder data",
                                 "device encryption and VPN use"]),
    ("Payments platform overview", ["how a transaction flows from checkout to settlement",
                                    "the ledger and reconciliation services", "merchant onboarding"]),
    ("First week schedule", ["Monday: laptop pickup and badge", "Tuesday: security module",
                             "Thursday: meet your buddy and the team"]),
    ("Engineering tooling guide", ["getting repo access", "the CI pipeline", "staging environments"]),
]
FORM_TITLES = [
    "Direct deposit authorization", "Benefits enrollment form", "Equipment acknowledgment",
    "Emergency contact form", "Confidentiality agreement signature page", "Tax withholding election",
]
FORM_FIELDS = ["Employee name", "Employee ID", "Manager", "Department", "Start date", "Home address",
               "Phone number", "Account number", "Routing number", "Beneficiary"]
HOOLI_PROJECTS: dict[str, tuple[str, list[str]]] = {
    "Atlas": ("data warehouse migration", [
        "warehouse schema migration", "shard rebalancing", "ETL cutover", "cold storage tiering",
        "query planner upgrade", "backfill jobs"]),
    "Beacon": ("mobile push notification service", [
        "push delivery latency", "device token registry", "notification templates", "APNs gateway",
        "quiet hours settings", "delivery receipts"]),
}
PROJECT_DOC_TYPES = ["status update", "design doc", "sprint report", "risk review"]
SHARED_PROJECT_LINES = [
    "QA signoff slipped by a few days", "Launch review is scheduled with the leads",
    "Headcount request is pending approval", "Dependencies on the platform team are tracked weekly",
    "Budget is on track for the quarter", "Stakeholders asked for a clearer rollout plan",
    "Oncall load was light this sprint", "Documentation needs another pass",
]
NESTED_COURSES = {"CS101": "CS 101", "BIO150": "BIO 150"}
ASSIGNMENT_TASKS = ["Implement", "Analyze the running time of", "Trace by hand", "Explain and give an example of",
                    "Write test cases for", "Compare two approaches to"]
TRIPS = [
    ("Lisbon, Portugal", (38.72, -9.14), ["Tram 28 ride", "Belem tower", "Alfama market"]),
    ("Kyoto, Japan", (35.01, 135.77), ["Fushimi Inari gates", "Arashiyama bamboo grove", "Tea house"]),
    ("Banff, Canada", (51.18, -115.57), ["Lake Louise sunrise", "Gondola summit", "Trail to the falls"]),
    ("Reykjavik, Iceland", (64.15, -21.94), ["Hallgrimskirkja", "Blue Lagoon", "Northern lights"]),
    ("Oaxaca, Mexico", (17.07, -96.72), ["Monte Alban ruins", "Street food tour", "Zocalo evening"]),
]
FAMILY_EVENTS = [
    "Mom's birthday dinner", "Sunday lunch at grandma's", "Kids soccer game", "Thanksgiving table",
    "Backyard barbecue with the cousins", "Holiday morning with the kids", "Dad's retirement party",
]
HOME_GPS = (37.77, -122.42)

NESTED_DIR_THEMES: dict[str, tuple[str, list[str]]] = {
    "tax_form": ("Finance", DIR_THEMES["tax_form"]), "invoice": ("Finance", DIR_THEMES["invoice"]),
    "receipt": ("Finance", DIR_THEMES["receipt"]), "finance_doc": ("Finance", ["Bills and invoices"]),
    "resume": ("Work", DIR_THEMES["resume"]), "onboarding_doc": ("Work", DIR_THEMES["onboarding_doc"]),
    "form": ("Work", ["New job", "Downloads/work"]), "meeting_notes": ("Work", DIR_THEMES["meeting_notes"]),
    "project_doc": ("Work", ["Downloads/work", "Work stuff"]), "work_doc": ("Work", ["Work stuff"]),
    "lecture_notes": ("School", DIR_THEMES["lecture_notes"]), "lecture_slides": ("School", DIR_THEMES["lecture_slides"]),
    "assignment": ("School", ["Classes", "Downloads/school"]), "school_doc": ("School", ["Classes"]),
    "photo": ("Photos", DIR_THEMES["photo"]), "screenshot": ("Photos", DIR_THEMES["screenshot"]),
}
NESTED_CROSS_STEMS = {
    **CROSS_STEMS,
    "form": ["lecture_notes", "invoice", "Untitled"],
    "project_doc": ["lecture_notes", "taxes", "random_thoughts"],
    "assignment": ["Resume_draft", "invoice", "meeting_notes"],
    "work_doc": ["taxes_stuff", "lecture_week3", "Untitled"],
    "school_doc": ["Resume_draft", "invoice_scan", "notes"],
    "finance_doc": ["lecture_week3", "meeting_notes", "random"],
}


def _ndoc(kind: str, stem: str, ext: str, lines: list[str], one_liner: str) -> Made:
    m = _doc(kind, stem, ext, lines)
    m.one_liner = one_liner
    return m


def gen_tax_nested(rng: random.Random, year: int, hard: bool) -> Made:
    who, employer, d = _person(rng), rng.choice(EMPLOYERS), _date(rng, year)
    v = rng.randrange(6 if hard else 3)
    ssn = f"XXX-XX-{rng.randint(1000, 9999)}"
    one = f"Form {rng.choice(['W-2', '1099-NEC', '1040'])} tax year {year} - {_money(rng.randint(500_000, 9_000_000))}"
    if v == 0:
        lines = [f"Form W-2 Wage and Tax Statement {year}", "Department of the Treasury - Internal Revenue Service",
                 f"Tax year: {year}", f"Recipient: {who}", f"SSN: {ssn}", f"Employer: {employer}",
                 f"Box 1 Wages, tips, other compensation: {_money(rng.randint(4_000_000, 14_000_000))}",
                 f"Box 2 Federal income tax withheld: {_money(rng.randint(400_000, 2_000_000))}",
                 f"Box 17 State income tax: {_money(rng.randint(100_000, 800_000))}",
                 f"Copy B - keep this copy for your {year} records."]
        return _ndoc("tax_form", f"W-2_{year}_{employer.split()[0]}", "pdf", lines, one)
    if v == 1:
        lines = [f"Form 1099-NEC Nonemployee Compensation {year}", f"Tax year: {year}", f"Payer: {employer}",
                 f"Recipient: {who}", f"SSN: {ssn}", f"Box 1 Nonemployee compensation: {_money(rng.randint(50_000, 9_000_000))}",
                 f"Federal income tax withheld: {_money(rng.randint(0, 200_000))}",
                 f"This is important {year} tax information."]
        return _ndoc("tax_form", f"1099-NEC_{year}_{employer.split()[0]}", "pdf", lines, one)
    if v == 2:
        lines = [f"Form 1040 U.S. Individual Income Tax Return {year}", f"Tax year: {year}", f"Name: {who}",
                 "Filing status: Single", f"Line 1a Wages: {_money(rng.randint(4_000_000, 14_000_000))}",
                 f"Line 11 Adjusted gross income: {_money(rng.randint(4_000_000, 14_000_000))}",
                 f"Line 24 Total tax: {_money(rng.randint(400_000, 2_000_000))}", "Standard deduction claimed",
                 f"Sign here: ____________  Date: {d.isoformat()}"]
        return _ndoc("tax_form", f"1040_{year}", "pdf", lines, one)
    if v == 3:
        lines = [f"Dear {who.split()[0]},", f"Enclosed is a summary of your {year} federal and state tax returns.",
                 f"Tax year {year} adjusted gross income: {_money(rng.randint(4_000_000, 14_000_000))}",
                 f"Total tax: {_money(rng.randint(400_000, 2_000_000))}",
                 f"Refund expected: {_money(rng.randint(10_000, 400_000))}",
                 f"Please review, sign Form 8879 and return it so we can e-file your {year} return.",
                 f"Regards, {rng.choice(LAST)} & Co. CPAs"]
        return _ndoc("tax_form", f"tax_return_summary_{year}", rng.choice(["pdf", "txt"]), lines, one)
    if v == 4:
        lines = [f"Form 1040-ES Estimated Tax Payment Voucher - tax year {year}",
                 "Department of the Treasury - Internal Revenue Service", f"Name: {who}",
                 f"Amount of estimated tax for {year}: {_money(rng.randint(80_000, 900_000))}",
                 "Make check payable to United States Treasury"]
        return _ndoc("tax_form", f"1040ES_voucher_{year}", "pdf", lines, one)
    lines = [f"{year} tax prep worksheet - {who}", f"Deductible expenses paid in {year}:"]
    for vendor, svc in zip(rng.sample(VENDORS, 4), rng.sample(SERVICES, 4), strict=True):
        lines.append(f"{vendor} - {svc.lower()}: {_money(rng.randint(15_000, 480_000))}")
    lines += [f"Total {year} deductions: {_money(rng.randint(600_000, 2_400_000))}", "Hand to accountant before filing."]
    return _ndoc("tax_form", f"tax_prep_{year}", rng.choice(["md", "txt", "pdf"]), lines, one)


def gen_invoice_nested(rng: random.Random, vkey: str, hard: bool, vendor_override: str | None = None) -> Made:
    vendor, pool = NESTED_VENDORS[vkey] if vendor_override is None else (vendor_override, SERVICES)
    client = rng.choice(CLIENTS)
    vtag = vendor.split()[0]
    d = _date(rng, 2025)
    due = d + dt.timedelta(days=rng.choice([14, 30, 45]))
    names = rng.sample(SERVICES if hard else pool, rng.randint(3, 5))
    items = [(s, rng.randint(1, 20), rng.randint(2000, 45000)) for s in names]
    sub = sum(q * u for _, q, u in items)
    total = sub + sub * 8 // 100
    num = f"INV-2025-{rng.randint(100, 9999):04d}"
    one = f"{vendor} - payment due {due.isoformat()} - {_money(total)} owed"
    v = rng.randrange(4 if hard else 1)
    if v == 0:
        lines = [vendor, "INVOICE", f"Invoice number: {num}", f"Invoice date: {d.isoformat()}",
                 f"Due date: {due.isoformat()}", f"Bill to: {client}", _address(rng),
                 "Description | Qty | Unit price | Amount"]
        lines += [f"{s} | {q} | {_money(u)} | {_money(q * u)}" for s, q, u in items]
        lines += [f"Subtotal: {_money(sub)}", f"Sales tax (8%): {_money(sub * 8 // 100)}",
                  f"Total due: {_money(total)}", "Payment terms: Net 30",
                  f"Please remit payment to {vendor}, {_address(rng)}", "Thank you for your business."]
        return _ndoc("invoice", f"Invoice_{vtag}_{num[-4:]}", "pdf", lines, one)
    if v == 1:
        lines = [f"{vendor} - Statement of charges", f"Account holder: {client}", f"Period ending: {d.isoformat()}",
                 "Item | Qty | Rate | Charge"]
        lines += [f"{s} | {q} | {_money(u)} | {_money(q * u)}" for s, q, u in items]
        lines += [f"Amount due: {_money(total)}", f"Pay by {due.isoformat()}", f"Remit to {vendor}."]
        return _ndoc("invoice", f"{vtag}_statement_{d:%b%Y}", "pdf", lines, one)
    if v == 2:
        lines = ["BILL", vendor, f"Customer: {client}", f"Issued: {d.isoformat()}"]
        lines += [f"{s} - {_money(q * u)}" for s, q, u in items]
        lines += [f"Balance owed: {_money(total)}", f"Please pay {vendor} within {(due - d).days} days."]
        return _ndoc("invoice", f"{vtag}_bill_{num[-4:]}", "pdf", lines, one)
    lines = [f"Subject: Payment due for work completed - {vendor}", f"Hi {rng.choice(FIRST)},",
             f"Here is what you owe {vendor} for {items[0][0].lower()} and {items[1][0].lower()}."]
    lines += [f"  {s}: {_money(q * u)}" for s, q, u in items]
    lines += [f"Total owed: {_money(total)}", f"Please wire the money by {due.isoformat()}.",
              f"Thanks, {vendor} accounts team"]
    return _ndoc("invoice", f"{vtag}_email_{d:%b%Y}", rng.choice(["txt", "md"]), lines, one)


def gen_onboarding_doc_nested(rng: random.Random, hard: bool) -> Made:
    title, sections = rng.choice(ONBOARDING_TOPICS)
    one = f"PayPal onboarding guide: {title}"
    if hard and rng.random() < 0.4:
        lines = [f"Welcome to PayPal, {_person(rng).split()[0]}!", f"Your manager: {_person(rng)}",
                 f"This guide covers: {title.lower()}.", "Day one: pick up your laptop and badge from the front desk",
                 "Week one: shadow the Payments team", f"Questions? Ask your buddy, {rng.choice(FIRST)}."]
        return _ndoc("onboarding_doc", "welcome_to_PayPal_" + title.split()[0].lower(), rng.choice(["pdf", "md"]), lines, one)
    lines = [f"PayPal onboarding guide: {title}", "Welcome to the Payments team.",
             "This guide explains what you need to know in your first weeks."]
    for i, sec in enumerate(sections):
        lines += [f"Section {i + 1}: {sec.capitalize()}",
                  f"You will learn {sec}. Read it before your first sprint and ask your buddy if anything is unclear.",
                  f"Related reading: the {rng.choice(['wiki', 'runbook', 'handbook'])} page on {sec.split()[0]}."]
    return _ndoc("onboarding_doc", "PayPal_" + title.replace(" ", "_"), rng.choice(["pdf", "md"]), lines, one)


def gen_form_nested(rng: random.Random, hard: bool) -> Made:
    title = rng.choice(FORM_TITLES)
    one = f"PayPal form - {title} - sign and return to HR"
    if hard and rng.random() < 0.4:
        who = _person(rng)
        lines = [f"PayPal policy acknowledgment - {title}",
                 f"I, {who}, confirm that I have read the PayPal code of conduct and this {title.lower()}.",
                 "Signature: ____________", f"Date: {_date(rng, 2025).isoformat()}",
                 "Return the signed form to HR within your first five days."]
        return _ndoc("form", "acknowledgment_" + title.split()[0].lower(), rng.choice(["pdf", "md"]), lines, one)
    lines = [f"PayPal HR Form {rng.randint(10, 99)}: {title}", "Please print clearly and complete every field."]
    lines += [f"{f}: ____________________" for f in rng.sample(FORM_FIELDS, rng.randint(4, 6))]
    lines += ["I certify that the information above is correct.", "Employee signature: ____________   Date: ________",
              "Return the completed form to People Operations."]
    return _ndoc("form", "PayPal_" + title.replace(" ", "_") + "_form", rng.choice(["pdf", "md"]), lines, one)


def gen_paypal_meeting_nested(rng: random.Random, hard: bool) -> Made:
    title = rng.choice(MEETINGS)
    thing = rng.choice(["ledger migration", "fraud rules", "checkout latency fix", "API v3"])
    one = f"PayPal Payments team meeting notes - action: {rng.choice(['ship', 'review', 'defer'])} the API v3"
    if hard and rng.random() < 0.5:
        lines = [f"{title} recap - PayPal Payments", f"- Key takeaway: the {thing} slipped one week",
                 f"- Decision: keep the {thing} in the current quarter", f"- Owner: {rng.choice(FIRST)}",
                 "- Customers affected: merchants on the PayPal payments platform", "- Follow-up scheduled"]
        return _ndoc("meeting_notes", title.replace(" ", "_") + "_recap", rng.choice(["md", "txt"]), lines, one)
    lines = [f"PayPal Payments - {title} - meeting notes", f"Date: {_date(rng, 2025).isoformat()}",
             f"Attendees: {_person(rng)}, {_person(rng)}, {_person(rng)}"]
    for _ in range(rng.randint(5, 8)):
        lines.append(f"- {rng.choice(['Action', 'Decision', 'Risk', 'Follow-up'])}: "
                     f"{rng.choice(['ship', 'review', 'defer', 'escalate'])} the {thing} ({rng.choice(FIRST)} owns)")
    return _ndoc("meeting_notes", title.replace(" ", "_").replace(":", ""), rng.choice(["md", "txt"]), lines, one)


def gen_project_doc(rng: random.Random, name: str, hard: bool) -> Made:
    summary, terms = HOOLI_PROJECTS[name]
    doc = rng.choice(PROJECT_DOC_TYPES)
    d = _date(rng, 2025)
    one = f"Hooli Project {name} - {doc} {d.isoformat()}: {rng.choice(terms)} on track"
    lines = [f"Hooli Project {name} - {doc}", f"Date: {d.isoformat()}", f"Owner: {_person(rng)}"]
    if hard:  # mostly shared project-management vocabulary; the name appears a few times, the topic once
        lines += [f"Summary: Project {name} ({summary}) is progressing.", *rng.sample(SHARED_PROJECT_LINES, 4),
                  f"- {rng.choice(['Milestone', 'Risk', 'Blocker'])}: {rng.choice(terms)}",
                  f"Next review of Project {name}: {_date(rng, 2025).isoformat()}"]
    else:
        for _ in range(rng.randint(6, 9)):
            lines.append(f"- {rng.choice(['Milestone', 'Risk', 'Decision', 'Blocker'])}: "
                         f"{rng.choice(['finish', 'review', 'defer', 'escalate'])} {rng.choice(terms)} "
                         f"({rng.choice(FIRST)} owns)")
        lines.append(f"Project {name} goal: {summary}.")
    return _ndoc("project_doc", f"{name}_{doc.replace(' ', '_')}", rng.choice(["md", "pdf", "txt"]), lines, one)


def gen_lecture_nested(rng: random.Random, key: str, hard: bool) -> Made:
    course = NESTED_COURSES[key]
    topic, terms = COURSES[course]
    wk = rng.randint(1, 14)
    a, b, c = rng.sample(terms, 3)
    one = f"{course} {topic}: {a}, {b}, {c} - lecture notes"
    v = rng.randrange(5 if hard else 2)
    if v == 0:
        lines = [f"{course} - {topic} - Lecture {wk} notes", f"Date: {_date(rng, 2025).isoformat()}",
                 f"Topics today: {a}, {b}, {c}"] + _sentences(rng, topic, terms, rng.randint(10, 14))
        return _ndoc("lecture_notes", f"{key}_week{wk}_notes", rng.choice(["md", "txt"]), lines, one)
    if v == 1:
        lines = [f"{course}: {topic} - Lecture {wk}"]
        for t in rng.sample(terms, 4):
            lines += [f"Slide: {t.capitalize()}", f"- Why {t} matters in {topic}", f"- Worked example involving {t}"]
        m = _ndoc("lecture_slides", f"{key}_L{wk}_slides", "pptx" if _pptx else "md", lines, one)
        return m
    if v == 2:  # Cornell style
        lines = [f"{course} week {wk}", "Cues | Notes", f"{a} | what it is, why it matters in {topic}",
                 f"{b} | contrast with {c}; prof gave two examples", f"{c} | likely on the exam"]
    elif v == 3:  # scrappy outline
        lines = [f"{course} {topic.lower()} - {_date(rng, 2025):%b %d}", f"* {a}: prof spent 20 min on this",
                 f"* {b} (see textbook ch. {rng.randint(2, 12)})", f"  - ties back to {c}",
                 f"Q for office hours: why does {b} not apply to {c}?"]
    else:  # textbook reading notes
        lines = [f"{course} {topic} - chapter {rng.randint(1, 12)} reading notes", f"Key term: {a}",
                 f"Definition: {a} describes how a system relates to {b}.", f"Key term: {c}",
                 f"Example in the text uses {b}; the lecture added {c}."]
    return _ndoc("lecture_notes", rng.choice([f"{key}_wk{wk}", f"notes_{wk}_{topic.split()[0]}"]),
                 rng.choice(["md", "txt"]), lines, one)


def gen_assignment(rng: random.Random, hard: bool) -> Made:
    course = NESTED_COURSES["CS101"]
    topic, terms = COURSES[course]
    k = rng.randint(1, 9)
    due = _date(rng, 2025)
    probs = rng.sample(terms, rng.randint(3, 4))
    one = f"{course} Assignment {k} - due {due.isoformat()} - {', '.join(probs[:2])}"
    v = rng.randrange(3 if hard else 1)
    if v == 1:
        lines = [f"Subject: {course} homework {k} reminder", "Hi all,",
                 f"Homework {k} for {course} is due {due.isoformat()}. It covers {', '.join(probs)}.",
                 "Submit your solutions through the course portal. No late work after the grace period.",
                 f"Office hours are on Thursdays. - TA {rng.choice(FIRST)}"]
        return _ndoc("assignment", f"hw{k}_reminder", rng.choice(["txt", "md"]), lines, one)
    if v == 2:
        lines = [f"{course} Lab {k} handout", f"Due: {due.isoformat()}", "Starter code is provided in the course repository."]
        lines += [f"Task {i + 1}: {rng.choice(ASSIGNMENT_TASKS)} {p}." for i, p in enumerate(probs)]
        lines.append("Grading: correctness 70%, style 30%.")
        return _ndoc("assignment", f"lab{k}", "pdf", lines, one)
    lines = [f"{course} - Assignment {k}: {topic}", f"Due: {due.isoformat()}  Total: 100 points"]
    lines += [f"Problem {i + 1} ({rng.choice([10, 15, 20, 25])} pts): {rng.choice(ASSIGNMENT_TASKS)} {p}."
              for i, p in enumerate(probs)]
    lines += ["Submission: upload a single PDF to the course portal before the deadline.",
              "Late policy: 10% penalty per day."]
    return _ndoc("assignment", f"{NESTED_COURSES['CS101'].replace(' ', '')}_Assignment{k}", rng.choice(["pdf", "pdf", "md"]), lines, one)


def _dms(x: float) -> tuple[float, float, float]:
    x = abs(x)
    return (float(int(x)), float(int((x - int(x)) * 60)), round(((x - int(x)) * 60 % 1) * 60, 1))


def _photo_nested(rng: random.Random, desc: str | None, gps: tuple[float, float] | None, stem_hint: str) -> Made:
    img = _base_image(rng, 640, 480)
    d = ImageDraw.Draw(img)
    for _ in range(rng.randint(4, 9)):
        x, y = rng.randrange(600), rng.randrange(440)
        r = rng.randint(20, 120)
        d.ellipse((x, y, x + r, y + r), fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    make, model = rng.choice(PHOTO_CAMERAS)
    when = dt.datetime(2024, 1, 1) + dt.timedelta(seconds=rng.randrange(366 * 86400))
    stamp = when.strftime("%Y:%m:%d %H:%M:%S")
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x0132] = make, model, stamp
    if desc:
        exif[0x010E] = desc
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = sub[0x9004] = stamp
    sub[0xA434] = f"{model} lens {rng.choice([24, 35, 50, 85])}mm"
    if gps:
        g = exif.get_ifd(0x8825)
        g[1], g[2] = ("N" if gps[0] >= 0 else "S"), _dms(gps[0])
        g[3], g[4] = ("E" if gps[1] >= 0 else "W"), _dms(gps[1])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, exif=exif)
    stem = stem_hint if stem_hint else f"IMG_{rng.randint(1000, 9999)}"
    return Made("photo", stem, rng.choice(["jpg", "jpg", "jpeg"]), buf.getvalue())


def gen_photo_trip(rng: random.Random, hard: bool) -> Made:
    place, gps, scenes = rng.choice(TRIPS)
    scene = rng.choice(scenes)
    stem = "" if hard or rng.random() < 0.5 else f"{place.split(',')[0]} {scene.split()[0]} {rng.randint(1, 40)}"
    return _photo_nested(rng, f"{place} trip - {scene}", gps, stem)


def gen_photo_family(rng: random.Random, hard: bool) -> Made:
    ev = rng.choice(FAMILY_EVENTS)
    stem = "" if hard or rng.random() < 0.5 else f"{ev.split()[0]} {ev.split()[-1]} {rng.randint(1, 40)}"
    return _photo_nested(rng, f"Family - {ev}", HOME_GPS, stem)


# ---- ancestor-only documents: a careful human can only say which ancestor folder they belong to


def gen_anc_paypal(rng: random.Random, hard: bool) -> Made:
    v = rng.randrange(3)
    who = _person(rng)
    if v == 0:
        lines = ["PayPal Payments team - weekly update", f"Date: {_date(rng, 2025).isoformat()}",
                 "Status: the team is on track for the quarter.", f"Contact {rng.choice(FIRST)} with questions.",
                 "Please review the attached material and note the upcoming dates."]
        stem = "PayPal_team_update"
    elif v == 1:
        lines = [f"PayPal Payments - notes for {who.split()[0]}", f"Date: {_date(rng, 2025).isoformat()}",
                 "Topics: payments platform, team structure, upcoming milestones.",
                 f"Follow up with {rng.choice(FIRST)} and {rng.choice(FIRST)} next week."]
        stem = "PayPal_notes"
    else:
        lines = ["PayPal - Payments platform", "Overview for the team.", "Please read before the next session.",
                 f"Prepared by {who}."]
        stem = "PayPal_payments_overview"
    return _ndoc("work_doc", stem, rng.choice(["md", "pdf", "txt"]), lines, "PayPal Payments team update - see attached")


def gen_anc_paypal_onboarding(rng: random.Random, hard: bool) -> Made:
    who = _person(rng)
    lines = [f"Welcome to PayPal, {who.split()[0]}!", f"Your start date is {_date(rng, 2025).isoformat()}.",
             "Before your first day, review the onboarding material and complete the new hire paperwork.",
             "Day one: pick up your laptop and badge from the front desk.", "People Operations will follow up."]
    return _ndoc("work_doc", "PayPal_new_hire_welcome", rng.choice(["md", "pdf", "txt"]), lines,
                 "PayPal new hire: laptop pickup Monday, badge at front desk")


def gen_anc_hooli_projects(rng: random.Random, hard: bool) -> Made:
    d = _date(rng, 2025)
    lines = ["Hooli Projects portfolio overview", f"Date: {d.isoformat()}", f"Owner: {_person(rng)}",
             "Active projects: Atlas and Beacon.", *rng.sample(SHARED_PROJECT_LINES, 4),
             "Both Project Atlas and Project Beacon are reviewed at the monthly portfolio meeting."]
    return _ndoc("work_doc", "Hooli_projects_overview", rng.choice(["md", "pdf", "txt"]), lines,
                 f"Hooli projects portfolio overview {d.isoformat()}: Atlas and Beacon")


def gen_anc_hooli(rng: random.Random, hard: bool) -> Made:
    d = _date(rng, 2025)
    topic = rng.choice(["all-hands agenda", "offsite logistics", "contractor onboarding", "security reminder"])
    lines = [f"Hooli - {topic}", f"Date: {d.isoformat()}", f"Organizer: {_person(rng)}",
             *rng.sample(SHARED_PROJECT_LINES, 3), "Details to follow by email."]
    return _ndoc("work_doc", "Hooli_" + topic.replace(" ", "_"), rng.choice(["md", "pdf", "txt"]), lines,
                 f"Hooli - {topic} {d.isoformat()}")


def gen_anc_taxes(rng: random.Random, hard: bool) -> Made:
    who = _person(rng)
    lines = [f"Tax document checklist - {who}", "Gather before meeting with your accountant:",
             "- W-2 from each employer", "- 1099 forms for freelance income", "- Mortgage interest statement (Form 1098)",
             "- Charitable donation receipts", f"Questions? Contact {rng.choice(LAST)} & Co. CPAs."]
    return _ndoc("tax_form", "tax_checklist", rng.choice(["md", "txt", "pdf"]), lines,
                 "Tax document checklist - gather W-2s, 1099s and receipts for your CPA")


def gen_anc_invoice(rng: random.Random, hard: bool) -> Made:
    return gen_invoice_nested(rng, "Acme", hard, vendor_override=rng.choice(CLIENTS))


def gen_anc_finance(rng: random.Random, hard: bool) -> Made:
    d = _date(rng, 2025)
    lines = [f"Monthly account summary - {d:%B %Y}", f"Account ending {rng.randint(1000, 9999)}",
             f"Opening balance: {_money(rng.randint(100_000, 900_000))}"]
    for _ in range(rng.randint(4, 7)):
        lines.append(f"{_date(rng, 2025).isoformat()}  {rng.choice(['Card purchase', 'Transfer', 'Direct debit', 'Deposit'])}  "
                     f"{_money(rng.randint(500, 250_000))}")
    lines.append(f"Closing balance: {_money(rng.randint(100_000, 900_000))}")
    return _ndoc("finance_doc", f"account_summary_{d:%b%Y}", "pdf", lines,
                 f"Monthly account summary {d:%B %Y} - closing balance {_money(rng.randint(100_000, 900_000))}")


def gen_anc_cs101(rng: random.Random, hard: bool) -> Made:
    course = NESTED_COURSES["CS101"]
    topic, terms = COURSES[course]
    lines = [f"{course} {topic} - course syllabus", "Instructor office hours: Tuesday and Thursday",
             f"Topics this term: {', '.join(rng.sample(terms, 4))}", "Grading: assignments 40%, midterm 25%, final 35%",
             "Lectures are on Monday and Wednesday; assignments are posted weekly."]
    return _ndoc("school_doc", "CS101_syllabus", rng.choice(["md", "pdf", "txt"]), lines,
                 f"{course} {topic} - course syllabus")


def gen_anc_school(rng: random.Random, hard: bool) -> Made:
    lines = ["Fall semester study schedule", f"Week of {_date(rng, 2025).isoformat()}",
             f"CS 101 midterm: {_date(rng, 2025).isoformat()}", f"BIO 150 midterm: {_date(rng, 2025).isoformat()}",
             "Plan: review lecture notes, finish assignments, book a study room.", "Library hours extended during exams."]
    return _ndoc("school_doc", "study_schedule", rng.choice(["md", "txt", "pdf"]), lines,
                 "Fall semester study schedule - CS 101 and BIO 150 midterms")


def gen_anc_photo(rng: random.Random, hard: bool) -> Made:
    return _photo_nested(rng, None, None, "")


def _gen_receipt_n(rng: random.Random, hard: bool) -> Made:
    return (gen_receipt_hard if hard else gen_receipt)(rng)


def _gen_resume_n(rng: random.Random, hard: bool) -> Made:
    return (gen_resume_hard if hard else gen_resume)(rng)


def _gen_shot_n(rng: random.Random, hard: bool) -> Made:
    return (gen_screenshot_hard if hard else gen_screenshot)(rng)


def _gen_code_n(rng: random.Random, hard: bool) -> Made:
    return gen_code_project(rng)


NestedGen = Callable[[random.Random, bool], Made]


@dataclass(frozen=True)
class _Slot:
    weight: float
    make: NestedGen
    examples: int = 0  # pre-seeded output/ files (leaf slots only)
    ancestor: str = ""  # ancestor-only slot: the deepest certain ancestor
    plausible: tuple[str, ...] = ()  # leaves the ground-truth `category` may be drawn from


def _leaf_slots() -> dict[str, _Slot]:
    s: dict[str, _Slot] = {}

    def leaf(cat: str, weight: float, examples: int, fn: NestedGen) -> None:
        s[cat] = _Slot(weight, fn, examples)

    leaf("Finance/Taxes/2024", 4, 1, lambda r, h: gen_tax_nested(r, 2024, h))
    leaf("Finance/Taxes/2025", 5, 2, lambda r, h: gen_tax_nested(r, 2025, h))
    leaf("Finance/Invoices/Acme", 7, 2, lambda r, h: gen_invoice_nested(r, "Acme", h))
    leaf("Finance/Invoices/Initech", 6, 1, lambda r, h: gen_invoice_nested(r, "Initech", h))
    leaf("Finance/Receipts", 9, 3, _gen_receipt_n)
    leaf("Work/PayPal/Onboarding/Docs", 4, 2, gen_onboarding_doc_nested)
    leaf("Work/PayPal/Onboarding/Forms", 4, 1, gen_form_nested)
    leaf("Work/PayPal/Meetings", 5, 2, gen_paypal_meeting_nested)
    leaf("Work/Hooli/Projects/Atlas", 5, 2, lambda r, h: gen_project_doc(r, "Atlas", h))
    leaf("Work/Hooli/Projects/Beacon", 5, 1, lambda r, h: gen_project_doc(r, "Beacon", h))
    leaf("Work/Resumes", 3, 1, _gen_resume_n)
    leaf("School/CS101/Lectures", 5, 3, lambda r, h: gen_lecture_nested(r, "CS101", h))
    leaf("School/CS101/Assignments", 4, 2, lambda r, h: gen_assignment(r, h))
    leaf("School/BIO150/Lectures", 5, 1, lambda r, h: gen_lecture_nested(r, "BIO150", h))
    leaf("Photos/2024/Trips", 6, 2, gen_photo_trip)
    leaf("Photos/2024/Family", 6, 2, gen_photo_family)
    leaf("Photos/Screenshots", 6, 2, _gen_shot_n)
    leaf("Code", 4, 2, _gen_code_n)
    assert sorted(s) == sorted(NESTED_LEAVES)

    def anc(path: str, weight: float, fn: NestedGen, plausible: tuple[str, ...]) -> None:
        assert all(p in s and p.startswith(path + "/") for p in plausible), path
        s["~" + path] = _Slot(weight, fn, 0, path, plausible)

    anc("Work/PayPal", 2.5, gen_anc_paypal, ("Work/PayPal/Onboarding/Docs", "Work/PayPal/Meetings"))
    anc("Work/PayPal/Onboarding", 2, gen_anc_paypal_onboarding,
        ("Work/PayPal/Onboarding/Docs", "Work/PayPal/Onboarding/Forms"))
    anc("Work/Hooli/Projects", 2, gen_anc_hooli_projects, ("Work/Hooli/Projects/Atlas", "Work/Hooli/Projects/Beacon"))
    anc("Work/Hooli", 1, gen_anc_hooli, ("Work/Hooli/Projects/Atlas", "Work/Hooli/Projects/Beacon"))
    anc("Finance/Taxes", 2, gen_anc_taxes, ("Finance/Taxes/2024", "Finance/Taxes/2025"))
    anc("Finance/Invoices", 1.5, gen_anc_invoice, ("Finance/Invoices/Acme", "Finance/Invoices/Initech"))
    anc("Finance", 1, gen_anc_finance, ("Finance/Receipts", "Finance/Taxes/2025"))
    anc("School/CS101", 1.5, gen_anc_cs101, ("School/CS101/Lectures", "School/CS101/Assignments"))
    anc("School", 1, gen_anc_school, ("School/CS101/Lectures", "School/BIO150/Lectures"))
    anc("Photos/2024", 1.5, gen_anc_photo, ("Photos/2024/Trips", "Photos/2024/Family"))
    return s


NESTED_SLOTS = _leaf_slots()


def make_nested(rng: random.Random, slot: _Slot, cat: str, hard: bool) -> Made:
    """Generate one file for a slot and stamp its ground truth on the Made."""
    m = slot.make(rng, hard)
    if slot.ancestor:
        m.category, m.ancestor_only = rng.choice(slot.plausible), slot.ancestor
    else:
        m.category = cat
    return m


# --------------------------------------------------------------------------- derived files


def _extra_line(rng: random.Random, kind: str) -> str:
    if kind == "invoice":
        return f"{rng.choice(SERVICES)} | {rng.randint(1, 9)} | {_money(rng.randint(2000, 40000))} | revised"
    if kind == "receipt":
        return f"{rng.choice(['Gift card', 'Bag fee', 'Coupon', 'Tip'])} {_money(rng.randint(50, 900))}"
    if kind == "resume":
        return f"- Delivered {rng.choice(SERVICES).lower()} on a {rng.randint(2, 9)}-month timeline"
    if kind == "tax_form":
        return f"Corrected amount: {_money(rng.randint(50_000, 9_000_000))}"
    return f"Revised note {rng.randint(100, 999)}: {rng.choice(SKILLS)} follow-up for {rng.choice(FIRST)}"


def make_version(rng: random.Random, src: Made) -> Made:
    """Re-render src with ~30% of its non-title lines replaced (~70% text overlap)."""
    assert src.lines is not None
    lines = list(src.lines)
    idx = list(range(1, len(lines)))
    for i in rng.sample(idx, max(1, round(0.3 * len(idx)))):
        lines[i] = _extra_line(rng, src.kind)
    return Made(src.kind, src.stem, src.ext, RENDERERS[src.ext.lower()](lines), lines=lines,
                category=src.category, ancestor_only=src.ancestor_only, one_liner=src.one_liner)


def make_resized(src: Made) -> Made:
    img = Image.open(io.BytesIO(src.data))
    exif = img.info.get("exif")
    small = img.resize((img.width // 2, img.height // 2), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if src.ext.lower() == "png":
        small.save(buf, "PNG")
    else:
        small.save(buf, "JPEG", quality=80, **({"exif": exif} if exif else {}))
    return Made(src.kind, src.stem, src.ext, buf.getvalue(),
                category=src.category, ancestor_only=src.ancestor_only)


# --------------------------------------------------------------------------- placement

INPUT_DIRS = [("", 62), ("Downloads", 20), ("Desktop stuff", 12), ("Downloads/old stuff", 6)]


@dataclass
class _Placed:
    made: Made
    entry: Entry
    dir: str = ""


@dataclass
class _Namer:
    seen: set[str] = field(default_factory=set)

    def claim(self, directory: str, name: str) -> str:
        # Messy names must still be legal on Windows (CI runs there): no <>:"/\|?* or
        # control chars, no trailing dots/spaces.
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ") or "_"
        p = Path(name)
        stem, ext, n = p.stem if p.suffix else name, p.suffix, 1
        cand = name
        while (directory + "/" + cand).lower() in self.seen:
            n += 1
            cand = f"{stem} ({n}){ext}"
        self.seen.add((directory + "/" + cand).lower())
        return cand


def _messy(rng: random.Random, stem: str) -> str:
    r = rng.random()
    if r < 0.35:
        return stem.replace("_", " ")
    if r < 0.5:
        return stem.lower()
    return stem


def _weighted(rng: random.Random, table: list[tuple[str, float]]) -> str:
    total = sum(w for _, w in table)
    x = rng.random() * total
    for k, w in table:
        x -= w
        if x <= 0:
            return k
    return table[-1][0]


def _full_name(m: Made, stem: str | None = None) -> str:
    return f"{stem if stem is not None else m.stem}.{m.ext}" if m.ext else (stem or m.stem)


def _write(base: Path, rel: str, m: Made) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if m.files is not None:
        for frel, data in sorted(m.files.items()):
            fp = p / frel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
    else:
        p.write_bytes(m.data)


# --------------------------------------------------------------------------- main


def generate(out: Path, n: int = 300, seed: int = 0, nested: bool = False,
             hard: bool = False) -> Manifest:
    rng = random.Random(seed)
    gens = HARD_GENERATORS if hard else GENERATORS
    kinds = dict(KINDS)
    categories = list(BASE_CATEGORIES)
    kind_table = [(k, w) for k, (_, w) in sorted(kinds.items())]
    if nested:  # a depth-4 tree replaces the flat categories; slots are leaves or ancestor-only groups
        kinds, categories = {}, list(NESTED_LEAVES)
        kind_table = [(k, sl.weight) for k, sl in sorted(NESTED_SLOTS.items())]
    cross_stems = NESTED_CROSS_STEMS if nested else CROSS_STEMS
    doc_kinds = DOC_KINDS | NESTED_DOC_KINDS if nested else DOC_KINDS

    def cat_of(m: Made) -> str:
        return m.category or kinds[m.kind][0]

    if out.exists():
        shutil.rmtree(out)
    (out / "input").mkdir(parents=True)
    for cat in categories:
        (out / "output" / cat).mkdir(parents=True)
    manifest = Manifest(seed=seed, n=n, nested=nested, hard=hard, categories=sorted(categories))

    # ---- pre-seeded output examples (clean names, a few per category)
    namer_out = _Namer()
    if nested:  # 1-3 examples per leaf, so every subtree has a centroid
        for cat, slot in sorted(NESTED_SLOTS.items()):
            for _ in range(slot.examples):
                m = make_nested(rng, slot, cat, hard)
                name = namer_out.claim(cat, _full_name(m))
                _write(out / "output", f"{cat}/{name}", m)
                manifest.examples.append(Entry(rel_path=f"{cat}/{name}", category=cat, kind=m.kind))
    for kind, (cat, _) in ([] if nested else sorted(kinds.items())):
        count = 2 if kind == "code_project" else (3 if kind not in ("lecture_slides",) else 2)
        for _ in range(count):
            m = gens[kind](rng)
            name = namer_out.claim(cat, _full_name(m))
            _write(out / "output", f"{cat}/{name}", m)
            manifest.examples.append(Entry(rel_path=f"{cat}/{name}", category=cat, kind=kind))

    # ---- input: base files, then derived ones
    n_exact = round(0.07 * n)
    n_resized = round(0.04 * n)
    n_version = round(0.06 * n)
    n_base = max(1, n - n_exact - n_resized - n_version)

    namer = _Namer()
    placed: list[_Placed] = []

    def place(m: Made, entry_kw: dict[str, object], dir_: str, name: str) -> _Placed:
        name = namer.claim(dir_, name)
        rel = f"{dir_}/{name}" if dir_ else name
        kind = m.kind
        entry = Entry(rel_path=rel, category=cat_of(m), kind=kind, ancestor_only=m.ancestor_only or None,
                      **entry_kw)
        _write(out / "input", rel, m)
        p = _Placed(m, entry, dir_)
        placed.append(p)
        return p

    base: list[_Placed] = []
    for _ in range(n_base):
        kind = _weighted(rng, kind_table)
        if nested:
            m = make_nested(rng, NESTED_SLOTS[kind], kind, hard)
            kind = m.kind
        else:
            m = gens[kind](rng)
        if hard and kind in doc_kinds and rng.random() < 0.10:
            m = make_sparse(rng, m)
        dir_ = _weighted(rng, [(d, w) for d, w in INPUT_DIRS])
        bad_dir = False
        if hard and not m.is_bundle:
            r = rng.random()
            if nested:
                right = NESTED_DIR_THEMES[kind][1]
                wrong = sorted({d for g, ds in NESTED_DIR_THEMES.values() if g != cat_of(m).split("/")[0]
                                for d in ds} - set(right))
            else:
                right = DIR_THEMES[kind]
                wrong = sorted({d for k, ds in DIR_THEMES.items() if kinds.get(k, ("",))[0] != kinds[kind][0]
                                for d in ds} - set(DIR_THEMES[kind]))
            if r < 0.08:  # a folder that happens to be right
                dir_ = rng.choice(right)
            elif r < 0.15:  # a folder themed for a different category
                dir_, bad_dir = rng.choice(wrong), True
        if m.is_bundle:  # keep projects near the top level, as people do
            dir_ = rng.choice(["", "", "Desktop stuff"])
        base.append(place(m, {"misleading_dir": bad_dir}, dir_, _full_name(m, _messy(rng, m.stem))))

    def rename(b: _Placed, new_stem: str) -> None:
        old = out / "input" / b.entry.rel_path
        new_name = namer.claim(b.dir, f"{new_stem}.{b.made.ext}")
        new_rel = f"{b.dir}/{new_name}" if b.dir else new_name
        old.rename(out / "input" / new_rel)
        b.entry.rel_path = new_rel

    candidates = [b for b in base if not b.made.is_bundle]
    if hard:  # ~15% generic names (sparse scans favoured) and ~15% misleading names
        n_pick = round(0.15 * len(candidates))
        sparse = [b for b in candidates if b.made.sparse]
        others = [b for b in candidates if not b.made.sparse]
        n_sp = min(len(sparse), round(0.6 * len(sparse)), n_pick)
        generic = rng.sample(sparse, n_sp) + rng.sample(others, min(len(others), n_pick - n_sp))
        taken = {id(b) for b in generic}
        for b in generic:
            rename(b, _generic_stem(rng, b.made.kind))
            b.entry.generic_name = True
        rest = [b for b in candidates if id(b) not in taken]
        mislead = rng.sample(rest, min(n_pick, len(rest)))
        for b in mislead:
            rename(b, rng.choice(cross_stems[b.made.kind]))
            b.entry.misleading_name = True
    else:  # misleading names (~5% of non-bundle base files)
        n_mis = round(0.05 * len(base))
        for b in rng.sample(candidates, min(n_mis, len(candidates))):
            pool = GENERIC_STEMS + cross_stems.get(b.made.kind, []) * 2
            if b.made.kind in IMAGE_KINDS:
                pool = cross_stems[b.made.kind]
            rename(b, rng.choice(pool))
            b.entry.misleading_name = True

    def derived_name(p: _Placed, style: str) -> str:
        stem = Path(p.entry.rel_path).stem
        return {
            "v2": f"{stem}_v2", "final": f"{stem} final", "num": f"{stem} (1)",
            "copy": f"{stem} copy", "copyof": f"Copy of {stem}", "dash": f"{stem}-2",
        }[style] + f".{p.made.ext}"

    # exact dupes: byte-identical, different name, sometimes different folder
    pool_exact = [b for b in base if not b.made.is_bundle]
    for src in rng.sample(pool_exact, min(n_exact, len(pool_exact))):
        style = rng.choice(["num", "copy", "copyof", "dash"])
        dir_ = src.dir if rng.random() < 0.5 else _weighted(rng, list(INPUT_DIRS))
        place(src.made, {"dup_of": src.entry.rel_path, "misleading_name": src.entry.misleading_name,
         "generic_name": src.entry.generic_name},
              dir_, derived_name(src, style))

    # resized image copies
    pool_img = [b for b in base if b.made.kind in IMAGE_KINDS]
    for src in rng.sample(pool_img, min(n_resized, len(pool_img))):
        m = make_resized(src.made)
        stem = Path(src.entry.rel_path).stem
        dir_ = src.dir if rng.random() < 0.5 else _weighted(rng, list(INPUT_DIRS))
        place(m, {"near_dup_of": src.entry.rel_path, "misleading_name": src.entry.misleading_name,
         "generic_name": src.entry.generic_name},
              dir_, f"{stem}{rng.choice(['_small', '_resized', ' (1)', '_web'])}.{m.ext}")

    # edited docs
    pool_doc = [b for b in base if b.made.lines is not None]
    for src in rng.sample(pool_doc, min(n_version, len(pool_doc))):
        m = make_version(rng, src.made)
        style = rng.choice(["v2", "final", "num", "copy"])
        place(m, {"version_of": src.entry.rel_path, "misleading_name": src.entry.misleading_name,
         "generic_name": src.entry.generic_name},
              src.dir, derived_name(src, style))

    manifest.entries = sorted((p.entry for p in placed), key=lambda e: e.rel_path)
    skip: Any = {}
    extra: set[str] = set()  # keep non-hard / non-nested manifest.json byte-identical to older formats
    if not hard:
        extra |= {"generic_name", "misleading_dir"}
    if not nested:
        extra.add("ancestor_only")
    if extra:
        skip = {"entries": {"__all__": extra}, "examples": {"__all__": extra}}
    if not hard:
        skip["hard"] = True
    (out / "manifest.json").write_text(manifest.model_dump_json(indent=2, exclude=skip), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--nested", action="store_true")
    ap.add_argument("--hard", action="store_true", help="realistic, ambiguous corpus")
    a = ap.parse_args()
    m = generate(a.out, a.n, a.seed, a.nested, a.hard)
    kinds: dict[str, int] = {}
    for e in m.entries:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    print(f"wrote {len(m.entries)} input items, {len(m.examples)} examples to {a.out}")
    for k in sorted(kinds):
        print(f"  {k}: {kinds[k]}")


if __name__ == "__main__":
    main()
