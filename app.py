import os,json,sqlite3,threading,time,html,re,hashlib
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,quote_plus
from urllib.request import Request,urlopen
from datetime import datetime,timezone,timedelta

PORT=int(os.getenv("PORT","10000"))
DB=os.getenv("DB_PATH","/tmp/pokesale_v5.db")
SCAN_MINUTES=max(10,int(os.getenv("SCAN_MINUTES","15")))
UA="Mozilla/5.0 (compatible; Pokesale/5.0; personal retail monitor)"
LOCK=threading.Lock()

STORES=[
 ("HOME","Walmart","Prince Frederick","150 Solomons Island Rd N"),
 ("HOME","GameStop","Prince Frederick","725 Solomons Island Rd N Ste C"),
 ("HOME","Five Below","Prince Frederick","855 Solomons Island Rd N"),
 ("HOME","ALDI","Prince Frederick","429 Solomons Island Rd N"),
 ("HOME","Dollar General","St. Leonard","625 Calvert Beach Rd"),
 ("WORK","Target","Greenbelt","6100 Greenbelt Rd"),
 ("WORK","Target","Cherry Hill","12000 Cherry Hill Rd"),
 ("WORK","Five Below","Greenbelt","6000 Greenbelt Rd #65A"),
 ("WORK","Five Below","Laurel","14260A Baltimore Ave"),
 ("WORK","Dollar Tree","Beltsville","10464 Baltimore Ave"),
 ("WORK","ALDI","Beltsville","10912 Baltimore Ave"),
 ("WORK","Sam's Club","Laurel","3535 Russett Green E"),
 ("ANNAPOLIS","Target","Annapolis","1911 Towne Centre Blvd"),
 ("ANNAPOLIS","Walmart","Annapolis","55 Forest Plaza")
]

FEEDS=[
 ("Target","https://www.target.com/c/trading-cards-toys-games/pokemon/-/N-27p31Z569t0"),
 ("Walmart","https://www.walmart.com/browse/collectibles/pokemon-cards/5967908_9807313_2611231"),
 ("GameStop","https://www.gamestop.com/toys-games/trading-cards/pokemon"),
 ("Five Below","https://www.fivebelow.com/categories/toys-and-games/trading-cards"),
 ("Dollar General","https://www.dollargeneral.com/c/toys/trading-cards")
]

def db():
 c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; return c

def init():
 os.makedirs(os.path.dirname(DB) or ".",exist_ok=True)
 c=db(); c.executescript("""
 create table if not exists products(
 id text primary key,source text,retailer text,name text,retail_price real,market_price real,
 url text,image text,status text,last_seen text,first_seen text,category text default 'Pokemon',
 location text default 'Catalog / online',confidence integer default 25,stock_state text default 'observed');
 create table if not exists scans(id integer primary key,ts text,source text,found int,error text);
 create table if not exists inventory_history(id integer primary key,product_id text,ts text,state text,confidence integer,location text);
 create index if not exists ix_products_seen on products(last_seen);
 create index if not exists ix_hist_product on inventory_history(product_id,ts);
 """)
 cols={r[1] for r in c.execute("pragma table_info(products)")}
 for n,d in [("first_seen","text"),("category","text default 'Pokemon'"),("location","text default 'Catalog / online'"),("confidence","integer default 25"),("stock_state","text default 'observed'")]:
  if n not in cols:c.execute(f"alter table products add column {n} {d}")
 c.commit(); c.close()
init()

def fetch(url):
 req=Request(url,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,application/json","Accept-Language":"en-US,en;q=0.8"})
 return urlopen(req,timeout=25).read().decode("utf-8","ignore")
def fetch_json(url):return json.loads(fetch(url))
def pid(source,name,location=""):return hashlib.sha1((source+"|"+name.lower()+"|"+location.lower()).encode()).hexdigest()[:24]

def category_for(n):
 s=n.lower()
 if any(x in s for x in ("pokemon","pokémon","elite trainer","booster","poke")):return "Pokemon"
 if any(x in s for x in ("topps","panini","football","baseball","basketball","sports card")):return "Sports Cards"
 return "Other Flips"
def confidence_for(status):
 s=status.lower()
 if "confirmed restock" in s:return 96
 if "store pickup" in s or "store-specific" in s:return 85
 if "retailer catalog" in s:return 45
 if "market data" in s:return 20
 return 30

def upsert(source,retailer,name,retail,market,url,image,status,location="Catalog / online",stock_state="observed"):
 c=db(); now=datetime.now(timezone.utc).isoformat(); ident=pid(source,name,location); conf=confidence_for(status)
 old=c.execute("select stock_state from products where id=?",(ident,)).fetchone()
 c.execute("""insert into products(id,source,retailer,name,retail_price,market_price,url,image,status,last_seen,first_seen,category,location,confidence,stock_state)
 values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(id) do update set
 retail_price=coalesce(excluded.retail_price,products.retail_price),
 market_price=coalesce(excluded.market_price,products.market_price),
 url=coalesce(nullif(excluded.url,''),products.url),image=coalesce(nullif(excluded.image,''),products.image),
 status=excluded.status,last_seen=excluded.last_seen,category=excluded.category,location=excluded.location,
 confidence=excluded.confidence,stock_state=excluded.stock_state""",
 (ident,source,retailer,name,retail,market,url,image,status,now,now,category_for(name),location,conf,stock_state))
 if old is None or old["stock_state"]!=stock_state:
  c.execute("insert into inventory_history(product_id,ts,state,confidence,location) values(?,?,?,?,?)",(ident,now,stock_state,conf,location))
 c.commit(); c.close()

def log(source,n,err=""):
 c=db(); c.execute("insert into scans(ts,source,found,error) values(?,?,?,?)",(datetime.now(timezone.utc).isoformat(),source,n,err[:800])); c.commit(); c.close()

def walk(x):
 if isinstance(x,dict):
  yield x
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)

def money(v):
 try:
  if isinstance(v,dict):v=v.get("price") or v.get("value") or v.get("currentPrice")
  if isinstance(v,str):
   m=re.search(r"\d+(?:\.\d{1,2})?",v.replace(",",""))
   v=m.group(0) if m else None
  f=float(v)
  return f if 0<f<2000 else None
 except:return None

def productish(name):
 n=(name or "").lower()
 return ("pokemon" in n or "pokémon" in n) and any(x in n for x in ("elite trainer","etb","booster","collection"," tin","tin ","box","bundle","blister"))

def json_candidates(raw):
 roots=[]
 for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',raw,re.I|re.S):
  try:roots.append(json.loads(html.unescape(m.group(1)).strip()))
  except:pass
 for marker in ('__NEXT_DATA__','__PRELOADED_STATE__'):
  m=re.search(r'<script[^>]*(?:id=["\']'+marker+r'["\'])[^>]*>(.*?)</script>',raw,re.I|re.S)
  if m:
   try:roots.append(json.loads(html.unescape(m.group(1)).strip()))
   except:pass
 return roots

def parse_retailer(raw,base):
 out={}
 for root in json_candidates(raw):
  for d in walk(root):
   name=d.get("name") or d.get("title") or d.get("productName")
   if not isinstance(name,str) or not productish(name):continue
   offer=d.get("offers") or {}
   price=money(d.get("price") or d.get("currentPrice") or d.get("priceString"))
   if price is None and isinstance(offer,dict):price=money(offer.get("price") or offer.get("lowPrice"))
   if price is None:continue
   image=d.get("image") or d.get("imageUrl") or d.get("primaryImage") or ""
   if isinstance(image,list):image=image[0] if image else ""
   if isinstance(image,dict):image=image.get("url") or image.get("src") or ""
   url=d.get("url") or d.get("canonicalUrl") or ""
   if isinstance(url,str) and url.startswith("/"):url=base.rstrip("/")+url
   key=re.sub(r"\W+"," ",name.lower()).strip()
   out[key]=(name,price,url if isinstance(url,str) else "",image if isinstance(image,str) else "")
 # Conservative visible-HTML fallback. Never interprets this as local stock.
 text=html.unescape(re.sub(r"<[^>]+>"," ",raw))
 for m in re.finditer(r'((?:Pok[eé]mon).{0,150}?(?:Elite Trainer Box|ETB|Booster (?:Bundle|Box|Pack)|Collection|Tin)).{0,100}?\$([0-9]{1,3}(?:\.[0-9]{2})?)',text,re.I):
  name=" ".join(m.group(1).split()); p=money(m.group(2))
  if p:out.setdefault(re.sub(r"\W+"," ",name.lower()).strip(),(name,p,"",""))
 return list(out.values())[:250]

def scan_retailers():
 for retailer,url in FEEDS:
  try:
   rows=parse_retailer(fetch(url),url.split("/",3)[0]+"//"+url.split("/",3)[2])
   for name,price,purl,img in rows:
    link=purl or url
    upsert("retailer:"+retailer,retailer,name,price,None,link,img,
           "RETAILER CATALOG + PRICE — LOCAL STOCK NOT CONFIRMED","Catalog / online","catalog")
   log(retailer,len(rows),"OK" if rows else "Page fetched, but no parseable Pokemon sealed products/prices were exposed.")
  except Exception as ex:log(retailer,0,type(ex).__name__+": "+str(ex))

def sealed_name(name):
 n=name.lower()
 good=("booster box","elite trainer","booster bundle","booster pack","collection"," tin","tin ","blister","build & battle","battle box","ex box","trainer box","premium box","poster collection")
 bad=("single card","code card","energy card","sleeves","portfolio","binder","playmat")
 return any(x in n for x in good) and not any(x in n for x in bad)

def scan_market():
 try:
  groups=fetch_json("https://tcgcsv.com/tcgplayer/3/groups").get("results",[])
  newest=sorted(groups,key=lambda g:g.get("publishedOn") or "",reverse=True)[:18]
  ids=list(dict.fromkeys([2374]+[g.get("groupId") for g in newest if g.get("groupId")]))
  count=0
  for gid in ids:
   try:
    prods=fetch_json(f"https://tcgcsv.com/tcgplayer/3/{gid}/products").get("results",[])
    prices=fetch_json(f"https://tcgcsv.com/tcgplayer/3/{gid}/prices").get("results",[]); pm={}
    for q in prices:
     v=q.get("marketPrice")
     if v is not None:pm[q.get("productId")]=max(float(v),pm.get(q.get("productId"),0))
    for p in prods:
     name=p.get("name",""); market=pm.get(p.get("productId"))
     if not sealed_name(name) or market is None:continue
     upsert("TCGCSV","TCGplayer",name,None,market,p.get("url") or "https://www.tcgplayer.com/search/pokemon/product?q="+quote_plus(name),p.get("imageUrl") or "","TCGPLAYER MARKET DATA")
     count+=1
   except Exception:pass
   time.sleep(.05)
  log("TCGCSV",count)
 except Exception as ex:log("TCGCSV",0,type(ex).__name__+": "+str(ex))

STOP={"pokemon","pokémon","trading","card","game","tcg","the","box","cards","new","sealed","with","and"}
def tokens(s):
 s=s.lower().replace("pokémon","pokemon").replace("elite trainer box"," etb ")
 return [x for x in re.sub(r"[^a-z0-9 ]"," ",s).split() if x not in STOP]
def kind(s):
 n=s.lower()
 if "elite trainer" in n or re.search(r"\betb\b",n):return "etb"
 if "booster bundle" in n:return "booster bundle"
 if "booster box" in n:return "booster box"
 if "booster pack" in n:return "booster pack"
 if " tin" in " "+n:return "tin"
 if "collection" in n:return "collection"
 return "other"
def match_score(a,b):
 ka,kb=kind(a),kind(b)
 if ka!=kb and "other" not in (ka,kb):return 0
 A,B=set(tokens(a)),set(tokens(b))
 if not A or not B:return 0
 j=len(A&B)/len(A|B)
 contain=len(A&B)/min(len(A),len(B))
 bonus=.12 if ka==kb and ka!="other" else 0
 return min(1,j*.65+contain*.35+bonus)

def opportunities(limit=500):
 c=db(); rows=[dict(r) for r in c.execute("select * from products order by last_seen desc limit 1800")]; c.close()
 market=[r for r in rows if r["market_price"]]
 for r in rows:
  if r["retail_price"] is not None and not r["market_price"]:
   best=None;score=0
   for m in market:
    s=match_score(r["name"],m["name"])
    if s>score:score=s;best=m
   threshold=.60 if kind(r["name"])=="etb" else .68
   if best and score>=threshold:
    r["market_price"]=best["market_price"];r["image"]=r["image"] or best["image"]
    r["market_match"]=best["name"];r["match_score"]=round(score,2)
  rp,mp=r.get("retail_price"),r.get("market_price")
  r["projected_resale"]=round(mp,2) if mp is not None else None
  if rp is not None and mp is not None:
   profit=mp*.8675-rp-5
   r["profit"]=round(profit,2);r["roi"]=round(profit/rp*100,1) if rp else None
  else:r["profit"]=r["roi"]=None
 return rows[:limit]

def scan_all():
 if not LOCK.acquire(False):return
 try:
  scan_retailers()
  scan_market()
 finally:LOCK.release()
def worker():
 time.sleep(3)
 while True:scan_all();time.sleep(SCAN_MINUTES*60)
threading.Thread(target=worker,daemon=True).start()

def esc(x):return html.escape(str(x or ""),quote=True)
def age(ts):
 try:
  mins=int((datetime.now(timezone.utc)-datetime.fromisoformat(ts)).total_seconds()/60)
  return f"{max(0,mins)}m ago" if mins<60 else f"{mins//60}h ago" if mins<1440 else f"{mins//1440}d ago"
 except:return ""

CSS="""*{box-sizing:border-box}body{margin:0;background:#07101f;color:#eef5ff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.w{max-width:1120px;margin:auto;padding:12px 10px 70px}.top{display:flex;justify-content:space-between;align-items:center}.brand{font-size:27px;font-weight:950}.mut{color:#94a8c5;font-size:12px;line-height:1.45}.nav{display:flex;gap:7px;overflow:auto;padding:10px 0}.nav a,.btn{color:#fff;background:#18365d;padding:8px 10px;border-radius:10px;text-decoration:none;white-space:nowrap;font-size:12px}.hero,.card,.store{background:#101c32;border:1px solid #1e3455;border-radius:16px}.hero{padding:13px}.stats,.money{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.stat,.money div{background:#0b172b;padding:8px;border-radius:10px}.num{font-size:20px;font-weight:900}.section{margin-top:20px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(255px,1fr));gap:9px}.card{overflow:hidden}.pic{width:100%;height:145px;object-fit:contain;background:#fff}.pad{padding:11px}.name{font-weight:850;margin:7px 0}.tag{font-size:10px;background:#18365d;border-radius:99px;padding:5px 7px}.money b{display:block}.money span{font-size:9px;color:#849bb9}.good{color:#72efa4}.warn{color:#ffd171}.empty{padding:14px;background:#0e1a30;border-radius:13px;color:#8fa4c2}.store{padding:11px;margin:7px 0}@media(max-width:520px){.grid{grid-template-columns:1fr}}"""

def card(r):
 def cash(v):return "—" if v is None else f"${v:,.2f}"
 img=f"<img class=pic src='{esc(r.get('image'))}' loading=lazy>" if r.get("image") else ""
 profit=r.get("profit");roi=r.get("roi"); conf=int(r.get("confidence") or 0)
 return f"""<article class=card>{img}<div class=pad><span class=tag>{esc(r.get('retailer'))} · {esc(r.get('location'))}</span><div class=name>{esc(r.get('name'))}</div><div class=money><div><span>RETAIL</span><b>{cash(r.get('retail_price'))}</b></div><div><span>RESALE</span><b>{cash(r.get('projected_resale'))}</b></div><div><span>NET / ROI</span><b class="{'good' if profit is not None and profit>0 else ''}">{cash(profit)}{(' · '+str(roi)+'%') if roi is not None else ''}</b></div></div><div class=mut>{esc(r.get('status'))}<br>{conf}% confidence · {age(r.get('last_seen'))}{('<br>Matched: '+esc(r.get('market_match'))) if r.get('market_match') else ''}</div><p><a class=btn href="{esc(r.get('url'))}" target=_blank>Open source</a></p></div></article>"""
def section(t,items):return f"<section class=section><h2>{esc(t)}</h2><div class=grid>"+("".join(card(x) for x in items) if items else '<div class=empty>No qualifying observations yet.</div>')+"</div></section>"

def home():
 rows=opportunities()
 deals=sorted([r for r in rows if r.get("profit") is not None and r["profit"]>0],key=lambda r:r.get("profit") or -999,reverse=True)[:10]
 etbs=sorted([r for r in rows if kind(r["name"])=="etb"],key=lambda r:(r.get("profit") is not None,r.get("profit") or -999),reverse=True)[:12]
 retail=[r for r in rows if r.get("retail_price") is not None][:10]
 c=db();n=c.execute("select count(*) from products").fetchone()[0];hist=c.execute("select count(*) from inventory_history").fetchone()[0];c.close()
 return f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Pokesale v5</title><style>{CSS}</style><div class=w><div class=top><div><div class=brand>⚡ Pokesale v5</div><div class=mut>ETB-first retail-to-resale radar</div></div><a class=btn href=/scan>Scan now</a></div><div class=nav><a href=#deals>Best Deals</a><a href=#etb>ETBs</a><a href=#retail>Retail sightings</a><a href=/stores>Stores</a><a href=/diagnostics>Diagnostics</a></div><div class=hero><b>Evidence-first</b><div class=mut>Retail catalog sightings are never labeled local stock. Store-specific confirmation requires store-specific evidence.</div><div class=stats><div class=stat><div class=num>{n}</div><div class=mut>tracked</div></div><div class=stat><div class=num>{len(deals)}</div><div class=mut>positive deals</div></div><div class=stat><div class=num>{hist}</div><div class=mut>events</div></div></div></div><div id=deals>{section("🔥 Best Deals",deals)}</div><div id=etb>{section("⚡ Elite Trainer Boxes",etbs)}</div><div id=retail>{section("🛒 Retail Price Sightings",retail)}</div></div>"""

def diagnostics():
 c=db();ss=c.execute("select * from scans order by id desc limit 60").fetchall();c.close()
 cards="".join(f"<div class=store><b>{esc(x['source'])}: {x['found']}</b><div class=mut>{esc(x['ts'])}<br>{esc(x['error'] or 'OK')}</div></div>" for x in ss)
 return f"<meta name=viewport content='width=device-width'><style>{CSS}</style><div class=w><h1>Diagnostics</h1><a class=btn href=/>Back</a>{cards}</div>"
def stores():
 zones={}
 for z,r,n,a in STORES:zones.setdefault(z,[]).append((r,n,a))
 body="".join(f"<h2>{esc(z)}</h2>"+''.join(f"<div class=store><b>{esc(r)} — {esc(n)}</b><div class=mut>{esc(a)}</div></div>" for r,n,a in vals) for z,vals in zones.items())
 return f"<meta name=viewport content='width=device-width'><style>{CSS}</style><div class=w><h1>Hunt Zones</h1><a class=btn href=/>Back</a>{body}</div>"

class H(BaseHTTPRequestHandler):
 def out(self,s,ct="text/html; charset=utf-8",code=200):
  b=s.encode();self.send_response(code);self.send_header("Content-Type",ct);self.send_header("Content-Length",str(len(b)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(b)
 def do_GET(self):
  p=urlparse(self.path).path
  if p=="/":return self.out(home())
  if p=="/stores":return self.out(stores())
  if p=="/diagnostics":return self.out(diagnostics())
  if p=="/scan":threading.Thread(target=scan_all,daemon=True).start();self.send_response(303);self.send_header("Location","/");self.end_headers();return
  if p=="/health":
   c=db();n=c.execute("select count(*) from products").fetchone()[0];c.close();return self.out(json.dumps({"ok":True,"products":n,"version":"5.0"}),"application/json")
  if p=="/api/opportunities":return self.out(json.dumps(opportunities(),separators=(",",":")),"application/json")
  if p=="/api/stores":return self.out(json.dumps([{"zone":z,"retailer":r,"city":n,"address":a} for z,r,n,a in STORES]),"application/json")
  return self.out("Not found","text/plain",404)
 def log_message(self,*a):pass

if __name__=="__main__":ThreadingHTTPServer(("0.0.0.0",PORT),H).serve_forever()
