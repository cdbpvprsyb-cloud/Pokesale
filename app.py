import os,json,sqlite3,threading,time,html,re,hashlib,math
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,quote_plus
from urllib.request import Request,urlopen
from datetime import datetime,timezone,timedelta

PORT=int(os.getenv('PORT','10000'))
DB=os.getenv('DB_PATH','/tmp/pokesale_refined.db')
SCAN_MINUTES=max(10,int(os.getenv('SCAN_MINUTES','15')))
UA='Pokesale/4.0 (+personal retail monitor) Mozilla/5.0'
LOCK=threading.Lock()
STORES=[
 ('HOME','Walmart','Prince Frederick','150 Solomons Island Rd N'),('HOME','GameStop','Prince Frederick','725 Solomons Island Rd N Ste C'),('HOME','Five Below','Prince Frederick','855 Solomons Island Rd N'),('HOME','ALDI','Prince Frederick','429 Solomons Island Rd N'),('HOME','Dollar General','St. Leonard','625 Calvert Beach Rd'),
 ('WORK','Target','Greenbelt','6100 Greenbelt Rd'),('WORK','Target','Cherry Hill','12000 Cherry Hill Rd'),('WORK','Five Below','Greenbelt','6000 Greenbelt Rd #65A'),('WORK','Five Below','Laurel','14260A Baltimore Ave'),('WORK','Dollar Tree','Beltsville','10464 Baltimore Ave'),('WORK','ALDI','Beltsville','10912 Baltimore Ave'),('WORK',"Sam's Club",'Laurel','3535 Russett Green E'),
 ('ANNAPOLIS','Target','Annapolis','1911 Towne Centre Blvd'),('ANNAPOLIS','Walmart','Annapolis','55 Forest Plaza'),('ANNAPOLIS','GameStop','Annapolis','Annapolis retail area'),('ANNAPOLIS','Five Below','Annapolis','Annapolis retail area')]

def db():
 c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; return c

def init():
 os.makedirs(os.path.dirname(DB) or '.',exist_ok=True)
 c=db(); c.executescript('''
 create table if not exists products(
  id text primary key,source text,retailer text,name text,retail_price real,market_price real,
  url text,image text,status text,last_seen text,first_seen text,category text default 'Pokemon',
  location text default 'Catalog / online',confidence integer default 25,stock_state text default 'observed');
 create table if not exists scans(id integer primary key,ts text,source text,found int,error text);
 create table if not exists inventory_history(id integer primary key,product_id text,ts text,state text,confidence integer,location text);
 create index if not exists ix_products_seen on products(last_seen);
 create index if not exists ix_hist_product on inventory_history(product_id,ts);
 ''')
 # Safe migration from earlier Pokesale databases.
 cols={r[1] for r in c.execute('pragma table_info(products)')}
 for name,ddl in [('first_seen','text'),('category',"text default 'Pokemon'"),('location',"text default 'Catalog / online'"),('confidence','integer default 25'),('stock_state',"text default 'observed'")]:
  if name not in cols: c.execute(f'alter table products add column {name} {ddl}')
 c.commit(); c.close()
init()

def fetch(url): return urlopen(Request(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.8'}),timeout=22).read().decode('utf-8','ignore')
def fetch_json(url): return json.loads(fetch(url))
def norm(s): return ' '.join(html.unescape(re.sub(r'<[^>]+>',' ',s)).split())
def pid(source,name,location=''): return hashlib.sha1((source+'|'+name.lower()+'|'+location.lower()).encode()).hexdigest()[:24]
def category_for(name):
 n=name.lower()
 if any(x in n for x in ('pokemon','pokémon','elite trainer','booster','poké','poke')): return 'Pokemon'
 if any(x in n for x in ('topps','panini','football','baseball','basketball','sports card')): return 'Sports Cards'
 return 'Other Flips'
def confidence_for(status):
 s=status.lower()
 if 'confirmed restock' in s: return 96
 if 'store pickup' in s or 'store-specific' in s: return 85
 if 'catalog observed' in s: return 42
 if 'market data' in s: return 20
 return 30

def upsert(source,retailer,name,retail,market,url,image,status,location='Catalog / online',stock_state='observed'):
 c=db(); now=datetime.now(timezone.utc).isoformat(); ident=pid(source,name,location); cat=category_for(name); conf=confidence_for(status)
 old=c.execute('select stock_state from products where id=?',(ident,)).fetchone()
 c.execute('''insert into products(id,source,retailer,name,retail_price,market_price,url,image,status,last_seen,first_seen,category,location,confidence,stock_state)
 values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(id) do update set retail_price=coalesce(excluded.retail_price,products.retail_price),market_price=coalesce(excluded.market_price,products.market_price),url=coalesce(nullif(excluded.url,''),products.url),image=coalesce(nullif(excluded.image,''),products.image),status=excluded.status,last_seen=excluded.last_seen,category=excluded.category,location=excluded.location,confidence=excluded.confidence,stock_state=excluded.stock_state''',
 (ident,source,retailer,name,retail,market,url,image,status,now,now,cat,location,conf,stock_state))
 if old is None or old['stock_state']!=stock_state: c.execute('insert into inventory_history(product_id,ts,state,confidence,location) values(?,?,?,?,?)',(ident,now,stock_state,conf,location))
 c.commit(); c.close()
def log(source,n,err=''):
 c=db(); c.execute('insert into scans(ts,source,found,error) values(?,?,?,?)',(datetime.now(timezone.utc).isoformat(),source,n,err[:500])); c.commit(); c.close()

def parse_target(raw):
 text=html.unescape(re.sub(r'<script[^>]*>[\s\S]*?</script>',' ',raw,flags=re.I)); text=re.sub(r'<[^>]+>','\n',text)
 lines=[' '.join(x.split()) for x in text.splitlines() if x.strip()]; rows=[]
 for i,x in enumerate(lines):
  m=re.fullmatch(r'\$([0-9]{1,4}(?:\.[0-9]{2})?)',x)
  if not m: continue
  for n in lines[i+1:i+12]:
   if 12<len(n)<240 and ('pokemon' in n.lower() or 'trading card' in n.lower()): rows.append((n,float(m.group(1)))); break
 out={}
 for n,p in rows:
  if 0<p<1000: out[n.lower()]=(n,p)
 return list(out.values())[:200]

def scan_retailers():
 feeds=[('Target','https://www.target.com/c/trading-cards-toys-games/pokemon/-/N-27p31Z569t0')]
 for retailer,url in feeds:
  try:
   rows=parse_target(fetch(url))
   for name,price in rows: upsert('retailer:'+retailer,retailer,name,price,None,'https://www.target.com/s?searchTerm='+quote_plus(name),'','CATALOG OBSERVED — LOCAL STOCK NOT CONFIRMED')
   log(retailer,len(rows))
  except Exception as ex: log(retailer,0,type(ex).__name__+': '+str(ex))

def sealed_name(name):
 n=name.lower(); good=('booster box','elite trainer','booster bundle','booster pack','collection',' tin','tin ','blister','build & battle','battle box','ex box','trainer box','premium box','poster collection')
 bad=('single card','code card','energy card','sleeves','portfolio','binder','playmat')
 return any(x in n for x in good) and not any(x in n for x in bad)
def scan_market():
 try:
  groups=fetch_json('https://tcgcsv.com/tcgplayer/3/groups').get('results',[])
  newest=sorted(groups,key=lambda g:g.get('publishedOn') or '',reverse=True)[:14]; ids=[2374]+[g.get('groupId') for g in newest if g.get('groupId')!=2374]; count=0
  for gid in ids:
   try:
    prods=fetch_json(f'https://tcgcsv.com/tcgplayer/3/{gid}/products').get('results',[]); prices=fetch_json(f'https://tcgcsv.com/tcgplayer/3/{gid}/prices').get('results',[]); pm={}
    for q in prices:
     v=q.get('marketPrice')
     if v is not None: pm[q.get('productId')]=max(float(v),pm.get(q.get('productId'),0))
    for p in prods:
     name=p.get('name',''); market=pm.get(p.get('productId'))
     if not sealed_name(name) or market is None: continue
     upsert('TCGCSV','TCGplayer',name,None,market,p.get('url') or 'https://www.tcgplayer.com/search/pokemon/product?q='+quote_plus(name),p.get('imageUrl') or '','TCGPLAYER MARKET DATA'); count+=1
   except Exception: pass
   time.sleep(.08)
  log('TCGCSV',count)
 except Exception as ex: log('TCGCSV',0,type(ex).__name__+': '+str(ex))

def keyname(s):
 s=s.lower().replace('pokémon','pokemon'); s=re.sub(r'[^a-z0-9 ]',' ',s)
 for w in ('pokemon','trading','card','game','tcg','the','box'): s=re.sub(r'\b'+w+r'\b',' ',s)
 return ' '.join(s.split())
def opportunities(limit=250):
 c=db(); rows=[dict(r) for r in c.execute('select * from products order by last_seen desc limit 1200')]; c.close(); market=[r for r in rows if r['market_price']]
 for r in rows:
  if r['retail_price'] is not None and not r['market_price']:
   kr=set(keyname(r['name']).split()); best=None; score=0
   for m in market:
    km=set(keyname(m['name']).split()); union=len(kr|km); sim=len(kr&km)/union if union else 0
    if sim>score: score=sim; best=m
   if best and score>=.58:
    r['market_price']=best['market_price']; r['image']=r['image'] or best['image']; r['match_score']=round(score,2)
  rp=r.get('retail_price'); mp=r.get('market_price')
  r['projected_resale']=round(mp,2) if mp is not None else None
  if rp is not None and mp is not None:
   # Conservative 13.25% marketplace fee + $5 shipping/handling estimate.
   profit=mp*.8675-rp-5; r['profit']=round(profit,2); r['roi']=round(profit/rp*100,1) if rp else None
  else: r['profit']=r['roi']=None
 return rows[:limit]

def scan_all():
 if not LOCK.acquire(False): return
 try: scan_retailers(); scan_market()
 finally: LOCK.release()
def worker():
 time.sleep(4)
 while True: scan_all(); time.sleep(SCAN_MINUTES*60)
threading.Thread(target=worker,daemon=True).start()

def esc(x): return html.escape(str(x or ''),quote=True)
def age(ts):
 try:
  d=datetime.now(timezone.utc)-datetime.fromisoformat(ts); mins=int(d.total_seconds()/60)
  if mins<60:return f'{max(0,mins)}m ago'
  if mins<1440:return f'{mins//60}h ago'
  return f'{mins//1440}d ago'
 except:return ''
CSS='''*{box-sizing:border-box}body{margin:0;background:#07101f;color:#eef5ff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.w{max-width:1120px;margin:auto;padding:14px 12px 70px}.top{display:flex;align-items:center;justify-content:space-between;gap:10px}.brand{font-size:27px;font-weight:950}.sub{color:#93a9c8;font-size:12px}.nav,.chips{display:flex;gap:7px;overflow:auto;padding:9px 0;scrollbar-width:none}.nav a,.chip{color:#dceaff;background:#12223d;padding:9px 12px;border-radius:999px;text-decoration:none;white-space:nowrap;border:1px solid #203b62;font-size:13px}.hero{background:linear-gradient(135deg,#11284c,#101a31);padding:14px;border-radius:18px;border:1px solid #24446d}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}.stat{background:#0c1830;border-radius:13px;padding:10px}.num{font-size:21px;font-weight:900}.section{margin-top:22px}.section h2{font-size:20px;margin:0 0 9px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:9px}.card{background:#101c32;border:1px solid #1e3455;border-radius:17px;overflow:hidden}.pic{width:100%;height:165px;object-fit:contain;background:#fff}.noimg{height:82px;display:flex;align-items:center;justify-content:center;background:#0c1830;color:#60799c;font-size:12px}.pad{padding:12px}.row{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.tag{display:inline-block;border-radius:999px;padding:5px 8px;background:#18365d;font-size:11px}.conf{font-size:11px;font-weight:800}.good{color:#72efa4}.warn{color:#ffd171}.mut{color:#94a8c5;font-size:12px;line-height:1.45}.name{font-size:16px;font-weight:850;margin:8px 0;line-height:1.25}.money{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:10px 0}.money div{background:#0b172b;padding:7px;border-radius:10px}.money b{display:block;font-size:15px}.money span{font-size:9px;color:#849bb9;text-transform:uppercase}.btn{display:inline-block;color:#fff;text-decoration:none;background:#2055a0;padding:8px 10px;border-radius:9px;font-size:12px}.empty{padding:14px;background:#0e1a30;border-radius:13px;color:#8fa4c2}.store{padding:12px;background:#101c32;border-radius:14px;margin:7px 0}@media(max-width:520px){.w{padding:11px 9px 65px}.grid{grid-template-columns:1fr}.pic{height:150px}.brand{font-size:24px}.money b{font-size:14px}}'''

def card(r):
 img=f"<img class=pic src='{esc(r.get('image'))}' loading=lazy onerror=\"this.outerHTML='<div class=noimg>Image unavailable</div>'\">" if r.get('image') else '<div class=noimg>Image pending from market feed</div>'
 conf=int(r.get('confidence') or 0); cc='good' if conf>=80 else 'warn'; resale=r.get('projected_resale'); profit=r.get('profit'); roi=r.get('roi')
 def cash(v): return '—' if v is None else f'${v:,.2f}'
 return f'''<article class=card>{img}<div class=pad><div class=row><span class=tag>{esc(r.get('retailer'))} · {esc(r.get('location'))}</span><span class="conf {cc}">{conf}% confidence</span></div><div class=name>{esc(r.get('name'))}</div><div class=money><div><span>Retail</span><b>{cash(r.get('retail_price'))}</b></div><div><span>Resale</span><b>{cash(resale)}</b></div><div><span>Net / ROI</span><b class={'good' if profit is not None and profit>0 else ''}>{cash(profit)}{(' · '+str(roi)+'%') if roi is not None else ''}</b></div></div><div class=mut>{esc(r.get('status'))}<br>Updated {age(r.get('last_seen'))}</div><p><a class=btn href="{esc(r.get('url'))}" target=_blank rel=noopener>Open source</a></p></div></article>'''

def section(title,items): return f'<section class=section><h2>{esc(title)}</h2><div class=grid>'+(''.join(card(x) for x in items) if items else '<div class=empty>No qualifying observations yet.</div>')+'</div></section>'
def home(qs):
 rows=opportunities(); deals=sorted([r for r in rows if r.get('profit') is not None and r['profit']>0],key=lambda r:(r.get('roi') or -999),reverse=True)[:8]
 retail=[r for r in rows if r.get('retail_price') is not None]; closest=sorted(retail,key=lambda r:(0 if r.get('location')!='Catalog / online' else 1,-(r.get('confidence') or 0)))[:8]
 cutoff=datetime.now(timezone.utc)-timedelta(hours=24); new=[]
 for r in rows:
  try:
   if datetime.fromisoformat(r['first_seen'])>=cutoff:new.append(r)
  except:pass
 new=new[:8]; cats={x:[r for r in rows if r.get('category')==x][:10] for x in ('Pokemon','Sports Cards','Other Flips')}
 c=db(); n=c.execute('select count(*) from products').fetchone()[0]; hist=c.execute('select count(*) from inventory_history').fetchone()[0]; c.close()
 return f'''<!doctype html><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Pokesale</title><style>{CSS}</style><div class=w><div class=top><div><div class=brand>⚡ Pokesale</div><div class=sub>Retail-to-resale opportunity radar</div></div><a class=btn href=/scan>Scan now</a></div><div class=nav><a href=#best>Best Deals</a><a href=#closest>Closest Deals</a><a href=#new>New Restocks</a><a href=#pokemon>Pokemon</a><a href=#sports>Sports Cards</a><a href=#other>Other Flips</a><a href=/stores>Stores</a><a href=/diagnostics>Diagnostics</a></div><div class=hero><b>Confidence-first inventory</b><div class=mut>Catalog sightings are not presented as shelf stock. Confidence rises only when stronger store-specific evidence is available. Profit uses projected resale less a conservative 13.25% marketplace fee and $5 handling/shipping estimate.</div><div class=stats><div class=stat><div class=num>{n}</div><div class=mut>tracked</div></div><div class=stat><div class=num>{len(deals)}</div><div class=mut>positive deals</div></div><div class=stat><div class=num>{hist}</div><div class=mut>stock events</div></div></div></div><div id=best>{section('🔥 Best Deals',deals)}</div><div id=closest>{section('📍 Closest Deals',closest)}</div><div id=new>{section('🆕 New Restocks',new)}</div><div id=pokemon>{section('⚡ Pokemon',cats['Pokemon'])}</div><div id=sports>{section('🏈 Sports Cards',cats['Sports Cards'])}</div><div id=other>{section('💎 Other Flips',cats['Other Flips'])}</div></div>'''
def stores_page():
 zones={}
 for z,r,n,a in STORES: zones.setdefault(z,[]).append((r,n,a))
 body=''.join(f'<section class=section><h2>{esc(z.title())}</h2>'+''.join(f'<div class=store><b>{esc(r)} — {esc(n)}</b><div class=mut>{esc(a)}</div></div>' for r,n,a in vals)+'</section>' for z,vals in zones.items())
 return f'<meta name=viewport content="width=device-width,initial-scale=1"><style>{CSS}</style><div class=w><h1>Hunt Zones</h1><a class=btn href=/>Back</a>{body}</div>'
def diag():
 c=db(); ss=c.execute('select * from scans order by id desc limit 50').fetchall(); c.close(); cards=''.join(f"<div class=store><b>{esc(x['source'])}: {x['found']}</b><div class=mut>{esc(x['ts'])}<br>{esc(x['error'] or 'OK')}</div></div>" for x in ss) or '<div class=empty>Background scan is starting.</div>'
 return f'<meta name=viewport content="width=device-width,initial-scale=1"><style>{CSS}</style><div class=w><h1>Diagnostics</h1><a class=btn href=/>Back</a>{cards}</div>'
class H(BaseHTTPRequestHandler):
 def out(self,s,ct='text/html; charset=utf-8',code=200):
  b=s.encode(); self.send_response(code); self.send_header('Content-Type',ct); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(b)
 def do_GET(self):
  u=urlparse(self.path); p=u.path
  if p=='/': return self.out(home(parse_qs(u.query)))
  if p=='/stores': return self.out(stores_page())
  if p=='/diagnostics': return self.out(diag())
  if p=='/scan': threading.Thread(target=scan_all,daemon=True).start(); self.send_response(303); self.send_header('Location','/'); self.end_headers(); return
  if p=='/health':
   try:
    c=db(); n=c.execute('select count(*) from products').fetchone()[0]; c.close(); return self.out(json.dumps({'ok':True,'products':n,'version':'4.0'}),'application/json')
   except Exception as ex:return self.out(json.dumps({'ok':False,'error':type(ex).__name__}),'application/json',503)
  if p=='/api/opportunities': return self.out(json.dumps(opportunities(),separators=(',',':')),'application/json')
  if p=='/api/stores': return self.out(json.dumps([{'zone':z,'retailer':r,'city':n,'address':a} for z,r,n,a in STORES]),'application/json')
  return self.out('Not found','text/plain; charset=utf-8',404)
 def log_message(self,*a): pass
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',PORT),H).serve_forever()
