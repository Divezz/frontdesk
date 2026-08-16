#!/usr/bin/env python3
"""
build.py — turn whatever you know about a business into a working FRONTDESK demo.

Three ways in, because most small businesses in Manchester and Salford don't
have a website worth scraping:

    python build.py --url https://someshop.co.uk     # they have a site
    python build.py --notes bolton-gym.txt           # you pasted their Google
                                                     # listing, Instagram bio,
                                                     # or a price list
    python build.py --interview                      # you asked them 9 questions

The interview is the one that matters. A barber with no website can't be sold a
better website — but he can be shown a thing that answers his customers at 10pm,
built from a five-minute conversation at his chair.

    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path

from anthropic import Anthropic

MODEL = "claude-sonnet-4-5"
HERE = Path(__file__).parent
TEMPLATE = HERE / "index.html"
OUT_DIR = HERE / "demos"
USER_AGENT = "FrontdeskDemoBot/1.0 (+builds a demo for the site owner)"

WORTH_READING = (
    "price", "pricing", "cost", "rate", "menu", "service", "treatment",
    "about", "contact", "faq", "book", "booking", "hour", "opening",
    "timetable", "membership", "class",
)
MAX_PAGES = 6
MAX_BYTES = 900_000
MAX_CORPUS = 18_000


# --------------------------------------------------------------- scraping
class Extractor(HTMLParser):
    """Pull visible text and links out of HTML. Standard library only."""

    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.links: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.chunks.append(data.strip())

    @property
    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self.chunks))


def fetch(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if "html" not in resp.headers.get("Content-Type", ""):
                return None
            raw = resp.read(MAX_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        print(f"  ! couldn't read {url} ({exc})", file=sys.stderr)
        return None


def crawl(start: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(start)
    if not parsed.scheme:
        start = "https://" + start
        parsed = urllib.parse.urlparse(start)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(urllib.parse.urljoin(origin, "/robots.txt"))
    try:
        robots.read()
    except Exception:
        robots = None

    def permitted(u: str) -> bool:
        return robots.can_fetch(USER_AGENT, u) if robots else True

    if not permitted(start):
        sys.exit("robots.txt disallows this site — ask the owner directly instead.")

    print(f"→ reading {start}")
    home = fetch(start)
    if not home:
        sys.exit("Couldn't read the homepage. Check the URL, or use --interview instead.")

    ex = Extractor()
    ex.feed(home)
    corpus = [f"=== homepage ({start}) ===\n{ex.text}"]
    seen = {start.rstrip("/")}

    candidates: list[str] = []
    for href in ex.links:
        absolute = urllib.parse.urljoin(start, href).split("#")[0].rstrip("/")
        if urllib.parse.urlparse(absolute).netloc != parsed.netloc:
            continue
        if absolute in seen or absolute in candidates:
            continue
        if any(w in absolute.lower() for w in WORTH_READING):
            candidates.append(absolute)

    for url in candidates[: MAX_PAGES - 1]:
        if not permitted(url):
            continue
        time.sleep(0.6)  # don't hammer a small business's server
        print(f"→ reading {url}")
        html = fetch(url)
        if not html:
            continue
        sub = Extractor()
        sub.feed(html)
        if len(sub.text) > 120:
            corpus.append(f"=== {url} ===\n{sub.text}")

    return "\n\n".join(corpus)[:MAX_CORPUS], parsed.netloc.replace("www.", "").split(".")[0]


# -------------------------------------------------------------- interview
QUESTIONS = [
    ("name", "Business name", True),
    ("what", "What do they do? (barber, gym, nail salon, garage…)", True),
    ("hours", "Opening hours — as they'd say them", True),
    ("prices", "Prices. One per line, blank line to finish", True),
    ("where", "Address, and anything about parking", False),
    ("booking", "How do customers book? Phone number, app, walk-ins?", True),
    ("policy", "Deposits, cancellations, lateness — any rules?", False),
    ("asked", "What three things do customers ask you constantly?", False),
    ("extra", "Anything else worth knowing? Kids, cards, dogs, access…", False),
]


def read_block(prompt: str) -> str:
    """Read an answer. A blank line ends it; a blank first line skips the question."""
    print(f"\n{prompt}")
    lines: list[str] = []
    while True:
        try:
            line = input("  ").strip()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return "\n".join(lines)


def interview() -> tuple[str, str]:
    print("=" * 62)
    print("  FRONTDESK — new business")
    print("  Nine questions. Blank line moves you on. Ctrl-C to bail.")
    print("=" * 62)
    answers: dict[str, str] = {}
    for key, prompt, required in QUESTIONS:
        while True:
            value = read_block(prompt)
            if value or not required:
                answers[key] = value
                break
            print("  (needed — this one can't be blank)")

    notes = "\n\n".join(f"=== {prompt} ===\n{answers[key]}" for key, prompt, _ in QUESTIONS if answers.get(key))
    return notes, answers["name"]


# ---------------------------------------------------------------- drafting
DRAFT_PROMPT = """Below is everything known about a small business.

Build the knowledge base for an after-hours assistant that answers their
customers' questions.

Return ONLY a JSON object, no commentary, matching exactly this shape:

{{
  "business": "the trading name",
  "host": "one lowercase word, no spaces",
  "duty": "ON DUTY <closing>-<opening> using their real hours if known, else ON DUTY 18:00-09:00",
  "channels": "web · sms · instagram",
  "greeting": "one or two sentences: the business is closed right now but you can still help, naming 2-3 things you can help with that this business actually offers",
  "suggested": ["4 short questions a real customer would type, lowercase, no question marks"],
  "knowledge": [
    {{
      "id": "one lowercase word, e.g. pricing",
      "k": ["8-14 words or short phrases a customer might use for this topic, lowercase"],
      "a": "the answer, in the business's own voice, using ONLY facts given below"
    }}
  ],
  "fallback": "what to say when the answer isn't in the knowledge base: admit it plainly, refuse to guess, offer to take their details"
}}

Hard rules:
- Every fact must appear in the source material below. Never invent a price,
  phone number, address or opening time. If prices aren't given, don't create a
  pricing entry — an assistant that quotes a made-up price destroys the owner's
  trust on day one.
- 6 to 12 entries, covering what customers actually ask: prices, hours,
  location and parking, booking, cancellation, contact, plus whatever else this
  particular business is about.
- Keywords are what a rushed customer types at 11pm ("how much", "open sunday",
  "park", "get in"), not formal terms. Include the obvious misspellings.
- For price lists use \\n between lines and pad with spaces to align.
- Write like someone who works there. No corporate filler, no emoji.

SOURCE MATERIAL
---------------
{corpus}"""


def draft_config(corpus: str, fallback_name: str) -> dict:
    print("\n→ drafting knowledge base")
    client = Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": DRAFT_PROMPT.format(corpus=corpus)}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)

    try:
        cfg = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            sys.exit("Model didn't return usable JSON — re-run.")
        cfg = json.loads(text[start : end + 1])

    cfg.setdefault("business", fallback_name.title())
    cfg.setdefault("host", re.sub(r"[^a-z0-9]", "", fallback_name.lower()) or "frontdesk")
    cfg.setdefault("channels", "web · sms · instagram")
    cfg.setdefault("duty", "ON DUTY 18:00–09:00")

    if not cfg.get("knowledge"):
        sys.exit("No knowledge entries produced — not enough source material.")
    for entry in cfg["knowledge"]:
        entry["k"] = [k.lower() for k in entry.get("k", [])]
    return cfg


def render(cfg: dict, slug: str) -> Path:
    template = TEMPLATE.read_text(encoding="utf-8")
    block = "// <<<CONFIG>>>\nconst CONFIG = " + json.dumps(cfg, indent=2, ensure_ascii=False) + ";\n// <<<END CONFIG>>>"
    out, count = re.subn(
        r"// <<<CONFIG>>>.*?// <<<END CONFIG>>>",
        lambda _: block,          # lambda avoids backslash escaping in the replacement
        template,
        flags=re.DOTALL,
    )
    if count != 1:
        sys.exit("Config markers missing from index.html — did the template change?")
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{slug}.html"
    path.write_text(out, encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a FRONTDESK demo for a business.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="their website")
    src.add_argument("--notes", type=Path, help="a text file: Google listing, Instagram bio, price list")
    src.add_argument("--interview", action="store_true", help="answer nine questions about them")
    ap.add_argument("--name", help="override the output filename")
    args = ap.parse_args()

    if args.url:
        corpus, guess = crawl(args.url)
        print(f"→ {len(corpus):,} characters of site text")
    elif args.notes:
        if not args.notes.exists():
            sys.exit(f"No such file: {args.notes}")
        corpus = args.notes.read_text(encoding="utf-8")[:MAX_CORPUS]
        guess = args.notes.stem
        print(f"→ {len(corpus):,} characters of notes")
    else:
        corpus, guess = interview()

    cfg = draft_config(corpus, guess)
    slug = re.sub(r"[^a-z0-9]+", "-", (args.name or cfg["business"]).lower()).strip("-")
    path = render(cfg, slug)

    print(f"\n✓ {cfg['business']} — {len(cfg['knowledge'])} entries")
    print(f"  {path}")
    print("\n  Open it. Ask it three questions. Fix anything wrong in the CONFIG block.")
    print("  Never send one you haven't read — a wrong price costs you the client.")


if __name__ == "__main__":
    main()
