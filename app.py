import os, sqlite3, hashlib, threading, time
from datetime import datetime, timezone
from flask import Flask, render_template_string, request, redirect, url_for, jsonify, Response
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()
DB=os.path.join(os.path.dirname(__file__), 'radar.db')
SCAN_MINUTES=int(os.getenv('SCAN_MINUTES','10'))
MIN_PROFIT=float(os.getenv('MIN_PROFIT','10'))
MIN_ROI=float(os.getenv('MIN_ROI','25'))
SMS_MIN_PROFIT=float(os.getenv('SMS_MIN_PROFIT','25'))
SMS_MIN_ROI=float(os.getenv('SMS_MIN_ROI','40'))
app=Flask(__name__); app.secret_key=os.getenv('FLASK_SECRET','dev')
HEADERS={'User-Agent':'Mozilla/5.0 FlipRadar/1.0 (personal inventory monitor)'}

ZONES=[
 ('Home','Huntingtown / Prince Frederick','20639',18),
 ('Work','Beltsville','20705',10),
]
# Verified seed locations; add/remove from dashboard DB as needed.
STORES=[
 ('Home','Walmart','Prince Frederick','150 Solomons Island Rd N, Prince Frederick, MD 20678','https://www.walmart.com/store/1716-prince-frederick-md'),
 ('Home','Five Below','Prince Frederick','855 Solomons Island Rd N, Prince Frederick, MD 20678','https://locations.fivebelow.com/md/prince-frederick'),
 ('Home','GameStop','Prince Frederick','725 Solomons Island Rd N Ste C, Prince Frederick, MD 20678','https://www.gamestop.com/store/us/md/prince-frederick/'),
 ('Home','ALDI','Prince Frederick','429 Solomons Island Rd N, Prince Frederick, MD 20678','https://www.aldi.us/'),
 ('Home',"Sam's Club",'Waldorf','2365 Crain Hwy, Waldorf, MD 20601','https://www.samsclub.com/club/6655-waldorf-md'),
 ('Work','Target','Greenbelt','6100 Greenbelt Rd, Greenbelt, MD 20770','https://www.target.com/sl/greenbelt-store/1295'),
 ('Work','Five Below','Greenbelt','6000 Greenbelt Rd Unit 65A, Greenbelt, MD 20770','https://locations.fivebelow.com/md/greenbelt/6000-greenbelt-road'),
 ('Work','Target','Cherry Hill','12000 Cherry Hill Rd, Silver Spring, MD 20904','https://www.target.com/store-locator/find-stores/20904'),
 ('Work','Five Below','Cherry Hill','12012 Cherry Hill Rd, Silver Spring, MD 20904','https://locations.fivebelow.com/md/silver-spring'),
 ('Work','Five Below','Laurel','14260A Baltimore Ave, Laurel, MD 20707','https://locations.fivebelow.com/md/laurel'),
 ('Work','Dollar Tree','Beltsville','10464 Baltimore Ave, Beltsville, MD 20705','https://www.dollartree.com/locations/md/beltsville/'),
 ('Work','ALDI','Beltsville','10912 Baltimore Ave, Beltsville, MD 20705','https://www.aldi.us/'),
 ('Work','Dollar Tree','Greenbelt','7573 Greenbelt Rd, Greenbelt, MD 20770','https://www.dollartree.com/locations/md/greenbelt/'),
 ('Work','ALDI','College Park','8904 62nd Ave, College Park, MD 20740','https://www.aldi.us/'),
 ('Work','ALDI','Laurel','14100 Baltimore Ave Ste 103, Laurel, MD 20707','https://www.aldi.us/'),
 ('Work','ALDI','Laurel Corridor','3331 B Corridor Marketplace, Laurel, MD 20724','https://www.aldi.us/'),
 ('Work',"Sam's Club",'Laurel','3535 Russett Green E, Laurel, MD 20724','https://www.samsclub.com/club/6434-laurel-md'),
]
WATCH=[
 ('Pokemon','pokemon'),('Pokemon','elite trainer box'),('Pokemon','booster bundle'),('Pokemon','collection box'),
 ('TCG','one piece card game'),('Sports Cards','panini prizm'),('Sports Cards','topps chrome'),
 ('Collectibles','hot wheels premium'),('Collectibles','funko exclusive'),('LEGO','lego'),
 ('Dollar Store','pokemon trading card'),('Dollar Store','blind bag'),('Dollar Store','seasonal'),
 ('Warehouse','members mark clearance'),('Warehouse','limited edition'),('ALDI Finds','aldi finds'),('ALDI Finds','special buy'),
]

def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
 c=db(); c.executescript('''
 CREATE TABLE IF NOT EXISTS stores(id INTEGER PRIMARY KEY,zone TEXT,retailer TEXT,name TEXT,address TEXT,url TEXT UNIQUE,enabled INTEGER DEFAULT 1);
 CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,store_id INTEGER,category TEXT,title TEXT,url TEXT,price REAL,market REAL,stock TEXT,seen_at TEXT,fingerprint TEXT UNIQUE);
 CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,fingerprint TEXT UNIQUE,sent_at TEXT,channel TEXT);
 CREATE TABLE IF NOT EXISTS manual_items(id INTEGER PRIMARY KEY,store_id INTEGER,category TEXT,title TEXT,url TEXT,retail REAL,market REAL,stock TEXT DEFAULT 'IN STOCK',created_at TEXT);
 ''')
 for z,r,n,a,u in STORES: c.execute('INSERT OR IGNORE INTO stores(zone,retailer,name,address,url) VALUES(?,?,?,?,?)',(z,r,n,a,u))
 c.commit(); c.close()

def money(v): return None if v is None else round(float(v),2)
def economics(retail,market):
 if retail is None or market is None or retail<=0:return (None,None)
 # conservative default: 13.25% marketplace fee + $5 shipping/handling
 net=market*(1-.1325)-5-retail
 return round(net,2), round(net/retail*100,1)

def send_sms(body):
 sid=os.getenv('TWILIO_ACCOUNT_SID'); tok=os.getenv('TWILIO_AUTH_TOKEN'); frm=os.getenv('TWILIO_FROM'); to=os.getenv('SMS_TO')
 if not all([sid,tok,frm,to]): return False
 from twilio.rest import Client
 Client(sid,tok).messages.create(body=body[:1500],from_=frm,to=to); return True

def maybe_alert(fp,title,store,retail,market,url):
 profit,roi=economics(retail,market)
 if profit is None or not (profit>=SMS_MIN_PROFIT or roi>=SMS_MIN_ROI): return
 c=db(); old=c.execute('SELECT 1 FROM alerts WHERE fingerprint=?',(fp,)).fetchone()
 if old: c.close(); return
 ok=send_sms(f'FLIP RADAR: {title} @ {store}. Retail ${retail:.2f}, market ${market:.2f}, est profit ${profit:.2f} ({roi:.0f}% ROI). {url}')
 if ok: c.execute('INSERT INTO alerts(fingerprint,sent_at,channel) VALUES(?,?,?)',(fp,datetime.now(timezone.utc).isoformat(),'sms')); c.commit()
 c.close()

def scan_store(s):
 # Safe baseline collector: verifies retailer pages are reachable and discovers watch terms present in public HTML.
 # Retailers frequently render exact local stock client-side; manual/API adapters can add precise stock without breaking the core app.
 try:
  resp=requests.get(s['url'],headers=HEADERS,timeout=15); resp.raise_for_status()
  text=BeautifulSoup(resp.text,'html.parser').get_text(' ',strip=True).lower()
  now=datetime.now(timezone.utc).isoformat(); c=db()
  for category,term in WATCH:
   if term in text:
    fp=hashlib.sha256(f"{s['id']}|{term}|{now[:13]}".encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO observations(store_id,category,title,url,stock,seen_at,fingerprint) VALUES(?,?,?,?,?,?,?)',(s['id'],category,term.title(),s['url'],'PAGE MATCH',now,fp))
  c.commit(); c.close()
 except Exception as e:
  print('scan error',s['retailer'],s['name'],e)

def scan_all():
 c=db(); stores=c.execute('SELECT * FROM stores WHERE enabled=1').fetchall(); c.close()
 for s in stores: scan_store(s)

def rows():
 c=db(); data=c.execute('''SELECT m.*,s.zone,s.retailer,s.name store_name,s.address FROM manual_items m JOIN stores s ON s.id=m.store_id ORDER BY m.created_at DESC''').fetchall(); c.close()
 out=[]
 for x in data:
  d=dict(x); d['profit'],d['roi']=economics(d['retail'],d['market']); out.append(d)
 return out

HTML='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0b1020"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Flip Radar"><link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/icon.svg"><title>Local Flip Radar</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,system-ui;margin:0;background:#0b1020;color:#edf2ff;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}.wrap{max-width:1100px;margin:auto;padding:18px}.top{position:sticky;top:0;background:#0b1020ee;backdrop-filter:blur(10px);padding:8px 0;z-index:5}.pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#202b49;margin-right:6px;font-size:12px}.cards{display:flex;gap:12px;flex-wrap:wrap}.card,form{background:#151d34;padding:16px;border-radius:14px;margin:12px 0}.hot{border:1px solid #ffd166}a{color:#78b7ff}input,select,button{padding:10px;border-radius:8px;border:0;margin:4px}button{cursor:pointer}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #29334f;text-align:left}.good{color:#6ee7a8}.muted{color:#9aa7c2}@media(max-width:700px){.wrap{padding:12px}h1{font-size:25px}.cards{display:grid;grid-template-columns:repeat(3,1fr)}.card{padding:12px;font-size:13px}form input,form select,form button{box-sizing:border-box;width:100%;margin:5px 0}table{font-size:12px;display:block;overflow-x:auto;white-space:nowrap}.hide{display:none}}
</style></head><body><div class="wrap"><div class="top"><h1>📡 Local Flip Radar</h1><span class="pill">🏠 Huntingtown / Prince Frederick</span><span class="pill">💼 Beltsville + 10 mi</span></div><p class="muted">Phone-first resale opportunity dashboard</p>
<div class="cards"><div class="card"><b>{{stores}}</b><br>stores monitored</div><div class="card"><b>{{items|length}}</b><br>priced opportunities</div><div class="card"><b>{{hot}}</b><br>above threshold</div></div>
<form method="post" action="/add"><b>Add / price an opportunity</b><br><select name="store_id">{% for s in store_rows %}<option value="{{s.id}}">{{s.zone}} — {{s.retailer}} {{s.name}}</option>{% endfor %}</select><input name="category" placeholder="Pokemon"><input name="title" required placeholder="Item name"><input name="retail" required type="number" step=".01" placeholder="Retail"><input name="market" required type="number" step=".01" placeholder="Market"><input name="url" placeholder="Product URL"><button>Add</button></form>
<table><tr><th>Item</th><th>Store</th><th>Cost</th><th>Market</th><th>Profit</th><th>ROI</th><th class="hide">Status</th></tr>{% for x in items %}<tr class="{% if x.profit and (x.profit>=minprofit or x.roi>=minroi) %}hot{% endif %}"><td>{% if x.url %}<a href="{{x.url}}" target="_blank">{{x.title}}</a>{% else %}{{x.title}}{% endif %}<br><span class="muted">{{x.category}}</span></td><td>{{x.zone}} · {{x.retailer}}<br><span class="muted">{{x.store_name}}</span></td><td>${{'%.2f'|format(x.retail)}}</td><td>${{'%.2f'|format(x.market)}}</td><td class="good">${{'%.2f'|format(x.profit)}}</td><td class="good">{{x.roi}}%</td><td class="hide">{{x.stock}}</td></tr>{% endfor %}</table>
<p><a href="/stores">Store list</a> · <a href="/scan">Run scan now</a> · <a href="/api/opportunities">JSON API</a></p><div class="card"><b>📲 Install on iPhone</b><br><span class="muted">Safari → Share → Add to Home Screen. Flip Radar will open like an app.</span></div></div><script>if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(()=>{});}</script></body></html>'''

@app.route('/')
def home():
 c=db(); sr=c.execute('SELECT * FROM stores WHERE enabled=1 ORDER BY zone,retailer').fetchall(); c.close(); its=rows(); hot=sum(1 for x in its if x['profit'] is not None and (x['profit']>=MIN_PROFIT or x['roi']>=MIN_ROI))
 return render_template_string(HTML,items=its,hot=hot,stores=len(sr),store_rows=sr,minprofit=MIN_PROFIT,minroi=MIN_ROI)
@app.post('/add')
def add():
 c=db(); vals=(int(request.form['store_id']),request.form.get('category','Other'),request.form['title'],request.form.get('url',''),float(request.form['retail']),float(request.form['market']),'IN STOCK',datetime.now(timezone.utc).isoformat())
 c.execute('INSERT INTO manual_items(store_id,category,title,url,retail,market,stock,created_at) VALUES(?,?,?,?,?,?,?,?)',vals); c.commit(); itemid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; s=c.execute('SELECT * FROM stores WHERE id=?',(vals[0],)).fetchone(); c.close()
 fp=hashlib.sha256(f'manual|{itemid}'.encode()).hexdigest(); maybe_alert(fp,vals[2],f"{s['retailer']} {s['name']}",vals[4],vals[5],vals[3]); return redirect(url_for('home'))
@app.route('/stores')
def stores():
 c=db(); sr=c.execute('SELECT * FROM stores ORDER BY zone,retailer').fetchall(); c.close(); return '<h2>Monitored stores</h2>'+''.join(f"<p><b>{s['zone']}: {s['retailer']} {s['name']}</b><br>{s['address']}<br><a href='{s['url']}'>retailer page</a></p>" for s in sr)+'<p><a href="/">Back</a></p>'
@app.route('/scan')
def scan(): threading.Thread(target=scan_all,daemon=True).start(); return redirect(url_for('home'))
@app.route('/api/opportunities')
def api(): return jsonify(rows())
@app.route('/health')
def health(): return {'ok':True,'time':datetime.now(timezone.utc).isoformat()}

@app.route('/manifest.webmanifest')
def manifest():
 return jsonify({"name":"Local Flip Radar","short_name":"Flip Radar","start_url":"/","display":"standalone","background_color":"#0b1020","theme_color":"#0b1020","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any maskable"}]})

@app.route('/sw.js')
def sw():
 js="const C='flip-radar-v1';self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(['/','/manifest.webmanifest','/icon.svg'])))});self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('fetch',e=>{if(e.request.method==='GET'){e.respondWith(fetch(e.request).then(r=>{let x=r.clone();caches.open(C).then(c=>c.put(e.request,x));return r}).catch(()=>caches.match(e.request)))}});"
 return Response(js,mimetype='application/javascript')

@app.route('/icon.svg')
def icon():
 svg="<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'><rect width='512' height='512' rx='110' fill='#0b1020'/><circle cx='256' cy='256' r='170' fill='none' stroke='#edf2ff' stroke-width='34'/><path d='M86 256h340' stroke='#edf2ff' stroke-width='34'/><circle cx='256' cy='256' r='58' fill='#ffd166' stroke='#edf2ff' stroke-width='25'/></svg>"
 return Response(svg,mimetype='image/svg+xml')

init_db()

# Start one background scan scheduler when the app process starts.
# render.yaml deliberately runs a single Gunicorn worker so only one scheduler is created.
scheduler=BackgroundScheduler(daemon=True)
scheduler.add_job(scan_all,'interval',minutes=SCAN_MINUTES,max_instances=1,coalesce=True)
scheduler.start()

if __name__=='__main__':
 app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=False)
