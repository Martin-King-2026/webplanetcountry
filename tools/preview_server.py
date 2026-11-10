"""Локальный предпросмотр сайта без Blogger.

Отдаёт посты из build/preview/posts.json в оболочке, повторяющей разметку темы,
и имитирует ленты Blogger (/feeds/posts/default/-/метка?alt=json), чтобы работали списки и поиск.
Запуск: py tools/preview_server.py [--port 8765]
"""
import argparse
import html
import json
import pathlib
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
PREFIX = "/2026/10/"

SHELL = """<!DOCTYPE html>
<html lang="ru" data-lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script>(function(d){{try{{var t=localStorage.getItem('wp-theme'),l=localStorage.getItem('wp-lang'),q=/[?&]lang=(en|ru)/.exec(location.search);if(t)d.setAttribute('data-theme',JSON.parse(t));l=q?q[1]:(l?JSON.parse(l):'ru');d.setAttribute('data-lang',l);d.lang=l}}catch(e){{}}}})(document.documentElement);</script>
<title>{title} — предпросмотр</title>
<link href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400..700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/theme/src/style.css">{head}</head><body>
<div class="wrap"><aside class="side">
<div class="side-top"><a class="brand" href="/"><span class="dot">&#8203;</span><span><span class="ru">Веб-камеры планеты</span><span class="en">Webcams of the Planet</span></span></a>
<button class="btn burger" data-i18n-label="menu" type="button"><svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button></div>
<div class="side-body">
<div class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg><input autocomplete="off" data-i18n-ph="search" placeholder="Страна, город или камера" type="search"></div>
{menu}
<div class="tools-row"><button class="btn" id="wp-fav-btn" type="button"><svg viewBox="0 0 24 24"><path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/></svg><span data-i18n="fav">Избранное</span></button>
<button class="btn" data-i18n-label="theme" id="wp-theme" type="button"><svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg></button>
<button class="btn" data-i18n-label="lang" id="wp-lang" type="button">EN</button></div>
<ul class="favs" hidden id="wp-favs"><li>&#8203;</li></ul></div></aside>
<main class="main">{main}</main></div>
<div id="popular"><div id="PopularPosts1">{pop1}</div><div id="PopularPosts2">{pop2}</div></div>
<footer class="foot"><span class="ru">Локальный предпросмотр.</span><span class="en">Local preview.</span></footer>
<script>window.WP_CONFIG = {{ windyKey: '' }};</script>
<script src="/theme/src/app.js"></script></body></html>"""


class Store:
    def __init__(self):
        self.posts = json.loads((ROOT / "build" / "preview" / "posts.json").read_text(encoding="utf-8"))
        self.by_slug = {p["slug"]: p for p in self.posts}
        self.menu = (ROOT / "theme" / "src" / "menu.html").read_text(encoding="utf-8").replace("@@P@@", PREFIX)

    def page(self, title, main, blank_id=None):
        """blank_id — страница как её сейчас отдаёт Blogger без виджетов: пустые секции, номер поста только в заголовке."""
        cams = [p for p in self.posts if "cam" in p["labels"]][:8]
        pop = "".join(f'<a href="{PREFIX}{p["slug"]}.html" data-title="{html.escape(p["title"])}">{html.escape(p["title"])}</a>' for p in cams)
        if blank_id is not None:
            head = (f'<link rel="alternate" type="application/atom+xml" href="/feeds/{blank_id}/comments/default">'
                    f"<script>var _blogger = {{'pageType': 'item', 'postId': '{blank_id}'}};</script>")
            return SHELL.format(title=html.escape(title), head=head, menu='<div class="menu no-items section" id="menu"></div>',
                                main='<div class="main-sec no-items section" id="main"></div>', pop1="", pop2="")
        menu = f'<div class="menu section" id="menu"><div class="widget HTML" id="HTML1">{self.menu}</div></div>'
        return SHELL.format(title=html.escape(title), head="", menu=menu, main=main, pop1=pop, pop2=pop)

    def entry(self, p, host):
        return {"title": {"$t": p["title"]}, "content": {"$t": p["content"]},
                "link": [{"rel": "alternate", "href": f"http://{host}{PREFIX}{p['slug']}.html"}]}

    def feed(self, label, maxn, host):
        items = [p for p in self.posts if not label or label in p["labels"]]
        items = sorted(items, key=lambda p: -p["n"])[:maxn]
        entry = [self.entry(p, host) for p in items]
        return {"feed": {"entry": entry, "openSearch$totalResults": {"$t": str(len(entry))}}}


class Handler(BaseHTTPRequestHandler):
    store = None

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 — имя задано http.server
        u = urllib.parse.urlparse(self.path)
        path, qs = urllib.parse.unquote(u.path), urllib.parse.parse_qs(u.query)
        s = self.store
        if path.startswith("/theme/src/"):
            f = ROOT / path.lstrip("/")
            if f.is_file():
                if f.suffix == ".js":  # меню подставляется в скрипт так же, как при сборке темы
                    js = f.read_text(encoding="utf-8").replace("/*@@MENU_JS@@*/''", json.dumps(s.menu, ensure_ascii=False))
                    return self.send(200, js, "application/javascript; charset=utf-8")
                return self.send(200, f.read_bytes(), "text/css; charset=utf-8")
            return self.send(404, "нет файла")
        m = re.match(r"^/feeds/posts/default/(\d+)$", path)  # один пост по номеру, как в Blogger
        if m:
            i = int(m.group(1))
            if i >= len(s.posts):
                return self.send(404, "{}", "application/json; charset=utf-8")
            return self.send(200, json.dumps({"entry": s.entry(s.posts[i], self.headers.get("Host"))}, ensure_ascii=False),
                             "application/json; charset=utf-8")
        m = re.match(r"^/feeds/posts/(?:default|summary)(?:/-/([^/]+))?$", path)
        if m:
            data = s.feed(m.group(1), int(qs.get("max-results", ["25"])[0]), self.headers.get("Host"))
            return self.send(200, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")
        if path == "/":
            return self.send(200, s.page("Главная", '<div class="post"><div class="wp" data-t="home"><p class="ld">…</p></div></div>'))
        if path == "/search":
            q = qs.get("q", [""])[0].lower()
            hits = [p for p in s.posts if q and q in p["title"].lower()][:50]
            lis = "".join(f'<li><a href="{PREFIX}{p["slug"]}.html" data-title="{html.escape(p["title"])}">{html.escape(p["title"])}</a></li>' for p in hits)
            return self.send(200, s.page("Поиск", f'<div class="post"><h1>Поиск: {html.escape(q)}</h1><ul class="plist">{lis}</ul></div>'))
        m = re.match(r"^" + re.escape(PREFIX) + r"([a-z0-9-]+)\.html$", path)
        if m and m.group(1) in s.by_slug:
            p = s.by_slug[m.group(1)]
            if qs.get("blank"):  # ?blank=1 — проверка запасного вывода страницы скриптом
                return self.send(200, s.page(p["title"], "", blank_id=s.posts.index(p)))
            t = html.escape(p["title"])
            return self.send(200, s.page(p["title"], f'<article class="post"><h1 class="post-title" data-title="{t}">{t}</h1>'
                                                     f'<div class="post-body">{p["content"]}</div></article>'))
        return self.send(404, s.page("404", '<div class="post"><h1 data-i18n="notFound">Страница не найдена</h1>'
                                            '<p>В предпросмотре есть не все города — только образцы.</p></div>'))

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    Handler.store = Store()
    print(f"Предпросмотр: http://localhost:{args.port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
