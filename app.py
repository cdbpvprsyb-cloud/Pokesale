import os,json,sqlite3,threading,time,html,re,hashlib
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,quote_plus
from urllib.request import Request,urlopen
from datetime import datetime,timezone,timedelta

PORT=int(os.getenv("PORT","10000"))
DB=os.getenv("DB_PATH","/tmp/pokesale_v6.db")
SCAN_MINUTES=max(10,int(os.getenv("SCAN_MINUTES","15")))
UA="Mozilla/5.0 (compatible; Pokesale/6.0; personal retail monitor)"
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


# ---------------- POKESALE v6: LOCAL SIGNAL + AI SCOUT ----------------
BESTBUY_API_KEY=os.getenv("BESTBUY_API_KEY","").strip()
OPENAI_API_KEY=os.getenv("OPENAI_API_KEY","").strip()
OPENAI_MODEL=os.getenv("OPENAI_MODEL","gpt-5.6-luna").strip()
SCOUT_ENABLED=os.getenv("SCOUT_ENABLED","1").lower() not in ("0","false","no")
SCOUT_MINUTES=max(30,int(os.getenv("SCOUT_MINUTES","60")))
SCOUT_POSTAL=os.getenv("SCOUT_POSTAL","20639").strip()
MIN_PROFIT=float(os.getenv("MIN_PROFIT","15"))
MIN_ROI=float(os.getenv("MIN_ROI","25"))

# County/city vocabulary is deliberately explicit: generic nationwide chatter is rejected.
LOCAL_TERMS=(
 "calvert","prince frederick","huntingtown","dunkirk","chesapeake beach","north beach","lusby","solomons","st. leonard","saint leonard",
 "st. mary","saint mary","california md","lexington park","leonardtown","charlotte hall",
 "charles county","waldorf","la plata","white plains","hughesville",
 "anne arundel","annapolis","glen burnie","severn","hanover","arundel mills",
 "prince george","brandywine","bowie","greenbelt","college park","lanham","woodmore"
)
PRIORITY_RETAILERS=("Target","Walmart","Sam's Club","Costco","Walgreens","Best Buy","GameStop","Five Below","Barnes & Noble","Macy's","Hot Topic","BoxLunch")


def init_v6():
 c=db(); c.executescript("""
 create table if not exists signals(
   id text primary key, discovered_at text, observed_at text, retailer text, location text,
   product text, retail_price real, source_url text, source_type text, evidence text,
   confidence integer, summary text, fingerprint text, last_seen text, alerted integer default 0);
 create index if not exists ix_signals_seen on signals(last_seen);
 create index if not exists ix_signals_fp on signals(fingerprint);
 create table if not exists scout_runs(id integer primary key, ts text, source text, found integer, error text);
 """); c.commit(); c.close()
init_v6()


def post_json(url,payload,headers=None,timeout=60):
 data=json.dumps(payload).encode()
 h={"User-Agent":UA,"Content-Type":"application/json","Accept":"application/json"}
 if headers:h.update(headers)
 req=Request(url,data=data,headers=h,method="POST")
 return json.loads(urlopen(req,timeout=timeout).read().decode("utf-8","ignore"))


def source_fingerprint(retailer,location,product,source_url):
 raw="|".join((retailer or "",location or "",product or "",source_url or "")).lower()
 return hashlib.sha1(raw.encode()).hexdigest()[:24]


def local_relevant(text):
 s=(text or "").lower()
 return any(x in s for x in LOCAL_TERMS)


def save_signal(x,source_type="AI WEB SCOUT"):
 retailer=str(x.get("retailer") or "Unknown")[:80]
 location=str(x.get("location") or "Unknown")[:160]
 product=str(x.get("product") or "Unknown product")[:240]
 url=str(x.get("source_url") or "")[:1200]
 summary=str(x.get("summary") or "")[:1000]
 evidence=str(x.get("evidence") or "UNVERIFIED COMMUNITY REPORT")[:80].upper()
 observed=str(x.get("source_timestamp") or x.get("observed_at") or "")[:80]
 try: price=float(x["reported_price"]) if x.get("reported_price") not in (None,"") else None
 except: price=None
 try: conf=max(1,min(100,int(x.get("confidence") or 50)))
 except: conf=50
 # Hard guardrail: AI/web results must have a useful local Maryland connection.
 if not local_relevant(" ".join((location,summary))): return False
 fp=source_fingerprint(retailer,location,product,url)
 now=datetime.now(timezone.utc).isoformat(); ident=hashlib.sha1((fp+"|"+observed).encode()).hexdigest()[:24]
 c=db(); old=c.execute("select id from signals where fingerprint=? order by last_seen desc limit 1",(fp,)).fetchone()
 if old:
  c.execute("update signals set last_seen=?,confidence=max(confidence,?),summary=case when length(?)>length(summary) then ? else summary end where id=?",(now,conf,summary,summary,old["id"]))
 else:
  c.execute("insert into signals(id,discovered_at,observed_at,retailer,location,product,retail_price,source_url,source_type,evidence,confidence,summary,fingerprint,last_seen) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident,now,observed,retailer,location,product,price,url,source_type,evidence,conf,summary,fp,now))
 c.commit(); c.close(); return old is None


def response_output_text(obj):
 parts=[]
 for item in obj.get("output",[]):
  for p in item.get("content",[]) if isinstance(item,dict) else []:
   if isinstance(p,dict) and p.get("type") in ("output_text","text") and p.get("text"):parts.append(p["text"])
 return "\n".join(parts)


def parse_json_array(text):
 text=(text or "").strip()
 if text.startswith("```"):
  text=re.sub(r"^```(?:json)?\s*|\s*```$","",text,flags=re.I|re.S).strip()
 try:
  x=json.loads(text); return x if isinstance(x,list) else x.get("signals",[]) if isinstance(x,dict) else []
 except: pass
 m=re.search(r"\[.*\]",text,re.S)
 if m:
  try:return json.loads(m.group(0))
  except:pass
 return []


def scan_ai_web():
 if not (SCOUT_ENABLED and OPENAI_API_KEY):
  log("AI Web Scout",0,"Disabled: set OPENAI_API_KEY on Render to enable public-web scouting."); return
 prompt="""You are the PokeSale local retail-drop scout. Search the public web for NEW retail inventory/restock/drop signals in Southern Maryland and nearby Maryland relevant to Calvert, St. Mary's, Charles, Anne Arundel, and Prince George's counties. Prioritize Target, Walmart, Sam's Club, Costco, Walgreens, Best Buy, GameStop, Five Below, Barnes & Noble, Macy's, Hot Topic and BoxLunch. Prioritize Pokemon TCG ETBs, booster bundles, premium collections and other scarce/high-resale sealed products. You may also report unusually strong retail-arbitrage signals for LEGO, Hot Wheels, sports cards, gaming hardware, and collectible toys.

Search retailer pages, official announcements, Reddit, public forums, deal/restock trackers and indexed public pages. Do NOT treat catalog presence, nationwide chatter, marketplace reseller listings, or a product page alone as local inventory. Every result MUST have a useful Maryland/local connection. Distinguish CONFIRMED RETAILER/STORE AVAILABILITY from STRONG LOCAL SIGNAL and UNVERIFIED COMMUNITY REPORT. Prefer signals from the last 48 hours. Return ONLY a JSON array, maximum 15 objects. Each object: retailer, location, product, reported_price (number or null), source_timestamp, source_url, evidence, confidence (1-100), summary. If there are no meaningful local signals return []."""
 payload={"model":OPENAI_MODEL,"tools":[{"type":"web_search"}],"input":prompt,"store":False}
 try:
  obj=post_json("https://api.openai.com/v1/responses",payload,{"Authorization":"Bearer "+OPENAI_API_KEY},90)
  rows=parse_json_array(response_output_text(obj)); added=0
  for x in rows[:15]:
   if isinstance(x,dict) and save_signal(x):added+=1
  log("AI Web Scout",len(rows),f"{added} new local signals")
 except Exception as ex:log("AI Web Scout",0,type(ex).__name__+": "+str(ex))


def bestbuy_products():
 if not BESTBUY_API_KEY:return []
 # Official Products API; query a bounded Pokemon result set, then verify store availability by SKU.
 q=quote_plus(BESTBUY_API_KEY)
 url=f"https://api.bestbuy.com/v1/products(search=Pokemon)?format=json&pageSize=100&show=sku,name,salePrice,regularPrice,url,image,onlineAvailability,inStoreAvailability&apiKey={q}"
 try:return fetch_json(url).get("products",[])
 except:return []


def scan_bestbuy_local():
 if not BESTBUY_API_KEY:
  log("Best Buy Local",0,"Disabled: set BESTBUY_API_KEY on Render."); return
 found=0
 for p in bestbuy_products():
  name=p.get("name") or ""
  if not productish(name):continue
  sku=p.get("sku")
  if not sku:continue
  try:
   url=f"https://api.bestbuy.com/v1/products/{sku}/stores.json?postalCode={quote_plus(SCOUT_POSTAL)}&apiKey={quote_plus(BESTBUY_API_KEY)}"
   stores=fetch_json(url).get("stores",[])
   for st in stores:
    state=(st.get("state") or "").upper()
    city=st.get("city") or ""
    address=st.get("address") or ""
    loc=f"{city}, {state} — {address}".strip(" —")
    if state!="MD" or not local_relevant(loc):continue
    price=money(p.get("salePrice") or p.get("regularPrice"))
    status="STORE-SPECIFIC AVAILABILITY — BEST BUY OFFICIAL API"
    upsert("bestbuy-api", "Best Buy", name, price, None, p.get("url") or "", p.get("image") or "", status, loc, "in_stock")
    save_signal({"retailer":"Best Buy","location":loc,"product":name,"reported_price":price,"source_timestamp":datetime.now(timezone.utc).isoformat(),"source_url":p.get("url") or "","evidence":"CONFIRMED RETAILER/STORE AVAILABILITY","confidence":95 if not st.get("lowStock") else 90,"summary":"Official Best Buy API reports this SKU available at this store"},"BEST BUY OFFICIAL API")
    found+=1
   time.sleep(.15)
  except Exception:pass
 log("Best Buy Local",found,"OK")


def signal_rows(limit=80):
 c=db(); rows=[dict(r) for r in c.execute("select * from signals order by discovered_at desc limit ?",(limit,))]; c.close(); return rows


def deal_metrics(r):
 rp,mp=r.get("retail_price"),r.get("market_price")
 if rp is None or mp is None:return None,None
 profit=mp*.8675-rp-5
 return round(profit,2),round(profit/rp*100,1) if rp else None

# v6 improves matching by requiring product type compatibility and rewarding distinctive shared tokens.
def match_score(a,b):
 ka,kb=kind(a),kind(b)
 if ka!=kb and "other" not in (ka,kb):return 0
 A,B=set(tokens(a)),set(tokens(b))
 if not A or not B:return 0
 common=A&B
 j=len(common)/len(A|B); contain=len(common)/min(len(A),len(B))
 distinctive=sum(1 for x in common if len(x)>=5)
 bonus=(.16 if ka==kb and ka!="other" else 0)+min(.18,distinctive*.045)
 return min(1,j*.55+contain*.35+bonus)


def opportunities(limit=500):
 c=db(); rows=[dict(r) for r in c.execute("select * from products order by last_seen desc limit 2500")]; c.close()
 market=[r for r in rows if r["market_price"]]
 for r in rows:
  if r["retail_price"] is not None and not r["market_price"]:
   best=None;score=0
   for m in market:
    s=match_score(r["name"],m["name"])
    if s>score:score=s;best=m
   threshold=.52 if kind(r["name"])=="etb" else .62
   if best and score>=threshold:
    r["market_price"]=best["market_price"];r["image"]=r["image"] or best["image"]
    r["market_match"]=best["name"];r["match_score"]=round(score,2)
  r["projected_resale"]=round(r["market_price"],2) if r.get("market_price") is not None else None
  r["profit"],r["roi"]=deal_metrics(r)
  r["buy_signal"]=bool(r["profit"] is not None and r["profit"]>=MIN_PROFIT and r["roi"]>=MIN_ROI and r.get("confidence",0)>=60)
 return rows[:limit]


def scan_all():
 if not LOCK.acquire(False):return
 try:
  scan_retailers()
  scan_market()
  scan_bestbuy_local()
 finally:LOCK.release()


def scan_scout():
 # AI scout is kept on a slower cadence to control API cost.
 scan_ai_web()


def signal_card(x):
 ev=esc(x.get("evidence") or "")
 price=f"${x['retail_price']:.2f}" if x.get("retail_price") is not None else "Price not reported"
 link=f'<a class=btn href="{esc(x.get("source_url"))}" target=_blank>Source</a>' if x.get("source_url") else ""
 return f"<article class=card><div class=body><div class=badge>{ev}</div><h3>{esc(x.get('product'))}</h3><div class=price>{price}</div><div class=mut><b>{esc(x.get('retailer'))}</b> · {esc(x.get('location'))}<br>{x.get('confidence',0)}% evidence confidence<br>{esc(x.get('observed_at') or x.get('discovered_at'))}</div><p>{esc(x.get('summary'))}</p>{link}</div></article>"


def home():
 rows=opportunities()
 deals=sorted([r for r in rows if r.get("profit") is not None and r["profit"]>0],key=lambda r:r.get("profit") or -999,reverse=True)[:12]
 buy=sorted([r for r in rows if r.get("buy_signal")],key=lambda r:(r.get("confidence",0),r.get("profit") or 0),reverse=True)[:12]
 etbs=sorted([r for r in rows if kind(r["name"])=="etb"],key=lambda r:(r.get("profit") is not None,r.get("profit") or -999),reverse=True)[:12]
 sigs=signal_rows(24)
 c=db();n=c.execute("select count(*) from products").fetchone()[0];hist=c.execute("select count(*) from inventory_history").fetchone()[0];c.close()
 sig_html="".join(signal_card(x) for x in sigs) if sigs else '<div class=empty>No new local signals yet.</div>'
 return f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Pokesale v6</title><style>{CSS}</style><div class=w><div class=top><div><div class=brand>⚡ Pokesale v6</div><div class=mut>AI-assisted Southern Maryland retail-arbitrage radar</div></div><a class=btn href=/scan>Scan now</a></div><div class=nav><a href=#signals>Local Signals</a><a href=#buy>Buy Signals</a><a href=#deals>Best Deals</a><a href=#etb>ETBs</a><a href=/stores>Stores</a><a href=/diagnostics>Diagnostics</a></div><div class=hero><b>Evidence-first</b><div class=mut>Catalog pages never count as local stock. AI/community reports are labeled separately from official store-specific availability.</div><div class=stats><div class=stat><div class=num>{n}</div><div class=mut>products</div></div><div class=stat><div class=num>{len(sigs)}</div><div class=mut>local signals</div></div><div class=stat><div class=num>{len(buy)}</div><div class=mut>buy signals</div></div></div></div><section id=signals class=section><h2>📡 Local Drop Signals</h2><div class=grid>{sig_html}</div></section><div id=buy>{section("🚨 Buy Signals",buy)}</div><div id=deals>{section("🔥 Positive Spreads",deals)}</div><div id=etb>{section("⚡ Elite Trainer Boxes",etbs)}</div></div>"""


def diagnostics():
 c=db();ss=c.execute("select * from scans order by id desc limit 80").fetchall();c.close()
 cards="".join(f"<div class=store><b>{esc(x['source'])}: {x['found']}</b><div class=mut>{esc(x['ts'])}<br>{esc(x['error'] or 'OK')}</div></div>" for x in ss)
 env=f"<div class=store><b>v6 integrations</b><div class=mut>AI Web Scout: {'ENABLED' if OPENAI_API_KEY else 'needs OPENAI_API_KEY'}<br>Best Buy Local: {'ENABLED' if BESTBUY_API_KEY else 'needs BESTBUY_API_KEY'}<br>Scout postal: {esc(SCOUT_POSTAL)}</div></div>"
 return f"<meta name=viewport content='width=device-width'><style>{CSS}</style><div class=w><h1>Diagnostics</h1><a class=btn href=/>Back</a>{env}{cards}</div>"


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
  if p=="/scan":
   threading.Thread(target=scan_all,daemon=True).start(); threading.Thread(target=scan_scout,daemon=True).start()
   self.send_response(303);self.send_header("Location","/");self.end_headers();return
  if p=="/health":
   c=db();n=c.execute("select count(*) from products").fetchone()[0];s=c.execute("select count(*) from signals").fetchone()[0];c.close();return self.out(json.dumps({"ok":True,"products":n,"signals":s,"version":"6.0","ai_scout":bool(OPENAI_API_KEY),"bestbuy":bool(BESTBUY_API_KEY)}),"application/json")
  if p=="/api/opportunities":return self.out(json.dumps(opportunities(),separators=(",",":")),"application/json")
  if p=="/api/signals":return self.out(json.dumps(signal_rows(),separators=(",",":")),"application/json")
  if p=="/api/stores":return self.out(json.dumps([{"zone":z,"retailer":r,"city":n,"address":a} for z,r,n,a in STORES]),"application/json")
  return self.out("Not found","text/plain",404)
 def log_message(self,*a):pass


def worker():
 time.sleep(3)
 while True:scan_all();time.sleep(SCAN_MINUTES*60)
def scout_worker():
 time.sleep(15)
 while True:scan_scout();time.sleep(SCOUT_MINUTES*60)
threading.Thread(target=worker,daemon=True).start()
threading.Thread(target=scout_worker,daemon=True).start()

if __name__=="__main__":ThreadingHTTPServer(("0.0.0.0",PORT),H).serve_forever()
