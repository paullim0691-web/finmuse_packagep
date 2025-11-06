
# backend/main.py - minimal FinMuse backend (FastAPI)
import os, json, sqlite3, uuid, datetime, logging, asyncio
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import httpx

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
ADMIN_SECRET = os.getenv("FINMUSE_ADMIN_SECRET", "change_me")
DB_PATH = os.getenv("FINMUSE_DB_PATH", "finmuse.db")
DAILY_LLM_CALL_LIMIT = int(os.getenv("DAILY_LLM_CALL_LIMIT", "100"))
SITE_DOMAIN = os.getenv("SITE_DOMAIN", "http://localhost:8000")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("finmuse")

app = FastAPI(title="FinMuse Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute(\"\"\"CREATE TABLE IF NOT EXISTS articles (
      id TEXT PRIMARY KEY,
      title TEXT,
      source TEXT,
      original_url TEXT UNIQUE,
      published_at TEXT,
      raw_text TEXT,
      tl_dr TEXT,
      summary_pro TEXT,
      evidence TEXT,
      confidence REAL,
      status TEXT,
      created_at TEXT
    )\"\"\")
    cur.execute(\"\"\"CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)\"\"\")
    conn.commit(); conn.close()

init_db()

def _meta_get(k: str):
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT v FROM meta WHERE k = ?", (k,))
    r = cur.fetchone(); conn.close(); return r[0] if r else None

def _meta_set(k: str, v: str):
    conn = get_conn(); cur = conn.cursor(); cur.execute("INSERT OR REPLACE INTO meta (k,v) VALUES (?,?)", (k, str(v)))
    conn.commit(); conn.close()

def llm_reset_if_needed():
    today = datetime.date.today().isoformat()
    last = _meta_get("llm_last_reset")
    if last != today:
        _meta_set("llm_last_reset", today); _meta_set("llm_calls_today", "0")

def llm_get_calls():
    llm_reset_if_needed(); v = _meta_get("llm_calls_today") or "0"
    try: return int(v)
    except: return 0

def llm_increment():
    llm_reset_if_needed(); calls = llm_get_calls() + 1; _meta_set("llm_calls_today", str(calls)); return calls

def llm_allow_call(): return llm_get_calls() < DAILY_LLM_CALL_LIMIT

async def call_openai_chat(messages: list, model: str = "gpt-4o-mini", max_tokens: int = 500, timeout: int = 30):
    if not OPENAI_API_KEY:
        return None
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.0}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code != 200:
                logger.warning("OpenAI error %s %s", r.status_code, r.text[:300]); return None
            data = r.json()
            if "choices" in data and len(data["choices"])>0:
                choice = data["choices"][0]; msg = choice.get("message") or {}; return msg.get("content") or choice.get("text")
            return None
    except Exception as e:
        logger.exception("call_openai_chat exception: %s", e); return None

def summarizer_fallback(text: str) -> str:
    if not text: return ""
    s = text.replace("\\n"," ").split(". ")
    return (". ".join(s[:2]) + ("." if len(s)>0 else ""))[:700]

async def generate_pro_summary(raw_text: str, title: str):
    easy = summarizer_fallback(raw_text)
    if OPENAI_API_KEY and llm_allow_call():
        sys_prompt = ("You are a senior financial analyst. Output STRICT JSON only. Keys: tl_dr, summary, impact_short, impact_mechanism, evidence (list of {source,quote,url}), confidence. Do NOT invent facts.")
        messages = [{"role":"system","content":sys_prompt},{"role":"user","content":f"Title: {title}\\nText:\\n{raw_text[:6000]}"}]
        ai_resp = await call_openai_chat(messages, max_tokens=500)
        if ai_resp:
            try:
                parsed = json.loads(ai_resp)
                tl = parsed.get("tl_dr") or easy
                summary = parsed.get("summary") or easy
                evidence = parsed.get("evidence") or []
                conf = float(parsed.get("confidence") or 0.5)
                llm_increment()
                return tl, summary, evidence, conf
            except Exception:
                llm_increment()
                return easy, ai_resp.strip()[:1200], [], 0.4
    return easy, easy, [], 0.5

async def fetch_from_newsapi(page_size:int=20):
    if not NEWS_API_KEY:
        return []
    url = f"https://newsapi.org/v2/top-headlines?category=business&pageSize={page_size}&apiKey={NEWS_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                logger.warning("NewsAPI error %s %s", r.status_code, r.text[:200]); return []
            return r.json().get("articles", [])
    except Exception as e:
        logger.exception("fetch_from_newsapi error: %s", e); return []

def generate_article_html(article):
    aid = article['id']; title = article['title']; summary = article.get('summary_pro') or article.get('tl_dr') or ''; tl = article.get('tl_dr') or ''
    evidence = article.get('evidence') or []; published = article.get('published_at') or article.get('created_at')
    url = f"{SITE_DOMAIN.rstrip('/')}/articles/{aid}.html"
    json_ld = {"@context":"https://schema.org","@type":"NewsArticle","headline": title,"datePublished": published,"mainEntityOfPage": url,"publisher": {"@type":"Organization","name":"FinMuse"},"articleBody": summary[:4000]}
    parts = []
    parts.append("<!doctype html>")
    parts.append("<html lang='ko'><head>")
    parts.append("<meta charset='utf-8'/>")
    parts.append("<meta name='viewport' content='width=device-width,initial-scale=1'/>")
    parts.append(f"<title>{title} | FinMuse</title>")
    parts.append(f"<meta name='description' content='{summary[:160]}'/>")
    parts.append(f"<link rel='canonical' href='{url}'/>")
    parts.append(f"<meta property='og:title' content='{title}'/>")
    parts.append(f"<meta property='og:description' content='{summary[:200]}'/>")
    parts.append("<script type='application/ld+json'>")
    parts.append(json.dumps(json_ld, ensure_ascii=False))
    parts.append("</script>")
    parts.append("<link rel='stylesheet' href='/static/style.css'>")
    parts.append("</head><body><main class='page'><article class='article'>")
    parts.append(f"<h1>{title}</h1>")
    parts.append(f"<div class='meta'>Source: {article.get('source','')}, Published: {published}</div>")
    parts.append(f"<section class='tl-dr'><strong>요약:</strong><p>{tl}</p></section>")
    parts.append(f"<section class='pro'><strong>전문가 분석:</strong><p>{summary}</p></section>")
    parts.append("<section class='evidence'><strong>근거:</strong><ul>")
    for ev in evidence:
        src = ev.get('source',''); quote = ev.get('quote','').replace('<','&lt;').replace('>','&gt;'); link = ev.get('url','')
        parts.append(f"<li>{src}: \"{quote}\" <a href='{link}' target='_blank' rel='nofollow'>원문</a></li>")
    parts.append("</ul></section>")
    parts.append("<footer class='footer'>FinMuse - 자동 생성 리포트</footer>")
    parts.append("</article></main></body></html>")
    html = "".join(parts)
    artdir = os.path.join(STATIC_DIR, 'articles'); os.makedirs(artdir, exist_ok=True)
    path = os.path.join(artdir, f"{aid}.html")
    with open(path, 'w', encoding='utf-8') as f: f.write(html)
    return path

def update_sitemap_and_rss():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, title, published_at, created_at FROM articles WHERE status = 'published' ORDER BY published_at DESC LIMIT 1000")
    rows = cur.fetchall()
    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>\\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for r in rows:
        aid = r['id']; pub = r['published_at'] or r['created_at']
        url = f"{SITE_DOMAIN.rstrip('/')}/articles/{aid}.html"
        sitemap.append(f"<url><loc>{url}</loc><lastmod>{pub}</lastmod></url>")
    sitemap.append('</urlset>')
    with open(os.path.join(STATIC_DIR, 'sitemap.xml'), 'w', encoding='utf-8') as f: f.write('\\n'.join(sitemap))
    rss_items = []
    for r in rows[:50]:
        aid = r['id']; title = r['title']; pub = r['published_at'] or r['created_at']
        url = f"{SITE_DOMAIN.rstrip('/')}/articles/{aid}.html"
        rss_items.append(f"<item><title>{title}</title><link>{url}</link><pubDate>{pub}</pubDate></item>")
    rss = f"<?xml version='1.0' encoding='utf-8'?><rss version='2.0'><channel><title>FinMuse</title>{''.join(rss_items)}</channel></rss>"
    with open(os.path.join(STATIC_DIR, 'rss.xml'), 'w', encoding='utf-8') as f: f.write(rss)

async def fetch_from_newsapi(page_size:int=20):
    if not NEWS_API_KEY:
        return []
    url = f"https://newsapi.org/v2/top-headlines?category=business&pageSize={page_size}&apiKey={NEWS_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                logger.warning("NewsAPI error %s %s", r.status_code, r.text[:200]); return []
            return r.json().get("articles", [])
    except Exception as e:
        logger.exception("fetch_from_newsapi error: %s", e); return []

async def scrape_and_process():
    logger.info('Starting scrape_and_process cycle')
    fetched = await fetch_from_newsapi(20)
    if not fetched:
        fetched = [{'title':'Sample: Fed cuts rate by 25 bps','url':'https://example.com/fed-cut','source':{'name':'ExampleNews'},'publishedAt':datetime.datetime.utcnow().isoformat()+'Z','content':'The Fed lowered rates by 25 basis points citing slowing growth.'}]
    conn = get_conn(); cur = conn.cursor(); saved = 0
    for a in fetched:
        url = a.get('url'); title = a.get('title') or ''
        src = (a.get('source') or {}).get('name') or a.get('source') or 'unknown'
        published = a.get('publishedAt') or datetime.datetime.utcnow().isoformat()+'Z'
        raw = a.get('content') or a.get('description') or title
        if not url: continue
        try:
            cur.execute("SELECT id FROM articles WHERE original_url = ?", (url,))
            if cur.fetchone(): continue
            _id = str(uuid.uuid4())
            cur.execute("INSERT INTO articles (id,title,source,original_url,published_at,raw_text,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (_id, title, src, url, published, raw, 'new', datetime.datetime.utcnow().isoformat()+'Z'))
            conn.commit(); saved += 1
        except Exception as e:
            logger.exception('db insert error: %s', e)
    conn.close()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id,title,raw_text,source,published_at FROM articles WHERE status = 'new' ORDER BY created_at LIMIT 20")
    rows = cur.fetchall()
    for r in rows:
        aid = r['id']; title = r['title']; raw = r['raw_text']; src = r['source']; published = r['published_at']
        try:
            tl, summary, evidence, conf = await generate_pro_summary(raw, title)
            cur.execute("UPDATE articles SET tl_dr = ?, summary_pro = ?, evidence = ?, confidence = ?, status = ? WHERE id = ?",
                        (tl, summary, json.dumps(evidence, ensure_ascii=False), conf, 'published' if conf>=0.5 else 'draft', aid))
            conn.commit()
            article = {'id':aid,'title':title,'tl_dr':tl,'summary_pro':summary,'evidence':evidence,'published_at':published,'created_at':datetime.datetime.utcnow().isoformat()+'Z','source':src}
            if conf>=0.5:
                generate_article_html(article)
            update_sitemap_and_rss()
        except Exception as e:
            logger.exception('process article failed: %s', e)
    conn.close()
    logger.info('Scrape cycle finished, saved %d new articles', saved)

async def periodic_runner():
    await asyncio.sleep(2)
    while True:
        try:
            await scrape_and_process()
        except Exception as e:
            logger.exception('periodic_runner error: %s', e)
        await asyncio.sleep(60*60)

@app.on_event('startup')
async def startup_event():
    os.makedirs(os.path.join(STATIC_DIR,'articles'), exist_ok=True)
    asyncio.create_task(periodic_runner())

@app.get('/health')
def health():
    return {'status':'ok','time': datetime.datetime.utcnow().isoformat()+'Z','llm_calls_today': llm_get_calls()}

@app.post('/admin/scrape')
async def admin_scrape(x_admin_secret: Optional[str] = Header(None)):
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail='unauthorized')
    await scrape_and_process()
    return {'status':'ok'}

@app.get('/api/news')
def api_news(limit: int = 20):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id,title,source,published_at,tl_dr,confidence,status FROM articles ORDER BY published_at DESC LIMIT ?", (limit,))
    rows = cur.fetchall(); items = []
    for r in rows:
        items.append({'id': r['id'], 'title': r['title'], 'source': r['source'], 'published_at': r['published_at'], 'summary': r['tl_dr'], 'confidence': float(r['confidence'] or 0.5), 'status': r['status']})
    conn.close()
    return {'items': items, 'meta': {'count': len(items), 'generated_at': datetime.datetime.utcnow().isoformat()+'Z'}}

@app.get('/api/article/{article_id}')
def api_article(article_id: str):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
    r = cur.fetchone(); conn.close()
    if not r:
        raise HTTPException(status_code=404, detail='not found')
    item = dict(r); item['evidence'] = json.loads(item['evidence']) if item.get('evidence') else []
    return item

@app.get('/')
async def index():
    idx = os.path.join(STATIC_DIR, 'index.html')
    if os.path.isfile(idx):
        return Response(open(idx,'rb').read(), media_type='text/html')
    return {'service': 'FinMuse running'}
