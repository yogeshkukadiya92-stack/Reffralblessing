import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get('CIRCLE_HOST', '127.0.0.1')
PUBLIC_URL = os.environ.get('CIRCLE_PUBLIC_URL', '').rstrip('/')
ADMIN = os.environ.get('CIRCLE_ADMIN_PASSWORD')
if HOST != '127.0.0.1' and (not ADMIN or not PUBLIC_URL.startswith('https://')):
    raise SystemExit('Hosted mode requires CIRCLE_ADMIN_PASSWORD and an HTTPS CIRCLE_PUBLIC_URL.')
ADMIN = ADMIN or secrets.token_urlsafe(18)
DB_PATH = os.environ.get('CIRCLE_DB_PATH', str(ROOT / 'circle.sqlite3'))
LEGACY_CODE = 'YOGESH24'
SESSION_SECONDS = 7 * 24 * 60 * 60
WORKSHOPS = {'personality-awareness': ('Personality Awareness', 10),
             'mindful-communication': ('Mindful Communication', 20)}
REWARDS = {'Learning Toolkit': 50, 'VIP Workshop Pass': 150, 'Community Mentor Session': 250}
escape = html.escape


def database():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


with closing(database()) as db, db:
    db.executescript('''
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY, workshop TEXT NOT NULL, name TEXT NOT NULL,
            phone TEXT NOT NULL, referred INTEGER NOT NULL, points INTEGER NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0, UNIQUE(workshop, phone));
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY, reward TEXT NOT NULL, points INTEGER NOT NULL,
            date TEXT DEFAULT CURRENT_DATE, status TEXT DEFAULT 'Pending approval',
            nonce TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            salt TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS links (
            code TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            workshop TEXT NOT NULL DEFAULT '', recipient TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            share_actions INTEGER NOT NULL DEFAULT 0, opens INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id),
            is_admin INTEGER NOT NULL DEFAULT 0, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts (
            client TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires INTEGER NOT NULL);
    ''')
    db.execute('BEGIN IMMEDIATE')
    for table, column, definition in (
        ('registrations', 'link_code', 'TEXT REFERENCES links(code)'),
        ('registrations', 'created_at', 'TEXT'),
        ('claims', 'user_id', 'INTEGER REFERENCES users(id)'),
    ):
        if column not in {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
    db.execute('CREATE INDEX IF NOT EXISTS registrations_by_link ON registrations(link_code)')
    db.execute('CREATE INDEX IF NOT EXISTS links_by_owner ON links(user_id)')
    db.execute('CREATE INDEX IF NOT EXISTS claims_by_owner ON claims(user_id)')


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1,
                          maxmem=64 * 1024 * 1024).hex()


def workshop_name(slug):
    return WORKSHOPS.get(slug, (slug, 0))[0]


def totals(db, user_id):
    earned = db.execute('''SELECT COALESCE(SUM(r.points), 0) FROM registrations r
                           JOIN links l ON l.code=r.link_code WHERE l.user_id=? AND r.verified=1''',
                        (user_id,)).fetchone()[0]
    reserved = db.execute('SELECT COALESCE(SUM(points),0) FROM claims WHERE user_id=?',
                          (user_id,)).fetchone()[0]
    return earned, reserved


def table(title, headers, rows):
    columns = ''.join(f'<th scope="col" role="columnheader">{escape(label)}</th>' for label in headers)
    body = ''.join(
        '<tr role="row">' + ''.join(
            f'<td role="cell" data-label="{escape(label)}">{cell}</td>'
            for label, cell in zip(headers, row, strict=True)
        ) + '</tr>' for row in rows
    )
    return (f'<div class="table" role="region" aria-label="{escape(title)} table" tabindex="0">'
            f'<table role="table" aria-label="{escape(title)}"><thead role="rowgroup">'
            f'<tr role="row">{columns}</tr></thead><tbody role="rowgroup">{body}</tbody></table></div>')


class App(BaseHTTPRequestHandler):
    def send(self, body, status=200, mime='text/html; charset=utf-8', headers=()):
        payload = body.encode()
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.end_headers()
        self.wfile.write(payload)

    def json(self, body, status=200):
        return self.send(json.dumps(body), status, 'application/json')

    def redirect(self, location, cookie=None):
        headers = [('Location', location)]
        if cookie is not None:
            headers.append(('Set-Cookie', cookie))
        return self.send('', 303, headers=headers)

    def page(self, title, body, status=200):
        self.send(f'''<!doctype html><html lang="en"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{escape(title)} · Circle</title><style>
        *{{box-sizing:border-box}}body{{margin:0;background:#f6f8f7;color:#173e33;font:16px system-ui}}
        main{{max-width:1140px;margin:5vh auto;padding:24px}}section{{background:white;padding:24px;border-radius:14px;margin:18px 0}}
        h1{{font-size:36px;letter-spacing:-1px}}label{{display:block;margin:14px 0}}form{{max-width:480px}}
        input{{display:block;width:100%;padding:12px;border:1px solid #a9bab1;border-radius:8px;margin-top:6px}}
        button{{background:#ef9866;color:#173e33;border:0;border-radius:8px;padding:12px 18px;font:inherit;cursor:pointer}}
        a{{color:#173e33}}small{{color:#526b60}}.table{{overflow:auto}}table{{width:100%;border-collapse:collapse}}
        th,td{{text-align:left;padding:10px;border-bottom:1px solid #dde7e0;white-space:nowrap}}
        .links{{display:flex;gap:18px;align-items:center;flex-wrap:wrap}}.links form{{margin:0}}</style>
        <main><a href="/">Circle</a><h1>{escape(title)}</h1>{body}</main></html>''', status)

    def cookie(self, value, lifetime):
        secure = '; Secure' if PUBLIC_URL else ''
        return f'circle_session={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={lifetime}{secure}'

    def token_hash(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
        except CookieError:
            return ''
        token = cookies.get('circle_session')
        return hashlib.sha256(token.value.encode()).hexdigest() if token else ''

    def session(self):
        with closing(database()) as db:
            return db.execute("""SELECT s.user_id,s.is_admin,u.name,u.username FROM sessions s
                 LEFT JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>unixepoch()""",
                              (self.token_hash(),)).fetchone()

    def start_session(self, user_id=None, is_admin=False):
        token = secrets.token_urlsafe(32)
        with closing(database()) as db, db:
            db.execute('DELETE FROM sessions WHERE token_hash=? OR expires<=unixepoch()', (self.token_hash(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,unixepoch()+?)',
                       (hashlib.sha256(token.encode()).hexdigest(), user_id, int(is_admin), SESSION_SECONDS))
        return self.redirect('/admin' if is_admin else '/', self.cookie(token, SESSION_SECONDS))

    def auth_page(self, path, error='', status=200):
        signup = path == '/signup'
        admin = path == '/admin/login'
        title = 'Admin login' if admin else 'Create your account' if signup else 'Member login'
        notice = f'<p role="alert">{escape(error)}</p>' if error else ''
        name = '<label>Full name<input name="name" required maxlength="80" autocomplete="name"></label>' if signup else ''
        username = ('<label>Username<input name="username" required minlength="3" maxlength="40" '
                    'autocomplete="username"></label>') if not admin else ''
        minimum = 'minlength="12"' if signup else ''
        autocomplete = 'new-password' if signup else 'current-password'
        links = ('<a href="/login">Member login</a>' if admin else
                 '<a href="/login">Already have an account? Log in</a>' if signup else
                 '<a href="/signup">Create account</a> · <a href="/admin/login">Admin login</a>')
        self.page(title, f'''<section>{notice}<form method="post" action="{path}">{name}{username}
        <label>Password<input name="password" type="password" required maxlength="256" {minimum}
        autocomplete="{autocomplete}"></label><button>{title}</button></form><p>{links}</p></section>''', status)

    def referral(self, code, slug='', count=False):
        if code == LEGACY_CODE:
            return code
        if not re.fullmatch(r'[A-Za-z0-9_-]{16}', code):
            return ''
        with closing(database()) as db, db:
            if count:
                cursor = db.execute("UPDATE links SET opens=opens+1 WHERE code=? AND workshop IN ('',?)",
                                    (code, slug))
                return code if cursor.rowcount else ''
            return code if db.execute("SELECT 1 FROM links WHERE code=? AND workshop IN ('',?)",
                                      (code, slug)).fetchone() else ''

    def admin_page(self):
        with closing(database()) as db:
            users = db.execute('''SELECT u.username,u.name,COUNT(l.code) AS links,
                COALESCE(SUM(l.share_actions),0) AS shares,COALESCE(SUM(l.opens),0) AS opens,
                COALESCE(SUM(r.registrations),0) AS registrations,COALESCE(SUM(r.earned),0) AS earned,
                COALESCE(c.reserved,0) AS reserved
                FROM users u LEFT JOIN links l ON l.user_id=u.id
                LEFT JOIN (SELECT link_code,COUNT(*) AS registrations,SUM(points*verified) AS earned
                           FROM registrations GROUP BY link_code) r ON r.link_code=l.code
                LEFT JOIN (SELECT user_id,SUM(points) AS reserved FROM claims GROUP BY user_id) c ON c.user_id=u.id
                GROUP BY u.id ORDER BY u.username''').fetchall()
            links = db.execute('''SELECT l.*,u.username,COUNT(r.id) AS registrations,
                COALESCE(SUM(r.verified),0) AS confirmed,COALESCE(SUM(r.points*r.verified),0) AS earned
                FROM links l JOIN users u ON u.id=l.user_id LEFT JOIN registrations r ON r.link_code=l.code
                GROUP BY l.code ORDER BY l.created_at DESC''').fetchall()
            registrations = db.execute('''SELECT r.*,u.username,l.recipient FROM registrations r
                LEFT JOIN links l ON l.code=r.link_code LEFT JOIN users u ON u.id=l.user_id
                ORDER BY r.id DESC''').fetchall()
            claims = db.execute('''SELECT c.*,u.username FROM claims c LEFT JOIN users u ON u.id=c.user_id
                ORDER BY c.id DESC''').fetchall()

        def cells(*values):
            return tuple(escape(str(value)) for value in values)

        def action(field, value, label):
            return f'<form method="post" action="/admin"><input type="hidden" name="{field}" value="{value}"><button>{label}</button></form>'

        def status(label, pending):
            return f'<span class="status{" pending" if pending else ""}">{escape(label)}</span>'

        members = table('Members', ('Username', 'Member', 'Links', 'Share actions', 'Opens', 'Registrations', 'Earned', 'Available'),
                        (cells(r['username'], r['name'], r['links'], r['shares'], r['opens'], r['registrations'],
                               r['earned'], r['earned'] - r['reserved']) for r in users))
        shares = table('Referral links', ('Owner', 'Recipient label', 'Link code', 'Workshop', 'Share actions', 'Opens', 'Registrations', 'Verified', 'Earned'),
                       (cells(r['username'], r['recipient'] or '—', r['code'], workshop_name(r['workshop']) or 'All',
                              r['share_actions'], r['opens'], r['registrations'], r['confirmed'], r['earned']) for r in links))
        records = table('Registrations', ('Participant', 'Mobile', 'Workshop', 'Registered at (UTC)', 'Referred by', 'Recipient label', 'Link code', 'Status', 'Points earned', 'Action'),
                        (cells(r['name'], r['phone'], workshop_name(r['workshop']), r['created_at'] or '—',
                               r['username'] or ('Legacy referral' if r['referred'] else 'Direct'),
                               r['recipient'] or '—', r['link_code'] or ('YOGESH24' if r['referred'] else '—'),
                               ) + (status('Verified' if r['verified'] else 'Pending', not r['verified']),
                                    str(r['points'] if r['verified'] and r['referred'] else 0)) +
                         ('—' if r['verified'] else action('id', r['id'], 'Verify'),) for r in registrations))
        rewards = table('Reward requests', ('Member', 'Reward', 'Points', 'Date', 'Status', 'Action'),
                        (cells(r['username'] or 'Legacy', r['reward'], r['points'], r['date']) +
                         (status(r['status'], r['status'] == 'Pending approval'),) +
                         ((action('claim', r['id'], 'Approve') if r['status'] == 'Pending approval' else '—'),)
                         for r in claims))
        reports = {
            'registrations': ('Registrations', registrations, records,
                              'Workshop sign-ups will appear here for verification.'),
            'members': ('Members', users, members,
                        'Members will appear after creating a Circle account.'),
            'shares': ('Referral links', links, shares,
                       'Links will appear when members join or create an invite.'),
            'rewards': ('Reward requests', claims, rewards,
                        'Requests will appear when members redeem their points.'),
        }
        query = parse_qs(urlsplit(self.path).query)
        current = query.get('view', ['registrations'])[0]
        if current not in reports:
            current = 'registrations'
        title, rows, report, empty_message = reports[current]
        links_hint = ('<p class="hint">Shares count copy and WhatsApp clicks. Opens may include repeat visits.</p>'
                      if current == 'shares' else '')
        search = ('<label class="search" for="report-search">Search this report'
                  '<input type="search" id="report-search" placeholder="Search rows"></label>') if rows else ''
        content = (report + '<div class="empty" id="no-results" role="status" hidden>No matching rows.</div>'
                   if rows else f'<div class="empty"><strong>No {title.lower()} yet</strong><p>{empty_message}</p></div>')
        panel = (f'<section class="panel" aria-labelledby="report-title"><div class="panel-head">'
                 f'<div><h2 id="report-title">{title}</h2>{links_hint}</div>{search}</div>{content}</section>')
        nav = ''.join(
            f'<a class="button" href="/admin?view={key}"'
            f'{" aria-current=page" if key == current else ""}>{item[0]}</a>'
            for key, item in reports.items()
        )
        metrics = ''.join(
            f'<div class="metric"><dt>{title}</dt><dd>{value:,}</dd></div>'
            for title, value in (
                ('Members', len(users)),
                ('Referral links', len(links)),
                ('Pending registrations', sum(not r['verified'] for r in registrations)),
                ('Blessing Points awarded', sum(r['points'] for r in registrations if r['verified'] and r['referred'])),
            )
        )
        feedback = {'verified': 'Registration verified.', 'approved': 'Reward request approved.'}
        message = feedback.get(query.get('result', [''])[0])
        notice = f'<p class="notice" role="status">{message}</p>' if message else ''
        self.send(Template((ROOT / 'admin.html').read_text()).substitute(
            metrics=metrics, nav=nav, notice=notice, panel=panel))

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        query = parse_qs(url.query)
        code = query.get('ref', [''])[0]
        if path == '/api/points' and not self.session():
            return self.json({'status': 'ok'})
        if path in ('/login', '/signup', '/admin/login'):
            return self.auth_page(path)
        if path == '/' and code:
            if not self.referral(code, count=True):
                return self.page('Referral link unavailable', '', 404)
            return self.page('Explore workshops', ''.join(
                f'<section><a href="/w/{slug}?ref={code}&amp;from=invite">{name}</a></section>'
                for slug, (name, _) in WORKSHOPS.items()))
        if path.startswith('/w/') and (slug := path[3:]) in WORKSHOPS:
            if code and not self.referral(code, slug, count=query.get('from') != ['invite']):
                return self.page('Referral link unavailable', '', 404)
            name = workshop_name(slug)
            return self.page(name, f'''<section><p>Reserve your place in this workshop.</p>
            <form method="post" action="/w/{slug}"><input type="hidden" name="ref" value="{code}">
            <label>Full name<input name="name" required maxlength="80" autocomplete="name"></label>
            <label>Mobile with country code<input name="phone" required type="tel" autocomplete="tel" placeholder="+91…"></label>
            <button>Register</button></form></section>''')
        session = self.session()
        if path == '/admin':
            return self.admin_page() if session and session['is_admin'] else self.redirect('/admin/login')
        if path not in ('/', '/api/activity', '/api/points', '/claims', '/redeem'):
            return self.page('Page unavailable', '', 404)
        if session and session['is_admin'] and path == '/':
            return self.redirect('/admin')
        if not session or not session['user_id']:
            return self.json({'error': 'Member login required'}, 401) if path.startswith('/api/') or path == '/claims' else self.redirect('/login')
        user_id = session['user_id']
        if path == '/':
            return self.send((ROOT / 'index.html').read_text())
        if path == '/redeem':
            reward = query.get('reward', [''])[0]
            if reward not in REWARDS:
                return self.page('Unknown reward', '', 404)
            return self.page('Confirm reward request', f'''<section><p>{reward} · {REWARDS[reward]} Blessing Points</p>
                <form method="post"><input type="hidden" name="reward" value="{reward}">
                <input type="hidden" name="nonce" value="{secrets.token_hex(16)}">
                <button>Reserve points and submit request</button></form></section>''')
        with closing(database()) as db:
            claims = db.execute('SELECT reward AS name,points,date,status FROM claims WHERE user_id=? ORDER BY id DESC',
                                (user_id,)).fetchall()
            if path == '/claims':
                return self.json([dict(row) for row in claims])
            records = db.execute('''SELECT r.workshop,r.points,r.verified,r.created_at FROM registrations r
                JOIN links l ON l.code=r.link_code WHERE l.user_id=? ORDER BY r.id DESC''', (user_id,)).fetchall()
            activity = [dict(row) | {'workshop': workshop_name(row['workshop'])} for row in records]
            earned, reserved = totals(db, user_id)
            if path == '/api/points':
                confirmed = sum(row['verified'] for row in records)
                return self.json({'points': earned - reserved, 'confirmed': confirmed, 'pending': len(records) - confirmed})
            links = db.execute('''SELECT l.code,l.recipient,l.workshop,l.opens,l.share_actions,
                COUNT(r.id) AS registrations, COALESCE(SUM(r.points*r.verified),0) AS earned
                FROM links l LEFT JOIN registrations r ON r.link_code=l.code
                WHERE l.user_id=? GROUP BY l.code ORDER BY l.created_at DESC,l.rowid DESC''', (user_id,)).fetchall()
            return self.json({'name': session['name'], 'activity': activity, 'claims': [dict(row) for row in claims],
                              'links': [dict(row) for row in links], 'earned': earned, 'balance': earned - reserved})

    def authenticate(self, path, field, password):
        username = field('username').lower()
        name = field('name')
        signup = path == '/signup'
        admin = path == '/admin/login'
        if not 1 <= len(password) <= 256:
            return self.auth_page(path, 'Check your password.', 400)
        if not admin and not re.fullmatch(r'[a-z0-9_.-]{3,40}', username):
            return self.auth_page(path, 'Enter a valid username.', 400)
        if signup and (len(password) < 12 or not 1 <= len(name) <= 80):
            return self.auth_page(path, 'Enter your name and use at least 12 characters for your password.', 400)

        # Use a key independent of client-supplied forwarding headers.
        rate_key = 'admin' if admin else ('signup' if signup else 'user:' + username)
        limit = 30 if signup else 10
        with closing(database()) as db, db:
            db.execute('DELETE FROM login_attempts WHERE expires<=unixepoch()')
            attempt = db.execute('''INSERT INTO login_attempts VALUES (?,1,unixepoch()+900)
                ON CONFLICT(client) DO UPDATE SET attempts=attempts+1 WHERE attempts<?
                RETURNING attempts''', (rate_key, limit)).fetchone()
            if attempt is None:
                return self.auth_page(path, 'Too many attempts. Try again in 15 minutes.', 429)
            user = None if admin or signup else db.execute(
                'SELECT * FROM users WHERE username=?', (username,)).fetchone()

        if signup:
            salt = secrets.token_hex(16)
            password_hash = hash_password(password, salt)
            try:
                with closing(database()) as db, db:
                    user_id = db.execute('INSERT INTO users(username,name,salt,password_hash) VALUES(?,?,?,?)',
                                         (username, name, salt, password_hash)).lastrowid
                    db.execute('INSERT INTO links(code,user_id) VALUES(?,?)', (secrets.token_urlsafe(12), user_id))
            except sqlite3.IntegrityError:
                return self.auth_page(path, 'That username is unavailable.', 409)
            return self.start_session(user_id)
        if admin:
            valid = hmac.compare_digest(password.encode(), ADMIN.encode())
        else:
            computed = hash_password(password, user['salt'] if user else '00' * 16)
            valid = user is not None and hmac.compare_digest(computed, user['password_hash'])
        if not valid:
            return self.auth_page(path, 'The login details did not match.', 401)
        with closing(database()) as db, db:
            db.execute('DELETE FROM login_attempts WHERE client=?', (rate_key,))
        return self.start_session(user['id'] if user else None, admin)

    def do_POST(self):
        if self.headers.get('Origin') != (PUBLIC_URL or 'http://' + self.headers.get('Host', '')):
            return self.send('Invalid origin', 403)
        size = self.headers.get('Content-Length', '')
        if not size.isdigit() or not 0 < int(size) <= 4096:
            return self.send('Invalid request body', 400)
        fields = parse_qs(self.rfile.read(int(size)).decode(errors='replace'))

        def field(key):
            return fields.get(key, [''])[0].strip()

        path = urlsplit(self.path).path
        if path in ('/login', '/signup', '/admin/login'):
            return self.authenticate(path, field, fields.get('password', [''])[0])
        if path == '/logout':
            with closing(database()) as db, db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(),))
            return self.redirect('/login', self.cookie('', 0))
        if path.startswith('/w/') and path[3:] in WORKSHOPS:
            return self.register(path[3:], field)

        session = self.session()
        if path == '/admin':
            if not session or not session['is_admin']:
                return self.redirect('/admin/login')
            with closing(database()) as db, db:
                if field('claim'):
                    changed = db.execute("UPDATE claims SET status='Approved' WHERE id=? AND status='Pending approval'",
                                         (field('claim'),)).rowcount
                    return_path = '/admin?view=rewards' + ('&result=approved' if changed else '')
                else:
                    changed = db.execute('UPDATE registrations SET verified=1 WHERE id=? AND verified=0',
                                         (field('id'),)).rowcount
                    return_path = '/admin?view=registrations' + ('&result=verified' if changed else '')
            return self.redirect(return_path)
        if path not in ('/api/links', '/api/share', '/redeem'):
            return self.page('Page unavailable', '', 404)
        if not session or not session['user_id']:
            return self.json({'error': 'Member login required'}, 401) if path.startswith('/api/') else self.redirect('/login')
        user_id = session['user_id']
        if path == '/api/links':
            workshop, recipient = field('workshop'), field('recipient')
            if (workshop and workshop not in WORKSHOPS) or not 1 <= len(recipient) <= 80:
                return self.json({'error': 'Enter a recipient label and choose a workshop.'}, 400)
            code = secrets.token_urlsafe(12)
            with closing(database()) as db, db:
                db.execute('INSERT INTO links(code,user_id,workshop,recipient) VALUES(?,?,?,?)',
                           (code, user_id, workshop, recipient))
            return self.json({'code': code, 'workshop': workshop}, 201)
        if path == '/api/share':
            with closing(database()) as db, db:
                changed = db.execute('UPDATE links SET share_actions=share_actions+1 WHERE code=? AND user_id=?',
                                     (field('code'), user_id)).rowcount
            return self.json({'ok': bool(changed)}, 200 if changed else 404)

        reward, nonce = field('reward'), field('nonce')
        if reward not in REWARDS or not re.fullmatch('[a-f0-9]{32}', nonce):
            return self.page('Invalid request', '', 400)
        with closing(database()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            claim = db.execute('SELECT user_id,reward FROM claims WHERE nonce=?', (nonce,)).fetchone()
            if claim and (claim['user_id'] != user_id or claim['reward'] != reward):
                return self.page('Invalid request', '', 409)
            if not claim:
                earned, reserved = totals(db, user_id)
                if earned - reserved < REWARDS[reward]:
                    return self.page('Insufficient Blessing Points', '<p>Your balance has changed.</p>', 409)
                db.execute('INSERT INTO claims(reward,points,nonce,user_id) VALUES(?,?,?,?)',
                           (reward, REWARDS[reward], nonce, user_id))
        return self.redirect('/?reward=requested')

    def register(self, slug, field):
        name = field('name')
        phone = re.sub(r'[ ()-]', '', field('phone'))
        code = field('ref')
        if not 1 <= len(name) <= 80 or not re.fullmatch(r'\+[1-9][0-9]{7,14}', phone):
            return self.page('Check your details', '<p>Enter your name and a mobile number with country code.</p>', 400)
        if code and not self.referral(code, slug):
            return self.page('Referral link unavailable', '', 400)
        try:
            with closing(database()) as db, db:
                db.execute('''INSERT INTO registrations(workshop,name,phone,referred,points,link_code,created_at)
                    VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)''',
                           (slug, name, phone, int(bool(code)), WORKSHOPS[slug][1],
                            code if code and code != LEGACY_CODE else None))
        except sqlite3.IntegrityError:
            return self.page('Registration already received', '<p>This mobile number already has a place reserved.</p>', 409)
        return self.page('Registration received', '<p>Your registration is pending verification.</p>', 201)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '4174'))
    if HOST == '127.0.0.1' and 'CIRCLE_ADMIN_PASSWORD' not in os.environ:
        print('Local admin password: ' + ADMIN, flush=True)
    ThreadingHTTPServer((HOST, port), App).serve_forever()
