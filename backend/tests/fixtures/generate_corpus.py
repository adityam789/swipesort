"""Synthetic messy-folder corpus with ground truth (SPEC §15).

    uv run python tests/fixtures/generate_corpus.py OUT_DIR --n 300 --seed 0 [--nested]

Writes OUT_DIR/input (the mess), OUT_DIR/output (pre-seeded category tree with a few
examples per category) and OUT_DIR/manifest.json (ground truth). Fully deterministic
for a given (seed, n, nested) on a given set of installed libraries.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import random
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import letter
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


class Manifest(BaseModel):
    seed: int
    n: int
    nested: bool
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
NESTED_CATEGORIES = ["Work/PayPal/Onboarding/Docs", "Work/PayPal/Meetings"]

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
NESTED_KINDS: dict[str, tuple[str, float]] = {
    "onboarding_doc": ("Work/PayPal/Onboarding/Docs", 4),
    "meeting_notes": ("Work/PayPal/Meetings", 3),
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
    return buf.getvalue()


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
    return buf.getvalue()


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
    return Made(src.kind, src.stem, src.ext, RENDERERS[src.ext.lower()](lines), lines=lines)


def make_resized(src: Made) -> Made:
    img = Image.open(io.BytesIO(src.data))
    exif = img.info.get("exif")
    small = img.resize((img.width // 2, img.height // 2), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if src.ext.lower() == "png":
        small.save(buf, "PNG")
    else:
        small.save(buf, "JPEG", quality=80, **({"exif": exif} if exif else {}))
    return Made(src.kind, src.stem, src.ext, buf.getvalue())


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


def generate(out: Path, n: int = 300, seed: int = 0, nested: bool = False) -> Manifest:
    rng = random.Random(seed)
    kinds = dict(KINDS)
    categories = list(BASE_CATEGORIES)
    if nested:
        kinds.update(NESTED_KINDS)
        categories += NESTED_CATEGORIES
    kind_table = [(k, w) for k, (_, w) in sorted(kinds.items())]

    if out.exists():
        shutil.rmtree(out)
    (out / "input").mkdir(parents=True)
    for cat in categories:
        (out / "output" / cat).mkdir(parents=True)
    manifest = Manifest(seed=seed, n=n, nested=nested, categories=sorted(categories))

    # ---- pre-seeded output examples (clean names, a few per category)
    namer_out = _Namer()
    for kind, (cat, _) in sorted(kinds.items()):
        count = 2 if kind == "code_project" else (3 if kind not in ("lecture_slides",) else 2)
        for _ in range(count):
            m = GENERATORS[kind](rng)
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
        entry = Entry(rel_path=rel, category=kinds[kind][0], kind=kind, **entry_kw)  # type: ignore[arg-type]
        _write(out / "input", rel, m)
        p = _Placed(m, entry, dir_)
        placed.append(p)
        return p

    base: list[_Placed] = []
    for _ in range(n_base):
        kind = _weighted(rng, kind_table)
        m = GENERATORS[kind](rng)
        dir_ = _weighted(rng, [(d, w) for d, w in INPUT_DIRS])
        if m.is_bundle:  # keep projects near the top level, as people do
            dir_ = rng.choice(["", "", "Desktop stuff"])
        base.append(place(m, {}, dir_, _full_name(m, _messy(rng, m.stem))))

    # misleading names (~5% of non-bundle base files)
    candidates = [b for b in base if not b.made.is_bundle]
    n_mis = round(0.05 * len(base))
    for b in rng.sample(candidates, min(n_mis, len(candidates))):
        pool = GENERIC_STEMS + CROSS_STEMS.get(b.made.kind, []) * 2
        if b.made.kind in IMAGE_KINDS:
            pool = CROSS_STEMS[b.made.kind]
        new_stem = rng.choice(pool)
        old = out / "input" / b.entry.rel_path
        new_name = namer.claim(b.dir, f"{new_stem}.{b.made.ext}")
        new_rel = f"{b.dir}/{new_name}" if b.dir else new_name
        old.rename(out / "input" / new_rel)
        b.entry.rel_path = new_rel
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
        place(src.made, {"dup_of": src.entry.rel_path, "misleading_name": src.entry.misleading_name},
              dir_, derived_name(src, style))

    # resized image copies
    pool_img = [b for b in base if b.made.kind in IMAGE_KINDS]
    for src in rng.sample(pool_img, min(n_resized, len(pool_img))):
        m = make_resized(src.made)
        stem = Path(src.entry.rel_path).stem
        dir_ = src.dir if rng.random() < 0.5 else _weighted(rng, list(INPUT_DIRS))
        place(m, {"near_dup_of": src.entry.rel_path, "misleading_name": src.entry.misleading_name},
              dir_, f"{stem}{rng.choice(['_small', '_resized', ' (1)', '_web'])}.{m.ext}")

    # edited docs
    pool_doc = [b for b in base if b.made.lines is not None]
    for src in rng.sample(pool_doc, min(n_version, len(pool_doc))):
        m = make_version(rng, src.made)
        style = rng.choice(["v2", "final", "num", "copy"])
        place(m, {"version_of": src.entry.rel_path, "misleading_name": src.entry.misleading_name},
              src.dir, derived_name(src, style))

    manifest.entries = sorted((p.entry for p in placed), key=lambda e: e.rel_path)
    (out / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--nested", action="store_true")
    a = ap.parse_args()
    m = generate(a.out, a.n, a.seed, a.nested)
    kinds: dict[str, int] = {}
    for e in m.entries:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    print(f"wrote {len(m.entries)} input items, {len(m.examples)} examples to {a.out}")
    for k in sorted(kinds):
        print(f"  {k}: {kinds[k]}")


if __name__ == "__main__":
    main()
