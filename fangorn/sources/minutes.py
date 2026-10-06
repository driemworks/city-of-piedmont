"""
Piedmont City Council minutes → Fangorn records, read the way any stranger can: the public
minutes page (https://www.piedmontcity.org/government/minutes) lists every meeting, and each
links its official PDF. Two record types:

- piedmont.meeting.v1: one per meeting — the whole minutes text, its PDF and the PDF's sha256.
- piedmont.item.v1: one per paragraph of business ("Resolution 2024-01 was the next item…"),
  with the page it is on, so a result jumps straight to that page of the official record.

The minutes are already public records, so nothing is withheld: what the PDF says is what is
searchable. The sha256 is computed here from the bytes actually parsed; a mismatch with the
city's own fingerprint is printed, never hidden.

    python -m sources.minutes --output-dir .ship/stage/piedmont-minutes --cache-dir .ship/cache/minutes
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.request

from quickbeam import SourceBase

PAGE = "https://www.piedmontcity.org/government/minutes"
UA = "Mozilla/5.0 (compatible; piedmont-minutes/1.0; +https://www.piedmontcity.org)"
MEETING, ITEM = "piedmont.meeting.v1", "piedmont.item.v1"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def listing(html: str) -> list[dict]:
    """Every minutes row the page renders. Next.js streams its server data as
    self.__next_f.push([1,"<json string>"]) chunks; joined, they hold one object per row."""
    blob = "".join(json.loads(f'"{m}"') for m in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html))
    dec, rows, seen = json.JSONDecoder(), [], set()
    for m in re.finditer(r'\{"id":"[0-9a-f-]{36}","title"', blob):
        obj, _ = dec.raw_decode(blob, m.start())
        if obj.get("file_url") and obj.get("meeting_date") and obj["id"] not in seen:
            seen.add(obj["id"])
            rows.append({k: obj.get(k) for k in ("id", "title", "meeting_date", "file_url", "sha256")})
    return rows


def pages_text(data: bytes) -> list[str]:
    """Text per page. The PDFs carry no space glyphs, only gaps; a tight word_margin turns
    the gaps back into spaces ("TheCityCouncil" → "The City Council")."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTTextContainer
    out = []
    for page in extract_pages(io.BytesIO(data), laparams=LAParams(word_margin=0.02)):
        out.append("\n\n".join(el.get_text() for el in page if isinstance(el, LTTextContainer)))
    return out


# Letterhead at the top of a page: "CITY COUNCIL", "CITY OF PIEDMONT", the date, the time.
HEAD = re.compile(r"(?i)^(city council|city of piedmont|special called meeting|.*organizational meeting.*|"
                  r"[a-z]+ ?\d{1,2}, ?\d{4}|\d{1,2}:\d{2} ?[ap]\.?m\.?|page \d+.*)$")


def paragraphs(pages: list[str]) -> list[tuple[int, str]]:
    """(page, paragraph) in reading order. A block that does not end a sentence runs on into
    the next one, across a page break too, and keeps the page it started on."""
    out: list[list] = []
    for n, text in enumerate(pages, 1):
        for block in re.split(r"\n\s*\n", text):
            s = re.sub(r"\s+", " ", block).strip()
            if not s or HEAD.match(s) or re.fullmatch(r"[_\s]*(clerk|mayor|city clerk|\s)*[_\s]*", s, re.I):   # signature lines
                continue
            if out and (not re.search(r"[.!?:)\"”]$", out[-1][1]) or ABBR.search(out[-1][1])):
                out[-1][1] += " " + s
            else:
                out.append([n, s])
    return [(n, s) for n, s in out]


ABBR = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|Jr|Sr|No)\.$")


# What a paragraph is, by what it says first. Order matters: a resolution approving a bid is a resolution.
KINDS = [
    ("call to order", r"\bmet in (regular|special|called)|present were|invocation|pledge of allegiance"),
    ("ordinance", r"\bordinance\b"),
    ("resolution", r"\bresolution\b"),
    ("bills", r"\bbills\b"),
    ("minutes", r"\bminutes\b.*\bapproved|approve the minutes"),
    ("appointment", r"\b(re)?appoint"),
    ("bid", r"\bbids?\b|\bproposals?\b"),
    ("contract", r"\bcontracts?\b|\bagreements?\b"),
    ("purchase", r"\bpurchas|\bbuy\b|\bquotes?\b"),
    ("public hearing", r"public hearing"),
    ("visitors", r"\bvisitors?\b|public comments?"),
    ("reports", r"\breports?\b|council reports|clerk|mayor'?s comments"),
    ("adjournment", r"\badjourn"),
]


def kind_of(text: str) -> str:
    head = text[:240].lower()
    return next((k for k, rx in KINDS if re.search(rx, head)), "business")


def heading_of(text: str) -> str:
    """The paragraph's first sentence, as a title."""
    first = re.split(r"(?<!\bMr\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bMrs\.)(?<=[a-z0-9)][.!?])\s+(?=[A-Z])", text, maxsplit=1)[0]
    return first if len(first) <= 140 else first[:137].rsplit(" ", 1)[0] + "…"


def descriptor(title: str) -> str:
    """"MINUTES January 2, 2025 Special Called Meeting" → "Special Called Meeting"; a regular meeting → "Regular Meeting"."""
    rest = re.sub(r"(?i)^minutes\s*|[A-Za-z]+ \d{1,2}, \d{4}", "", title or "").strip(" -–—:,")
    return re.sub(r"\s+", " ", rest) or "Regular Meeting"


class MinutesSource(SourceBase):
    name = "minutes"
    stems = {MEETING: "meetings", ITEM: "items"}
    snapshot_stems = {"meetings", "items"}   # the page is read whole every run; the latest wins

    def add_source_args(self, p):
        p.add_argument("--page", default=PAGE, help="The public page that lists the minutes")
        p.add_argument("--cache-dir", default=".ship/cache/minutes", help="PDFs, by their sha256 of the URL")
        p.add_argument("--delay", type=float, default=1.0, help="Seconds between PDF downloads")
        p.add_argument("--max-docs", type=int, default=0)

    def read(self, cursor, args):
        rows = sorted(listing(fetch(args.page).decode("utf-8", "ignore")), key=lambda r: r["meeting_date"], reverse=True)
        rows = rows[: args.max_docs or None]
        print(f"[minutes] {len(rows)} meeting(s) listed on {args.page}")
        os.makedirs(args.cache_dir, exist_ok=True)
        out = []
        for r in rows:
            path = os.path.join(args.cache_dir, hashlib.sha256(r["file_url"].encode()).hexdigest()[:24] + ".pdf")
            try:
                if not os.path.exists(path):
                    data = fetch(r["file_url"])
                    with open(path, "wb") as f:
                        f.write(data)
                    time.sleep(args.delay)
                data = open(path, "rb").read()
                if not data.startswith(b"%PDF"):
                    raise ValueError("not a PDF")
                pages = pages_text(data)
            except Exception as err:  # noqa: BLE001 — one bad file must not sink the run
                print(f"[minutes]   skip {r['file_url']}: {err}", file=sys.stderr)
                continue
            sha = hashlib.sha256(data).hexdigest()
            if r.get("sha256") and r["sha256"] != sha:
                print(f"[minutes]   {r['meeting_date']}: PDF sha256 {sha[:12]}… differs from the city's {r['sha256'][:12]}…", file=sys.stderr)
            out.append({**r, "pdf_sha256": sha, "pages": pages})
        return out

    def build_graph(self, records):
        meetings, items = [], []
        for r in records:
            date, desc, url = r["meeting_date"], descriptor(r["title"]), r["file_url"]
            meeting_id = f"piedmont:meeting:{date}"
            label = f"City Council {desc} · {date}"
            paras = paragraphs(r["pages"])
            common = {"date": date, "year": date[:4], "meeting_type": desc, "body": "City Council",
                      "city": "Piedmont", "state": "AL", "pdf_sha256": r["pdf_sha256"]}
            meetings.append({"name": meeting_id, "fields": {
                "entityType": MEETING, "meeting_id": meeting_id, "title": label, "url": url,
                "pages": len(r["pages"]), "items": len(paras), **common,
                "text": f"Piedmont City Council {desc}, {date}. " + " ".join(s for _, s in paras)}})
            for i, (page, s) in enumerate(paras, 1):
                item_id = f"{meeting_id}#{i}"
                items.append({"name": item_id, "fields": {
                    "entityType": ITEM, "item_id": item_id, "meeting_id": meeting_id, "heading": heading_of(s),
                    "meeting": label, "kind": kind_of(s), "n": i, "page": page,
                    "url": f"{url}#page={page}", **common, "text": s}})
        return {MEETING: meetings, ITEM: items}, []

    def next_cursor(self, records, prev):
        return prev   # a snapshot: the whole listing every run


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:
        assert descriptor("MINUTES January 2, 2025 Special Called Meeting") == "Special Called Meeting"
        assert descriptor("MINUTES September 1, 2026") == "Regular Meeting"
        assert kind_of("Resolution 2024-01 was the next item on the agenda.") == "resolution"
        assert kind_of("The bills totaling $635,808.67 were then presented") == "bills"
        assert kind_of("Terry Kiser made the motion to appoint Terry Batey to the Zoning Board") == "appointment"
        got = paragraphs(["CITY COUNCIL\n\nJANUARY 16, 2024\n\nThe bills were\n\n", "approved.\n\nMeeting adjourned."])
        assert got == [(1, "The bills were approved."), (2, "Meeting adjourned.")], got
        assert paragraphs(["Visitor comments- Mr.\n\nSmith spoke.\n\n____ ____\nCLERK MAYOR"]) == [(1, "Visitor comments- Mr. Smith spoke.")]
        assert heading_of("Visitor comments- Mr. Smith spoke. Then more.") == "Visitor comments- Mr. Smith spoke."
        assert kind_of("Councilman South made a motion to approve the purchase of a new knuckle boom truck") == "purchase"
        html = 'self.__next_f.push([1,"x{\\"id\\":\\"%s\\",\\"title\\":\\"MINUTES May 6, 2025\\",\\"meeting_date\\":\\"2025-05-06\\",\\"file_url\\":\\"https://x/a.pdf\\",\\"sha256\\":\\"ab\\"}"])' % ("0" * 36)
        assert listing(html) == [{"id": "0" * 36, "title": "MINUTES May 6, 2025", "meeting_date": "2025-05-06", "file_url": "https://x/a.pdf", "sha256": "ab"}]
        print("ok")
        sys.exit(0)
    from quickbeam.ingest.scrapers.harness import run_source
    run_source(MinutesSource(), sys.argv[1:])
