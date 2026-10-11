from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urljoin, quote_plus
from html.parser import HTMLParser
import json, re, ssl, urllib.request, os, html as html_lib

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8765"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"

# Use search/listing pages rather than only the homepages.
SOURCES = [
    {"name": "Adorio", "url": "https://www.adorio.hr/poslovi.php"},
    {"name": "PickJobs", "url": "https://pick.jobs/hr"},
    {"name": "Jooble Hrvatska", "url": "https://hr.jooble.org/posao/" + quote_plus("skladistar")},
    {"name": "Moj Posao", "url": "https://www.moj-posao.net/Pretraga-Poslova/"},
    {"name": "Bika", "url": "https://www.bika.net/poslovi"},
    {"name": "Freelance.hr", "url": "https://www.freelance.hr/hr"},
    {"name": "Oglasnik", "url": "https://www.oglasnik.hr/posao"},
    {"name": "Posao.hr", "url": "https://www.posao.hr/"},
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

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "a" and d.get("href"):
            self.current_link = urljoin(self.base, d["href"])
            self.link_text = []

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "a" and self.current_link:
            label = clean(" ".join(self.link_text))
            self.links.append((self.current_link, label))
            self.current_link, self.link_text = None, []

    def handle_data(self, data):
        t = clean(data)
        if not t:
            return
        self.text.append(t)
        if self.in_title:
            self.title.append(t)
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
    "help with work permit", "dozvola za boravak i rad"
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
        "permit": "Ayuda indicada en el anuncio" if permit else "No confirmada",
        "foreigner": "Confirmar con el empleador si acepta ciudadanos colombianos",
        "status": "yellow",
        "note": "Verifica directamente con el empleador que el alojamiento sea gratuito y que pueda gestionar el permiso de trabajo para tu caso.",
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
        pp = Parser(resolved_url)
        try:
            pp.feed(page)
        except Exception:
            continue
        body = clean(" ".join(pp.text))
        title = clean(" ".join(pp.title)) or link_label
        if not title or not body or not is_specific_job(resolved_url, title, body):
            continue
        free, permit, _ = classify(title, body)
        # Show real vacancies even if the conditions aren't explicitly stated;
        # the UI labels them as unconfirmed rather than claiming they are met.
        if resolved_url in result_urls:
            continue
        result_urls.add(resolved_url)
        results.append(make_job(src["name"], resolved_url, title, body))
        if len(results) >= 12:
            break
    return results

def search_all(query="skladistar radnik smjestaj dozvola"):
    keywords = [x.lower() for x in re.findall(r"[\wÄÄÄÅ¡Å¾ÄÄÄÅ Å½]+", query) if len(x) > 2]
    jobs = []
    for source in SOURCES:
        try:
            jobs.extend(search_source(source, keywords))
        except Exception:
            continue
    seen, deduped = set(), []
    for job in jobs:
        key = job["url"]
        if key not in seen:
            seen.add(key)
            deduped.append(job)
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
