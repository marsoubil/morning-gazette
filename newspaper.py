#!/usr/bin/env python3
"""
The Morning Gazette
-------------------
Collects today's news from RSS feeds, asks Gemini to rewrite the headlines in
1920s newspaper style, and builds a printable broadsheet front page
(HTML, and optionally PDF) with a lead story, side column, coloured section
boxes and a reference for every story.

Usage:
    python3 newspaper.py              # build today's paper
    python3 newspaper.py --open       # build it and open it in your browser
    python3 newspaper.py --pdf        # also make a PDF (needs Google Chrome)
    python3 newspaper.py --print      # make the PDF and send it to the default printer
    python3 newspaper.py --demo       # sample stories only: tests the layout, no internet needed
    python3 newspaper.py --no-ai      # real news, original headlines (skips Gemini)
"""

import argparse
import datetime
import html
import json
import os
import re
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

# ======================================================================
#  SETTINGS - this is the part you edit to make the paper yours
# ======================================================================

PAPER_NAME = "The Morning Gazette"
PAPER_TAGLINE = "Sydney, New South Wales"
PRICE = "Price: Two Pence"

# Sections of the paper, in order.
#   slot:   "front" = lead story + side column (use for ONE section)
#           "wide"  = a box across the whole page
#           "box"   = a half-width box
#   count:  how many stories to show
#   color:  the section's accent colour
#   feeds:  (feed address, name shown as the source)
#   prefer: stories mentioning these words are moved to the top of the section
SECTIONS = [
    {
        "name": "Sydney & Australia",
        "slot": "front",
        "color": "#8b1e1e",
        "count": 7,
        "feeds": [
            ("https://www.abc.net.au/news/feed/5555986/rss.xml", "ABC News"),
            ("https://www.abc.net.au/news/feed/10719986/rss.xml", "ABC News"),
            ("https://www.sbs.com.au/news/topic/latest/feed", "SBS News"),
        ],
        "prefer": ["sydney", "nsw", "new south wales"],
    },
    {
        "name": "Finance & Markets",
        "slot": "box",
        "color": "#1f3a5f",
        "count": 4,
        "feeds": [
            ("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20409666", "CNBC"),
            ("https://www.cnbc.com/id/10000664/device/rss/rss.html", "CNBC"),
            ("https://www.abc.net.au/news/feed/104217374/rss.xml", "ABC Business"),
            ("https://www.cnbc.com/id/20910258/device/rss/rss.html", "CNBC"),
        ],
        "prefer": ["stock", "shares", "wall street", "asx", "s&p", "nasdaq", "dow", "oil", "gold", "bitcoin", "rate"],
    },
    {
        "name": "Sporting Life",
        "slot": "box",
        "color": "#1f4e3d",
        "count": 4,
        "feeds": [
            ("https://www.abc.net.au/news/feed/103728570/rss.xml", "ABC Sport"),
            ("http://feeds.bbci.co.uk/sport/rss.xml", "BBC Sport"),
        ],
        "prefer": [],
    },
    {
        "name": "Health & Nutrition",
        "slot": "box",
        "color": "#9a6a12",
        "count": 4,
        "feeds": [
            ("https://www.sciencedaily.com/rss/health_medicine/nutrition.xml", "ScienceDaily"),
            ("https://sciencedaily.com/rss/health_medicine/dietary_supplements.xml", "ScienceDaily"),
            ("https://www.sciencedaily.com/rss/health_medicine/diet_and_weight_loss.xml", "ScienceDaily"),
            ("http://feeds.bbci.co.uk/news/health/rss.xml", "BBC News"),
        ],
        "prefer": ["peptide", "glp-1", "semaglutide", "longevity", "supplement", "protein",
                   "nutrition", "gut", "metabolic", "creatine", "vitamin", "diet"],
    },
]

# Gemini models to try, in order. If one is not available to you, the next is used.
MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.8-flash", "gemini-3-flash-preview"]

# Where to find Chrome on a Mac (used only for --pdf / --print)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# ======================================================================
#  Everything below is the machinery. You don't need to change it.
# ======================================================================

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
ATOM = "{http://www.w3.org/2005/Atom}"


def load_env():
    """Read GEMINI_API_KEY from a file called .env next to this script (if present)."""
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def ask_for_key():
    """First run only: ask for the Gemini key and save it next to this script in .env"""
    if os.environ.get("GEMINI_API_KEY"):
        return
    print()
    print("Gemini needs your API key to write the 1920s headlines.")
    print("Paste it here (or type it), then press Enter.")
    print("Or just press Enter to skip, and the paper will use the original headlines.")
    try:
        key = input("API key: ").strip().strip('"').strip("'").replace(" ", "")
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not key:
        print("No key entered, skipping Gemini this time.")
        return
    env_file = HERE / ".env"
    env_file.write_text(f"GEMINI_API_KEY={key}\n")
    try:
        env_file.chmod(0o600)
    except OSError:
        pass
    os.environ["GEMINI_API_KEY"] = key
    print(f"Saved. You won't be asked again. (Stored privately in {env_file})")
    print()


def clean(text, limit=450):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def fetch_feed(url, label, limit):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (MorningGazette)"})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read()
    root = ET.fromstring(data)
    items = []

    raw = []
    for item in root.iter("item"):  # standard RSS
        raw.append((item.findtext("title"), item.findtext("description"), item.findtext("link")))
    if not raw:  # Atom feeds
        for entry in root.iter(ATOM + "entry"):
            link_el = entry.find(ATOM + "link")
            link = link_el.get("href") if link_el is not None else ""
            desc = entry.findtext(ATOM + "summary") or entry.findtext(ATOM + "content")
            raw.append((entry.findtext(ATOM + "title"), desc, link))

    for title, desc, link in raw:
        title = clean(title, 200)
        if not title:
            continue
        items.append({"title": title, "desc": clean(desc), "link": (link or "").strip(), "source": label})
        if len(items) >= limit:
            break
    return items


def score(story, prefer):
    text = (story["title"] + " " + story["desc"]).lower()
    return sum(1 for word in prefer if word in text)


def gather_section(cfg):
    per_feed = []
    for url, label in cfg["feeds"]:
        try:
            per_feed.append(fetch_feed(url, label, 15))
        except Exception as exc:  # one broken feed should never stop the paper
            print(f"  ! Could not read a {label} feed for '{cfg['name']}': {str(exc)[:120]}")

    merged, seen = [], set()
    depth = max((len(items) for items in per_feed), default=0)
    for i in range(depth):  # take turns between feeds so one source doesn't dominate
        for items in per_feed:
            if i < len(items):
                story = items[i]
                key = re.sub(r"\W+", " ", story["title"].lower()).strip()
                if key not in seen:
                    seen.add(key)
                    merged.append(story)

    prefer = [w.lower() for w in cfg.get("prefer", [])]
    if prefer:
        merged.sort(key=lambda s: -score(s, prefer))  # stable: keeps feed order within ties
    chosen = merged[: cfg["count"]]
    for story in chosen:
        story["section"] = cfg["name"]
    print(f"  {cfg['name']}: {len(chosen)} stories")
    return chosen


def gather_stories():
    stories = []
    for cfg in SECTIONS:
        stories += gather_section(cfg)
    return stories


def demo_stories():
    sample = {
        "Sydney & Australia": [
            ("Harbour ferry timetable updated after review", "Commuters will see changes to weekend services from next month. The review covered all inner-harbour routes."),
            ("Council approves new park near the waterfront", "Residents welcomed the plan, which includes walking paths and shade trees."),
            ("Heatwave expected across the state this week", "Forecasters advise people to stay hydrated and check on neighbours."),
            ("New rail timetable starts Monday", "Operators say trips will be more frequent at peak times."),
            ("Bushfire season outlook released", "Authorities urge households to prepare an emergency plan."),
            ("Local markets return to the Rocks", "Stallholders expect large crowds this weekend."),
            ("Beaches reopen after water-quality tests", "Officials confirmed results are within safe limits."),
            ("University announces new research centre", "The centre will focus on ocean science."),
            ("Library hours extended for exam season", "Branches will open late on weeknights."),
        ],
        "Finance & Markets": [
            ("Markets steady as investors await rate decision", "Share prices moved little as traders watched for central bank news."),
            ("Oil slips as supply worries ease", "Crude futures fell for a second session."),
            ("Tech shares lead Wall Street higher", "Chipmakers posted the strongest gains of the day."),
            ("Gold holds near record as dollar softens", "Bullion has been supported by steady buying."),
            ("Australian dollar edges up against greenback", "Traders cited stronger commodity prices."),
            ("Bank profits beat forecasts", "Lenders reported lower bad-debt charges."),
        ],
        "Sporting Life": [
            ("Home side wins thriller in final minute", "A late goal sealed the result in front of a record crowd."),
            ("Swimmer breaks national record at championships", "The new mark beats the previous best by almost half a second."),
            ("Cricket squad named for summer series", "Two new faces join the touring party."),
            ("Rugby league finals venue confirmed", "Organisers expect a sell-out."),
            ("Tennis star advances in straight sets", "She next faces the second seed."),
        ],
        "Health & Nutrition": [
            ("Study links protein intake to healthy ageing", "Researchers followed thousands of adults over a decade."),
            ("Researchers examine new peptide therapies", "Early trials show promise but larger studies are needed."),
            ("Gut bacteria may influence weight, scientists say", "The findings are from a small study."),
            ("Experts weigh benefits of creatine supplements", "Reviews suggest benefits for muscle and possibly memory."),
            ("Vitamin D levels vary across the seasons", "Doctors suggest checking levels before supplementing."),
        ],
    }
    out = []
    n = 0
    for section, items in sample.items():
        for title, desc in items:
            n += 1
            out.append({
                "section": section, "title": title, "desc": desc,
                "link": f"https://example.com/demo/story-{n}", "source": "Demo Wire",
            })
    return out


def rewrite_with_gemini(stories):
    """Ask Gemini for 1920s-style headlines and blurbs. Returns True on success."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  ! No GEMINI_API_KEY found (see README). Using original headlines.")
        return False
    try:
        from google import genai
    except ImportError:
        print("  ! The google-genai package is not installed. Using original headlines.")
        return False

    lead_section = next((c["name"] for c in SECTIONS if c["slot"] == "front"), None)
    payload = []
    seen_sections = set()
    for i, s in enumerate(stories):
        first_in_section = s["section"] not in seen_sections
        seen_sections.add(s["section"])
        if first_in_section and s["section"] == lead_section:
            length = "four sentences"
        elif first_in_section:
            length = "two sentences"
        else:
            length = "one short sentence"
        payload.append({
            "id": i, "section": s["section"], "title": s["title"], "text": s["desc"],
            "blurb_length": length,
        })

    prompt = (
        "You are the editor of a 1920s big-city broadsheet newspaper (think New York or Detroit). "
        "For each story below, write:\n"
        "  - 'headline': a punchy period-style headline, at most 12 words\n"
        "  - 'blurb': 1920s newspaper prose summarising the story, with the number of sentences "
        "given in 'blurb_length'\n"
        "Rules: use ONLY facts contained in the supplied title and text. Do NOT invent names, "
        "numbers, quotes or details. Keep the era's flavour but stay readable. Never joke about "
        "tragedy. For health and nutrition stories, stay neutral and factual: do not add health "
        "claims, cures or advice that the text does not contain.\n"
        "Return ONLY a JSON array of objects with keys: id, headline, blurb.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    client = genai.Client(api_key=api_key)
    for model in MODELS:
        for attempt in range(1, 4):  # if Google is busy, wait and try again
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
                text = (response.text or "").strip()
                text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
                results = json.loads(text)
                for entry in results:
                    idx = int(entry["id"])
                    stories[idx]["headline"] = clean(entry.get("headline", ""), 160)
                    stories[idx]["blurb"] = clean(entry.get("blurb", ""), 700)
                print(f"  Headlines written by {model}")
                return True
            except Exception as exc:
                message = str(exc)
                busy = any(word in message for word in ("503", "UNAVAILABLE", "429", "overloaded"))
                print(f"  ! {model} failed (try {attempt} of 3): {message[:120]}")
                if busy and attempt < 3:
                    print("    Google is busy. Waiting 20 seconds, then trying again...")
                    time.sleep(20)
                    continue
                break
    print("  ! No Gemini model worked. Using original headlines.")
    return False


CSS = """
@page { size: A4 portrait; margin: 8mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
:root { --ink: #1c1a16; --paper: #f3ead2; --rule: #8a7e63; }
body {
  margin: 0; padding: 18px; background: #d9cfb8;
  font-family: 'IM Fell English', 'Georgia', 'Times New Roman', serif; color: var(--ink);
}
a { color: inherit; text-decoration: none; border-bottom: 1px dotted currentColor; }
.paper {
  max-width: 1040px; margin: 0 auto; padding: 22px 28px 26px;
  background: var(--paper); border: 1px solid var(--rule);
  box-shadow: 0 2px 18px rgba(60,45,20,.35);
}
.dateline {
  display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  font-size: 11.5px; letter-spacing: .12em; text-transform: uppercase;
  border-top: 3px double var(--ink); border-bottom: 1px solid var(--ink); padding: 5px 2px;
}
.masthead {
  font-family: 'UnifrakturCook', 'Old English Text MT', 'Georgia', serif;
  text-align: center; font-size: 72px; line-height: 1.05; margin: 10px 0 2px; font-weight: 700;
}
.tagline {
  text-align: center; font-style: italic; font-size: 14px; padding-bottom: 8px;
  border-bottom: 4px double var(--ink); margin-bottom: 16px;
}
.front { display: grid; grid-template-columns: 2.1fr 1fr; gap: 22px; margin-bottom: 18px; }
.lead { border-right: 1px solid var(--rule); padding-right: 22px; }
.kicker {
  display: inline-block; background: var(--accent); color: var(--paper);
  font-family: 'Playfair Display', 'Georgia', serif; text-transform: uppercase;
  letter-spacing: .2em; font-size: 11px; padding: 3px 10px; margin-bottom: 8px;
}
.lead h2 {
  font-family: 'Playfair Display', 'Georgia', serif; font-size: 40px; line-height: 1.06;
  margin: 2px 0 10px; text-transform: uppercase; font-weight: 900;
}
.lead .blurb { column-count: 2; column-gap: 22px; column-rule: 1px solid var(--rule); font-size: 16px; line-height: 1.45; text-align: justify; hyphens: auto; }
.stoppress { background: rgba(139,30,30,.07); border-top: 4px solid var(--accent); padding: 8px 12px 4px; }
.stoppress h3 {
  font-family: 'Playfair Display', 'Georgia', serif; text-transform: uppercase; letter-spacing: .22em;
  font-size: 13px; text-align: center; margin: 0 0 8px; color: var(--accent);
}
.brief { padding: 7px 0; border-bottom: 1px dotted var(--rule); }
.brief:last-child { border-bottom: none; }
.brief h5 { font-family: 'Playfair Display', 'Georgia', serif; font-size: 14.5px; line-height: 1.15; margin: 0 0 2px; text-transform: uppercase; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 10px; }
.box { border: 1px solid var(--ink); background: rgba(255,255,255,.18); }
.box.wide { grid-column: 1 / -1; }
.box h3 {
  margin: 0; background: var(--accent); color: var(--paper);
  font-family: 'Playfair Display', 'Georgia', serif; text-transform: uppercase; letter-spacing: .28em;
  font-size: 14px; text-align: center; padding: 6px 4px;
}
.box .body { padding: 8px 11px 2px; }
.box.wide .body { column-count: 3; column-gap: 22px; column-rule: 1px solid var(--rule); }
.story { break-inside: avoid; margin-bottom: 11px; padding-bottom: 9px; border-bottom: 1px dotted var(--rule); }
.story:last-child { border-bottom: none; }
.story h4 { font-family: 'Playfair Display', 'Georgia', serif; font-size: 14.5px; line-height: 1.15; margin: 0 0 3px; text-transform: uppercase; }
.story.feature h4 { font-size: 18px; color: var(--accent); }
.story p { margin: 0; font-size: 12.5px; line-height: 1.35; text-align: justify; hyphens: auto; }
.cite { display: block; font-size: 10.5px; font-style: italic; color: #5a5242; margin-top: 3px; }
.refs { border-top: 3px double var(--ink); padding-top: 8px; }
.refs h3 { font-family: 'Playfair Display', 'Georgia', serif; text-transform: uppercase; letter-spacing: .22em; font-size: 12px; text-align: center; margin: 0 0 8px; }
.refs ol { list-style: none; margin: 0; padding: 0; column-count: 2; column-gap: 24px; font-size: 10px; line-height: 1.35; }
.refs li { break-inside: avoid; margin-bottom: 4px; word-break: break-word; }
.footer { border-top: 4px double var(--ink); margin-top: 10px; padding-top: 6px; text-align: center; font-size: 10.5px; font-style: italic; }
@media screen and (max-width: 800px) {
  .masthead { font-size: 42px; } .lead h2 { font-size: 28px; }
  .front, .grid { grid-template-columns: 1fr; }
  .lead { border-right: none; padding-right: 0; }
  .lead .blurb, .box.wide .body, .refs ol { column-count: 1; }
}
@media print {
  body { background: none; padding: 0; }
  .paper { box-shadow: none; border: none; max-width: none; padding: 0; zoom: 0.76; }
  .masthead { font-size: 62px; }
  .lead h2 { font-size: 34px; }
}
"""

FONTS = (
    "https://fonts.googleapis.com/css2?family=IM+Fell+English:ital@0;1"
    "&family=Playfair+Display:wght@700;900&family=UnifrakturCook:wght@700&display=swap"
)


def esc(text):
    return html.escape(text or "")


def headline_of(story):
    return esc(story.get("headline") or story["title"])


def blurb_of(story):
    return esc(story.get("blurb") or story["desc"])


def cite_html(story):
    link = story.get("link", "")
    label = esc(story["source"])
    inner = f"<a href='{html.escape(link, quote=True)}'>{label}</a>" if link else label
    return f"<span class='cite'>&mdash; {inner}</span>"


def short_url(link):
    parsed = urlparse(link)
    text = parsed.netloc.replace("www.", "") + parsed.path
    return text if len(text) <= 70 else text[:67] + "..."


def render(stories, ai_used):
    today = datetime.date.today()
    date_text = today.strftime("%A, %-d %B %Y")
    issue_no = today.timetuple().tm_yday

    by_section = {}
    for s in stories:
        by_section.setdefault(s["section"], []).append(s)

    # number the stories in the order they appear on the page
    ordered = []
    front_cfg = next((c for c in SECTIONS if c["slot"] == "front"), None)
    page_sections = ([front_cfg] if front_cfg else []) + [c for c in SECTIONS if c["slot"] != "front"]
    for cfg in page_sections:
        ordered += by_section.get(cfg["name"], [])
    for number, story in enumerate(ordered, start=1):
        story["ref"] = number

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{esc(PAPER_NAME)} - {esc(date_text)}</title>",
        f"<link rel='stylesheet' href='{FONTS}'>",
        f"<style>{CSS}</style></head><body><div class='paper'>",
        f"<div class='dateline'><span>Vol. I &middot; No. {issue_no}</span>"
        f"<span>{esc(date_text)}</span><span>{esc(PRICE)}</span></div>",
        f"<div class='masthead'>{esc(PAPER_NAME)}</div>",
        f"<div class='tagline'>{esc(PAPER_TAGLINE)}</div>",
    ]

    # front page: lead story + side column
    front_stories = by_section.get(front_cfg["name"], []) if front_cfg else []
    if front_stories:
        lead, rest = front_stories[0], front_stories[1:]
        parts.append(f"<div class='front' style='--accent:{front_cfg['color']}'>")
        parts.append(
            f"<div class='lead'><span class='kicker'>{esc(front_cfg['name'])} &middot; Top Story</span>"
            f"<h2>{headline_of(lead)}</h2><div class='blurb'>{blurb_of(lead)}</div>{cite_html(lead)}</div>"
        )
        parts.append("<aside class='stoppress'><h3>Stop Press</h3>")
        for s in rest:
            parts.append(f"<div class='brief'><h5>{headline_of(s)}</h5>{cite_html(s)}</div>")
        parts.append("</aside></div>")

    # coloured boxes
    parts.append("<div class='grid'>")
    for cfg in SECTIONS:
        if cfg["slot"] == "front":
            continue
        items = by_section.get(cfg["name"], [])
        if not items:
            continue
        wide = " wide" if cfg["slot"] == "wide" else ""
        parts.append(f"<div class='box{wide}' style='--accent:{cfg['color']}'><h3>{esc(cfg['name'])}</h3><div class='body'>")
        for i, s in enumerate(items):
            feature = " feature" if i == 0 else ""
            parts.append(
                f"<div class='story{feature}'><h4>{headline_of(s)}</h4>"
                f"<p>{blurb_of(s)}</p>{cite_html(s)}</div>"
            )
        parts.append("</div></div>")
    parts.append("</div>")

    credit = "Headlines set in the style of the 1920s by Gemini" if ai_used else "Original headlines shown"
    parts.append(
        f"<div class='footer'>{credit} &middot; Health and finance items are news reports, "
        "not medical or financial advice &middot; Printed fresh each morning</div>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)


def make_pdf(html_path, pdf_path):
    if not Path(CHROME).exists():
        print("  ! Google Chrome not found, so no PDF. Open the .html file and press Cmd+P to print.")
        return False
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         "--virtual-time-budget=8000", f"--print-to-pdf={pdf_path}", html_path.as_uri()],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return pdf_path.exists()


LABEL = "com.morninggazette.daily"
INSTALL_DIR = Path.home() / "Gazette"
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")


def launchctl(*args):
    try:
        return subprocess.run(["launchctl", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return None


def install_schedule(hour, minute):
    """Set the Mac to build and print the paper every day at hour:minute."""
    INSTALL_DIR.mkdir(exist_ok=True)
    target = INSTALL_DIR / "newspaper.py"
    if Path(__file__).resolve() != target.resolve():
        shutil.copy2(Path(__file__).resolve(), target)
    env_here = HERE / ".env"
    env_there = INSTALL_DIR / ".env"
    if env_here.exists() and env_here.resolve() != env_there.resolve():
        shutil.copy2(env_here, env_there)
    if env_there.exists():
        env_there.chmod(0o600)
    else:
        print("  Note: no API key saved yet, so printed papers will use the original headlines.")
        print("  Run the paper once by hand (without --schedule) and paste your key when asked.")

    log = INSTALL_DIR / "gazette.log"
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(target), "--print"],
        "WorkingDirectory": str(INSTALL_DIR),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST, "wb") as handle:
        plistlib.dump(plist, handle)

    uid = str(os.getuid())
    launchctl("bootout", f"gui/{uid}", str(PLIST))  # remove an older schedule, if any
    result = launchctl("bootstrap", f"gui/{uid}", str(PLIST))
    if result is not None and result.returncode != 0:
        print(f"  ! Could not start the schedule: {result.stderr.strip()[:200]}")
        return
    print(f"Scheduled! The paper will build and print every day at {hour:02d}:{minute:02d}.")
    print(f"  Your Mac must be awake (not sleeping) at that time, with the printer on.")
    print(f"  Files live in: {INSTALL_DIR}")
    print(f"  If something goes wrong, look at: {log}")
    if not Path(CHROME).exists():
        print("  ! Google Chrome was not found, so it cannot make the PDF for printing.")


def remove_schedule():
    uid = str(os.getuid())
    launchctl("bootout", f"gui/{uid}", str(PLIST))
    if PLIST.exists():
        PLIST.unlink()
    print("Schedule removed. The paper will no longer print by itself.")


def main():
    parser = argparse.ArgumentParser(description="Build today's newspaper.")
    parser.add_argument("--schedule", metavar="HH:MM", help="print the paper every day at this time, e.g. 07:00")
    parser.add_argument("--test-in", type=int, metavar="MINUTES", help="schedule a print this many minutes from now (to test)")
    parser.add_argument("--unschedule", action="store_true", help="stop the daily printing")
    parser.add_argument("--demo", action="store_true", help="use sample stories (no internet needed)")
    parser.add_argument("--no-ai", action="store_true", help="skip Gemini, keep original headlines")
    parser.add_argument("--open", action="store_true", help="open the finished paper in your browser")
    parser.add_argument("--pdf", action="store_true", help="also create a PDF (needs Google Chrome)")
    parser.add_argument("--print", dest="do_print", action="store_true", help="create the PDF and print it")
    args = parser.parse_args()

    if args.unschedule:
        remove_schedule()
        return
    if args.test_in is not None:
        when = datetime.datetime.now() + datetime.timedelta(minutes=max(args.test_in, 1))
        install_schedule(when.hour, when.minute)
        print("  (This is only a test. Set your real morning time later with --schedule 07:00)")
        return
    if args.schedule:
        try:
            hour, minute = (int(x) for x in args.schedule.split(":"))
            assert 0 <= hour < 24 and 0 <= minute < 60
        except (ValueError, AssertionError):
            print("Please give the time like this: --schedule 07:00")
            return
        install_schedule(hour, minute)
        return

    load_env()
    if not (args.demo or args.no_ai):
        ask_for_key()
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("Gathering the news...")
    if args.demo:
        counts = {c["name"]: c["count"] for c in SECTIONS}
        used = {}
        stories = []
        for s in demo_stories():
            used[s["section"]] = used.get(s["section"], 0) + 1
            if used[s["section"]] <= counts.get(s["section"], 99):
                stories.append(s)
    else:
        stories = gather_stories()
    if not stories:
        print("No stories could be fetched. Check your internet connection and try again.")
        sys.exit(1)

    ai_used = False
    if not args.no_ai and not args.demo:
        print("Setting the type (Gemini)...")
        ai_used = rewrite_with_gemini(stories)

    stamp = datetime.date.today().isoformat()
    html_path = OUTPUT_DIR / f"gazette-{stamp}.html"
    html_path.write_text(render(stories, ai_used), encoding="utf-8")
    print(f"Done! Your paper: {html_path}")

    if args.pdf or args.do_print:
        pdf_path = OUTPUT_DIR / f"gazette-{stamp}.pdf"
        if make_pdf(html_path, pdf_path):
            print(f"PDF ready: {pdf_path}")
            if args.do_print:
                subprocess.run(["lp", str(pdf_path)], check=False)
                print("Sent to the printer.")

    if args.open:
        subprocess.run(["open", str(html_path)], check=False)


if __name__ == "__main__":
    main()
