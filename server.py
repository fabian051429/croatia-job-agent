from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urljoin
from html.parser import HTMLParser
import json, re, ssl, urllib.request, os

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8765"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"

SOURCES = [
    {"name": "Adorio", "url": "https://www.adorio.hr/poslovi.php"},
    {"name": "PickJobs", "url": "https://pick.jobs/hr"},
    {"name": "Jooble Hrvatska", "url": "https://hr.jooble.org/"},
    {"name": "Moj Posao", "url": "https://www.moj-posao.net/"},
    {"name": "Bika", "url": "https://www.bika.net/poslovi"},
    {"name": "Freelance.hr", "url": "https://www.freelance.hr/hr"},
    {"name": "Oglasnik", "url": "https://www.oglasnik.hr/posao"},
    {"name": "Posao.hr", "url": "https://www.posao.hr/"},
]

class Parser(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.links = []
        self.text = []
        self.title = []
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
            self.current_link = None
            self.link_text = []

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
    return re.sub(r"\s+", " ", s or "").strip()

def fetch(url, timeout=6):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.7"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            data = r.read(500000)
            enc = r.headers.get_content_charset() or "utf-8"
            return data.decode(enc, errors="replace"), r.geturl()
    except Exception:
        return "", url

# These terms must explicitly establish BOTH free accommodation and support
# with the work/residence permit. A generic mention of accommodation or
# foreign workers is not sufficient.
FREE_TERMS = [
    "besplatan smjeÅ¡taj", "besplatan smjestaj",
    "besplatni smjeÅ¡taj", "besplatni smjestaj",
    "smjeÅ¡taj je besplatan", "smjestaj je besplatan",
    "smjeÅ¡taj bez naknade", "smjestaj bez naknade",
    "troÅ¡ak smjeÅ¡taja snosi poslodavac", "trosak smjestaja snosi poslodavac",
    "poslodavac osigurava besplatan smjeÅ¡taj",
    "poslodavac osigurava besplatan smjestaj",
    "free accommodation", "accommodation provided free",
    "free housing provided",
]
PERMIT_TERMS = [
    "poslodavac ishoduje dozvolu", "poslodavac osigurava dozvolu",
    "pomoÄ pri ishoÄenju dozvole", "pomoc pri ishodjenju dozvole",
    "pomoÄ oko radne dozvole", "pomoc oko radne dozvole",
    "pomaÅ¾emo pri ishoÄenju radne dozvole", "pomazemo pri ishodjenju radne dozvole",
    "sponzoriramo radnu dozvolu", "sponzorstvo radne dozvole",
    "work permit sponsorship", "visa sponsorship", "we sponsor work permits",
    "assistance with work permit", "help with work permit",
]
JOB_TERMS = [
    "posao", "radnik", "radnica", "skladiÅ¡t", "skladist", "konobar",
    "kuhar", "kuharica", "ÄistaÄ", "cistac", "worker", "waiter",
    "cook", "warehouse", "cleaner", "job", "zapoÅ¡lj", "zaposlj",
]
GENERIC_TITLES = [
    "poslovi", "posao", "jobs", "job search", "traÅ¾ilica poslova",
    "trazilica poslova", "rezultati pretrage", "poÄetna", "pocetna",
    "home", "career", "karijera", "oglasi za posao",
]

def has_any(text, terms):
    t = (text or "").lower()
    return any(term in t for term in terms)

def classify(title, body):
    combined = (title + " " + body).lower()
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
    min_salary = min(nums) if nums else None
    return free, permit, min_salary

def is_specific_job(url, title, body):
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    title_low = clean(title).lower()
    body_low = body.lower()

    if not path or title_low in GENERIC_TITLES:
        return False
    if any(x in path.lower() for x in ["/poslovi", "/oglasi-za-posao", "/category/", "/kategorija/"]):
        # Allow only if it looks like an individual advert URL, not a listing page.
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2:
            return False
    if not has_any(title_low, JOB_TERMS):
        # Some sites use a company/job title that doesn't include a known role;
        # require an advert-like URL and enough job-specific text instead.
        if len(path.split("/")) < 2 or not has_any(body_low, JOB_TERMS):
            return False
    if len(body.split()) < 35:
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
        "accommodation": "Gratis confirmado en el anuncio",
        "permit": "Ayuda con permiso indicada en el anuncio",
        "foreigner": "Revisar condiciones para ciudadano colombiano con el empleador",
        "status": "green" if min_salary is not None and min_salary >= 1200 else "yellow",
        "note": "El anuncio contiene indicaciones explÃ­citas de alojamiento gratuito y ayuda con permiso. Confirma directamente con el empleador que aplica a tu nacionalidad y situaciÃ³n.",
        "evidence": body[:1200],
    }

def search_source(src, keywords):
    html, final_url = fetch(src["url"])
    if not html:
        return []
    parser = Parser(final_url)
    try:
        parser.feed(html)
    except Exception:
        return []

    domain = urlparse(final_url).netloc
    candidates = []
    seen = set()
    for href, label in parser.links:
        u = href.split("#")[0]
        parsed = urlparse(u)
        if parsed.scheme not in ("http", "https") or parsed.netloc != domain:
            continue
        if u in seen:
            continue
        seen.add(u)
        low_path = parsed.path.lower()
        # Crawl likely job/search paths only. Never treat the source homepage itself as a job.
        if any(x in low_path for x in ["/posao", "/poslovi", "/job", "/jobs", "/oglas", "/oglasi", "/vacancy", "/career"]):
            candidates.append((u, label))

    results = []
    for url, link_label in candidates[:12]:
        page, resolved_url = fetch(url, timeout=6)
        if not page:
            continue
        pp = Parser(resolved_url)
        try:
            pp.feed(page)
        except Exception:
            continue
        body = clean(" ".join(pp.text))
        title = clean(" ".join(pp.title)) or link_label
        if not title or not body:
            continue
        if not is_specific_job(resolved_url, title, body):
            continue
        free, permit, _ = classify(title, body)
        # Strict gate: no generic agency pages, and no jobs unless BOTH required
        # conditions are explicitly stated in the page text.
        if not (free and permit):
            continue
        results.append(make_job(src["name"], resolved_url, title, body))
        if len(results) >= 5:
            break
    return results

def search_all(query="skladiÅ¡tar radnik smjeÅ¡taj dozvola"):
    # Keep the user's query terms, but the mandatory conditions are enforced
    # independently by classify(), not by loose keyword matching.
    keywords = [x.lower() for x in re.findall(r"[\wÄÄÄÅ¡Å¾ÄÄÄÅ Å½]+", query) if len(x) > 3]
    jobs = []
    for source in SOURCES:
        try:
            jobs.extend(search_source(source, keywords))
        except Exception:
            continue
    seen = set()
    deduped = []
    for job in jobs:
        key = job["url"]
        if key not in seen:
            seen.add(key)
            deduped.append(job)
    return deduped[:40]

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
            query = parse_qs(parsed.query).get("q", ["skladiÅ¡tar radnik smjeÅ¡taj dozvola"])[0]
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
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_error(404)

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    print(f"Agente de Empleo Croacia: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
