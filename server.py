from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urljoin, quote_plus
from html.parser import HTMLParser
import json, re, ssl, urllib.request, os, html as html_lib

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8765"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"

# Use search/listing pages rather than only the homepages.
# Start with two sources whose pages explicitly describe accommodation/document support.
# HZZ and other portals can be added after these two work reliably.
SOURCES = [
    {"name": "Humble Hunters", "url": "https://humble-hunters.hr/en/jobs"},
    {"name": "Gettalent", "url": "https://get-talent.hr/career"},
]

JOB_PATH_HINTS = (
    "/posao", "/poslovi", "/job", "/jobs", "/oglas", "/oglasi",
    "/vacancy", "/career", "/radno-mjesto", "/radna-mjesta", "/jdp/"
)
LISTING_PATH_HINTS = ("/category/", "/kategorija/", "/poslovi", "/oglasi-za-posao")
GENERIC_TITLES = {
    "poslovi", "posao", "jobs", "job search", "traÅ¾ilica poslova",
    "rezultati pretrage", "poÄetna", "home", "career", "karijera",
    "oglasi za posao", "pronaÄi posao", "pronadi posao"
}

# Text matching is accent-insensitive so Croatian diacritics/encoding variants
# don't cause otherwise relevant pages to be discarded.
def norm(s):
    s = html_lib.unescape(s or "").lower()
    replacements = {"Ä":"c", "Ä":"c", "Ä":"d", "Å¡":"s", "Å¾":"z",
                    "Ã¦":"c", "Ã¨":"c", "Ã°":"d", "Å":"s", "Â¾":"z"}
    for a, b in replacements.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()

class Parser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links, self.text, self.title = [], [], []
        self.in_title = False
        self.current_link = None
        self.link_text = []
        self.ignored_depth = 0
        self.scope_depth = 0
        self.scoped_text = []
        self.headings = []
        self.heading_depth = 0
        self.current_heading = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header", "aside"):
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag in ("main", "article"):
            self.scope_depth += 1
        if tag in ("h1", "h2"):
            self.heading_depth += 1
            self.current_heading = []
        if tag == "title":
            self.in_title = True
        if tag == "a" and d.get("href"):
            self.current_link = urljoin(self.base, d["href"])
            self.link_text = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header", "aside") and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if self.ignored_depth:
            return
        if tag in ("main", "article") and self.scope_depth:
            self.scope_depth -= 1
        if tag in ("h1", "h2") and self.heading_depth:
            heading = clean(" ".join(self.current_heading))
            if heading:
                self.headings.append(heading)
            self.heading_depth -= 1
            self.current_heading = []
        if tag == "title":
            self.in_title = False
        if tag == "a" and self.current_link:
            label = clean(" ".join(self.link_text))
            self.links.append((self.current_link, label))
            self.current_link, self.link_text = None, []

    def handle_data(self, data):
        if self.ignored_depth:
            return
        t = clean(data)
        if not t:
            return
        self.text.append(t)
        if self.scope_depth:
            self.scoped_text.append(t)
        if self.in_title:
            self.title.append(t)
        if self.heading_depth:
            self.current_heading.append(t)
        if self.current_link:
            self.link_text.append(t)

def clean(s):
    return re.sub(r"\s+", " ", html_lib.unescape(s or "")).strip()

def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            data = r.read(900000)
            enc = r.headers.get_content_charset() or "utf-8"
            return data.decode(enc, errors="replace"), r.geturl()
    except Exception:
        return "", url

FREE_TERMS = [
    "besplatan smjestaj", "besplatni smjestaj", "smjestaj je besplatan",
    "smjestaj bez naknade", "trosak smjestaja snosi poslodavac",
    "poslodavac osigurava besplatan smjestaj", "free accommodation",
    "accommodation provided free", "free housing provided",
    "osiguran besplatan smjestaj", "besplatan smjeÅ¡taj"
]
PERMIT_TERMS = [
    "poslodavac ishoduje dozvolu", "poslodavac osigurava dozvolu",
    "pomoc pri ishodjenju dozvole", "pomoc oko radne dozvole",
    "pomazemo pri ishodjenju radne dozvole", "sponzoriramo radnu dozvolu",
    "sponzorstvo radne dozvole", "work permit sponsorship",
    "visa sponsorship", "we sponsor work permits", "assistance with work permit",
    "help with work permit", "full visa and work-permit processing handled for you",
    "we handle all legal paperwork", "we prepare your employment contract, work permit",
    "documents and permits", "work permit processing", "visa and work permit",
    "radna dozvola", "dozvola za rad"
]
JOB_TERMS = [
    "posao", "radnik", "radnica", "skladist", "konobar", "kuhar",
    "kuharica", "cistac", "worker", "waiter", "cook", "warehouse",
    "cleaner", "job", "zaposlj", "vozac", "prodavac", "hotel", "sobar"
]

def has_any(text, terms):
    t = norm(text)
    return any(norm(term) in t for term in terms)

def classify(title, body):
    combined = norm(title + " " + body)
    free = has_any(combined, FREE_TERMS)
    permit = has_any(combined, PERMIT_TERMS)
    salary = re.search(r"(?:â¬|eur|eura)\s?([0-9][0-9 .]{2,5})(?:[-â]\s?([0-9][0-9 .]{2,5}))?", combined)
    nums = []
    if salary:
        for group in salary.groups():
            if group:
                try:
                    nums.append(int(re.sub(r"[^0-9]", "", group)))
                except ValueError:
                    pass
    return free, permit, min(nums) if nums else None

def is_listing_url(url):
    path = urlparse(url).path.lower().rstrip("/")
    return any(hint in path for hint in LISTING_PATH_HINTS)

def is_specific_job(url, title, body):
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    title_n = norm(title)
    body_n = norm(body)
    if not path or title_n in {norm(x) for x in GENERIC_TITLES}:
        return False
    # Reject technology, registration, login and platform marketing pages.
    if any(term in norm(url + " " + title_n) for term in
           ("registracija", "prijava", "napredne ai", "tehnologije", "o nama", "kontakt", "privacy", "cookie")):
        return False
    if is_listing_url(url):
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2:
            return False
    if not has_any(title_n, JOB_TERMS):
        if len(path.split("/")) < 2 or not has_any(body_n, JOB_TERMS):
            return False
    # Avoid accepting menus/homepages, but don't require an overly long description.
    if len(body_n.split()) < 22:
        return False
    return True

def make_job(source, url, title, body):
    free, permit, min_salary = classify(title, body)
    return {
        "source": source,
        "url": url,
        "title": clean(title)[:160],
        "company": "Por confirmar",
        "city": "Croacia (ubicaciÃ³n por verificar)",
        "salary": ("â¬" + str(min_salary) + "+" if min_salary else "No indicada"),
        "accommodation": "Gratis indicado en el anuncio" if free else "No confirmado",
        "permit": "TrÃ¡mite de visa/permiso mencionado en el anuncio; confirmar elegibilidad" if permit else "No confirmado en el anuncio",
        "foreigner": "Confirmar con el empleador si tramita permiso para ciudadanos colombianos",
        "status": "yellow",
        "note": "Verifica directamente con el empleador que el alojamiento sea gratuito y que pueda gestionar el permiso de trabajo para tu caso.",
        "evidence": body[:1200],
    }


def walk_jobposting(obj, found):
    if isinstance(obj, dict):
        typ = obj.get("@type", "")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(t).lower() == "jobposting" for t in types):
            found.append(obj)
        for value in obj.values():
            walk_jobposting(value, found)
    elif isinstance(obj, list):
        for value in obj:
            walk_jobposting(value, found)

def extract_jobpostings(raw_html):
    found = []
    # Read only explicit JSON-LD blocks; don't treat arbitrary page scripts as job data.
    for block in re.findall(
        r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""",
        raw_html, flags=re.I | re.S
    ):
        try:
            data = json.loads(html_lib.unescape(block).strip())
        except Exception:
            continue
        walk_jobposting(data, found)
    return found

def is_generic_listing_result(url, title, body):
    """Reject search/category pages masquerading as individual job ads."""
    title_n = norm(title)
    url_n = norm(url)
    body_n = norm(body)
    generic_phrases = (
        "search through", "find your ideal job", "for jobseekers",
        "job seekers", "search results", "results for", "browse jobs",
        "all jobs", "active ads to find", "homepage", "job search",
        "traÅ¾ilica poslova", "rezultati pretrage", "oglasi za posao",
    )
    if any(norm(phrase) in title_n for phrase in generic_phrases):
        return True
    # Known listing/category paths that often return many ads, not one vacancy.
    path = urlparse(url).path.lower().rstrip("/")
    if "posao.hr" in urlparse(url).netloc.lower():
        if path in ("/", "/zanimanja/radnik", "/zanimanja/skladistar", "/zanima/radnik"):
            return True
        if title_n.startswith("for jobseekers") or "search through" in title_n:
            return True
    # Listing pages often contain many separate vacancy snippets in one body.
    listing_signals = sum(body_n.count(term) for term in (
        "expires in", "salary disclosed", "search through", "job view", "homepage"
    ))
    if listing_signals >= 3:
        return True
    return False

def jobposting_to_result(src_name, url, item):
    title = clean(str(item.get("title") or ""))
    description = item.get("description") or ""
    # Convert HTML description to plain text without using the page's global menu text.
    description = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", str(description))
    description = re.sub(r"(?i)<br\\s*/?>|</p>|</li>|</div>", " ", description)
    description = re.sub(r"<[^>]+>", " ", description)
    body = clean(html_lib.unescape(description))
    if not title or len(body.split()) < 12:
        return None
    if not has_any(title, JOB_TERMS):
        return None
    company_data = item.get("hiringOrganization") or {}
    company = company_data.get("name", "Por confirmar") if isinstance(company_data, dict) else "Por confirmar"
    location = "Croacia (ubicaciÃ³n por verificar)"
    locations = item.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    if isinstance(locations, list) and locations:
        loc = locations[0].get("address", {}) if isinstance(locations[0], dict) else {}
        if isinstance(loc, dict):
            parts = [loc.get(k) for k in ("addressLocality", "addressRegion", "addressCountry") if loc.get(k)]
            if parts:
                location = ", ".join(str(x) for x in parts)
    free, permit, salary = classify(title, body)
    return {
        "source": src_name,
        "url": url,
        "title": title[:160],
        "company": clean(str(company)) or "Por confirmar",
        "city": location,
        "salary": ("â¬" + str(salary) + "+" if salary else "No indicada"),
        "accommodation": "Gratis indicado en el anuncio" if free else "No confirmado",
        "permit": "TrÃ¡mite de visa/permiso mencionado en el anuncio; confirmar elegibilidad" if permit else "No confirmado en el anuncio",
        "foreigner": "Confirmar con el empleador si tramita permiso para ciudadanos colombianos",
        "status": "yellow",
        "note": "Datos extraÃ­dos del anuncio. La menciÃ³n de visa/permiso no garantiza elegibilidad para ciudadanos colombianos; confirma con el empleador.",
        "evidence": body[:1200],
    }

def search_source(src, keywords):
    html_text, final_url = fetch(src["url"])
    if not html_text:
        return []
    parser = Parser(final_url)
    try:
        parser.feed(html_text)
    except Exception:
        return []

    domain = urlparse(final_url).netloc.lower()
    candidates, seen = [], set()
    # Include links to individual job pages and links with job-like labels.
    for href, label in parser.links:
        u = href.split("#")[0]
        p = urlparse(u)
        if p.scheme not in ("http", "https") or p.netloc.lower() != domain:
            continue
        if u in seen or not p.path.strip("/"):
            continue
        seen.add(u)
        label_n = norm(label)
        if any(h in p.path.lower() for h in JOB_PATH_HINTS) or has_any(label_n, JOB_TERMS):
            # Skip obvious listing/category pages when they have no extra path depth.
            if is_listing_url(u) and len([x for x in p.path.split("/") if x]) < 2:
                continue
            candidates.append((u, label))

    results, result_urls = [], set()
    # Visit more individual links; do not stop at the first 5 pages.
    for url, link_label in candidates[:35]:
        page, resolved_url = fetch(url, timeout=7)
        if not page:
            continue
        if resolved_url in result_urls:
            continue

        # First use JobPosting JSON-LD if this page exposes it.
        page_parser = Parser(resolved_url)
        try:
            page_parser.feed(page)
        except Exception:
            continue
        structured = extract_jobpostings(page)
        if structured:
            for item in structured:
                result = jobposting_to_result(src["name"], resolved_url, item)
                if result and not is_generic_listing_result(result["url"], result["title"], result["evidence"]):
                    result_urls.add(resolved_url)
                    results.append(result)
                    if len(results) >= 12:
                        break
            if len(results) >= 12:
                break
            if resolved_url in result_urls:
                continue

        # Fallback for job pages without JSON-LD: extract the page title and
        # main/article text only, never the site's full navigation/footer text.
        title = (page_parser.headings[0] if page_parser.headings else "") or clean(" ".join(page_parser.title)) or clean(link_label)
        body = clean(" ".join(page_parser.scoped_text))
        if not body:
            # Some sites do not use main/article. Use only a short, conservative
            # slice of visible text and require a job-like title before accepting.
            body = clean(" ".join(page_parser.text[:100]))
        if not title or not body or len(body.split()) < 12:
            continue
        if is_generic_listing_result(resolved_url, title, body):
            continue
        if not is_specific_job(resolved_url, title, body):
            continue
        if not relevant_to_query({"title": title, "evidence": body}, " ".join(keywords)):
            continue
        free, permit, salary = classify(title, body)
        # Prefer the city/company only when it is explicitly available in text;
        # do not guess either field from unrelated sitewide content.
        result = {
            "source": src["name"], "url": resolved_url, "title": title[:160],
            "company": "Por confirmar", "city": "Croacia (ubicaciÃ³n por verificar)",
            "salary": ("â¬" + str(salary) + "+" if salary else "No indicada"),
            "accommodation": "Gratis indicado en el anuncio" if free else "No confirmado",
            "permit": "TrÃ¡mite de visa/permiso mencionado en el anuncio; confirmar elegibilidad" if permit else "No confirmado en el anuncio",
            "foreigner": "Confirmar con el empleador si tramita permiso para ciudadanos colombianos",
            "status": "yellow",
            "note": "Datos extraÃ­dos del anuncio. Confirma con el empleador si el alojamiento es gratuito y si tramita permiso para ciudadanos colombianos.",
            "evidence": body[:1200],
        }
        result_urls.add(resolved_url)
        results.append(result)
        if len(results) >= 12:
            break
    return results

# Search aliases and stems keep Croatian queries useful despite grammatical endings.
QUERY_ALIASES = {
    "skladiste": ["skladist", "komision", "vilicar", "logistik", "magacin", "warehouse", "picker", "pakir"],
    "skladistar": ["skladist", "komision", "vilicar", "logistik", "magacin", "warehouse", "picker", "pakir"],
    "warehouse": ["skladist", "komision", "vilicar", "logistik", "magacin", "warehouse", "picker", "pakir"],
    "smjestaj": ["smjest", "accommodation", "housing", "stanovanje"],
    "dozvola": ["dozvol", "radna dozvol", "work permit", "sponsorship"],
    "vozac": ["vozac", "vozil", "driver", "dostav", "kamion"],
    "cistac": ["cist", "cleaner", "housekeeping"],
    "konobar": ["konobar", "waiter", "servir"],
    "kuhar": ["kuhar", "cook", "chef", "kuhinj"],
}

def query_terms(query):
    words = [norm(x) for x in re.findall(r"[\wÄÄÄÅ¡Å¾ÄÄÄÅ Å½]+", query) if len(x) > 2]
    terms = []
    for word in words:
        terms.extend(QUERY_ALIASES.get(word, [word]))
    # Stable unique terms, preserving order.
    return list(dict.fromkeys(terms))

def relevant_to_query(job, query):
    words = [norm(x) for x in re.findall(r"[\wÄÄÄÅ¡Å¾ÄÄÄÅ Å½]+", query) if len(x) > 2]
    # Broad queries should not make the list empty; specific searches must match.
    specific = [w for w in words if w not in {"posao", "radnik", "hrvatska", "croatia", "smjestaj", "dozvola"}]
    if not specific:
        return True
    title = norm(job.get("title", ""))
    body = norm(job.get("evidence", ""))
    terms = query_terms(" ".join(specific))

    # Match word beginnings, not arbitrary substrings. This avoids false positives
    # such as "sklad" matching the Croatian word "skladu" ("in accordance").
    def has_term(text, term):
        term = norm(term)
        if not term:
            return False
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"[a-z0-9]*", text) is not None

    # A role name in the title is the strongest signal. Description matches are
    # accepted only for concrete warehouse/logistics terms from the search aliases.
    if any(has_term(title, term) for term in terms):
        return True
    return any(has_term(body, term) for term in terms)

def search_all(query="skladistar radnik smjestaj dozvola"):
    keywords = query_terms(query)
    jobs = []
    for source in SOURCES:
        try:
            jobs.extend(search_source(source, keywords))
        except Exception:
            continue
    seen, deduped = set(), []
    for job in jobs:
        key = job["url"]
        if key in seen or not relevant_to_query(job, query):
            continue
        seen.add(key)
        deduped.append(job)
    # Put exact title matches before description-only matches.
    terms = query_terms(query)
    deduped.sort(key=lambda j: (not any(t in norm(j.get("title", "")) for t in terms)))
    return deduped[:80]

class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/sources":
            return self.send_json({"sources": SOURCES})
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", ["skladistar radnik smjestaj dozvola"])[0]
            return self.send_json({"query": query, "jobs": search_all(query)})
        if parsed.path in ("/", "/index.html"):
            try:
                with open("/opt/render/project/src/index.html", "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_error(404)
            return
        path = "." + parsed.path
        try:
            with open(path, "rb") as f:
                data = f.read()
            types = {".css": "text/css", ".js": "application/javascript", ".json": "application/json", ".txt": "text/plain"}
            content_type = next((v for ext, v in types.items() if path.endswith(ext)), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_error(404)

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    print(f"Agente de Empleo Croacia: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
