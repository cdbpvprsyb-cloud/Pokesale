import os,json,sqlite3,threading,time,html,re,hashlib
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,quote_plus
from urllib.request import Request,urlopen
from datetime import datetime,timezone
PORT=int(os.getenv('PORT','10000')); DB=os.getenv('DB_PATH','/tmp/pokesale_v3.db'); SCAN_MINUTES=int(os.getenv('SCAN_MINUTES','15'))
UA='Pokesale/3.0 (+personal retail monitor) Mozilla/5.0'
STORES=[('HOME','GameStop','Prince Frederick','725 Solomons Island Rd N Ste C'),('HOME','Five Below','Prince Frederick','855 Solomons Island Rd N'),('HOME','ALDI','Prince Frederick','429 Solomons Island Rd N'),('HOME','Walmart','Prince Frederick','150 Solomons Island Rd N'),('WORK','Target','Greenbelt','6100 Greenbelt Rd'),('WORK','Target','Cherry Hill','12000 Cherry Hill Rd'),('WORK','Five Below','Greenbelt','6000 Greenbelt Rd #65A'),('WORK','Five Below','Laurel','14260A Baltimore Ave'),('WORK','Dollar Tree','Beltsville','10464 Baltimore Ave'),('WORK','ALDI','Beltsville','10912 Baltimore Ave'),('WORK',"Sam's Club",'Laurel','3535 Russett Green E')]
LOCK=threading.Lock()

def db():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;return c
def init():
 c=db();c.executescript('''create table if not exists products(id text primary key,source text,retailer text,name text,retail_price real,market_price real,url text,image text,status text,last_seen text);create table if not exists scans(id integer primary key,ts text,source text,found int,error text);''');c.commit();c.close()
init()
def fetch(url): return urlopen(Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'}),timeout=25).read().decode('utf-8','ignore')
def fetch_json(url): return json.loads(fetch(url))
def norm(s): return ' '.join(html.unescape(re.sub(r'<[^>]+>',' ',s)).split())
def pid(source,name): return hashlib.sha1((source+'|'+name.lower()).encode()).hexdigest()[:24]
def upsert(source,retailer,name,retail,market,url,image,status):
 c=db();now=datetime.now(timezone.utc).isoformat();c.execute('''insert into products values(?,?,?,?,?,?,?,?,?,?) on conflict(id) do update set retail_price=excluded.retail_price,market_price=coalesce(excluded.market_price,products.market_price),url=excluded.url,image=excluded.image,status=excluded.status,last_seen=excluded.last_seen''',(pid(source,name),source,retailer,name,retail,market,url,image,status,now));c.commit();c.close()
def log(source,n,err=''):
 c=db();c.execute('insert into scans(ts,source,found,error) values(?,?,?,?)',(datetime.now(timezone.utc).isoformat(),source,n,err[:300]));c.commit();c.close()

def parse_target(raw):
 # Handles visible text and common embedded/server-rendered markup.
 text=html.unescape(re.sub(r'<script[^>]*>[\s\S]*?</script>',' ',raw,flags=re.I));text=re.sub(r'<[^>]+>','\n',text)
 lines=[' '.join(x.split()) for x in text.splitlines() if x.strip()];rows=[]
 for i,x in enumerate(lines):
  m=re.fullmatch(r'\$([0-9]{1,4}(?:\.[0-9]{2})?)',x)
  if not m: continue
  for n in lines[i+1:i+12]:
   low=n.lower()
   if 12<len(n)<240 and 'pokemon' in low and not low.startswith('pokemon :'):
    rows.append((n,float(m.group(1))));break
 # JSON/HTML fallback: find Pokemon title and nearby dollar value in either direction.
 if not rows:
  clean=html.unescape(raw).replace('\\u0026','&').replace('\\u0027',"'")
  for m in re.finditer(r'(Pokemon[^"<>]{12,210})',clean,re.I):
   n=norm(m.group(1)); window=clean[max(0,m.start()-900):min(len(clean),m.end()+900)]
   prices=re.findall(r'\$([0-9]{1,4}(?:\.[0-9]{2})?)',window)
   if prices and 12<len(n)<240: rows.append((n,float(prices[0])))
 out={}
 for n,p in rows:
  if 0<p<1000: out[n.lower()]=(n,p)
 return list(out.values())[:250]

def scan_retailers():
 feeds=[('Target','https://www.target.com/c/trading-cards-toys-games/pokemon/-/N-27p31Z569t0')]
 for retailer,url in feeds:
  try:
   rows=parse_target(fetch(url))
   for name,price in rows: upsert('retailer:'+retailer,retailer,name,price,None,'https://www.target.com/s?searchTerm='+quote_plus(name),'','CATALOG OBSERVED — LOCAL STOCK NOT CONFIRMED')
   log(retailer,len(rows))
  except Exception as e: log(retailer,0,type(e).__name__+': '+str(e))

def sealed_name(name):
 n=name.lower()
 good=('booster box','elite trainer','booster bundle','booster pack','collection',' tin','tin ','blister','build & battle','battle box','ex box','trainer box','premium box','poster collection','tech sticker')
 bad=('single card','code card','energy card','sleeves','portfolio','binder','playmat')
 return any(x in n for x in good) and not any(x in n for x in bad)
def scan_market():
 try:
  groups=fetch_json('https://tcgcsv.com/tcgplayer/3/groups').get('results',[])
  # Misc products plus newest published groups; keeps request volume reasonable.
  def ts(g): return g.get('publishedOn') or ''
  newest=sorted(groups,key=ts,reverse=True)[:18]
  ids=[2374]+[g.get('groupId') for g in newest if g.get('groupId')!=2374]
  count=0
  for gid in ids:
   try:
    prods=fetch_json(f'https://tcgcsv.com/tcgplayer/3/{gid}/products').get('results',[])
    prices=fetch_json(f'https://tcgcsv.com/tcgplayer/3/{gid}/prices').get('results',[])
    pm={}
    for q in prices:
     v=q.get('marketPrice')
     if v is not None: pm[q.get('productId')]=max(float(v),pm.get(q.get('productId'),0))
    for p in prods:
     name=p.get('name','')
     if not sealed_name(name): continue
     market=pm.get(p.get('productId'))
     if market is None: continue
     upsert('TCGCSV','Market',name,None,market,p.get('url') or 'https://www.tcgplayer.com/search/pokemon/product?q='+quote_plus(name),p.get('imageUrl') or '','TCGPLAYER MARKET DATA')
     count+=1
   except Exception: pass
   time.sleep(.12)
  log('TCGCSV',count)
 except Exception as e: log('TCGCSV',0,type(e).__name__+': '+str(e))

def scan_all():
 if not LOCK.acquire(False):return
 try: scan_retailers();scan_market()
 finally: LOCK.release()
def worker():
 time.sleep(3)
 while True:scan_all();time.sleep(max(600,SCAN_MINUTES*60))
threading.Thread(target=worker,daemon=True).start()
CSS='''body{margin:0;background:#07101f;color:#eef5ff;font-family:-apple-system,sans-serif}.w{max-width:1050px;margin:auto;padding:16px}a{color:white}.nav{display:flex;gap:8px;overflow:auto}.card,.note,.stat{background:#111d35;border-radius:16px;padding:13px}.nav a{background:#1b2b4b;padding:10px 14px;border-radius:999px;text-decoration:none;white-space:nowrap}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px}.name{font-weight:800;font-size:17px;margin:8px 0}.price{font-weight:900;font-size:24px}.mut{color:#9fb1cb;font-size:13px;line-height:1.45}.note{border:1px solid #705b25;color:#ffe7a0;margin:12px 0}.tag{display:inline-block;background:#19345b;border-radius:999px;padding:5px 8px;font-size:12px}.profit{color:#7af0aa;font-weight:800}.warn{color:#ffd27a}.img{width:100%;height:150px;object-fit:contain;background:white;border-radius:11px}.num{font-size:24px;font-weight:900}'''
def e(x):return html.escape(str(x),quote=True)
def home():
 c=db();ps=c.execute('select * from products order by case when retail_price is not null then 0 else 1 end,last_seen desc limit 500').fetchall();ss=c.execute('select * from scans order by id desc limit 6').fetchall();c.close();cards=''
 for x in ps:
  img=f"<img class=img src='{e(x['image'])}' loading=lazy>" if x['image'] else ''
  price=f"Retail ${x['retail_price']:.2f}" if x['retail_price'] is not None else ''
  market=f"Market ${x['market_price']:.2f}" if x['market_price'] is not None else ''
  vals=' · '.join(v for v in (price,market) if v)
  cards+=f"<div class=card>{img}<span class=tag>{e(x['retailer'])}</span><div class=name>{e(x['name'])}</div><div class=price>{vals}</div><div class=mut>{e(x['status'])}<br>Source: {e(x['source'])}</div><p><a href='{e(x['url'])}' target=_blank>Open source</a> · <a href='https://www.ebay.com/sch/i.html?_nkw={quote_plus(x['name'])}&LH_Sold=1&LH_Complete=1' target=_blank>eBay sold</a></p></div>"
 if not cards:cards='<div class=card>First scan is starting. Check Diagnostics in about a minute.</div>'
 status=' | '.join(f"{x['source']}: {x['found']}"+(' ERROR' if x['error'] else '') for x in ss) or 'starting'
 retail=sum(1 for x in ps if x['retail_price'] is not None); market=sum(1 for x in ps if x['market_price'] is not None)
 return f"<meta name=viewport content='width=device-width,initial-scale=1'><style>{CSS}</style><div class=w><h1>⚡ Pokesale</h1><div class=nav><a href=/>Products</a><a href=/stores>Stores</a><a href=/scan>Scan now</a><a href=/diagnostics>Diagnostics</a></div><div class=stats><div class=stat><div class=num>{len(ps)}</div><div class=mut>known products</div></div><div class=stat><div class=num>{retail}</div><div class=mut>retail sightings</div></div><div class=stat><div class=num>{market}</div><div class=mut>market priced</div></div></div><div class=note><b>Two-source radar.</b> Retailer catalog sightings and TCGplayer market data are tracked separately. Local store stock remains NOT CONFIRMED until a store-specific fulfillment source verifies it.<br><br>{status}</div><div class=grid>{cards}</div></div>"
def stores():return '<meta name=viewport content="width=device-width,initial-scale=1"><style>'+CSS+'</style><div class=w><h1>Stores</h1><a href=/>Back</a><div class=grid>'+''.join(f'<div class=card><b>{e(z)}: {e(r)} — {e(n)}</b><div class=mut>{e(a)}</div></div>' for z,r,n,a in STORES)+'</div></div>'
def diag():
 c=db();ss=c.execute('select * from scans order by id desc limit 40').fetchall();c.close();return '<meta name=viewport content="width=device-width,initial-scale=1"><style>'+CSS+'</style><div class=w><h1>Diagnostics</h1><a href=/>Back</a><div class=grid>'+''.join(f"<div class=card><b>{e(x['source'])}: {x['found']}</b><div class=mut>{e(x['ts'])}<br>{e(x['error'] or 'OK')}</div></div>" for x in ss)+'</div></div>'
class H(BaseHTTPRequestHandler):
 def out(self,s,ct='text/html'):
  b=s.encode();self.send_response(200);self.send_header('Content-Type',ct);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  p=urlparse(self.path).path
  if p=='/':return self.out(home())
  if p=='/stores':return self.out(stores())
  if p=='/diagnostics':return self.out(diag())
  if p=='/scan':threading.Thread(target=scan_all,daemon=True).start();self.send_response(302);self.send_header('Location','/');self.end_headers();return
  if p=='/health':
   c=db();n=c.execute('select count(*) from products').fetchone()[0];c.close();return self.out(json.dumps({'ok':True,'products':n}),'application/json')
  self.send_response(404);self.end_headers()
 def log_message(self,*a):pass
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',PORT),H).serve_forever()
