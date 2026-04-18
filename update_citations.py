#!/usr/bin/env python3
"""
Fetch citation counts from Crossref for all papers in my_publication.bib
and save to citations.json.  Run periodically (e.g. monthly) to refresh.

Usage:
    cd personal_website
    python update_citations.py
"""

import json
import re
import time
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("pip install requests")

BIB_FILE    = Path(__file__).parent / "my_publication.bib"
OUTPUT_FILE = Path(__file__).parent / "citations.json"
API         = "https://api.crossref.org/works"
EMAIL       = "jzzhao@tju.edu.cn"          # Crossref polite-pool

# ── helpers ──────────────────────────────────────────────────────────────────

SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")

def norm(title: str) -> str:
    """Normalise title for matching: subscripts → digits, lowercase, alnum only."""
    return re.sub(r"[^a-z0-9]", "", title.translate(SUB).lower())


def parse_bib(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    entries = []
    for m in re.finditer(r"@\w+\{(\w+),(.*?)\n\}", text, re.DOTALL):
        key, body = m.group(1), m.group(2)

        def field(name):
            fm = re.search(
                rf"\b{name}\s*=\s*\{{([^{{}}]*)\}}", body, re.I | re.S
            )
            return re.sub(r"\s+", " ", fm.group(1)).strip() if fm else None

        title = field("title")
        doi   = field("doi")
        if title:
            entries.append({"key": key, "title": title, "doi": doi, "norm": norm(title)})
    return entries


def crossref_lookup(title: str, doi: str | None) -> tuple[str | None, int | None]:
    headers = {"User-Agent": f"personal-website/1.0 (mailto:{EMAIL})"}

    if doi:
        r = requests.get(f"{API}/{doi}", headers=headers, timeout=15)
        if r.ok:
            msg = r.json().get("message", {})
            return msg.get("DOI", doi), msg.get("is-referenced-by-count")

    # title-search fallback
    r = requests.get(API, headers=headers, timeout=15, params={
        "query.title": title, "rows": 1,
        "select": "DOI,title,is-referenced-by-count",
    })
    if r.ok:
        items = r.json().get("message", {}).get("items", [])
        if items:
            return items[0].get("DOI"), items[0].get("is-referenced-by-count")

    return None, None

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    entries  = parse_bib(BIB_FILE)
    existing = json.loads(OUTPUT_FILE.read_text()) if OUTPUT_FILE.exists() else {}
    results  = {}

    for i, e in enumerate(entries):
        key = e["key"]
        print(f"[{i+1:2d}/{len(entries)}] {key} ...", end=" ", flush=True)

        # reuse existing DOI if already known
        prev_doi = existing.get(key, {}).get("doi") or e["doi"]
        doi, count = crossref_lookup(title=e["title"], doi=prev_doi)

        results[key] = {
            "title": e["title"],
            "norm":  e["norm"],
            "doi":   doi,
            "count": count,
        }
        print(f"{'cited ' + str(count) if count is not None else 'not found'}")
        time.sleep(0.5)   # be polite to Crossref

    OUTPUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\n✓  {len(results)} entries saved → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
