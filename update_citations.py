#!/usr/bin/env python3
"""
Sync publication metadata from my_publication.bib into citations.json,
and optionally fetch citation counts from Crossref.

Usage:
    cd personal_website
    python update_citations.py              # sync metadata + fetch all citation counts
    python update_citations.py --sync-only  # sync metadata from bib only (no network)

After adding a new paper to my_publication.bib:
  1. Run with --sync-only to add its metadata to citations.json.
  2. Manually set "topic" for the new entry in citations.json.
  3. Run without --sync-only periodically to refresh citation counts.

Topic values: topology | correlated | sc | spin | transport | other
"""

import argparse
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
EMAIL       = "jzzhao@tju.edu.cn"

# Default topic assignments (applied only when an entry has no existing "topic").
# After running the script, set "topic" directly in citations.json for new papers.
TOPIC_MAP: dict[str, str] = {
    "lu2013correlated":                         "topology",
    "weng2014topological":                      "topology",
    "zhao2012implementation":                   "correlated",
    "liu2015identification":                    "topology",
    "liu2015electronic":                        "topology",
    "liu2015anomalous":                         "correlated",
    "zhao2016topological":                      "topology",
    "lu2016elucidating":                        "other",
    "li2017pressure":                           "topology",
    "lv2019observation":                        "topology",
    "shang2019nodeless":                        "sc",
    "su2019strong":                             "topology",
    "peng2020mott":                             "correlated",
    "wang2019large":                            "topology",
    "totani2019s":                              "topology",
    "wang2020fermi":                            "transport",
    "shang2020time":                            "sc",
    "shang2020multigap":                        "sc",
    "guo2020nonsymmorphic":                     "topology",
    "grad2020photoexcited":                     "topology",
    "zhao2020highly":                           "transport",
    "shang2020superconductivity":               "sc",
    "zhao2021electronic":                       "correlated",
    "xiao2021thermoelectric":                   "transport",
    "tai2020two":                               "spin",
    "shang2021multigap":                        "sc",
    "zhu2022symmetry":                          "topology",
    "liu2022berry":                             "transport",
    "chen2020universal":                        "topology",
    "lai2021third":                             "transport",
    "liang2020isosymmetric":                    "topology",
    "lu2012electronic":                         "sc",
    "liu2021intrinsic":                         "transport",
    "biswas2021chiral":                         "sc",
    "zhu2022phononic":                          "topology",
    "jianzhou2021electronic":                   "correlated",
    "ghosh2022time":                            "sc",
    "qiu2019improved":                          "correlated",
    "xu2020electronic":                         "correlated",
    "shang2022unconventional":                  "sc",
    "yu2023anomalous":                          "topology",
    "shang2023fully":                           "sc",
    "wang2023magnetic":                         "spin",
    "li2023atom":                               "other",
    "shang2024nodeless":                        "sc",
    "xiao2024spin":                             "spin",
    "su2024highly":                             "topology",
    "yi2025charge":                             "correlated",
    "cao2025optical":                           "topology",
    "lv2025linear":                             "sc",
    "shang2025multiband":                       "sc",
    "cao2025enhanced":                          "topology",
    "zhong20255d":                              "topology",
    "shang2025discovery":                       "sc",
    "he2025coexistence":                        "sc",
    "gu2025gruneisen":                          "correlated",
    "wang2026orbital":                          "correlated",
    "zhang2026effect":                          "correlated",
    "wang2026flat":                             "correlated",
    "li2026giant":                              "correlated",
    "fanSwitchableAxionicMagnetoelectric2026":  "topology",
    "guo2026high":                              "spin",
}

# ── helpers ───────────────────────────────────────────────────────────────────

SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.translate(SUB).lower())


def clean_text(t: str) -> str:
    """Strip common LaTeX markup, normalise whitespace.

    $...$ math segments (e.g. ``$_{11}$``) are preserved verbatim so MathJax
    on the rendered page receives valid LaTeX.
    """
    t = re.sub(r'\\\"([aouAOUeEiI])',
               lambda m: {'a':'ä','o':'ö','u':'ü','A':'Ä','O':'Ö','U':'Ü',
                          'e':'ë','E':'Ë','i':'ï','I':'Ï'}.get(m.group(1), m.group(1)), t)

    # Protect $...$ math from the brace-stripping passes below.
    math_segs: list[str] = []

    def _stash(m: re.Match) -> str:
        math_segs.append(m.group(0))
        return f"\x00M{len(math_segs)-1}\x00"

    t = re.sub(r"\$[^$]+\$", _stash, t)

    t = re.sub(r"\\[a-zA-Z]+\{([^{}]*)\}", r"\1", t)  # \cmd{text} → text
    t = re.sub(r"\{([^{}]*)\}", r"\1", t)              # {text} → text
    t = re.sub(r"\\([%&$#])", r"\1", t)                # \& → &, \% → %, etc.

    for i, seg in enumerate(math_segs):
        t = t.replace(f"\x00M{i}\x00", seg)

    return re.sub(r"\s+", " ", t).strip()


_JOURNAL_NORM: dict[str, str] = {
    "physical review letters":                "Physical Review Letters",
    "physical review b":                      "Physical Review B",
    "physical review x":                      "Physical Review X",
    "physical review research":               "Physical Review Research",
    "physical review materials":              "Physical Review Materials",
    "nature communications":                  "Nature Communications",
    "nature nanotechnology":                  "Nature Nanotechnology",
    "science advances":                       "Science Advances",
    "advanced materials":                     "Advanced Materials",
    "npj quantum materials":                  "npj Quantum Materials",
    "npj computational materials":            "npj Computational Materials",
    "chinese physics b":                      "Chinese Physics B",
    "chinese physics letters":                "Chinese Physics Letters",
    "journal of physics: condensed matter":   "Journal of Physics: Condensed Matter",
    "journal of materials science":           "Journal of Materials Science",
    "new journal of physics":                 "New Journal of Physics",
    "scientific reports":                     "Scientific Reports",
    "aip advances":                           "AIP Advances",
    "the journal of physical chemistry letters": "Journal of Physical Chemistry Letters",
    "laser \\& photonics reviews":            "Laser & Photonics Reviews",
    "laser & photonics reviews":              "Laser & Photonics Reviews",
    "applied surface science":                "Applied Surface Science",
    "science bulletin":                       "Science Bulletin",
}


def normalize_journal(j: str) -> str:
    return _JOURNAL_NORM.get(j.lower(), j)


def auto_badge(journal: str | None) -> str | None:
    if not journal:
        return None
    j = journal.lower()
    if "physical review letters" in j:  return "prl"
    if "physical review x" in j:        return "prx"
    if "nature communications" in j:    return "nc"
    if "nature nanotechnology" in j:    return "nn"
    if "science advances" in j:         return "sa"
    if "advanced materials" in j:       return "adv"
    if "physical review b" in j:        return "prb"
    return None


def abbrev_author(raw: str) -> tuple[str, bool]:
    """Abbreviate one author name; return (abbreviated, is_me)."""
    raw = raw.strip()
    if not raw:
        return raw, False
    if "," in raw:
        last, first = raw.split(",", 1)
        last, first = last.strip(), first.strip()
    else:
        parts = raw.split()
        if len(parts) == 1:
            return raw, False
        last, first = parts[-1], " ".join(parts[:-1])

    first_parts = re.split(r"[\s\-]+", first)
    initial = (first_parts[0][0].upper() + ".") if first_parts and first_parts[0] else ""
    abbrev = f"{initial} {last}" if initial else last

    is_me = bool(re.match(r"^[Zz]hao$", last) and first and first[0].upper() == "J")
    return abbrev, is_me


def extract_field(body: str, name: str) -> str | None:
    """Extract a bib field value handling nested braces."""
    m = re.search(rf"\b{re.escape(name)}\s*=\s*\{{", body, re.I)
    if m:
        start = m.end()
        depth, i = 1, start
        while i < len(body) and depth:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        return re.sub(r"\s+", " ", body[start:i - 1]).strip()
    m = re.search(rf'\b{re.escape(name)}\s*=\s*"([^"]*)"', body, re.I | re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(rf"\b{re.escape(name)}\s*=\s*(\d+)", body, re.I)
    if m:
        return m.group(1)
    return None


def parse_bib(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    entries = []
    for m in re.finditer(r"@\w+\{(\w+),(.*?)\n\}", text, re.DOTALL):
        key, body = m.group(1), m.group(2)

        title      = extract_field(body, "title")
        doi        = extract_field(body, "doi")
        journal    = extract_field(body, "journal")
        volume     = extract_field(body, "volume")
        pages      = extract_field(body, "pages")
        year_raw   = extract_field(body, "year")
        author_str = extract_field(body, "author")

        if not title:
            continue

        title_clean = clean_text(title)

        # Parse authors
        authors, me_idx, et_al = [], None, False
        if author_str:
            raw_list = re.split(r"\s+and\s+", author_str)
            # Handle non-standard comma-separated "First Last" format
            if len(raw_list) == 1 and "," in author_str:
                parts = [p.strip() for p in author_str.split(",")]
                if all(" " in p for p in parts):
                    raw_list = parts
            for a in raw_list:
                a = a.strip()
                if a.lower() == "others":
                    et_al = True
                    continue
                abbrev, is_me = abbrev_author(a)
                if is_me and me_idx is None:
                    me_idx = len(authors)
                authors.append(abbrev)

        year = int(year_raw) if year_raw and year_raw.isdigit() else year_raw
        pages_clean = pages.replace("--", "\u2013") if pages else None

        entries.append({
            "key":     key,
            "title":   title_clean,
            "norm":    norm(title_clean),
            "doi":     doi,
            "authors": authors,
            "me":      me_idx,
            "et_al":   et_al,
            "journal": normalize_journal(clean_text(journal)) if journal else None,
            "volume":  volume,
            "pages":   pages_clean,
            "year":    year,
            "badge":   auto_badge(journal),
        })
    return entries


def crossref_lookup(title: str, doi: str | None) -> tuple[str | None, int | None]:
    headers = {"User-Agent": f"personal-website/1.0 (mailto:{EMAIL})"}
    if doi:
        r = requests.get(f"{API}/{doi}", headers=headers, timeout=15)
        if r.ok:
            msg = r.json().get("message", {})
            return msg.get("DOI", doi), msg.get("is-referenced-by-count")
    r = requests.get(API, headers=headers, timeout=15, params={
        "query.title": title, "rows": 1,
        "select": "DOI,title,is-referenced-by-count",
    })
    if r.ok:
        items = r.json().get("message", {}).get("items", [])
        if items:
            return items[0].get("DOI"), items[0].get("is-referenced-by-count")
    return None, None


def openalex_count(doi: str) -> int | None:
    """OpenAlex citation count; coverage is closer to Google Scholar than Crossref."""
    try:
        r = requests.get(f"https://api.openalex.org/works/doi:{doi}", timeout=15,
                         params={"mailto": EMAIL, "select": "cited_by_count"})
        if r.ok:
            return r.json().get("cited_by_count")
    except requests.RequestException:
        pass
    return None

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sync-only", action="store_true",
                        help="Sync metadata from bib only; skip Crossref fetch")
    args = parser.parse_args()

    entries  = parse_bib(BIB_FILE)
    existing = json.loads(OUTPUT_FILE.read_text()) if OUTPUT_FILE.exists() else {}
    results  = {}

    for i, e in enumerate(entries):
        key  = e["key"]
        prev = existing.get(key, {})

        entry = {
            "title":   e["title"],
            "norm":    e["norm"],
            "doi":     e["doi"] or prev.get("doi"),
            "count":   prev.get("count"),          # preserved; updated below if fetching
            "authors": e["authors"],
            "me":      e["me"],
            "et_al":   e["et_al"],
            "journal": e["journal"],
            "volume":  e["volume"],
            "pages":   e["pages"],
            "year":    e["year"],
            "badge":   e["badge"],
            "topic":   prev.get("topic") or TOPIC_MAP.get(key),
        }

        if not args.sync_only:
            print(f"[{i+1:2d}/{len(entries)}] {key} ...", end=" ", flush=True)
            doi, cr = crossref_lookup(title=e["title"], doi=entry["doi"])
            entry["doi"] = doi or entry["doi"]
            oa = openalex_count(entry["doi"]) if entry["doi"] else None
            counts = [c for c in (cr, oa) if c is not None]
            entry["count"] = max(counts) if counts else None
            print(f"crossref {cr} / openalex {oa} -> {entry['count']}"
                  if counts else "not found")
            time.sleep(0.5)

        results[key] = entry

    # metadata for the page footer; keys starting with "_" are skipped by the JS
    if args.sync_only:
        results["_meta"] = existing.get("_meta", {})
    else:
        results["_meta"] = {"updated": time.strftime("%Y-%m-%d")}

    OUTPUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    n_new = sum(1 for k in results if k not in existing and not k.startswith("_"))
    print(f"\n✓  {len(results)} entries saved ({n_new} new) → {OUTPUT_FILE}")
    if args.sync_only:
        print("   Tip: set \"topic\" for any new entries in citations.json, then open index.html.")


if __name__ == "__main__":
    main()
