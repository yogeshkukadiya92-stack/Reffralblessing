import hmac
import html
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get('CIRCLE_HOST', '127.0.0.1')
PUBLIC_URL = os.environ.get('CIRCLE_PUBLIC_URL', '').rstrip('/')
ADMIN = os.environ.get('CIRCLE_ADMIN_PASSWORD')
if HOST != '127.0.0.1' and (not ADMIN or not PUBLIC_URL.startswith('https://')):
    raise SystemExit('Hosted mode requires CIRCLE_ADMIN_PASSWORD and an HTTPS CIRCLE_PUBLIC_URL.')
ADMIN = ADMIN or secrets.token_urlsafe(18)
DB_PATH = os.environ.get('CIRCLE_DB_PATH', str(ROOT / 'circle.sqlite3'))
CODE = 'YOGESH24'
WORKSHOPS = {'personality-awareness': ('Personality Awareness', 10),
             'mindful-communication': ('Mindful Communication', 20)}

def database():
    return sqlite3.connect(DB_PATH)

with closing(database()) as db, db:
    db.execute('''CREATE TABLE IF NOT EXISTS registrations (
        id INTEGER PRIMARY KEY, workshop TEXT NOT NULL, name TEXT NOT NULL,
        phone TEXT NOT NULL, referred INTEGER NOT NULL, points INTEGER NOT NULL,
        verified INTEGER NOT NULL DEFAULT 0, UNIQUE(workshop, phone))''')

class App(BaseHTTPRequestHandler):
    def send(self, body, status=200, mime='text/html; charset=utf-8'):
        payload = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def page(self, title, body, status=200):
        self.send(f'''<!doctype html><html lang="en"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{title} · Circle</title><style>
        *{{box-sizing:border-box}}body{{margin:0;background:#f6f8f7;color:#173e33;font:16px system-ui}}
        main{{max-width:700px;margin:7vh auto;padding:24px}}section{{background:white;padding:24px;border-radius:14px;margin:18px 0}}
        h1{{font-size:36px;letter-spacing:-1px}}label{{display:block;margin:14px 0}}
        input{{display:block;width:100%;padding:12px;border:1px solid #d3ded7;border-radius:8px;margin-top:6px}}
        button{{background:#ef9866;color:#173e33;border:0;border-radius:8px;padding:12px 18px;font:inherit;cursor:pointer}}
        a{{color:#173e33}}small{{color:#526b60}}</style>
        <main><a href="/">Circle · Dashboard</a><h1>{title}</h1>{body}</main></html>''', status)

    def authorized(self):
        import base64
        expected = 'Basic ' + base64.b64encode(('admin:' + ADMIN).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode()):
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Circle local admin"')
        self.end_headers()
        return False

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        ref = query.get('ref', [''])[0] == CODE
        if url.path == '/' and ref:
            self.page('Explore workshops', ''.join(
                f'<section><a href="/w/{slug}?ref={CODE}">{name}</a></section>'
                for slug, (name, _) in WORKSHOPS.items()))
        elif url.path == '/':
            self.send((ROOT / 'index.html').read_text())
        elif url.path.startswith('/w/') and (slug := url.path[3:]) in WORKSHOPS:
            name, _ = WORKSHOPS[slug]
            self.page(name, f'''<section><p>Reserve your place in this workshop.</p>
            <form method="post" action="/w/{slug}">
            <input type="hidden" name="ref" value="{CODE if ref else ''}">
            <label>Full name<input name="name" required maxlength="80" autocomplete="name"></label>
            <label>Mobile with country code<input name="phone" required type="tel" autocomplete="tel" placeholder="+91…"></label>
            <button>Register</button></form></section>''')
        elif url.path == '/admin' and self.authorized():
            with closing(database()) as db:
                rows = db.execute('SELECT id, workshop, name, verified FROM registrations ORDER BY id DESC').fetchall()
            self.page('Verify registrations', ''.join(
                f'<section><strong>{html.escape(name)}</strong><p>{WORKSHOPS[slug][0]}</p>'
                + ('<small>Verified</small>' if verified else
                   f'<form method="post" action="/admin"><input type="hidden" name="id" value="{rid}"><button>Verify registration</button></form>')
                + '</section>' for rid, slug, name, verified in rows) or '<p>No registrations yet.</p>')
        elif url.path == '/api/activity':
            with closing(database()) as db:
                rows = db.execute('SELECT workshop, points, verified FROM registrations WHERE referred=1 ORDER BY id DESC').fetchall()
            records = [
                {'workshop': WORKSHOPS[slug][0], 'points': points, 'verified': bool(verified)}
                for slug, points, verified in rows
            ]
            self.send(json.dumps(records), mime='application/json')
        elif url.path == '/api/points':
            with closing(database()) as db:
                points, count, pending = db.execute(
                    'SELECT COALESCE(SUM(points*verified),0), COALESCE(SUM(verified),0), COUNT(*)-COALESCE(SUM(verified),0) FROM registrations WHERE referred=1').fetchone()
            self.send(f'{{"points":{points},"confirmed":{count},"pending":{pending}}}', mime='application/json')
        elif url.path != '/admin':
            self.page('Page unavailable', '<p>Choose a workshop from the dashboard.</p>', 404)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin != (PUBLIC_URL or 'http://' + self.headers.get('Host', '')):
            self.send('Invalid origin', 403)
            return
        size = self.headers.get('Content-Length', '')
        if not size.isdigit() or not 0 < int(size) <= 4096:
            self.send('Invalid request body', 400)
            return
        fields = parse_qs(self.rfile.read(int(size)).decode(errors='replace'))
        def field(key):
            return fields.get(key, [''])[0].strip()
        if self.path == '/admin':
            if not self.authorized():
                return
            with closing(database()) as db, db:
                db.execute('UPDATE registrations SET verified=1 WHERE id=? AND verified=0', (field('id'),))
            self.send_response(303)
            self.send_header('Location', '/admin')
            self.end_headers()
            return
        slug = self.path.removeprefix('/w/')
        if not self.path.startswith('/w/') or slug not in WORKSHOPS:
            self.page('Page unavailable', '', 404)
            return
        name = field('name')
        phone = re.sub(r'[ ()-]', '', field('phone'))
        if not 1 <= len(name) <= 80 or not re.fullmatch(r'\+[1-9][0-9]{7,14}', phone):
            self.page('Check your details', '<p>Enter your name and a mobile number with country code.</p>', 400)
            return
        try:
            with closing(database()) as db, db:
                db.execute('INSERT INTO registrations(workshop,name,phone,referred,points) VALUES(?,?,?,?,?)',
                           (slug, name, phone, int(field('ref') == CODE), WORKSHOPS[slug][1]))
        except sqlite3.IntegrityError:
            self.page('Registration already received', '<p>Use the dashboard to explore other workshops.</p>', 409)
            return
        self.page('Registration received', '<p>Your registration is pending verification.</p>', 201)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '4174'))
    if HOST == '127.0.0.1' and 'CIRCLE_ADMIN_PASSWORD' not in os.environ:
        print('Local admin: username admin, password ' + ADMIN, flush=True)
    ThreadingHTTPServer((HOST, port), App).serve_forever()
