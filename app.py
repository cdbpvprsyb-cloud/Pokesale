import os,json,sqlite3,threading,time,html,re,hashlib,math
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,quote_plus,quote
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from datetime import datetime,timezone,timedelta
import xml.etree.ElementTree as ET

VERSION="7.0-zero-cost"
PORT=int(os.getenv("PORT","10000"))
DB=os.getenv("DB_PATH","/tmp/pokesale_v7.db")
SCAN_MINUTES=max(15,int(os.getenv("SCAN_MINUTES","30")))
RSS_MINUTES=max(30,int(os.getenv("RSS_MINUTES","60")))
MARKET_HOURS=max(12,int(os.getenv("MARKET_HOURS","24")))
MIN_PROFIT=float(os.getenv("MIN_PROFIT","15")); MIN_ROI=float(os.getenv("MIN_ROI","25"))
UA="PokeSale/7.0 (+https://pokesale.onrender.com; zero-cost retail monitor)"
LOCK=threading.Lock()

STORES=[
 ("HOME","Walmart","Prince Frederick","150 Solomons Island Rd N"),("HOME","GameStop","Prince Frederick","725 Solomons Island Rd N Ste C"),
 ("HOME","Five Below","Prince Frederick","855 Solomons Island Rd N"),("HOME","Dollar General","St. Leonard","625 Calvert Beach Rd"),
 ("SOUTH","Target","California","45155 First Colony Way"),("SOUTH","Walmart","California","45485 Miramar Way"),
 ("SOUTH","GameStop","California","45205 Worth Ave"),("WEST","Target","Waldorf","3300 Western Pkwy"),("WEST","Walmart","Waldorf","11930 Acton Ln"),
 ("WEST","Target","Brandywine","15922 Crain Hwy"),("ANNAPOLIS","Target","Annapolis","1911 Towne Centre Blvd"),("ANNAPOLIS","Walmart","Annapolis","55 Forest Plaza"),
 ("NORTH","Target","Bowie","4600 Mitchellville Rd"),("NORTH","Target","Greenbelt","6100 Greenbelt Rd"),("NORTH","Sam's Club","Laurel","3535 Russett Green E")]
FEEDS=[
 ("Target","https://www.target.com/c/trading-cards-toys-games/pokemon/-/N-27p31Z569t0"),("Walmart","https://www.walmart.com/browse/collectibles/pokemon-cards/5967908_9807313_2611231"),
 ("GameStop","https://www.gamestop.com/toys-games/trading-cards/pokemon"),("Five Below","https://www.fivebelow.com/categories/toys-and-games/trading-cards"),("Dollar General","https://www.dollargeneral.com/c/toys/trading-cards")]
LOCAL_TERMS={"prince frederick","huntingtown","calvert","st. leonard","saint leonard","lusby","solomons","chesapeake beach","north beach","dunkirk","owings","california md","california maryland","lexington park","leonardtown","st. mary's","saint mary's","waldorf","la plata","charles county","brandywine","bowie","greenbelt","laurel","annapolis","anne arundel","upper marlboro","prince george's","southern maryland","somo","dmv","maryland"}
RETAILERS={"target","walmart","sam's club","sams club","costco","walgreens","cvs","best buy","gamestop","five below","barnes & noble","barnes and noble","macy's","macys","boxlunch","hot topic","dollar general","family dollar","hobby lobby","michaels"}
PRODUCT_WORDS=("pokemon","pokémon","elite trainer"," etb","booster","collection"," tin","bundle","blister","trainer box")

# Google News RSS is used only as a discovery lead source. It is never promoted to confirmed stock.
RSS_QUERIES=[
 'Pokemon restock Maryland Target Walmart Costco Sam Club',
 'Pokemon cards Waldorf Prince Frederick California Maryland restock',
 'Pokemon TCG Annapolis Bowie Brandywine restock',
 'Pokemon ETB Southern Maryland Target Walmart']

def utcnow(): return datetime.now(timezone.utc).isoformat()
def db():
 c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; c.execute("pragma journal_mode=WAL"); c.execute("pragma busy_timeout=20000"); return c

def init():
 os.makedirs(os.path.dirname(DB) or ".",exist_ok=True); c=db(); c.executescript("""
 create table if not exists products(id text primary key,source text,retailer text,name text,retail_price real,market_price real,url text,image text,status text,last_seen text,first_seen text,category text,location text,confidence integer,stock_state text);
 create table if not exists scans(id integer primary key,ts text,source text,found int,error text,duration_ms integer default 0);
 create table if not exists inventory_history(id integer primary key,product_id text,ts text,state text,confidence integer,location text);
 create table if not exists signals(id text primary key,source text,retailer text,location text,product text,price real,evidence text,confidence integer,url text,title text,first_seen text,last_seen text,expires_at text);
 create table if not exists meta(k text primary key,v text);
 create index if not exists ix_products_seen on products(last_seen); create index if not exists ix_signals_seen on signals(last_seen);
 """); c.commit(); c.close()
init()

def fetch(url,accept="text/html,application/xhtml+xml,application/json,application/xml,text/xml"):
 req=Request(url,headers={"User-Agent":UA,"Accept":accept,"Accept-Language":"en-US,en;q=0.8"})
 with urlopen(req,timeout=20) as r:
  raw=r.read(5_000_000); return raw.decode("utf-8","ignore")
def fetch_json(url): return json.loads(fetch(url,"application/json"))
def ident(*parts): return hashlib.sha1("|".join(str(x).strip().lower() for x in parts).encode()).hexdigest()[:24]
def log(source,n,err="",started=None):
 ms=int((time.monotonic()-started)*1000) if started else 0; c=db(); c.execute("insert into scans(ts,source,found,error,duration_ms) values(?,?,?,?,?)",(utcnow(),source,n,str(err)[:800],ms)); c.commit(); c.close()
def meta_get(k):
 c=db(); r=c.execute("select v from meta where k=?",(k,)).fetchone(); c.close(); return r[0] if r else None
def meta_set(k,v):
 c=db(); c.execute("insert into meta(k,v) values(?,?) on conflict(k) do update set v=excluded.v",(k,str(v))); c.commit(); c.close()

def money(v):
 try:
  if isinstance(v,dict): v=v.get("price") or v.get("value") or v.get("currentPrice")
  if isinstance(v,str):
   m=re.search(r"\d+(?:\.\d{1,2})?",v.replace(",","")); v=m.group(0) if m else None
  f=float(v); return f if 0<f<5000 else None
 except: return None

def category_for(n):
 s=(n or "").lower()
 if any(x in s for x in ("pokemon","pokémon","elite trainer","booster","poke")): return "Pokemon"
 if any(x in s for x in ("topps","panini","football","baseball","basketball","sports card")): return "Sports Cards"
 return "Other Flips"
def confidence_for(status):
 s=status.lower()
 if "confirmed retailer/store" in s:return 96
 if "store-specific" in s:return 85
 if "strong local" in s:return 75
 if "unverified local" in s:return 55
 if "retailer catalog" in s:return 35
 if "market data" in s:return 20
 return 30

def upsert(source,retailer,name,retail,market,url,image,status,location="Catalog / online",stock_state="observed",confidence=None):
 c=db(); now=utcnow(); i=ident(source,name,location); conf=int(confidence if confidence is not None else confidence_for(status)); old=c.execute("select stock_state from products where id=?",(i,)).fetchone()
 c.execute("""insert into products values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(id) do update set retail_price=coalesce(excluded.retail_price,products.retail_price),market_price=coalesce(excluded.market_price,products.market_price),url=case when excluded.url<>'' then excluded.url else products.url end,image=case when excluded.image<>'' then excluded.image else products.image end,status=excluded.status,last_seen=excluded.last_seen,category=excluded.category,location=excluded.location,confidence=excluded.confidence,stock_state=excluded.stock_state""",
 (i,source,retailer,name,retail,market,url or "",image or "",status,now,now,category_for(name),location,conf,stock_state))
 if old is None or old["stock_state"]!=stock_state:c.execute("insert into inventory_history(product_id,ts,state,confidence,location) values(?,?,?,?,?)",(i,now,stock_state,conf,location))
 c.commit(); c.close()

def walk(x):
 if isinstance(x,dict):
  yield x
  for v in x.values(): yield from walk(v)
 elif isinstance(x,list):
  for v in x: yield from walk(v)
def productish(n):
 s=(n or "").lower(); return any(x in s for x in PRODUCT_WORDS) and ("pokemon" in s or "pokémon" in s or "elite trainer" in s)
def json_candidates(raw):
 roots=[]
 for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',raw,re.I|re.S):
  try: roots.append(json.loads(html.unescape(m.group(1)).strip()))
  except: pass
 for marker in ('__NEXT_DATA__','__PRELOADED_STATE__'):
  m=re.search(r'<script[^>]*(?:id=["\']'+marker+r'["\'])[^>]*>(.*?)</script>',raw,re.I|re.S)
  if m:
   try: roots.append(json.loads(html.unescape(m.group(1)).strip()))
   except: pass
 return roots

def parse_retailer(raw,base):
 out={}
 for root in json_candidates(raw):
  for d in walk(root):
   name=d.get("name") or d.get("title") or d.get("productName")
   if not isinstance(name,str) or not productish(name):continue
   offer=d.get("offers") or {}; price=money(d.get("price") or d.get("currentPrice") or d.get("priceString"))
   if price is None and isinstance(offer,dict):price=money(offer.get("price") or offer.get("lowPrice"))
   if price is None:continue
   image=d.get("image") or d.get("imageUrl") or d.get("primaryImage") or ""; url=d.get("url") or d.get("canonicalUrl") or ""
   if isinstance(image,list):image=image[0] if image else ""
   if isinstance(image,dict):image=image.get("url") or image.get("src") or ""
   if isinstance(url,str) and url.startswith("/"):url=base.rstrip("/")+url
   out[re.sub(r"\W+"," ",name.lower()).strip()]=(name,price,url if isinstance(url,str) else "",image if isinstance(image,str) else "")
 text=html.unescape(re.sub(r"<[^>]+>"," ",raw))
 for m in re.finditer(r'((?:Pok[eé]mon).{0,150}?(?:Elite Trainer Box|ETB|Booster (?:Bundle|Box|Pack)|Collection|Tin)).{0,100}?\$([0-9]{1,3}(?:\.[0-9]{2})?)',text,re.I):
  name=" ".join(m.group(1).split()); p=money(m.group(2));
  if p:out.setdefault(re.sub(r"\W+"," ",name.lower()).strip(),(name,p,"",""))
 return list(out.values())[:250]

def scan_retailers():
 for retailer,url in FEEDS:
  st=time.monotonic()
  try:
   rows=parse_retailer(fetch(url),url.split("/",3)[0]+"//"+url.split("/",3)[2])
   for name,price,purl,img in rows:upsert("retailer:"+retailer,retailer,name,price,None,purl or url,img,"RETAILER CATALOG + PRICE — LOCAL STOCK NOT CONFIRMED","Catalog / online","catalog")
   log(retailer,len(rows),"OK" if rows else "Fetched but no parseable sealed products/prices",st)
  except Exception as ex:log(retailer,0,type(ex).__name__+": "+str(ex),st)

def sealed_name(name):
 n=name.lower(); good=("booster box","elite trainer","booster bundle","booster pack","collection"," tin","tin ","blister","build & battle","battle box","ex box","trainer box","premium box","poster collection")
 bad=("single card","code card","energy card","sleeves","portfolio","binder","playmat"); return any(x in n for x in good) and not any(x in n for x in bad)

def scan_market(force=False):
 st=time.monotonic(); last=meta_get("market_scan_epoch")
 if not force and last and time.time()-float(last)<MARKET_HOURS*3600:return log("TCGCSV",0,"Skipped: cached market data still fresh",st)
 try:
  groups=fetch_json("https://tcgcsv.com/tcgplayer/3/groups").get("results",[]); newest=sorted(groups,key=lambda g:g.get("publishedOn") or "",reverse=True)[:24]
  ids=list(dict.fromkeys([g.get("groupId") for g in newest if g.get("groupId")]))[:24]; count=0
  for gid in ids:
   try:
    prods=fetch_json(f"https://tcgcsv.com/tcgplayer/3/{gid}/products").get("results",[]); prices=fetch_json(f"https://tcgcsv.com/tcgplayer/3/{gid}/prices").get("results",[]); pm={}
    for q in prices:
     v=q.get("marketPrice")
     if v is not None:pm[q.get("productId")]=max(float(v),pm.get(q.get("productId"),0))
    for p in prods:
     name=p.get("name",""); market=pm.get(p.get("productId"))
     if sealed_name(name) and market is not None:upsert("TCGCSV","TCGplayer",name,None,market,p.get("url") or "https://www.tcgplayer.com/search/pokemon/product?q="+quote_plus(name),p.get("imageUrl") or "","TCGPLAYER MARKET DATA"); count+=1
   except Exception as ex: pass
   time.sleep(.10)
  meta_set("market_scan_epoch",time.time()); log("TCGCSV",count,"OK",st)
 except Exception as ex:log("TCGCSV",0,type(ex).__name__+": "+str(ex),st)

ALIASES={"etb":"elite trainer box","pkmn":"pokemon","sams":"sam's club","gamestop":"gamestop"}
def norm(s):
 s=html.unescape((s or "").lower()).replace("pokémon","pokemon").replace("&"," and ")
 for a,b in ALIASES.items():s=re.sub(r"\b"+re.escape(a)+r"\b",b,s)
 return " ".join(re.sub(r"[^a-z0-9' ]"," ",s).split())
def kind(s):
 n=norm(s)
 if "elite trainer box" in n:return "etb"
 if "booster bundle" in n:return "booster bundle"
 if "booster box" in n:return "booster box"
 if "booster pack" in n:return "booster pack"
 if re.search(r"\btin\b",n):return "tin"
 if "collection" in n:return "collection"
 return "other"
STOP={"pokemon","trading","card","game","tcg","the","box","cards","new","sealed","with","and","scarlet","violet"}
def tokens(s):return [x for x in norm(s).split() if x not in STOP]
def edition_flags(s):
 n=norm(s); return {x for x in ("pokemon center","exclusive","premium","ultra premium","poster","bundle") if x in n}
def match_score(a,b):
 ka,kb=kind(a),kind(b)
 if ka!=kb:return 0
 fa,fb=edition_flags(a),edition_flags(b)
 if ("pokemon center" in fa) != ("pokemon center" in fb):return 0
 A,B=set(tokens(a)),set(tokens(b))
 if not A or not B:return 0
 inter=len(A&B); j=inter/len(A|B); contain=inter/min(len(A),len(B)); score=.55*j+.45*contain
 # Require meaningful set/name overlap, not just generic product-type tokens.
 meaningful={x for x in A&B if x not in {"elite","trainer","booster","collection","tin","pack","bundle"}}
 if not meaningful:return 0
 return min(1,score+.08)

def extract_location(text):
 n=norm(text); hits=[x for x in LOCAL_TERMS if norm(x) in n]
 return max(hits,key=len) if hits else None
def extract_retailer(text):
 n=norm(text)
 for r in sorted(RETAILERS,key=len,reverse=True):
  if norm(r) in n:return r.title().replace("Sam'S","Sam's")
 return "Unknown"
def looks_restock(text):return bool(re.search(r"\b(restock|restocked|in stock|stocked|available|shelf|shelves|drop|dropped|pickup|limit [0-9]|had (?:a|some|tons|plenty))\b",norm(text)))
def extract_product(text):
 t=html.unescape(text or ""); m=re.search(r'(.{0,70}(?:Pok[eé]mon).{0,100}?(?:Elite Trainer Box|ETB|Booster (?:Bundle|Box|Pack)|Collection|Tin).{0,50})',t,re.I)
 return " ".join(m.group(1).split())[:180] if m else ("Pokemon TCG product" if "pokemon" in norm(t) else "Unknown product")

def add_signal(source,title,url,published=""):
 text=f"{title} {url}"; loc=extract_location(text)
 if not loc or not looks_restock(text) or "pokemon" not in norm(text):return False
 retailer=extract_retailer(text); product=extract_product(title); evidence="UNVERIFIED LOCAL WEB/RSS SIGNAL"; conf=58 if retailer!="Unknown" else 50
 i=ident(source,title,url); now=utcnow(); exp=(datetime.now(timezone.utc)+timedelta(hours=36)).isoformat(); c=db()
 c.execute("""insert into signals values(?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(id) do update set last_seen=excluded.last_seen,confidence=max(signals.confidence,excluded.confidence),expires_at=excluded.expires_at""",(i,source,retailer,loc,product,None,evidence,conf,url,title,now,now,exp)); c.commit(); c.close(); return True

def scan_rss():
 st=time.monotonic(); count=0; successes=0; errors=[]
 for q in RSS_QUERIES:
  try:
   raw=fetch("https://news.google.com/rss/search?q="+quote(q)+"&hl=en-US&gl=US&ceid=US:en","application/rss+xml,application/xml,text/xml")
   root=ET.fromstring(raw); successes+=1
   for item in root.findall(".//item"):
    title=(item.findtext("title") or "").strip(); link=(item.findtext("link") or "").strip(); pub=(item.findtext("pubDate") or "").strip()
    if add_signal("Google News RSS",title,link,pub):count+=1
  except Exception as ex:errors.append(type(ex).__name__+": "+str(ex))
 status="OK" if successes==len(RSS_QUERIES) else (f"PARTIAL {successes}/{len(RSS_QUERIES)}; "+" | ".join(errors[:2]) if successes else "FAILED; "+" | ".join(errors[:2]))
 log("RSS Local Scout",count,status,st)

def opportunities(limit=500):
 c=db(); rows=[dict(r) for r in c.execute("select * from products order by last_seen desc limit 2500")]; c.close(); market=[r for r in rows if r.get("market_price") is not None]
 for r in rows:
  if r.get("retail_price") is not None and r.get("market_price") is None:
   best=None;score=0
   for m in market:
    s=match_score(r["name"],m["name"])
    if s>score:score=s;best=m
   if best and score>=.67:r["market_price"]=best["market_price"];r["image"]=r.get("image") or best.get("image");r["market_match"]=best["name"];r["match_score"]=round(score,2)
  rp,mp=r.get("retail_price"),r.get("market_price")
  if rp is not None and mp is not None:
   # Conservative marketplace economics: 13.25% selling fee + $5 fulfillment reserve.
   profit=mp*.8675-rp-5; r["profit"]=round(profit,2);r["roi"]=round(profit/rp*100,1) if rp else None;r["buy_candidate"]=profit>=MIN_PROFIT and r["roi"]>=MIN_ROI
  else:r["profit"]=r["roi"]=None;r["buy_candidate"]=False
 return rows[:limit]

def live_signals(limit=100):
 c=db(); now=utcnow(); rows=[dict(r) for r in c.execute("select * from signals where expires_at>? order by last_seen desc limit ?",(now,limit))]; c.close(); return rows

def scan_all(force_market=False):
 if not LOCK.acquire(False):return False
 try:scan_retailers();scan_market(force_market);scan_rss();return True
 finally:LOCK.release()

def esc(s):return html.escape(str(s or ""))
def age(ts):
 try:
  d=datetime.now(timezone.utc)-datetime.fromisoformat(ts); m=int(d.total_seconds()/60); return f"{m}m ago" if m<60 else f"{m//60}h ago" if m<1440 else f"{m//1440}d ago"
 except:return ""
CSS="""body{font-family:system-ui;background:#0b1020;color:#eef2ff;margin:0}.w{max-width:1180px;margin:auto;padding:20px}.top,.nav,.stats{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.top{justify-content:space-between}.brand{font-size:28px;font-weight:900}.mut{color:#9ca8c7}.btn,.nav a{color:white;background:#243150;padding:9px 12px;border-radius:10px;text-decoration:none}.hero,.card,.store{background:#141d33;border:1px solid #293655;border-radius:16px;padding:16px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:12px}.num{font-size:25px;font-weight:900}.good{color:#6ee7a8}.warn{color:#ffd166}.bad{color:#ff8f8f}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#263554;font-size:12px}.stat{min-width:120px}.section{margin-top:25px}a{color:#9ec5ff}"""
def product_card(r):
 p=r.get("profit"); roi=r.get("roi"); cls="good" if r.get("buy_candidate") else "mut"
 return f"<article class=card><b>{esc(r['name'])}</b><div class=mut>{esc(r['retailer'])} · {esc(r['location'])}</div><p>Retail: <b>{'$%.2f'%r['retail_price'] if r.get('retail_price') is not None else '—'}</b> · Market: <b>{'$%.2f'%r['market_price'] if r.get('market_price') is not None else '—'}</b></p><p class={cls}>Profit: {('$%.2f'%p) if p is not None else '—'} · ROI: {(str(roi)+'%') if roi is not None else '—'}</p><div class=mut>{esc(r['status'])}<br>{r.get('confidence',0)}% evidence · {age(r.get('last_seen'))}</div><p><a href='{esc(r.get('url'))}' target=_blank>Source</a></p></article>"
def signal_card(s):return f"<article class=card><span class=pill>{esc(s['evidence'])}</span><h3>{esc(s['retailer'])} — {esc(s['location'])}</h3><b>{esc(s['product'])}</b><p>{esc(s['title'])}</p><div class=mut>{s['confidence']}% confidence · {age(s['last_seen'])}</div><p><a href='{esc(s['url'])}' target=_blank>Evidence</a></p></article>"
def home():
 rows=opportunities(); deals=sorted([r for r in rows if r.get("buy_candidate")],key=lambda x:x.get("profit") or -999,reverse=True)[:12]; retail=[r for r in rows if r.get("retail_price") is not None][:12]; sig=live_signals(20)
 c=db(); n=c.execute("select count(*) from products").fetchone()[0];c.close()
 return f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>PokeSale v7</title><style>{CSS}</style><div class=w><div class=top><div><div class=brand>⚡ PokeSale v7</div><div class=mut>Zero-cost evidence-first retail radar</div></div><a class=btn href=/scan>Scan now</a></div><div class=nav><a href=#signals>Local Signals</a><a href=#deals>Buy Candidates</a><a href=#retail>Retail</a><a href=/stores>Stores</a><a href=/diagnostics>Diagnostics</a></div><div class=hero><b>No paid AI required.</b><p class=mut>Catalog presence is never local stock. RSS/web discoveries are leads, not confirmed inventory. BUY requires both profit ≥ ${MIN_PROFIT:.0f} and ROI ≥ {MIN_ROI:.0f}%.</p><div class=stats><div class=stat><div class=num>{n}</div><div class=mut>tracked</div></div><div class=stat><div class=num>{len(sig)}</div><div class=mut>live local leads</div></div><div class=stat><div class=num>{len(deals)}</div><div class=mut>buy candidates</div></div></div></div><section id=signals class=section><h2>📍 Local Intelligence</h2><div class=grid>{''.join(signal_card(x) for x in sig) or '<div class=card>No qualifying local signals yet.</div>'}</div></section><section id=deals class=section><h2>🔥 Buy Candidates</h2><div class=grid>{''.join(product_card(x) for x in deals) or '<div class=card>No verified profitable matches yet.</div>'}</div></section><section id=retail class=section><h2>🛒 Retail Observations</h2><div class=grid>{''.join(product_card(x) for x in retail) or '<div class=card>No retail observations yet.</div>'}</div></section></div>"
def diagnostics():
 c=db(); rows=c.execute("select * from scans order by id desc limit 80").fetchall();c.close(); body="".join(f"<div class=store><b>{esc(r['source'])}: {r['found']}</b><div class=mut>{esc(r['ts'])} · {r['duration_ms']}ms<br>{esc(r['error'] or 'OK')}</div></div>" for r in rows)
 return f"<meta name=viewport content='width=device-width'><style>{CSS}</style><div class=w><h1>Diagnostics</h1><a class=btn href=/>Back</a>{body or '<div class=card>No scans yet.</div>'}</div>"
def stores_page():
 zones={}
 for z,r,n,a in STORES:zones.setdefault(z,[]).append((r,n,a))
 body="".join(f"<h2>{esc(z)}</h2>"+''.join(f"<div class=store><b>{esc(r)} — {esc(n)}</b><div class=mut>{esc(a)}</div></div>" for r,n,a in vals) for z,vals in zones.items())
 return f"<meta name=viewport content='width=device-width'><style>{CSS}</style><div class=w><h1>Stores</h1><a class=btn href=/>Back</a>{body}</div>"

class H(BaseHTTPRequestHandler):
 def out(self,s,ct="text/html; charset=utf-8",code=200):
  b=s.encode();self.send_response(code);self.send_header("Content-Type",ct);self.send_header("Content-Length",str(len(b)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(b)
 def do_GET(self):
  p=urlparse(self.path).path
  try:
   if p=="/":return self.out(home())
   if p=="/stores":return self.out(stores_page())
   if p=="/diagnostics":return self.out(diagnostics())
   if p=="/scan":threading.Thread(target=scan_all,daemon=True).start();self.send_response(303);self.send_header("Location","/");self.end_headers();return
   if p=="/health":
    c=db();n=c.execute("select count(*) from products").fetchone()[0];s=c.execute("select count(*) from signals where expires_at>?",(utcnow(),)).fetchone()[0];c.close();return self.out(json.dumps({"ok":True,"version":VERSION,"products":n,"live_signals":s,"paid_ai":False}),"application/json")
   if p=="/api/opportunities":return self.out(json.dumps(opportunities(),separators=(",",":")),"application/json")
   if p=="/api/signals":return self.out(json.dumps(live_signals(),separators=(",",":")),"application/json")
   if p=="/api/stores":return self.out(json.dumps([{"zone":z,"retailer":r,"city":n,"address":a} for z,r,n,a in STORES]),"application/json")
   return self.out("Not found","text/plain",404)
  except Exception as ex:return self.out("Internal error: "+type(ex).__name__,"text/plain",500)
 def log_message(self,*a):pass

def worker():
 time.sleep(5)
 while True:
  try:scan_all()
  except Exception as ex:log("worker",0,type(ex).__name__+": "+str(ex))
  time.sleep(SCAN_MINUTES*60)
if os.getenv("POKESALE_NO_WORKER")!="1":threading.Thread(target=worker,daemon=True).start()
if __name__=="__main__":ThreadingHTTPServer(("0.0.0.0",PORT),H).serve_forever()
