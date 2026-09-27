import os,tempfile,importlib.util,json,time
fd,path=tempfile.mkstemp();os.close(fd);os.unlink(path)
os.environ['DB_PATH']=path;os.environ['POKESALE_NO_WORKER']='1'
spec=importlib.util.spec_from_file_location('app','app.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

def ok(x,msg):
 if not x:raise AssertionError(msg)

def run():
 # normalization / type safety
 ok(a.kind('Pokemon 151 ETB')=='etb','ETB alias')
 ok(a.kind('Pokémon 151 Elite Trainer Box')=='etb','ETB full')
 ok(a.match_score('Pokemon 151 Elite Trainer Box','Pokemon 151 ETB')>.67,'same product match')
 ok(a.match_score('Pokemon Center 151 Elite Trainer Box','Pokemon 151 Elite Trainer Box')==0,'PC edition mismatch blocked')
 ok(a.match_score('Pokemon Surging Sparks Elite Trainer Box','Pokemon Journey Together Elite Trainer Box')<.67,'different set mismatch')
 # geographic parser
 ok(a.extract_location('Target Waldorf MD Pokemon restocked today')=='waldorf','waldorf')
 ok(a.extract_location('Pokemon restock in California Maryland Target') in ('california maryland','maryland'),'California MD')
 ok(a.extract_location('Target in Los Angeles Pokemon restock') is None,'nonlocal rejected')
 ok(a.extract_retailer('Waldorf Target restocked Pokemon')=='Target','retailer')
 ok(a.looks_restock('Target had plenty of Pokemon ETBs on shelves'),'restock language')
 # signal acceptance/rejection
 ok(a.add_signal('test','Waldorf Target Pokemon ETB restocked today','https://example.com/1'),'local signal accepted')
 ok(not a.add_signal('test','Nationwide Pokemon ETB release announced','https://example.com/2'),'generic rejected')
 ok(not a.add_signal('test','Waldorf Target LEGO restocked','https://example.com/3'),'nonpokemon rejected')
 ok(len(a.live_signals())==1,'dedupe/base signal count')
 a.add_signal('test','Waldorf Target Pokemon ETB restocked today','https://example.com/1');ok(len(a.live_signals())==1,'dedupe')
 # retailer parser JSON-LD
 raw='''<script type="application/ld+json">{"@type":"Product","name":"Pokemon 151 Elite Trainer Box","offers":{"price":"49.99"},"url":"/p/151"}</script>'''
 rows=a.parse_retailer(raw,'https://shop.example');ok(len(rows)==1 and rows[0][1]==49.99 and rows[0][2]=='https://shop.example/p/151','jsonld parse')
 # price bounds / malformed
 ok(a.money('$49.99')==49.99 and a.money('n/a') is None and a.money('$99999') is None,'money parsing')
 # opportunity economics + exact matching
 a.upsert('retailer:test','Target','Pokemon 151 Elite Trainer Box',49.99,None,'','','RETAILER CATALOG + PRICE — LOCAL STOCK NOT CONFIRMED')
 a.upsert('TCGCSV','TCGplayer','Pokemon 151 Elite Trainer Box',None,89.99,'','','TCGPLAYER MARKET DATA')
 ops=a.opportunities(); r=next(x for x in ops if x['retailer']=='Target');ok(r['market_match']=='Pokemon 151 Elite Trainer Box','market matched');ok(r['profit']>15 and r['buy_candidate'],'buy candidate')
 # wrong edition cannot match
 a.upsert('retailer:test','Target','Pokemon Center 151 Elite Trainer Box',49.99,None,'','','RETAILER CATALOG + PRICE — LOCAL STOCK NOT CONFIRMED')
 r=next(x for x in a.opportunities() if x['retailer']=='Target' and 'Center' in x['name']);ok(r.get('market_price') is None,'wrong edition blocked')
 # persistence/reopen
 c=a.db(); count=c.execute('select count(*) from signals').fetchone()[0];c.close();ok(count==1,'sqlite persisted')
 # pages should render without exceptions
 ok('PokeSale v7' in a.home(),'home render');ok('Diagnostics' in a.diagnostics(),'diagnostics render');ok('Stores' in a.stores_page(),'stores render')
 print('ALL TESTS PASSED')
run()

# Additional source-adapter tests using controlled RSS payloads; no network required.
def source_adapter_tests():
 original=a.fetch
 fake='''<?xml version="1.0"?><rss><channel><item><title>Waldorf Target Pokemon 151 ETB restocked today</title><link>https://example.com/local</link><pubDate>now</pubDate></item><item><title>Pokemon release nationwide</title><link>https://example.com/national</link></item></channel></rss>'''
 try:
  a.fetch=lambda *args,**kwargs: fake
  a.scan_rss()
  c=a.db(); row=c.execute("select found,error from scans where source='RSS Local Scout' order by id desc limit 1").fetchone(); c.close()
  ok(row['found']>=1 and row['error']=='OK','RSS adapter accepts local and logs success')
  def boom(*args,**kwargs): raise OSError('network down')
  a.fetch=boom; a.scan_rss(); c=a.db(); row=c.execute("select found,error from scans where source='RSS Local Scout' order by id desc limit 1").fetchone(); c.close()
  ok(row['found']==0 and row['error'].startswith('FAILED'),'RSS adapter exposes total network failure')
 finally:a.fetch=original
 print('SOURCE ADAPTER TESTS PASSED')
source_adapter_tests()
