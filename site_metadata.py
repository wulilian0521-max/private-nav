"""Read public website metadata without cookies, credentials or AI services."""
import http.client
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit


class MetadataUnavailable(Exception):
    pass


def normalize_url(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 2048:
        raise ValueError('请填写有效的网址。')
    value = value.strip()
    if '://' not in value:
        value = 'https://' + value
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except ValueError:
        valid = False
    if not valid or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('请填写 http:// 或 https:// 网址，不能包含账号或密码。')
    return value


def public_address(host, port):
    # Check every DNS answer, then connect directly to one of those addresses.
    # This also rejects IPv6 mapped loopback, mixed public/private DNS and rebinding.
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast or ipaddress.ip_address(address).is_reserved for address in addresses):
            raise MetadataUnavailable()
    except (ValueError, OSError):
        raise MetadataUnavailable() from None
    return addresses[0]


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts = []
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = True
        if tag == 'meta':
            attrs = dict(attrs)
            key = (attrs.get('property') or attrs.get('name') or '').lower()
            if key in ('og:title', 'og:site_name', 'description', 'og:description'):
                self.meta.setdefault(key, (attrs.get('content') or '')[:1000])

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, value):
        if self.in_title and sum(map(len, self.title_parts)) < 2000:
            self.title_parts.append(value[:2000])


def clean_text(value, limit):
    return re.sub(r'\s+', ' ', ''.join(c for c in value if c.isprintable() or c.isspace())).strip()[:limit]


def read_public_metadata(url):
    deadline = time.monotonic() + 3.5
    for _ in range(4):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            raise MetadataUnavailable()
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port not in (80, 443) or not parsed.hostname:
            raise MetadataUnavailable()
        host = parsed.hostname.encode('idna').decode('ascii')
        address = public_address(host, port)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MetadataUnavailable()
        connection_class = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        conn = connection_class(host, port, timeout=min(1.5, remaining))
        # HTTP Host and TLS SNI/certificate verification still use the original host.
        conn._create_connection = lambda target, timeout, source_address=None: socket.create_connection((address, port), timeout, source_address)
        try:
            # Query strings and fragments may contain access tokens; never fetch them.
            path = urlunsplit(('', '', parsed.path or '/', '', ''))
            conn.request('GET', path, headers={'User-Agent': 'PrivateNav/1.0 (website-title)', 'Accept': 'text/html,application/xhtml+xml', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location', '')
                if not location:
                    raise MetadataUnavailable()
                url = normalize_url(urljoin(url, location))
                continue
            content_type = response.getheader('Content-Type', '').lower()
            if response.status != 200 or not any(kind in content_type for kind in ('text/html', 'application/xhtml+xml')):
                raise MetadataUnavailable()
            chunks, size = [], 0
            while size < 262144:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MetadataUnavailable()
                if conn.sock:
                    conn.sock.settimeout(min(1.5, remaining))
                chunk = response.read1(min(32768, 262144 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if b'</head' in b''.join(chunks).lower():
                    break
            raw = b''.join(chunks)
            charset = re.search(r'charset\s*=\s*[\"\']?([\w-]+)', content_type)
            if not charset:
                charset = re.search(r'charset\s*=\s*[\"\']?([\w-]+)', raw[:2048].decode('ascii', errors='ignore'), re.I)
            try:
                html = raw.decode(charset.group(1) if charset else 'utf-8', errors='replace')
            except LookupError:
                html = raw.decode('utf-8', errors='replace')
            page = PageMetadata()
            page.feed(html)
            title = page.meta.get('og:title') or ''.join(page.title_parts) or page.meta.get('og:site_name', '')
            title = clean_text(title, 100)
            if not title or re.search(r'just a moment|access denied|attention required|403 forbidden|404 not found|502 bad gateway|安全验证|人机验证', title, re.I) or re.fullmatch(r'sign in|log ?in|登录|登入', title, re.I):
                raise MetadataUnavailable()
            return title, clean_text(page.meta.get('description') or page.meta.get('og:description', ''), 300)
        except (OSError, http.client.HTTPException, UnicodeError):
            raise MetadataUnavailable() from None
        finally:
            conn.close()
    raise MetadataUnavailable()


KNOWN_SITES = {
    'my.vultr.com': ('Vultr 服务器控制台', '服务器'),
    'vultr.com': ('Vultr', '服务器'),
    'dynadot.com': ('Dynadot 域名管理', '开发工具'),
    'cloudflare.com': ('Cloudflare', '服务器'),
    'github.com': ('GitHub', '开发工具'),
    'gitlab.com': ('GitLab', '开发工具'),
    'vercel.com': ('Vercel', '开发工具'),
    'netlify.com': ('Netlify', '开发工具'),
    'console.cloud.google.com': ('Google Cloud 控制台', '服务器'),
    'console.aws.amazon.com': ('AWS 控制台', '服务器'),
    'platform.openai.com': ('OpenAI 开发者平台', 'API 管理'),
    'chatgpt.com': ('ChatGPT', '其他'),
}


def recommend_category(text):
    groups = [
        ('CLI 管理', r'\bcli\b|命令行|终端|shell|terminal'),
        ('API 管理', r'\bapi\b|new[ -]?api|one[ -]?api|接口|令牌|token|swagger'),
        ('服务器', r'server|服务器|监控|monitor|vps|主机|grafana|prometheus|portainer|宝塔|1panel|云控制台'),
        ('开发工具', r'github|gitlab|开发|developer|文档|docs|vercel|netlify|域名|domain|代码|code|管理后台'),
    ]
    return next((category for category, pattern in groups if re.search(pattern, text, re.I)), '其他')


def describe_site(value, saved=()):
    url = normalize_url(value)
    parsed = urlsplit(url)
    key = (parsed.netloc.lower(), parsed.path.rstrip('/'))
    for item in saved:
        known = urlsplit(item['url'])
        if (known.netloc.lower(), known.path.rstrip('/')) == key:
            return {'url': url, 'title': item['title'], 'category': item['category'], 'source': 'saved'}
    host = parsed.hostname.lower()
    friendly_host = host.removeprefix('www.')
    known_site = next((details for domain, details in KNOWN_SITES.items() if friendly_host == domain or friendly_host.endswith('.' + domain)), None)
    title = known_site[0] if known_site else parsed.netloc
    text = host + ' ' + parsed.path
    category = known_site[1] if known_site else recommend_category(text)
    source, description = 'address', ''
    try:
        page_title, description = read_public_metadata(url)
        title = page_title
        detected = recommend_category(text + ' ' + title + ' ' + description)
        category = detected if detected != '其他' else category
        source = 'page'
    except (MetadataUnavailable, ValueError, UnicodeError):
        pass
    return {'url': url, 'title': clean_text(title, 100), 'category': category, 'source': source, 'description': description}
