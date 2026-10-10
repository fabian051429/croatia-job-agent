from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote, urljoin
from html.parser import HTMLParser
import json, re, ssl, urllib.request, urllib.error

import os
HOST = '0.0.0.0'
PORT = int(os.environ.get('PORT', '8765'))
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36'

SOURCES=[
 {'name':'Adorio','url':'https://www.adorio.hr/poslovi.php'},
 {'name':'PickJobs','url':'https://pick.jobs/hr'},
 {'name':'Jooble Hrvatska','url':'https://hr.jooble.org/'},
 {'name':'Moj Posao','url':'https://www.moj-posao.net/'},
 {'name':'Bika','url':'https://www.bika.net/poslovi'},
 {'name':'Freelance.hr','url':'https://www.freelance.hr/hr'},
 {'name':'Oglasnik','url':'https://www.oglasnik.hr/posao'},
 {'name':'Posao.hr','url':'https://www.posao.hr/'},
]

class Parser(HTMLParser):
 def __init__(self, base):
  super().__init__(); self.base=base; self.links=[]; self.text=[]
 def handle_starttag(self, tag, attrs):
  if tag=='a':
   d=dict(attrs); href=d.get('href')
   if href: self.links.append((urljoin(self.base,href), ''))
 def handle_data(self,data):
  t=re.sub(r'\\s+',' ',data).strip()
  if t: self.text.append(t)

def fetch(url, timeout=12):
 req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'hr-HR,hr;q=0.9,en;q=0.7'})
 ctx=ssl.create_default_context()
 try:
  with urllib.request.urlopen(req,timeout=timeout,context=ctx) as r:
   data=r.read(500000)
   enc=r.headers.get_content_charset() or 'utf-8'
   return data.decode(enc,errors='replace'), r.geturl()
 except Exception as e:
  return '', url

def clean(s): return re.sub(r'\\s+',' ',s or '').strip()

def classify(title, body, url):
 t=(title+' '+body).lower()
 free=any(x in t for x in ['besplatan smještaj','besplatan smjestaj','smještaj bez naknade','smjestaj bez naknade','trošak smještaja snosi poslodavac','trosak smjestaja snosi poslodavac','osiguran smještaj i hrana','osiguran smjestaj i hrana'])
 accom=free or 'smještaj' in t or 'smjestaj' in t
 permit=any(x in t for x in ['dozvola za boravak i rad','dozvolu za boravak i rad','radna dozvola','dozvole za rad','strani radnici','državljani trećih zemalja','drzavljani trecih zemalja'])
 salary=re.search(r'(?:€|eur|eura)\\s?([0-9][0-9 .]{2,5})(?:[-–]\\s?([0-9][0-9 .]{2,5}))?',t)
 nums=[]
 if salary:
  for g in salary.groups():
   if g: 
    try: nums.append(int(re.sub(r'[^0-9]','',g)))
    except: pass
 minsal=min(nums) if nums else None
 hard=free and permit
 status='green' if hard and (minsal is None or minsal>=1200) else 'yellow' if free else 'orange'
 return status, free, accom, permit, minsal

def make_job(source, url, title, body):
 status,free,accom,permit,minsal=classify(title,body,url)
 return {'source':source,'url':url,'title':clean(title)[:160],'company':'Por confirmar','city':'Croacia','salary':('€'+str(minsal)+'+' if minsal else 'No indicada'),'accommodation': 'Gratis confirmado' if free else ('Mencionado; verificar coste' if accom else 'No indicada'),'permit':'Posible/indicado; verificar' if permit else 'No confirmado','foreigner':'Indicio de contratación extranjera' if permit else 'No indicado','status':status,'note':'Resultado recopilado de la página pública. La aplicación exige verificar alojamiento gratuito y permiso para ciudadano colombiano antes de autorizar una candidatura.','evidence':body[:1800]}

def search_source(src, keywords):
 html,final=fetch(src['url'])
 if not html: return []
 p=Parser(final); p.feed(html)
 text=' '.join(p.text)
 out=[]
 # First, treat the landing page itself as a candidate only if it has strong job terms.
 if any(k in text.lower() for k in keywords):
  out.append(make_job(src['name'],final,src['name']+' — resultados relevantes',text))
 # Crawl a small set of same-domain links likely to be job detail/search pages.
 seen=set(); candidates=[]
 domain=urlparse(final).netloc
 for href,_ in p.links:
  u=href.split('#')[0]
  if urlparse(u).netloc!=domain or u in seen: continue
  seen.add(u)
  low=u.lower()
  if any(x in low for x in ['/posao','/poslovi','/job','/jobs','/oglasi','/tasks','/hr/poslovi']): candidates.append(u)
 for u in candidates[:18]:
  h,f=fetch(u,8)
  if not h: continue
  pp=Parser(f); pp.feed(h); tx=' '.join(pp.text)
  low=tx.lower()
  if any(k in low for k in keywords) and any(x in low for x in ['posao','radnik','worker','zapošlj','zaposlj','plać','plac','smješt','smjest']):
   title=(pp.text[0] if pp.text else 'Oferta')
   out.append(make_job(src['name'],f,title,tx))
  if len(out)>=8: break
 return out

def search_all(query='skladištar radnik smještaj dozvola'):
 kws=[x.lower() for x in re.findall(r'[\wčćđšžČĆĐŠŽ]+',query) if len(x)>3]
 # Add mandatory semantic terms; this prevents unrelated listings.
 kws += ['smještaj','smjestaj','dozvola','strani radnici','boravak']
 allj=[]
 for s in SOURCES:
  try: allj += search_source(s,kws)
  except Exception: pass
 # dedupe
 seen=set(); ded=[]
 for j in allj:
  key=(j['url'],j['title'])
  if key not in seen: seen.add(key); ded.append(j)
 return ded[:40]

class Handler(BaseHTTPRequestHandler):
 def send_json(self,obj,code=200):
  b=json.dumps(obj,ensure_ascii=False).encode('utf-8'); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_GET(self):
  p=urlparse(self.path)
  if p.path=='/api/sources': return self.send_json({'sources':SOURCES})
  if p.path=='/api/search':
   q=parse_qs(p.query).get('q',['skladištar radnik smještaj dozvola'])[0]
   return self.send_json({'query':q,'jobs':search_all(q)})
  if p.path=='/' or p.path=='/index.html':
   try:
   data=open('index.html','rb').read()
   except: self.send_error(404)
   return
  path='.'+p.path
  try:
   data=open(path,'rb').read(); types={'.css':'text/css','.js':'application/javascript','.json':'application/json','.txt':'text/plain'}; ct=next((v for k,v in types.items() if path.endswith(k)),'application/octet-stream'); self.send_response(200); self.send_header('Content-Type',ct); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
  except: self.send_error(404)
 def log_message(self,*a): pass

if __name__=='__main__':
 print(f'Agente de Empleo Croacia: http://{HOST}:{PORT}')
 ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8765'))), Handler).serve_forever()
