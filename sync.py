#!/usr/bin/env python3
"""
Union Ministries of India: refresh the saved copy that index.html reads when hosted.

    python3 sync.py             # check Wikipedia for portfolio changes, refresh Wikidata details and photos
    python3 sync.py --offline   # no network: rebuild data/site.json and the page's built-in copy from the files in data/
    python3 sync.py --force     # apply portfolio changes even when they look unusually large

What it reads (all in data/):
    ministries-meta.json   ministry names, descriptions, websites, X accounts (edit by hand)
    council.json           who holds which ministry. Updated automatically from Wikipedia; set "lock": true to stop that
    profiles.json          calling-card details checked by hand: residence, email, declared assets, birth details,
                           social accounts. Anything here wins over Wikidata
    changes.json           the "recent changes" list shown on the page. New entries are added automatically

What it writes:
    data/site.json         everything the page needs, in one file
    data/sync-status.json  what happened on the last run
    photos/                one 240 px photo per minister, from Wikimedia Commons
    index.html             the built-in copy between the SEED markers, and the plain-HTML directory for search engines
    sitemap.xml

Needs only Python 3.8+ (no packages to install). Wikimedia asks scripts to identify themselves:
set UMI_USER_AGENT to something with your contact details.
"""
import argparse
import hashlib
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

WP_API = "https://en.wikipedia.org/w/api.php"
WD_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
MINISTRY_PAGE = os.environ.get("UMI_MINISTRY_PAGE", "Third_Modi_ministry")
USER_AGENT = os.environ.get("UMI_USER_AGENT", "UnionMinistriesIndia/1.0 (set UMI_USER_AGENT to your contact details)")
SITE_URL = os.environ.get("UMI_SITE_URL", "").strip()
if SITE_URL and not SITE_URL.endswith("/"):
    SITE_URL += "/"
THUMB = 240
SCHEMA = 1
IST = timezone(timedelta(hours=5, minutes=30))

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
PHOTOS = os.path.join(HERE, "photos")


def log(*a):
    print(*a, flush=True)


def load(name, default=None):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    p = os.path.join(DATA, name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, p)


# ---------------------------------------------------------------- HTTP
def request(url, tries=5, timeout=60):
    wait = 2
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                ra = e.headers.get("Retry-After")
                time.sleep(int(ra) if ra and ra.isdigit() else wait)
                wait *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if i == tries - 1:
                raise
            time.sleep(wait)
            wait *= 2
    raise RuntimeError("Gave up after %d tries: %s" % (tries, url))


def api(base, **params):
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    return json.loads(request(base + "?" + urllib.parse.urlencode(params)))


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------- a small HTML tree
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs or {}), [], parent

    def text(self):
        out = []
        for c in self.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag in ("style", "script") or "reference" in c.attrs.get("class", "") or "noprint" in c.attrs.get("class", ""):
                continue
            elif c.tag == "br":
                out.append("; ")
            else:
                out.append(c.text())
        return "".join(out)

    def iter(self, tag=None):
        for c in self.children:
            if isinstance(c, Node):
                if tag is None or c.tag == tag:
                    yield c
                yield from c.iter(tag)


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse_html(s):
    b = TreeBuilder()
    b.feed(s)
    b.close()
    return b.root


def clean(s):
    s = re.sub(r"\[\s*[\w\s]*\d*\s*\]", "", s or "")        # [1], [a], [note 2]
    return re.sub(r"\s+", " ", s).strip(" ;")


def table_grid(table):
    """Expand a table into rows of cell Nodes, repeating cells that span rows or columns."""
    rows = [tr for tr in table.iter("tr")]
    grid, pending = [], {}          # pending[col] = [node, rows_left]
    for tr in rows:
        cells = [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]
        row, col, ci = [], 0, 0
        while ci < len(cells) or col in pending:
            if col in pending:
                node, left = pending[col]
                row.append(node)
                left -= 1
                if left <= 0:
                    del pending[col]
                else:
                    pending[col][1] = left
                col += 1
                continue
            c = cells[ci]
            ci += 1
            try:
                rs = max(1, int(c.attrs.get("rowspan", "1") or 1))
            except ValueError:
                rs = 1
            try:
                cs = max(1, int(c.attrs.get("colspan", "1") or 1))
            except ValueError:
                cs = 1
            for _ in range(cs):
                row.append(c)
                if rs > 1:
                    pending[col] = [c, rs - 1]
                col += 1
        grid.append(row)
    return grid


def wiki_link(cell):
    for a in cell.iter("a"):
        href = a.attrs.get("href", "")
        if href.startswith("/wiki/") and ":" not in href[6:].split("#")[0]:
            return urllib.parse.unquote(href[6:].split("#")[0]).replace("_", " "), clean(a.text())
        if "new" in a.attrs.get("class", "") and "title=" in href:       # red link: article doesn't exist yet
            t = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("title", [""])[0]
            if t:
                return t.replace("_", " "), clean(a.text())
    return None, None


def table_kind(heading):
    h = heading.lower()
    if "former" in h or "past" in h:
        return None
    if "independent" in h:
        return "ic"
    if "state" in h:
        return "mos"
    if "cabinet" in h:
        return "cabinet"
    return None


def parse_council_html(html):
    """Return incumbents as rows: {kind, portfolio, title, name, remarks}."""
    root = parse_html(html)
    out, headings = [], {}

    def walk(n):
        for c in n.children:
            if not isinstance(c, Node):
                continue
            if c.tag in ("h2", "h3", "h4"):
                lvl = int(c.tag[1])
                headings[lvl] = clean(c.text())
                for deeper in [k for k in headings if k > lvl]:
                    del headings[deeper]
                continue
            if c.tag == "table" and "wikitable" in c.attrs.get("class", ""):
                read_table(c, " / ".join(headings[k] for k in sorted(headings)))
                continue
            walk(c)

    def read_table(t, heading):
        kind = table_kind(heading.split(" / ")[-1]) or table_kind(heading)
        if not kind:
            return
        grid = table_grid(t)
        hdr_i, cols = None, {}
        for i, row in enumerate(grid[:4]):
            labels = [clean(c.text()).lower() for c in row]
            if any("portfolio" in l for l in labels) and any(l.startswith("minister") or l == "name" for l in labels):
                hdr_i = i
                for j, l in enumerate(labels):
                    if "portfolio" in l and "portfolio" not in cols:
                        cols["portfolio"] = j
                    elif (l.startswith("minister") or l == "name") and "minister" not in cols:
                        cols["minister"] = j
                    elif "left" in l and "left" not in cols:
                        cols["left"] = j
                    elif "remark" in l and "remarks" not in cols:
                        cols["remarks"] = j
                break
        if hdr_i is None or "portfolio" not in cols or "minister" not in cols:
            return
        for row in grid[hdr_i + 1:]:
            if len(row) <= max(cols.values()):
                continue
            if row[cols["portfolio"]].tag == "th" and row[cols["minister"]] is row[cols["portfolio"]]:
                continue                                   # a sub-heading row spanning the table
            left = clean(row[cols["left"]].text()).lower() if "left" in cols else ""
            if left and "incumbent" not in left and left not in ("-", "—", "–"):
                continue
            title, name = wiki_link(row[cols["minister"]])
            if not title:
                continue
            out.append({
                "kind": kind,
                "portfolio": clean(row[cols["portfolio"]].text()),
                "title": title,
                "name": name or title,
                "remarks": clean(row[cols["remarks"]].text()) if "remarks" in cols else "",
            })

    walk(root)
    seen, rows = set(), []
    for r in out:                                           # rowspans can repeat a row; keep one
        k = (r["kind"], r["portfolio"], r["title"])
        if k not in seen:
            seen.add(k)
            rows.append(r)
    return rows


def norm(s):
    s = s.lower().replace("&", " and ").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


NOT_A_MINISTRY = r"(?i)prime minister|policy issues|not allocated|all other portfolios|^office\b|^$"


def split_parts(text):
    """Pieces of a portfolio cell, as (normalised, original) pairs."""
    raw = [clean(p).strip(" .,") for p in re.split(r";|\n", text or "")]
    return [(norm(p), p) for p in raw if p]


GENERIC_WORDS = {"and", "of", "the", "department", "ministry", "minister", "ministers", "affairs", "india", "indian", "in", "for",
                 "state", "charge", "additional", "independent", "union", "government", "with", "also"}


def name_words(s):
    return {w for w in re.findall(r"[a-z]+", norm(s)) if len(w) > 2 and w not in GENERIC_WORDS}


def match_ministries(text, meta, unknown=None):
    """Ministry ids named in a portfolio cell. Pieces that match no ministry go into `unknown`.

    A piece matches when it is a ministry's name, or when its patterns match and the piece has no
    other significant words (so "Space Economy" is not mistaken for the Department of Space)."""
    hits = []
    for part, original in split_parts(text):
        pw = name_words(ministry_name(part)[0])
        found = [m for m in meta if not m.get("retired") and pw and any(pw == name_words(n) for n in [m["n"]] + list(m.get("formerNames") or []))]
        if not found:
            found = [m for m in meta if not m.get("retired") and any(re.search(p, part) for p in m.get("match", []))]
            known = set().union(*[name_words(n) for m in found for n in [m["n"]] + list(m.get("formerNames") or [])]) if found else set()
            if found and pw - known:
                found = []
        for m in found:
            if m["id"] not in hits:
                hits.append(m["id"])
        if not found and unknown is not None and pw and not re.search(NOT_A_MINISTRY, part):
            unknown.append(original)          # pieces with no real words ("and") are joins, not ministries
    return hits


def ministry_name(part):
    """'Minister of State in the Ministry of Space Economy (additional charge)' -> ('Space Economy', False)"""
    s = re.sub(r"\(.*?\)", "", clean(part)).strip(" .,")
    s = re.sub(r"^(minister of state|union minister|minister|ministry)\s+(in the ministry of|in the|of|for)\s+", "", s, flags=re.I)
    s = re.sub(r"^(the\s+)?ministry of\s+", "", s, flags=re.I).strip(" .,")
    dept = bool(re.match(r"department of\s", s, re.I))
    s = s[0].upper() + s[1:] if s else s
    return s, dept


# ---------------------------------------------------------------- council from Wikipedia
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def resolve_titles(titles):
    """Map each title to (canonical title, wikidata id). Follows redirects."""
    res = {}
    for part in chunks(sorted(set(titles)), 50):
        j = api(WP_API, action="query", titles="|".join(part), redirects=1, prop="pageprops", ppprop="wikibase_item")
        q = j.get("query", {})
        step = {}
        for k in ("normalized", "redirects"):
            for x in q.get(k, []):
                step[x["from"]] = x["to"]
        pages = {p["title"]: p for p in q.get("pages", [])}
        for t in part:
            s, n = t, 0
            while s in step and n < 5:
                s, n = step[s], n + 1
            p = pages.get(s, {})
            res[t] = (s, (p.get("pageprops") or {}).get("wikibase_item"), not p.get("missing", False))
    return res


def build_council_from_rows(rows, meta, council, resolved):
    """Turn table rows into a new council. Returns (new_council, problems, unknown_pieces)."""
    problems = []
    people_old = council["people"]
    by_canon = {}
    for pid, p in people_old.items():
        for t in [p.get("wikiResolved"), p.get("wiki")] + list(p.get("wikiAlt") or []):
            if t:
                by_canon.setdefault(t.lower(), pid)
    people, mins = {}, {m["id"]: {"lead": None, "charge": None, "mos": [], "note": None} for m in meta}
    unmatched = []

    def person_for(row, rank):
        canon, qid, _ = resolved.get(row["title"], (row["title"], None, True))
        pid = by_canon.get(canon.lower()) or by_canon.get(row["title"].lower())
        if not pid:
            pid = slug(canon)
            while pid in people_old and pid not in people:
                pid += "-x"
            by_canon[canon.lower()] = pid
        base = dict(people.get(pid) or people_old.get(pid) or {"n": row["name"], "wiki": canon, "wikiAlt": [], "extra": None})
        base["wikiResolved"] = canon
        if qid:
            base["qid"] = qid
        order = {"PM": 0, "C": 1, "IC": 2, "MoS": 3}
        prev = people.get(pid, {}).get("r")
        if prev is None or order[rank] < order[prev]:
            base["r"] = rank
        else:
            base["r"] = prev
        people[pid] = base
        return pid

    order = {"cabinet": 0, "ic": 1, "mos": 2}
    rows = sorted(rows, key=lambda r: order[r["kind"]])
    canon_of = lambda r: resolved.get(r["title"], (r["title"], None, True))[0]
    pm_canon = next((canon_of(r) for r in rows if r["kind"] == "cabinet" and norm(r["portfolio"]).startswith("prime minister")), None)
    for r in rows:
        pieces = []
        ids = match_ministries(r["portfolio"], meta, pieces)
        addl = "additional charge" in norm(r["portfolio"] + " " + r["remarks"])
        if r["kind"] == "cabinet":
            is_pm = pm_canon is not None and canon_of(r) == pm_canon
            pid = person_for(r, "PM" if is_pm else "C")
            charge = "PM" if is_pm else ("Additional charge" if addl else "Cabinet")
        elif r["kind"] == "ic":
            pid, charge = person_for(r, "IC"), "IC"
        else:
            pid, charge = person_for(r, "MoS"), None
        for piece in pieces:
            unmatched.append({"text": piece, "kind": r["kind"], "pid": pid, "title": canon_of(r)})
        for mid in ids:
            m = mins[mid]
            if charge is None:
                if pid not in m["mos"]:
                    m["mos"].append(pid)
            elif m["lead"] is None:
                m["lead"], m["charge"] = pid, charge
            elif m["lead"] != pid:
                problems.append("Two ministers in charge found for %s: %s and %s; kept %s" % (mid, m["lead"], pid, m["lead"]))
    for mid, m in mins.items():
        if m["lead"] in m["mos"]:
            m["mos"].remove(m["lead"])
    for p in people.values():
        p.setdefault("extra", None)
        if p.get("r") == "PM":
            p["extra"] = p.get("extra") or "Prime Minister. Also holds all important policy issues and every portfolio not allocated to another minister."
    # Keep the PMO note for whoever is MoS in the PMO
    for r in rows:
        if r["kind"] in ("mos", "ic") and "prime minister" in norm(r["portfolio"]) and "office" in norm(r["portfolio"]):
            pid = by_canon.get(resolved.get(r["title"], (r["title"],))[0].lower())
            if pid in people:
                people[pid]["extra"] = "Also Minister of State in the Prime Minister's Office."
    return {"people": people, "ministries": mins}, problems, unmatched


def validate(new, meta):
    cab = [p for p in new["people"].values() if p["r"] in ("C", "PM")]
    mos = [p for p in new["people"].values() if p["r"] == "MoS"]
    meta = [m for m in meta if not m.get("retired")]
    led = [m["id"] for m in meta if (new["ministries"].get(m["id"]) or {}).get("lead")]
    errs = []
    if not any(p["r"] == "PM" for p in new["people"].values()):
        errs.append("no Prime Minister found")
    if len(cab) < 20:
        errs.append("only %d Cabinet ministers found" % len(cab))
    if len(mos) < 15:
        errs.append("only %d Ministers of State found" % len(mos))
    if len(led) < len(meta) - 6:
        missing = sorted(set(m["id"] for m in meta) - set(led))
        errs.append("%d ministries have no minister in charge: %s" % (len(missing), ", ".join(missing)))
    return errs


def describe_changes(old, new, meta, skip=()):
    names = {m["id"]: m["n"] for m in meta}
    nm = lambda pid, src: (src["people"].get(pid) or {}).get("n", pid)
    out = []
    for pid in new["people"]:
        if pid not in old["people"]:
            out.append("%s joined the Council of Ministers as %s." % (nm(pid, new), {"C": "a Cabinet Minister", "IC": "a Minister of State (Independent Charge)", "MoS": "a Minister of State", "PM": "Prime Minister"}[new["people"][pid]["r"]]))
        elif old["people"][pid].get("r") != new["people"][pid].get("r"):
            out.append("%s is now %s." % (nm(pid, new), {"C": "a Cabinet Minister", "IC": "a Minister of State (Independent Charge)", "MoS": "a Minister of State", "PM": "Prime Minister"}[new["people"][pid]["r"]]))
    for pid in old["people"]:
        if pid not in new["people"]:
            out.append("%s left the Council of Ministers." % nm(pid, old))
    for mid, m in new["ministries"].items():
        if mid in skip:
            continue
        o = old["ministries"].get(mid, {})
        if m["lead"] != o.get("lead"):
            if o.get("lead"):
                out.append("%s: %s takes over from %s%s." % (names[mid], nm(m["lead"], new) if m["lead"] else "no minister", nm(o["lead"], old), " (additional charge)" if m["charge"] == "Additional charge" else ""))
            elif m["lead"]:
                out.append("%s: %s in charge." % (names[mid], nm(m["lead"], new)))
        elif m["charge"] != o.get("charge") and m["charge"] == "Additional charge":
            out.append("%s: %s now holds it as additional charge." % (names[mid], nm(m["lead"], new)))
        for pid in m["mos"]:
            if pid not in o.get("mos", []):
                out.append("%s: %s appointed Minister of State." % (names[mid], nm(pid, new)))
        for pid in o.get("mos", []):
            if pid not in m["mos"]:
                out.append("%s: %s no longer Minister of State." % (names[mid], nm(pid, old)))
    return out


def describe_new_ministry(name, dept):
    """Description, website and X account for a ministry, from Wikipedia and Wikidata. Best effort."""
    full = name if dept else "Ministry of " + name
    out = {"d": None, "web": None, "x": None, "wiki": None}
    try:
        res = resolve_titles([full + " (India)", full])
        for t in (full + " (India)", full):
            canon, qid, exists = res.get(t, (t, None, False))
            if not exists:
                continue
            j = api(WP_API, action="query", prop="extracts", exintro=1, explaintext=1, exsentences=3, titles=canon, redirects=1)
            pages = j.get("query", {}).get("pages", [])
            text = clean((pages[0] if pages else {}).get("extract", ""))
            if "india" not in text.lower():
                continue
            out["d"], out["wiki"] = text[:600], canon
            if qid:
                e = wd_entities([qid]).get(qid, {})
                c = e.get("claims", {})
                out["web"] = (claim_values(c, "P856") or [None])[0]
                out["x"] = (claim_values(c, "P2002") or [None])[0]
            break
    except Exception as ex:
        log("  couldn't describe %s: %s" % (full, ex))
    return out


def evolve_ministries(rows, meta, council, resolved, today, force, pending=None):
    """Notice ministries that are new, renamed or no longer in the tables, and update the list of ministries.

    Returns (meta, created_ids, change_lines, review_notes, abort_message)."""
    meta = [dict(m) for m in meta]
    _, _, unknown = build_council_from_rows(rows, meta, council, resolved)
    present = set()
    for r in rows:
        present.update(match_ministries(r["portfolio"], meta))
    active = [m for m in meta if not m.get("retired")]
    gone = [m for m in active if m["id"] not in present]
    lines, review, created = [], [], []

    # Pieces held by a Cabinet minister or a Minister of State (Independent Charge) are candidate ministries.
    cands = {}
    for u in unknown:
        name, dept = ministry_name(u["text"])
        if name and len(name) >= 3 and u["kind"] in ("cabinet", "ic"):
            cands.setdefault(name, dict(u, dept=dept))

    if len(cands) + len(gone) > 6 and not force:
        return meta, [], [], [], "%d ministries look new and %d look wound up. Nothing was changed; check Wikipedia, then run the workflow with force." % (
            len(cands), len(gone)), [], pending

    lead_title = lambda mid: (council["people"].get((council["ministries"].get(mid) or {}).get("lead") or "") or {}).get("wikiResolved")
    # Renamed: a ministry vanished and an unknown one appeared, headed by the same minister
    for m in list(gone):
        for name, u in list(cands.items()):
            if lead_title(m["id"]) and lead_title(m["id"]) == u["title"]:
                old_name = m["n"]
                m.setdefault("formerNames", []).append(old_name)
                m["n"] = name
                m["match"] = list(m.get("match", [])) + [re.escape(norm(name))]
                m.pop("missingSince", None)
                lines.append("Ministry of %s renamed Ministry of %s." % (old_name, name) if not m.get("dept") else "%s renamed %s." % (old_name, name))
                review.append("Treated \"%s\" as the new name of \"%s\" because the same minister holds it. Check the description in data/ministries-meta.json." % (name, old_name))
                gone.remove(m)
                del cands[name]
                break

    # New ministries: added when they are still there on a run at least 3 hours after first being seen
    pending = dict(pending or {})
    now = datetime.now(timezone.utc)
    for name in list(pending):
        if name not in cands:
            del pending[name]                       # gone again before it was confirmed
    for name, u in cands.items():
        first = pending.get(name)
        if not first:
            pending[name] = now.replace(microsecond=0).isoformat()
            review.append("\"%s\" (held by %s) looks like a new ministry. It will be added if it's still there on the next run." % (name, u["title"]))
            continue
        if now - datetime.fromisoformat(first) < timedelta(hours=3):
            continue
        del pending[name]
        mid = slug(name)
        while any(x["id"] == mid for x in meta):
            mid += "-new"
        info = describe_new_ministry(name, u["dept"])
        links = {}
        if info["web"]:
            links["web"] = info["web"]
        if info["x"]:
            links["x"] = info["x"]
        else:
            links["xs"] = (name if u["dept"] else "Ministry of " + name) + " India"
        meta.append({
            "id": mid, "n": name, "s": "Other", "dept": u["dept"],
            "d": info["d"] or "A newly created %s of the Government of India. A description will be added after review." % ("department" if u["dept"] else "ministry"),
            "links": links, "match": [re.escape(norm(name))], "auto": True, "added": today,
        })
        created.append(mid)
        who = (council["people"].get(u["pid"]) or {}).get("n") or u["title"]
        lines.append("New %s: %s%s, headed by %s." % ("department" if u["dept"] else "ministry", "" if u["dept"] else "Ministry of ", name, who))
        review.append("Added \"%s\" automatically%s. Check its description, sector, website and X account in data/ministries-meta.json, then delete \"auto\": true." % (
            name, " using Wikipedia's article on it" if info["d"] else " (no Wikipedia article found yet)"))

    # Ministries no longer in the tables: wait one day before removing, in case it's a passing edit
    keep = []
    for m in gone:
        if not m.get("missingSince"):
            m["missingSince"] = today
            keep.append(m["id"])
            review.append("\"%s\" isn't in Wikipedia's tables today. It stays on the site for now and will be removed if it's still missing on the next run." % m["n"])
        elif m["missingSince"] < today:
            m["retired"] = today
            m.pop("missingSince", None)
            lines.append("%s no longer appears in the Council of Ministers (merged or wound up)." % (m["n"] if m.get("dept") else "Ministry of " + m["n"]))
            review.append("Removed \"%s\" from the site because it has been missing from Wikipedia's tables for more than a day. To bring it back, delete its \"retired\" line in data/ministries-meta.json." % m["n"])
        else:
            keep.append(m["id"])
    for m in meta:
        if m["id"] in present:
            m.pop("missingSince", None)
            if m.get("retired"):
                m.pop("retired")
                lines.append("%s is listed again." % (m["n"] if m.get("dept") else "Ministry of " + m["n"]))
    _, _, still = build_council_from_rows(rows, meta, council, resolved)
    for u in still:
        if u["kind"] == "mos":
            review.append("A Minister of State portfolio wasn't recognised: \"%s\" (%s)." % (u["text"], u["title"]))
    return meta, created, lines, review, None, keep, pending


def refresh_council(council, meta, force, today):
    """Returns (new_council, new_meta, change_lines, problems, review_notes, revid). new_council is None if nothing should change."""
    log("Reading %s from Wikipedia…" % MINISTRY_PAGE)
    j = api(WP_API, action="parse", page=MINISTRY_PAGE, prop="text|revid", redirects=1)
    html = j["parse"]["text"]
    revid = j["parse"].get("revid")
    rows = parse_council_html(html)
    log("  %d current rows found in the ministers' tables" % len(rows))
    resolved = resolve_titles([r["title"] for r in rows])

    # A first read must look like a real council before ministries are added or removed on its strength
    first, _, _ = build_council_from_rows(rows, meta, council, resolved)
    errs = [e for e in validate(first, meta) if "no minister in charge" not in e]
    if errs:
        return None, meta, [], ["Portfolio check skipped, Wikipedia's tables didn't read cleanly: " + "; ".join(errs)], [], revid

    new_meta, created, ministry_lines, review, abort, keep, pending = evolve_ministries(
        rows, meta, council, resolved, today, force, council.get("pendingMinistries"))
    if pending:
        council["pendingMinistries"] = pending
    else:
        council.pop("pendingMinistries", None)
    # What was read, for checking by hand: every portfolio piece and the ministry it was matched to
    seen = {}
    for r in rows:
        for _, original in split_parts(r["portfolio"]):
            seen.setdefault(original, {"matched": match_ministries(original, new_meta), "tables": []})
            if r["kind"] not in seen[original]["tables"]:
                seen[original]["tables"].append(r["kind"])
    save("wikipedia-portfolios.json", {"revision": revid, "pieces": seen})
    if abort:
        return None, meta, [], [abort], review, revid
    new, problems, _ = build_council_from_rows(rows, new_meta, council, resolved)
    for mid in keep:                       # missing for the first time: show the last known ministers for now
        if mid in council["ministries"]:
            new["ministries"][mid] = dict(council["ministries"][mid])
            for pid in [council["ministries"][mid].get("lead")] + list(council["ministries"][mid].get("mos") or []):
                if pid and pid not in new["people"] and pid in council["people"]:
                    new["people"][pid] = council["people"][pid]
    for m in new_meta:
        if m.get("retired"):
            new["ministries"].pop(m["id"], None)
    errs = validate(new, new_meta)
    if errs:
        return None, meta, [], ["Portfolio check skipped, Wikipedia's tables didn't read cleanly: " + "; ".join(errs)] + problems, review, revid
    changes = ministry_lines + describe_changes(council, new, new_meta, skip=set(created))
    if len(changes) > 40 and not force:
        return None, meta, changes, ["%d portfolio changes found at once. Nothing was applied; check them, then run the workflow with force." % len(changes)] + problems, review, revid
    # Keep hand-written notes only where nothing changed
    for mid, m in new["ministries"].items():
        o = council["ministries"].get(mid, {})
        if o.get("note") and o.get("lead") == m["lead"] and o.get("mos") == m["mos"] and o.get("charge") == m["charge"]:
            m["note"] = o["note"]
    return new, new_meta, changes, problems, review, revid


# ---------------------------------------------------------------- Wikidata details and photos
def claim_values(claims, prop):
    vals = []
    for c in claims.get(prop, []):
        if c.get("rank") == "deprecated":
            continue
        if any(q in (c.get("qualifiers") or {}) for q in ("P582",)):       # has an end date
            continue
        dv = (c.get("mainsnak") or {}).get("datavalue")
        if not dv:
            continue
        vals.append((c.get("rank") == "preferred", dv["value"]))
    vals.sort(key=lambda x: not x[0])
    return [v for _, v in vals]


def wd_entities(ids, props="claims|labels"):
    out = {}
    for part in chunks(sorted(set(i for i in ids if i)), 50):
        j = api(WD_API, action="wbgetentities", ids="|".join(part), props=props, languages="en")
        out.update(j.get("entities", {}))
    return out


def wd_date(v):
    m = re.match(r"[+-](\d{4})-(\d\d)-(\d\d)", v.get("time", ""))
    if not m:
        return None
    y, mo, d = m.groups()
    prec = v.get("precision", 11)
    if prec >= 11 and mo != "00" and d != "00":
        return "%s-%s-%s" % (y, mo, d)
    if prec == 10 and mo != "00":
        return "%s-%s" % (y, mo)
    return y


def refresh_wikidata(people):
    """Returns {pid: details} from Wikidata, and downloads photos."""
    need = {pid: p for pid, p in people.items() if not p.get("qid")}
    if need:
        res = resolve_titles([p.get("wikiResolved") or p["wiki"] for p in need.values()] + [t for p in need.values() for t in (p.get("wikiAlt") or [])])
        for pid, p in need.items():
            for t in [p.get("wikiResolved") or p["wiki"]] + list(p.get("wikiAlt") or []):
                canon, qid, exists = res.get(t, (t, None, False))
                if qid:
                    p["qid"], p["wikiResolved"] = qid, canon
                    break
    ents = wd_entities([p.get("qid") for p in people.values()])
    places = set()
    for e in ents.values():
        for v in claim_values(e.get("claims", {}), "P19")[:1]:
            places.add(v["id"])
    pl = wd_entities(places)
    parents = set()
    for e in pl.values():
        for v in claim_values(e.get("claims", {}), "P131")[:1]:
            parents.add(v["id"])
    par = wd_entities(parents, props="labels")
    label = lambda e: ((e or {}).get("labels", {}).get("en") or {}).get("value")

    out = {}
    for pid, p in people.items():
        e = ents.get(p.get("qid") or "")
        if not e or "missing" in e:
            continue
        c = e.get("claims", {})
        d = {}
        v = claim_values(c, "P569")
        if v:
            d["dob"] = wd_date(v[0])
        v = claim_values(c, "P19")
        if v:
            place = pl.get(v[0]["id"])
            name = label(place)
            up = claim_values((place or {}).get("claims", {}), "P131")
            upn = label(par.get(up[0]["id"])) if up else None
            if name:
                d["pob"] = name + (", " + upn if upn and upn not in name else "")
        soc = {}
        for prop, key in (("P2002", "x"), ("P2003", "instagram"), ("P2013", "facebook"), ("P2397", "youtube"), ("P856", "website")):
            v = claim_values(c, prop)
            if v:
                soc[key] = v[0]
        if soc:
            d["social"] = soc
        v = claim_values(c, "P968")
        if v:
            d["email"] = re.sub(r"^mailto:", "", v[0])
        v = claim_values(c, "P18")
        if v:
            d["image"] = v[0]
        out[pid] = d
    return out


def strip_tags(s):
    return clean(htmllib.unescape(re.sub(r"<[^>]+>", "", s or "")))


def refresh_photos(people, wd, old_site):
    os.makedirs(PHOTOS, exist_ok=True)
    old = {pid: (p.get("photo") or {}) for pid, p in (old_site.get("people") or {}).items()}
    files = {pid: d["image"] for pid, d in wd.items() if d.get("image")}
    info = {}
    for part in chunks(sorted(set(files.values())), 40):
        j = api(COMMONS_API, action="query", titles="|".join("File:" + f for f in part), prop="imageinfo",
                iiprop="url|sha1|extmetadata", iiurlwidth=THUMB)
        norm_map = {x["from"]: x["to"] for x in j.get("query", {}).get("normalized", [])}
        pages = {p["title"]: p for p in j.get("query", {}).get("pages", [])}
        for f in part:
            t = "File:" + f
            p = pages.get(norm_map.get(t, t))
            if p and p.get("imageinfo"):
                info[f] = p["imageinfo"][0]
    result = {}
    for pid, f in files.items():
        ii = info.get(f)
        if not ii:
            continue
        sha = (ii.get("sha1") or hashlib.sha1(f.encode()).hexdigest())[:10]
        url = ii.get("thumburl") or ii.get("url")
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        qid = people[pid].get("qid") or pid
        name = "%s-%s%s" % (qid, sha, ext)
        path = os.path.join(PHOTOS, name)
        if not os.path.exists(path):
            try:
                data = request(url)
                with open(path, "wb") as fh:
                    fh.write(data)
                log("  photo saved: %s" % name)
            except Exception as ex:
                log("  photo failed for %s: %s" % (pid, ex))
                continue
            prev = old.get(pid, {}).get("file")
            if prev and prev != "photos/" + name and os.path.exists(os.path.join(HERE, prev)):
                os.remove(os.path.join(HERE, prev))
        md = ii.get("extmetadata") or {}
        result[pid] = {
            "file": "photos/" + name,
            "credit": strip_tags((md.get("Artist") or {}).get("value", ""))[:120] or None,
            "license": strip_tags((md.get("LicenseShortName") or {}).get("value", "")) or None,
            "page": ii.get("descriptionurl"),
        }
    return result


# ---------------------------------------------------------------- build
def merge_person(pid, p, prof, wd, photo):
    d = dict(wd or {})
    out = {
        "n": p["n"], "r": p["r"], "extra": p.get("extra"), "qid": p.get("qid"),
        "wiki": p.get("wikiResolved") or p.get("wiki"),
        "dob": d.get("dob"), "pob": d.get("pob"), "email": d.get("email"),
        "house": None, "residence": None, "assets": None,
        "social": dict(d.get("social") or {}), "photo": photo, "sources": [],
    }
    for k in ("dob", "pob", "email", "house", "residence", "assets", "n"):
        if prof.get(k):
            out[k] = prof[k]
    for k, v in (prof.get("social") or {}).items():
        if v:
            out["social"][k] = v
    out["sources"] = list(prof.get("sources") or [])
    if out["qid"]:
        out["sources"].append("https://www.wikidata.org/wiki/" + out["qid"])
    return out


def build_site(meta, council, profiles, changes, wd, photos, status):
    people = {}
    for pid, p in council["people"].items():
        people[pid] = merge_person(pid, p, profiles.get(pid, {}), wd.get(pid), photos.get(pid))
    mins = []
    for m in meta:
        if m.get("retired"):
            continue
        c = council["ministries"].get(m["id"]) or {}
        if not c.get("lead"):
            c = dict(c, lead=None)
        note = c.get("note")
        if m.get("auto") and not note:
            note = "Added automatically on %s when it appeared in the Council of Ministers. Its description and links are being checked." % m.get("added", "")
        mins.append({"id": m["id"], "n": m["n"], "s": m["s"], "d": m["d"], "dept": m.get("dept", False), "links": m.get("links", {}),
                     "lead": c.get("lead"), "charge": c.get("charge"), "mos": c.get("mos", []), "note": note})
    return {
        "schema": SCHEMA,
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "asOf": council.get("asOf"),
        "source": "https://en.wikipedia.org/wiki/" + MINISTRY_PAGE,
        "status": status,
        "changes": changes[:12],
        "people": people,
        "ministries": mins,
    }


def write_page(site):
    """Put the built-in copy and the plain-HTML directory into index.html."""
    p = os.path.join(HERE, "index.html")
    with open(p, encoding="utf-8") as f:
        page = f.read()
    seed = json.dumps(site, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = re.sub(r"(<script type=\"application/json\" id=\"seed\">).*?(</script>)", lambda m: m.group(1) + seed + m.group(2), page, count=1, flags=re.S)
    rows = []
    for m in site["ministries"]:
        lead = site["people"].get(m["lead"] or "", {}).get("n", "Vacant")
        mos = ", ".join(site["people"][x]["n"] for x in m["mos"] if x in site["people"]) or "None"
        rows.append("<li><strong>%s</strong>: %s. Minister in charge: %s. Ministers of State: %s.</li>" % (
            htmllib.escape(m["n"]), htmllib.escape(m["d"]), htmllib.escape(lead), htmllib.escape(mos)))
    static = "<ul>\n" + "\n".join(rows) + "\n</ul>"
    page = re.sub(r"(<!--STATIC-->).*?(<!--/STATIC-->)", lambda m: m.group(1) + static + m.group(2), page, count=1, flags=re.S)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(p + ".tmp", p)
    if SITE_URL:
        with open(os.path.join(HERE, "sitemap.xml"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                    "  <url><loc>%s</loc><lastmod>%s</lastmod><changefreq>daily</changefreq></url>\n</urlset>\n" % (SITE_URL, site["generated"][:10]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    meta = load("ministries-meta.json")["ministries"]
    council = load("council.json")
    profiles = {k: v for k, v in (load("profiles.json", {}) or {}).items() if not k.startswith("_")}
    changes = (load("changes.json", {"changes": []}) or {}).get("changes", [])
    old_site = load("site.json", {}) or {}
    status = {"ok": True, "messages": []}
    wd, photos = {}, {}
    today = datetime.now(IST).date().isoformat()

    if not a.offline:
        if council.get("lock"):
            status["messages"].append("council.json is locked, so portfolios were not checked against Wikipedia.")
        else:
            try:
                new, new_meta, diff, problems, review, revid = refresh_council(council, meta, a.force, today)
                status["messages"] += problems
                status["review"] = review
                status["wikipediaRevision"] = revid
                if new is None:
                    status["ok"] = False
                else:
                    if new_meta != meta:
                        meta = new_meta
                        mf = load("ministries-meta.json")
                        mf["ministries"] = meta
                        save("ministries-meta.json", mf)
                    if diff:
                        log("Portfolio changes:\n  " + "\n  ".join(diff))
                        council["people"], council["ministries"] = new["people"], new["ministries"]
                        changes = [{"date": today, "text": t} for t in diff] + changes
                        save("changes.json", {"changes": changes[:60]})
                        status["changed"] = len(diff)
                    else:
                        log("No portfolio changes.")
                        for pid, p in new["people"].items():          # keep resolved titles and ids
                            if pid in council["people"]:
                                council["people"][pid].update({k: p[k] for k in ("wikiResolved", "qid") if p.get(k)})
                    council["asOf"] = today
                save("council.json", council)
            except Exception as ex:
                status["ok"] = False
                status["messages"].append("Couldn't read Wikipedia's list of ministers: %s" % ex)
        try:
            log("Reading details from Wikidata…")
            wd = refresh_wikidata(council["people"])
            save("council.json", council)
            log("Checking photos on Wikimedia Commons…")
            photos = refresh_photos(council["people"], wd, old_site)
        except Exception as ex:
            status["ok"] = False
            status["messages"].append("Couldn't refresh Wikidata details or photos: %s" % ex)
    if not wd and old_site.get("people"):
        # Offline or failed: keep what the last good run found
        for pid, p in old_site["people"].items():
            if pid in council["people"]:
                photos.setdefault(pid, p.get("photo"))
                kept = {k: p.get(k) for k in ("dob", "pob", "email") if p.get(k)}
                if p.get("social"):
                    kept["social"] = p["social"]
                wd.setdefault(pid, kept)

    site = build_site(meta, council, profiles, changes, wd, photos, status)
    if old_site and {k: v for k, v in old_site.items() if k != "generated"} == {k: v for k, v in site.items() if k != "generated"}:
        site["generated"] = old_site["generated"]          # nothing changed: keep the date so browsers don't re-download
    save("site.json", site)
    save("sync-status.json", dict(status, at=datetime.now(timezone.utc).replace(microsecond=0).isoformat()))
    write_page(site)
    log("Done: %d ministries, %d ministers, %d photos. %s" % (len(site["ministries"]), len(site["people"]),
        sum(1 for p in site["people"].values() if p.get("photo")), "OK" if status["ok"] else "Needs a look: " + " | ".join(status["messages"])))
    review = status.get("review") or []
    if not status["ok"] or review:
        parts = []
        if not status["ok"]:
            parts.append("**The update couldn't be applied.** The site kept showing the last good data.\n\n" + "\n".join("- " + m for m in status["messages"]) +
                         "\n\nFix the cause, or edit `data/council.json` by hand and set `\"lock\": true` until Wikipedia's tables read cleanly again.")
        if review:
            parts.append("**Please check** (the site has already been updated):\n\n" + "\n".join("- " + m for m in review))
        with open(os.path.join(HERE, ".sync-review.md"), "w", encoding="utf-8") as f:
            f.write("Run of %s\n\n" % today + "\n\n".join(parts) + "\n")

if __name__ == "__main__":
    main()
