from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urljoin, quote_plus, urlencode
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
    "oglasi za posao", "pronaÄi posao", "pronadi posao",
    "open positions in croatia", "open positions", "live vacancies",
    "all open positions", "all open roles", "career opportunities", "all jobs"
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
    "valid passport for visa processing", "passport for visa processing",
    "visa processing", "visa process", "radna dozvola", "dozvola za rad"
]
JOB_TERMS = [
    # Warehouse, logistics and packing
    "skladis", "skladist", "skladistar", "warehouse", "logistics", "logistic",
    "komision", "picker", "packer", "packing", "pakir", "vilicar", "forklift",
    "magacin", "stock worker", "inventory",
    # Production, manufacturing and factory work
    "production", "proizvod", "factory", "fabrika", "manufacturing", "operator",
    "operater", "production line", "assembly", "montaza", "machine operator",
    "quality control", "pakiranje", "radnik u proizvodnji",
    # Cleaning, housekeeping and hospitality
    "cleaner", "cleaning", "cistac", "ciscenje", "housekeeping", "sobar", "sobarica",
    "hotel", "hospitality", "receptionist", "recepcionar", "kitchen porter",
    "dishwasher", "perac posuda", "kuhinj", "cook", "chef", "kuhar", "kuharica",
    "waiter", "waitress", "konobar", "konobarica", "bartender", "barmen",
    # Construction and trades
    "construction", "gradjev", "graÄev", "zidar", "mason", "tesar", "carpenter",
    "painter", "soboslikar", "plumber", "vodoinstalater", "electrician", "elektricar",
    "welder", "varilac", "installer", "montazer", "laborer", "helper",
    # Driving, delivery, agriculture, retail and food production
    "driver", "vozac", "delivery", "dostav", "truck", "kamion", "courier",
    "agriculture", "poljopriv", "farm worker", "farmer", "harvest", "berba",
    "retail", "cashier", "blagajnik", "prodavac", "trgovac", "butcher", "mesar",
    "food production", "radnik", "radnica", "djelatnik", "djelatnica", "worker",
    "zaposlj", "job"
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

# Strong signals mean the ad says the employer handles/sponsors the process,
# not merely that a passport or visa is needed. This still does not prove
# eligibility for any particular nationality.
PERMIT_HANDLING_TERMS = [
    "poslodavac ishoduje dozvolu", "poslodavac osigurava dozvolu",
    "pomoc pri ishodjenju dozvole", "pomoc oko radne dozvole",
    "pomazemo pri ishodjenju radne dozvole", "sponzoriramo radnu dozvolu",
    "sponzorstvo radne dozvole", "work permit sponsorship", "visa sponsorship",
    "we sponsor work permits", "assistance with work permit", "help with work permit",
    "full visa and work-permit processing handled for you",
    "work-permit processing handled for you", "we handle all legal paperwork",
    "we prepare your employment contract, work permit", "work permit processing handled",
]

def permit_status(title, body):
    # Classify from the same excerpt returned to the user. This prevents text
    # deep in a page (often a shared footer or another vacancy) from implying
    # that this particular employer handles the permit.
    excerpt = clean(body)[:1200]
    combined = norm(title + " " + excerpt)
    if has_any(combined, PERMIT_HANDLING_TERMS):
        return "El anuncio indica que gestiona visa/permiso; confirmar elegibilidad para ciudadanos colombianos"
    if has_any(combined, PERMIT_TERMS):
        return "Visa/permiso mencionado, pero no confirma quien lo tramita"
    return "No confirmado en el anuncio"

def priority_for(job):
    # Ranking is based on the work area/role only, never on accommodation,
    # work permit, or residence information. Those questions belong in outreach.
    title = norm(job.get("title", ""))
    evidence = norm(job.get("evidence", ""))
    role_terms = [
        "warehouse", "skladis", "skladist", "logistic", "komision", "picker", "packer",
        "pakir", "vilicar", "forklift", "driver", "vozac", "delivery", "cleaner", "cleaning",
        "cistac", "ciscenje", "housekeeping", "sobar", "cook", "kuhar", "kitchen", "chef",
        "waiter", "konobar", "bartender", "production", "proizvod", "factory", "fabrika",
        "operator", "operater", "construction", "gradjev", "zidar", "tesar", "carpenter",
        "painter", "plumber", "electrician", "welder", "agriculture", "farm", "harvest",
        "retail", "cashier", "prodavac", "trgovac", "butcher", "mesar", "hotel", "worker",
        "radnik", "radnica", "djelatnik", "djelatnica", "reception", "dishwasher"
    ]
    return "Alta" if any(term in title for term in role_terms) else "Media"

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
        "accommodation": "Preguntar por correo",
        "permit": "Preguntar por correo",
        "foreigner": "Preguntar si aceptan candidatos de Colombia",
        "status": "yellow",
        "note": "La oferta coincide con un Ã¡rea laboral buscada. Usa el borrador de correo para preguntar por alojamiento y trÃ¡mites.",
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
    body_n = norm(body)
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path.lower().rstrip("/") or "/"

    # Exact generic titles commonly returned by recruitment landing pages.
    generic_titles = {norm(x) for x in GENERIC_TITLES}
    if title_n in generic_titles:
        return True
    generic_title_phrases = (
        "open positions in croatia", "live vacancies", "all open positions",
        "all open roles", "browse all jobs", "find your ideal job",
        "search through", "search results", "results for", "job search",
        "traÅ¾ilica poslova", "rezultati pretrage", "oglasi za posao",
    )
    if any(norm(phrase) in title_n for phrase in generic_title_phrases):
        return True

    # Reject source landing pages, but allow individual vacancies below them.
    if "humble-hunters.hr" in host and path in ("/", "/en/jobs", "/jobs"):
        return True
    if "get-talent.hr" in host and path in ("/", "/career", "/hr/career", "/en/career"):
        return True
    if "posao.hr" in host:
        if path in ("/", "/zanimanja/radnik", "/zanimanja/skladistar", "/zanima/radnik"):
            return True
        if title_n.startswith("for jobseekers") or "search through" in title_n:
            return True

    # Listing pages often contain many separate vacancy snippets in one body.
    listing_signals = sum(body_n.count(term) for term in (
        "expires in", "salary disclosed", "search through", "job view", "homepage"
    ))
    # Some agency landing pages have a role-like heading but contain a catalogue
    # of many jobs. Reject obvious rollups, while retaining individual vacancy pages.
    if listing_signals >= 3:
        return True
    if body_n.count("view details") >= 4 or body_n.count("job opening") >= 4:
        return True
    if re.search(r"showing\s+\d+\s+of\s+\d+\s+(open roles|vacancies|jobs)", body_n):
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
        "accommodation": "Preguntar por correo",
        "permit": "Preguntar por correo",
        "foreigner": "Preguntar si aceptan candidatos de Colombia",
        "status": "yellow",
        "note": "La oferta coincide con un Ã¡rea laboral buscada. Usa el borrador de correo para preguntar por alojamiento y trÃ¡mites.",
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
    # Gettalent's career cards can be rendered in a way that does not expose
    # every card link consistently to a simple HTML parser. Seed known individual
    # vacancy pages, then still discover any additional links from the page.
    if "get-talent.hr" in domain:
        for path, label in (
            ("/hr/career/warehouse-operative", "Warehouse Operative"),
            ("/hr/career/cable-production-operator", "Cable Production Operator"),
            ("/hr/career/production-line-employee", "Production Line Employee"),
            ("/hr/career/concrete-formwork-worker", "Concrete & Formwork Worker"),
        ):
            u = urljoin(final_url, path)
            candidates.append((u, label))
            seen.add(u)

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
            "accommodation": "Preguntar por correo",
            "permit": "Preguntar por correo",
            "foreigner": "Preguntar si aceptan candidatos de Colombia",
            "status": "yellow",
            "note": "La oferta coincide con un Ã¡rea laboral buscada. Usa el borrador de correo para preguntar por alojamiento y trÃ¡mites.",
            "evidence": body[:1200],
        }
        result_urls.add(resolved_url)
        results.append(result)
        if len(results) >= 12:
            break
    return results

# Search aliases and stems keep Croatian queries useful despite grammatical endings.
QUERY_ALIASES = {
    # Warehouse and logistics
    "skladiste": ["skladis", "skladist", "komision", "vilicar", "logistik", "magacin", "warehouse", "picker", "pakir", "packer"],
    "skladistar": ["skladis", "skladist", "komision", "vilicar", "logistik", "magacin", "warehouse", "picker", "pakir", "packer"],
    "warehouse": ["skladis", "skladist", "komision", "vilicar", "logistik", "magacin", "warehouse", "picker", "pakir", "packer"],
    "logistics": ["logistic", "logistik", "warehouse", "skladis", "skladist", "delivery", "dostav", "forklift", "picker", "packer"],
    "logistic": ["logistics", "logistik", "warehouse", "skladis", "skladist", "delivery", "dostav"],
    # Production and factory
    "production": ["production", "proizvod", "factory", "fabrika", "manufacturing", "operator", "operater", "assembly", "montaza"],
    "factory": ["factory", "fabrika", "production", "proizvod", "manufacturing", "operator", "operater"],
    "proizvodnja": ["proizvod", "production", "factory", "fabrika", "manufacturing", "operator", "operater"],
    "operator": ["operator", "operater", "production", "proizvod", "factory", "fabrika"],
    # Cleaning, hotel and hospitality
    "cleaning": ["cleaning", "cleaner", "cist", "housekeeping", "sobar", "higijen"],
    "cleaner": ["cleaner", "cleaning", "cist", "housekeeping", "sobar", "higijen"],
    "cistac": ["cist", "cleaner", "cleaning", "housekeeping", "sobar", "higijen"],
    "hotel": ["hotel", "hospitality", "sobar", "housekeeping", "reception", "kuhar", "chef", "cook", "konobar", "waiter", "cleaner"],
    "hospitality": ["hotel", "hospitality", "sobar", "housekeeping", "reception", "kuhar", "chef", "cook", "konobar", "waiter", "cleaner"],
    "housekeeping": ["housekeeping", "sobar", "sobarica", "cleaner", "cleaning", "cist"],
    "kitchen": ["kitchen", "kuhinj", "cook", "chef", "kuhar", "dishwasher", "konobar"],
    "cook": ["cook", "chef", "kuhar", "kuharica", "kitchen", "kuhinj"],
    "chef": ["chef", "cook", "kuhar", "kuharica", "kitchen", "kuhinj"],
    "waiter": ["waiter", "waitress", "konobar", "konobarica", "servir", "hospitality"],
    "konobar": ["konobar", "waiter", "waitress", "servir", "hospitality"],
    # Construction and trades
    "construction": ["construction", "gradjev", "zidar", "mason", "tesar", "carpenter", "painter", "plumber", "electrician", "welder", "laborer"],
    "gradjevina": ["gradjev", "construction", "zidar", "mason", "tesar", "carpenter", "painter", "plumber", "electrician", "welder"],
    "electrician": ["electrician", "elektricar", "electrical", "maintenance"],
    "welder": ["welder", "varilac", "welding", "metal worker"],
    # Driving, agriculture, retail and food production
    "driver": ["vozac", "vozil", "driver", "dostav", "delivery", "kamion", "truck", "courier"],
    "vozac": ["vozac", "vozil", "driver", "dostav", "delivery", "kamion", "truck"],
    "agriculture": ["agriculture", "poljopriv", "farm", "farmer", "harvest", "berba", "seasonal worker"],
    "farm": ["farm", "farmer", "agriculture", "poljopriv", "harvest", "berba"],
    "retail": ["retail", "cashier", "blagajnik", "prodavac", "trgovac", "shop assistant"],
    "cashier": ["cashier", "blagajnik", "retail", "prodavac", "trgovac"],
    "smjestaj": ["smjest", "accommodation", "housing", "stanovanje"],
    "dozvola": ["dozvol", "radna dozvol", "work permit", "sponsorship"],
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

def canonical_job_url(url):
    """Normalize locale-prefixed versions and tracking query strings for deduplication."""
    p = urlparse(url or "")
    host = (p.netloc or "").lower().removeprefix("www.")
    parts = [part for part in p.path.split("/") if part]
    if parts and parts[0].lower() in {"en", "hr", "de", "it", "fr", "es"}:
        parts = parts[1:]
    path = "/" + "/".join(parts)
    return host + path.rstrip("/").lower()


def job_area(job):
    """Return a broad labour area using the job title first, then its description."""
    title = norm(job.get("title", ""))
    evidence = norm(job.get("evidence", ""))
    rules = [
        ("AlmacÃ©n y logÃ­stica", ("warehouse", "skladis", "skladist", "logistic", "picker", "packer", "forklift", "vilicar", "komision", "magacin")),
        ("ConstrucciÃ³n y oficios", ("construction", "carpenter", "tesar", "zidar", "mason", "electrician", "elektricar", "welder", "varilac", "plumber", "vodoinstalater", "concrete", "formwork", "painter")),
        ("Limpieza y mantenimiento", ("cleaner", "cleaning", "cistac", "housekeeping", "maintenance", "odrzavanje")),
        ("Hoteles y hostelerÃ­a", ("hotel", "hospitality", "cook", "chef", "kuhar", "kitchen", "waiter", "konobar", "receptionist", "sobar", "dishwasher", "bartender")),
        ("ConducciÃ³n y reparto", ("driver", "vozac", "delivery", "courier", "truck", "bus driver", "kamion")),
        ("Agricultura", ("agriculture", "farm worker", "farmer", "harvest", "berba", "poljopriv")),
        ("ProducciÃ³n y fÃ¡bricas", ("production", "proizvod", "factory", "fabrika", "manufacturing", "operator", "operater", "assembly", "cable production", "bakery production")),
        ("Comercio y alimentaciÃ³n", ("retail", "cashier", "blagajnik", "shop assistant", "sales assistant", "butcher", "mesar", "bakery", "counter")),
        ("MecÃ¡nica y vehÃ­culos", ("mechanic", "truck mechanic", "automotive", "vehicle technician")),
    ]
    # Prefer title matches so unrelated roles mentioned further down a page do not
    # change the category of a specific vacancy.
    for label, terms in rules:
        if has_any(title, terms):
            return label
    for label, terms in rules:
        if has_any(evidence, terms):
            return label
    return "Otros empleos"


def email_draft_details(title, company, city, job_url):
    title = clean(title) or "oglaÅ¡eno radno mjesto"
    company = clean(company) or "poslodavac"
    city = clean(city) or "Hrvatska"
    job_url = clean(job_url)
    subject = f"Upit za radno mjesto: {title}"
    body = (
        "PoÅ¡tovani,\n\n"
        f"obraÄam vam se povodom oglasa za radno mjesto {title}"
        f"{(' u mjestu ' + city) if city and city.lower() not in ('croacia (ubicaciÃ³n por verificar)', 'por confirmar') else ' u Hrvatskoj'}.\n\n"
        "DrÅ¾avljanin sam Kolumbije i molim vas za nekoliko informacija: \n"
        "1. PrihvaÄate li prijave drÅ¾avljana Kolumbije za ovo radno mjesto?\n"
        "2. PruÅ¾a li poslodavac smjeÅ¡taj? Ako da, je li besplatan ili se plaÄa i koliki je troÅ¡ak?\n"
        "3. MoÅ¾ete li pomoÄi u postupku ishoÄenja hrvatske dozvole za rad i reguliranja boravka?\n"
        "4. Kolika je plaÄa (bruto/neto), radno vrijeme, trajanje ugovora i oÄekivani datum poÄetka rada?\n"
        "5. Koji su dokumenti potrebni i koji je sluÅ¾beni postupak prijave?\n\n"
        f"Oglas za posao: {job_url}\n\n"
        "Hvala vam na vremenu i odgovoru. Å½ivotopis i dodatne informacije mogu dostaviti putem odgovarajuÄeg kanala za prijavu.\n\n"
        "SrdaÄan pozdrav,\n[VaÅ¡e ime i prezime]"
    )
    translation_es = (
        "Hola,\n\n"
        f"Me pongo en contacto con ustedes por la oferta para el puesto de {title}"
        f"{(' en ' + city) if city and city.lower() not in ('croacia (ubicaciÃ³n por verificar)', 'por confirmar') else ' en Croacia'}.\n\n"
        "Soy ciudadano colombiano y agradecerÃ­a que me confirmaran lo siguiente:\n"
        "1. Â¿Aceptan solicitudes de ciudadanos colombianos para este puesto?\n"
        "2. Â¿La empresa proporciona alojamiento? En caso afirmativo, Â¿es gratuito o de pago y cuÃ¡l es su coste?\n"
        "3. Â¿Pueden ayudar con el trÃ¡mite del permiso de trabajo croata y la regularizaciÃ³n de la residencia?\n"
        "4. Â¿CuÃ¡l es el salario (bruto/neto), el horario, la duraciÃ³n del contrato y la fecha prevista de inicio?\n"
        "5. Â¿QuÃ© documentos se necesitan y cuÃ¡l es el procedimiento oficial para postularse?\n\n"
        f"Oferta de empleo: {job_url}\n\n"
        "Gracias por su tiempo y respuesta. Puedo enviar mi currÃ­culum y mÃ¡s informaciÃ³n por el canal de solicitud correspondiente.\n\n"
        "Saludos cordiales,\n[Tu nombre y apellidos]"
    )
    mailto = "mailto:?" + urlencode({"subject": subject, "body": body})
    return {"subject": subject, "body": body, "translation_es": translation_es, "mailto": mailto}


def draft_url_for(job):
    params = {
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "city": job.get("city", ""),
        "url": job.get("url", ""),
    }
    base = os.environ.get("RENDER_EXTERNAL_URL", "https://croatia-job-agent.onrender.com").rstrip("/")
    return base + "/api/email-draft?" + urlencode(params)


def send_html(handler, markup, code=200):
    data = markup.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        pass


def email_draft_page(query):
    title = query.get("title", [""])[0]
    company = query.get("company", [""])[0]
    city = query.get("city", [""])[0]
    url = query.get("url", [""])[0]
    draft = email_draft_details(title, company, city, url)
    esc = html_lib.escape
    parts = [
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Borrador de correo</title>",
        "<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#202124}h1{font-size:1.5rem}p{line-height:1.5}.field{margin:16px 0}label{display:block;font-weight:650;margin-bottom:6px}textarea{box-sizing:border-box;width:100%;padding:12px;font:16px system-ui;border:1px solid #aaa;border-radius:8px}a{display:inline-block;padding:12px 16px;background:#174ea6;color:white;border-radius:8px;text-decoration:none}small{color:#555}</style></head><body>",
        "<h1>Borrador de correo al empleador</h1><p>Revisa el texto antes de enviarlo. No se ha enviado ningÃºn correo automÃ¡ticamente. Abre tu aplicaciÃ³n de correo para revisar el mensaje y aÃ±ade la direcciÃ³n del empleador.</p>",
        '<p><a href="' + esc(draft["mailto"], quote=True) + '">Abrir borrador en la aplicaciÃ³n de correo</a></p>',
        '<div class="field"><label>Asunto (croata; se enviarÃ¡ al empleador)</label><textarea readonly rows="2" onclick="this.select()">' + esc(draft["subject"]) + '</textarea></div>',
        '<div class="field"><label>Mensaje en croata (este es el texto del correo)</label><textarea readonly rows="20" onclick="this.select()">' + esc(draft["body"]) + '</textarea></div>',
        '<hr><h2>TraducciÃ³n al espaÃ±ol (solo para ti; no se incluye en el correo)</h2>',
        '<div class="field"><label>TraducciÃ³n de referencia</label><textarea readonly rows="20" onclick="this.select()">' + esc(draft["translation_es"]) + '</textarea></div>',
        "<small>Revisa el mensaje antes de enviarlo. No se envÃ­a automÃ¡ticamente. No envÃ­es nÃºmeros de pasaporte ni documentos sensibles hasta verificar al empleador.</small></body></html>"
    ]
    return "\n".join(parts)


def search_all(query="warehouse logistics production factory cleaning construction hotel hospitality kitchen driver agriculture retail"):
    keywords = query_terms(query)
    jobs = []
    for source in SOURCES:
        try:
            jobs.extend(search_source(source, keywords))
        except Exception:
            continue

    deduped = []
    by_url = {}
    by_role = {}
    location_terms = ("zagreb", "rijeka", "krapina", "sesvete", "zminj", "pÃ­sarovina", "pisarovina", "split", "osijek", "pula", "zadar", "varazdin", "varazdin", "dubrovnik")

    def location_key(job):
        city = norm(job.get("city", ""))
        if city and "ubicacion por verificar" not in city and "location to be verified" not in city and city not in ("croatia", "croacia"):
            return city.split(",")[0]
        body = norm(job.get("evidence", ""))
        found = [term for term in location_terms if re.search(r"(?<![a-z])" + re.escape(norm(term)) + r"(?![a-z])", body)]
        return found[0] if found else ""

    def quality(job):
        score = 0
        if job.get("company") and norm(job.get("company")) not in ("por confirmar", "unknown", "not specified"):
            score += 3
        if job.get("city") and "verificar" not in norm(job.get("city")) and "croacia" not in norm(job.get("city")):
            score += 2
        if job.get("salary") and norm(job.get("salary")) not in ("no indicada", "not indicated"):
            score += 1
        score += min(len(job.get("evidence", "")), 2000) / 2000
        return score

    for job in jobs:
        url = job.get("url", "")
        title = job.get("title", "")
        evidence = job.get("evidence", "")
        if is_generic_listing_result(url, title, evidence):
            continue
        if not is_specific_job(url, title, evidence):
            continue
        if not relevant_to_query(job, query):
            continue
        canonical = canonical_job_url(url)
        role_key = (norm(job.get("source", "")), norm(title), location_key(job))
        duplicate_index = by_url.get(canonical)
        if duplicate_index is None:
            # If the source supplies no reliable location, same source + same title
            # is the best available duplicate signal; unique locations stay separate.
            duplicate_index = by_role.get(role_key)
        if duplicate_index is not None:
            if quality(job) > quality(deduped[duplicate_index]):
                deduped[duplicate_index] = job
                # Rebuild indexes for the chosen record.
                by_url[canonical_job_url(job.get("url", ""))] = duplicate_index
                by_role[(norm(job.get("source", "")), norm(job.get("title", "")), location_key(job))] = duplicate_index
            continue
        index = len(deduped)
        deduped.append(job)
        by_url[canonical] = index
        by_role[role_key] = index

    # Order concrete roles ahead of vague descriptions; do not rank by permits or accommodation.
    terms = query_terms(query)
    def rank(job):
        title = norm(job.get("title", ""))
        title_match = any(t in title for t in terms)
        area = job_area(job)
        return (not title_match, area, title)
    deduped.sort(key=rank)
    for job in deduped:
        job["area"] = job_area(job)
        job["priority"] = "Alta" if job["area"] != "Otros empleos" else "Media"
        job["accommodation"] = "Preguntar por correo"
        job["permit"] = "Preguntar por correo"
        job["foreigner"] = "Preguntar si aceptan candidatos de Colombia"
        job["note"] = "Oferta detectada por Ã¡rea laboral. El borrador de correo pregunta por alojamiento, permiso de trabajo/residencia y candidatura desde Colombia."
        job["email_draft_url"] = draft_url_for(job)
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
        if parsed.path == "/api/email-draft":
            markup = email_draft_page(parse_qs(parsed.query))
            return send_html(self, markup)
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", ["warehouse logistics production factory cleaning construction hotel hospitality kitchen driver agriculture retail"])[0]
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
