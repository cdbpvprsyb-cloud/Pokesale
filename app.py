import os,json,sqlite3,threading,time,html,re
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,quote_plus
from urllib.request import Request,urlopen
from datetime import datetime,timezone
PORT=int(os.getenv('PORT','10000')); DB=os.getenv('DB_PATH','/tmp/pokesale.db')
STORES=[('HOME','GameStop','Prince Frederick','725 Solomons Island Rd N Ste C'),('HOME','Five Below','Prince Frederick','855 Solomons Island Rd N'),('HOME','ALDI','Prince Frederick','429 Solomons Island Rd N'),('HOME','Walmart','Prince Frederick','150 Solomons Island Rd N'),('WORK','Target','Greenbelt','6100 Greenbelt Rd'),('WORK','Target','Cherry Hill','12000 Cherry Hill Rd'),('WORK','Five Below','Greenbelt','6000 Greenbelt Rd #65A'),('WORK','Five Below','Laurel','14260A Baltimore Ave'),('WORK','Dollar Tree','Beltsville','10464 Baltimore Ave'),('WORK','ALDI','Beltsville','10912 Baltimore Ave'),('WORK',"Sam's Club",'Laurel','3535 Russett Green E')]
def db():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def init():
 c=db();c.execute('create table if not exists products(id integer primary key,retailer text,name text,price real,url text,last_seen text)');c.execute('create table if not exists scans(id integer primary key,ts text,retailer text,found int,error text)');c.commit();c.close()
init()
def fetch(url): return urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=20).read().decode('utf-8','ignore')
def scan():
 feeds=[('Walmart','https://www.walmart.com/cp/trading-cards/9807313'),('Target','https://www.target.com/c/trading-cards-toys-games/pokemon/-/N-27p31Z569t0')]
 for retailer,url in feeds:
  found=0;err=''
  try:
   raw=fetch(url); text=html.unescape(re.sub('<[^>]+>','\n',raw)); rows=[]
   if retailer=='Walmart':
    for m in re.finditer(r'current price[^$]{0,30}\$([0-9]+(?:\.[0-9]{2})?)[\s\S]{0,500}?###\s*([^\n<]{10,220})',text,re.I):
     price=float(m.group(1));name=' '.join(m.group(2).split())
     if 'pokemon' in name.lower(): rows.append((retailer,name,price,'https://www.walmart.com/search?q='+quote_plus(name)))
   else:
    lines=[' '.join(x.split()) for x in text.splitlines() if x.strip()]
    for i,x in enumerate(lines):
     pm=re.fullmatch(r'\$([0-9]+(?:\.[0-9]{2})?)',x)
     if pm:
      for name in lines[i+1:i+8]:
       if len(name)>12 and 'pokemon' in name.lower(): rows.append((retailer,name,float(pm.group(1)),'https://www.target.com/s?searchTerm='+quote_plus(name)));break
   c=db();now=datetime.now(timezone.utc).isoformat()
   for r,n,p,u in rows[:200]: c.execute('insert into products(retailer,name,price,url,last_seen) values(?,?,?,?,?)',(r,n,p,u,now))
   found=len(rows[:200]);c.execute('insert into scans(ts,retailer,found,error) values(?,?,?,?)',(now,retailer,found,''));c.commit();c.close()
  except Exception as e:
   err=str(e)[:200];c=db();c.execute('insert into scans(ts,retailer,found,error) values(?,?,?,?)',(datetime.now(timezone.utc).isoformat(),retailer,0,err));c.commit();c.close()
def worker():
 time.sleep(2)
 while True: scan();time.sleep(900)
threading.Thread(target=worker,daemon=True).start()
CSS='body{margin:0;background:#07101f;color:#eef5ff;font-family:-apple-system,sans-serif}.w{max-width:1000px;margin:auto;padding:16px}a{color:white}.nav{display:flex;gap:8px;overflow:auto}.b,.card,.note{background:#111d35;border-radius:16px;padding:13px;margin:8px 0}.nav a{background:#1b2b4b;padding:10px 14px;border-radius:999px;text-decoration:none;white-space:nowrap}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px}.name{font-weight:800;font-size:17px}.price{font-weight:900;font-size:25px}.mut{color:#9fb1cb;font-size:13px}.note{border:1px solid #705b25;color:#ffe7a0}'
def home():
 c=db();ps=c.execute('select * from products order by id desc limit 300').fetchall();ss=c.execute('select * from scans order by id desc limit 4').fetchall();c.close();cards=''
 for x in ps: cards+=f"<div class=card><div class=name>{html.escape(x['name'])}</div><div class=price>${x['price']:.2f}</div><div class=mut>{x['retailer']} · catalog observed · local stock NOT CONFIRMED</div><p><a href='{html.escape(x['url'])}' target=_blank>Retailer</a> · <a href='https://www.ebay.com/sch/i.html?_nkw={quote_plus(x['name'])}&LH_Sold=1&LH_Complete=1' target=_blank>eBay sold</a></p></div>"
 if not cards: cards='<div class=card>First live scan is starting. Check Diagnostics shortly.</div>'
 status=' | '.join(f"{x['retailer']}: {x['found']}"+(' ERROR' if x['error'] else '') for x in ss) or 'starting'
 return f"<meta name=viewport content='width=device-width,initial-scale=1'><style>{CSS}</style><div class=w><h1>⚡ Pokesale</h1><div class=nav><a href=/>Products</a><a href=/stores>Stores</a><a href=/scan>Scan now</a><a href=/diagnostics>Diagnostics</a></div><div class=note><b>No fake restocks.</b> Products/prices are auto-discovered. Local store stock is only claimed when a store-specific source confirms it.<br><br>{status}</div><div class=grid>{cards}</div></div>"
def stores(): return '<meta name=viewport content="width=device-width,initial-scale=1"><style>'+CSS+'</style><div class=w><h1>Stores</h1><a href=/>Back</a><div class=grid>'+''.join(f'<div class=card><b>{z}: {r} — {n}</b><div class=mut>{a}</div></div>' for z,r,n,a in STORES)+'</div></div>'
def diag():
 c=db();ss=c.execute('select * from scans order by id desc limit 30').fetchall();c.close();return '<meta name=viewport content="width=device-width,initial-scale=1"><style>'+CSS+'</style><div class=w><h1>Diagnostics</h1><a href=/>Back</a>'+''.join(f"<div class=card><b>{x['retailer']}: {x['found']}</b><div class=mut>{html.escape(x['error'] or 'OK')}</div></div>" for x in ss)+'</div>'
class H(BaseHTTPRequestHandler):
 def out(self,s,ct='text/html'):
  b=s.encode();self.send_response(200);self.send_header('Content-Type',ct);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  p=urlparse(self.path).path
  if p=='/':return self.out(home())
  if p=='/stores':return self.out(stores())
  if p=='/diagnostics':return self.out(diag())
  if p=='/scan':threading.Thread(target=scan,daemon=True).start();self.send_response(302);self.send_header('Location','/');self.end_headers();return
  if p=='/health':return self.out(json.dumps({'ok':True}),'application/json')
  self.send_response(404);self.end_headers()
 def log_message(self,*a):pass
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',PORT),H).serve_forever()
