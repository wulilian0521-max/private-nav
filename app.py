#!/usr/bin/env python3
"""Private, two-browser bookmark dashboard. Python standard library only."""
import argparse
import contextlib
import hashlib
import http.cookies
import json
import os
import secrets
import sqlite3
import threading
import time
import webbrowser
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from site_metadata import describe_site
from site_ai import enabled as ai_enabled, suggest_name
from site_search import search_bookmarks

ROOT = Path(__file__).resolve().parent
CATEGORIES = ['服务器', 'API 管理', 'CLI 管理', '开发工具', '其他']
COOKIE = 'private_nav_device'
METADATA_SLOTS = threading.BoundedSemaphore(2)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
        self.path = path / 'navigation.sqlite3'
        self.lock = threading.RLock()
        self.attempts = defaultdict(deque)
        self.setup_code = secrets.token_urlsafe(24)
        self.setup_expires = time.time() + 600
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS devices (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                  created REAL NOT NULL, last_seen REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS pairs (
                  hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS bookmarks (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
                  category TEXT NOT NULL, note TEXT NOT NULL, pinned INTEGER NOT NULL,
                  created REAL NOT NULL);
            ''')
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def devices(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT id,name,created,last_seen FROM devices ORDER BY created')]

    def authenticate(self, token):
        if not token:
            return None
        with self.db() as db:
            row = db.execute('SELECT id,name FROM devices WHERE token_hash=? AND created>?', (digest(token), time.time() - 31536000)).fetchone()
            if row:
                db.execute('UPDATE devices SET last_seen=? WHERE id=?', (time.time(), row['id']))
                return dict(row)
        return None

    def pair(self, code, name):
        with self.lock:
            now = time.time()
            attempts = self.attempts['pair']
            while attempts and now - attempts[0] > 60:
                attempts.popleft()
            if len(attempts) >= 8:
                raise ValueError('尝试次数较多，请一分钟后重试。')
            attempts.append(now)
            with self.db() as db:
                count = db.execute('SELECT COUNT(*) FROM devices').fetchone()[0]
                if count >= 2:
                    raise ValueError('已有两台浏览器获得授权，请先在已授权设备上移除旧设备。')
                initial = count == 0 and now < self.setup_expires and secrets.compare_digest(code, self.setup_code)
                row = db.execute('SELECT hash FROM pairs WHERE hash=? AND expires>?', (digest(code), now)).fetchone()
                if not initial and not row:
                    raise ValueError('配对码无效或已过期，请重新生成。')
                token = secrets.token_urlsafe(32)
                device_id = secrets.token_hex(12)
                db.execute('INSERT INTO devices VALUES (?,?,?,?,?)', (device_id, name, digest(token), now, now))
                db.execute('DELETE FROM pairs')
                self.setup_expires = 0
                return token


def clean_bookmark(data):
    def field(name, limit, default=''):
        value = data.get(name, default)
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('内容过长或格式不正确。')
        return value.strip()
    title = field('title', 100)
    url = field('url', 2048)
    category = field('category', 40, '其他') or '其他'
    if category in ('全部网址', '置顶'):
        raise ValueError('「全部网址」和「置顶」是系统筛选项，请使用其他分类名称。')
    note = field('note', 500)
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except ValueError:
        valid = False
    if not title or not valid or any(c.isspace() for c in url) or any(ord(c) < 32 for c in url):
        raise ValueError('请填写名称和完整的 http:// 或 https:// 网址；网址中不能包含用户名或密码。')
    if not isinstance(data.get('pinned', False), bool):
        raise ValueError('置顶格式不正确。')
    return (title, url, category, note, int(data.get('pinned', False)))


class Handler(BaseHTTPRequestHandler):
    server_version = 'PrivateNav'

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, fmt, *args):
        # Never write URL parameters, pairing codes, cookies or bookmark data to logs.
        pass

    @property
    def store(self):
        return self.server.store

    def reply(self, status, body, content_type='application/json; charset=utf-8', cookie=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            secure = '' if self.headers.get('Host', '').split(':')[0] in ('127.0.0.1', 'localhost') else '; Secure'
            self.send_header('Set-Cookie', COOKIE + '=' + cookie + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000' + secure)
        self.end_headers()
        self.wfile.write(body)

    def device(self):
        try:
            jar = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
            token = jar[COOKIE].value if COOKIE in jar else ''
        except http.cookies.CookieError:
            return None
        return self.store.authenticate(token)

    def valid_host(self):
        try:
            host = urlsplit('//' + self.headers.get('Host', '')).hostname
        except ValueError:
            return False
        domain = os.environ.get('NAV_DOMAIN', '').strip().lower()
        return host in ('localhost', '127.0.0.1') or bool(host and host.endswith('.ts.net')) or bool(domain and host == domain)

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'error': '不允许的访问地址。'})
        path = urlsplit(self.path).path
        static = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/icon.svg': ('icon.svg', 'image/svg+xml'), '/apple-touch-icon.png': ('apple-touch-icon.png', 'image/png')}
        if path in static:
            filename, mime = static[path]
            return self.reply(200, (ROOT / 'web' / filename).read_bytes(), mime)
        device = self.device()
        if not device:
            return self.reply(401, {'error': '请先授权此浏览器。'})
        if path == '/api/state':
            with self.store.db() as db:
                bookmarks = [dict(r) for r in db.execute('SELECT * FROM bookmarks ORDER BY pinned DESC,created DESC')]
            return self.reply(200, {'bookmarks': bookmarks, 'devices': self.store.devices(), 'currentDevice': device['id'], 'categories': CATEGORIES, 'aiNamingEnabled': bool(ai_enabled())})
        return self.reply(404, {'error': '页面不存在。'})

    def do_POST(self):
        if not self.valid_host():
            return self.reply(403, {'error': '不允许的访问地址。'})
        host = self.headers.get('Host', '')
        local = host.split(':')[0] in ('127.0.0.1', 'localhost')
        expected = ('http://' if local else 'https://') + host
        if self.headers.get('Origin') != expected:
            return self.reply(403, {'error': '来源验证失败，请刷新页面。'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': '仅支持 JSON 请求。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 * 1024:
                return self.reply(413, {'error': '数据大小不正确（最大 1 MB）。'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('数据格式不正确。')
        except (ValueError, UnicodeError):
            return self.reply(400, {'error': '数据格式不正确。'})
        path = urlsplit(self.path).path
        try:
            if path == '/api/pair':
                code, name = data.get('code', ''), data.get('name', '')
                if not isinstance(code, str) or len(code) > 200 or not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                    raise ValueError('请填写配对码和设备名称。')
                token = self.store.pair(code.strip(), name.strip())
                return self.reply(200, {'ok': True}, cookie=token)
            device = self.device()
            if not device:
                return self.reply(401, {'error': '请先授权此浏览器。'})
            if path == '/api/bookmarks/search':
                query = data.get('query')
                if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
                    raise ValueError('请用300字以内描述你想找的网址。')
                with self.store.lock:
                    now = time.time()
                    attempts = self.store.attempts['search:' + device['id']]
                    while attempts and now - attempts[0] > 60:
                        attempts.popleft()
                    if len(attempts) >= 12:
                        return self.reply(429, {'error': 'AI 查找次数较多，请稍后再试，仍可使用关键词搜索。'})
                    attempts.append(now)
                if not METADATA_SLOTS.acquire(blocking=False):
                    return self.reply(429, {'error': 'AI 正在处理其他请求，请稍后重试。'})
                try:
                    with self.store.db() as db:
                        bookmarks = [dict(row) for row in db.execute('SELECT id,title,url,category,note FROM bookmarks ORDER BY pinned DESC,created DESC')]
                    return self.reply(200, search_bookmarks(query.strip(), bookmarks))
                finally:
                    METADATA_SLOTS.release()
            if path == '/api/bookmarks/describe':
                with self.store.lock:
                    now = time.time()
                    attempts = self.store.attempts['metadata:' + device['id']]
                    while attempts and now - attempts[0] > 60:
                        attempts.popleft()
                    if len(attempts) >= 20:
                        return self.reply(429, {'error': '识别次数较多，请稍后再试，也可以手动填写。'})
                    attempts.append(now)
                if not METADATA_SLOTS.acquire(blocking=False):
                    return self.reply(429, {'error': '正在识别其他网址，请稍后再试。'})
                try:
                    with self.store.db() as db:
                        saved = [dict(row) for row in db.execute('SELECT title,url,category FROM bookmarks')]
                    categories = list(dict.fromkeys(CATEGORIES + [item['category'] for item in saved]))
                    return self.reply(200, suggest_name(describe_site(data.get('url'), saved), categories))
                finally:
                    METADATA_SLOTS.release()
            if path == '/api/pair-code':
                with self.store.lock, self.store.db() as db:
                    if db.execute('SELECT COUNT(*) FROM devices').fetchone()[0] >= 2:
                        raise ValueError('已授权两台浏览器；如需更换，请先移除旧设备。')
                    code = secrets.token_urlsafe(24)
                    db.execute('DELETE FROM pairs')
                    db.execute('INSERT INTO pairs VALUES (?,?)', (digest(code), time.time() + 600))
                return self.reply(200, {'code': code})
            if path == '/api/devices/remove':
                device_id = data.get('id')
                if not isinstance(device_id, str):
                    raise ValueError('设备编号不正确。')
                if device_id == device['id']:
                    raise ValueError('请在另一台已授权设备上移除此浏览器。')
                with self.store.lock, self.store.db() as db:
                    db.execute('DELETE FROM devices WHERE id=?', (device_id,))
                    db.execute('DELETE FROM pairs')
                return self.reply(200, {'ok': True})
            if path == '/api/bookmarks/save':
                values = clean_bookmark(data)
                bookmark_id = data.get('id')
                if bookmark_id is not None and not isinstance(bookmark_id, str):
                    raise ValueError('网址编号不正确。')
                with self.store.db() as db:
                    if bookmark_id:
                        result = db.execute('UPDATE bookmarks SET title=?,url=?,category=?,note=?,pinned=? WHERE id=?', values + (bookmark_id,))
                        if not result.rowcount:
                            raise ValueError('网址已被删除，请刷新页面。')
                    else:
                        db.execute('INSERT INTO bookmarks VALUES (?,?,?,?,?,?,?)', (secrets.token_hex(12),) + values + (time.time(),))
                return self.reply(200, {'ok': True})
            if path == '/api/bookmarks/delete':
                if not isinstance(data.get('id'), str):
                    raise ValueError('网址编号不正确。')
                with self.store.db() as db:
                    db.execute('DELETE FROM bookmarks WHERE id=?', (data['id'],))
                return self.reply(200, {'ok': True})
            if path == '/api/import':
                items = data.get('bookmarks')
                if not isinstance(items, list) or len(items) > 1000:
                    raise ValueError('一次最多导入 1000 个网址。')
                if not all(isinstance(item, dict) for item in items):
                    raise ValueError('导入文件格式不正确。')
                validated = [clean_bookmark(item) for item in items]
                added = 0
                with self.store.db() as db:
                    for values in validated:
                        if not db.execute('SELECT 1 FROM bookmarks WHERE url=?', (values[1],)).fetchone():
                            db.execute('INSERT INTO bookmarks VALUES (?,?,?,?,?,?,?)', (secrets.token_hex(12),) + values + (time.time(),))
                            added += 1
                return self.reply(200, {'ok': True, 'added': added})
            return self.reply(404, {'error': '接口不存在。'})
        except ValueError as error:
            return self.reply(400, {'error': str(error)})
        except sqlite3.Error:
            return self.reply(500, {'error': '数据暂时无法保存，请稍后重试。'})


def main():
    parser = argparse.ArgumentParser(description='私人网址导航，仅监听本机。')
    parser.add_argument('action', nargs='?', default='serve', choices=['serve', 'devices', 'revoke'])
    parser.add_argument('device_id', nargs='?')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--bind', default='127.0.0.1', choices=['127.0.0.1', '0.0.0.0'], help='容器内使用 0.0.0.0；通过 HTTPS 反向代理访问。')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'private-data')
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    domain = os.environ.get('NAV_DOMAIN', '').strip().lower()
    if domain:
        import re
        if len(domain) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', domain):
            parser.error('NAV_DOMAIN 必须是完整域名，例如 nav.example.com，不包含协议、端口或路径。')
    store = Store(args.data_dir)
    if args.action == 'devices':
        print(json.dumps(store.devices(), ensure_ascii=False, indent=2))
        return
    if args.action == 'revoke':
        if not args.device_id:
            parser.error('请指定 devices 列出的设备 id。')
        with store.db() as db:
            db.execute('DELETE FROM devices WHERE id=?', (args.device_id,))
            db.execute('DELETE FROM pairs')
        print('已移除指定设备的授权。重新运行导航页后可再次配对。')
        return
    try:
        server = ThreadingHTTPServer((args.bind, args.port), Handler)
    except OSError:
        parser.exit(1, '启动失败：端口被占用。若导航页已在运行，请打开原有页面。\n')
    server.store = store
    address = 'https://' + domain if domain else 'http://127.0.0.1:%s' % args.port
    print('\n私人导航已启动：' + address, flush=True)
    if not store.devices():
        print('\n首次配对码（10 分钟有效，仅输入你自己的导航页）：\n' + store.setup_code + '\n', flush=True)
    print('服务运行中。\n', flush=True)
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open('http://127.0.0.1:%s' % args.port)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
