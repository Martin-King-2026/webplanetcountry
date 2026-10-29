"""Статичная версия сайта для GitHub Pages: каждая страница — готовый HTML-файл, ограничений Blogger на число
публикаций нет. Содержимое страниц то же, что у постов Blogger (его делает build_posts.py), оформление — тема.
Ленты Blogger, по которым скрипт темы строит списки, заменены JSON-файлами в feeds/.

Служебные камеры (кухни, стройки…) в статичный сайт не попадают.

Запуск: py tools/build_static.py [--out build/static] [--prefix /] [--site-url https://логин.github.io/]
На GitHub сборку запускает .github/workflows/pages.yml сам, с адресом репозитория.
"""
import argparse
import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_posts as B  # noqa: E402

ROOT = B.ROOT
SRC = ROOT / "theme" / "src"

PAGE = """<!DOCTYPE html>
<html lang="ru" data-lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script>(function(d){{try{{var t=localStorage.getItem('wp-theme'),l=localStorage.getItem('wp-lang'),q=/[?&]lang=(en|ru)/.exec(location.search);if(t)d.setAttribute('data-theme',JSON.parse(t));l=q?q[1]:(l?JSON.parse(l):'ru');d.setAttribute('data-lang',l);d.lang=l}}catch(e){{}}}})(document.documentElement);</script>
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website"><meta property="og:title" content="{title}"><meta property="og:description" content="{desc}"><meta property="og:url" content="{url}">
<meta name="theme-color" content="#4353FF">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400..700&amp;display=swap">
<link rel="stylesheet" href="{p}assets/style.css?v={v}"></head><body>
<div class="wrap"><aside class="side">
<div class="side-top"><a class="brand" href="{p}"><span class="dot">&#8203;</span><span><span class="ru">Веб-камеры планеты</span><span class="en">Webcams of the Planet</span></span></a>
<button class="btn burger" data-i18n-label="menu" type="button"><svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button></div>
<div class="side-body">
<div class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg><input autocomplete="off" data-i18n-ph="search" placeholder="Страна, город или камера" type="search"></div>
<div class="menu section" id="menu">{menu}</div>
<div class="tools-row"><button class="btn" id="wp-fav-btn" type="button"><svg viewBox="0 0 24 24"><path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/></svg><span data-i18n="fav">Избранное</span></button>
<button class="btn" data-i18n-label="theme" id="wp-theme" type="button"><svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg></button>
<button class="btn" data-i18n-label="lang" id="wp-lang" type="button">EN</button></div>
<ul class="favs" hidden id="wp-favs"><li>&#8203;</li></ul></div></aside>
<main class="main"><div class="main-sec section" id="main">{main}</div></main></div>
<footer class="foot"><span class="ru">© Веб-камеры планеты. Тексты — Википедия и Викигид (CC BY-SA), погода — Open-Meteo, карты — OpenStreetMap. Трансляции принадлежат их владельцам и встроены с их платформ.</span><span class="en">© Webcams of the Planet. Texts: Wikipedia and Wikivoyage (CC BY-SA), weather: Open-Meteo, maps: OpenStreetMap. Streams belong to their owners and are embedded from their platforms.</span></footer>
<script>window.WP_CONFIG = {{ windyKey: '', static: true, base: '{p}' }};</script>
<script src="{p}assets/app.js?v={v}"></script>{counter}</body></html>"""
# Счётчик посещений GoatCounter: без cookie, поэтому баннер согласия не нужен
COUNTER = '\n<script data-goatcounter="https://{code}.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>'


def describe(post):
    """Описание страницы для поисковиков — по типу страницы и её заголовку."""
    ru = post["title"].split(" | ")[0]
    labels = post["labels"]
    if "cam" in labels:
        cam, _, city = ru.rpartition(" — ")
        return f"Веб-камера «{cam}», {city}: прямая трансляция онлайн, местное время и погода."
    if "city" in labels:
        return f"{ru}: веб-камеры онлайн, местное время и погода, достопримечательности и карта."
    if "country" in labels:
        return f"{ru}: веб-камеры городов страны онлайн, карта, госустройство и описание для туристов."
    return f"{ru}. Веб-камеры городов мира: прямые трансляции, погода и карты."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "build" / "static"))
    ap.add_argument("--prefix", default="/", help="путь сайта: / для логин.github.io, /имя-репозитория/ для остальных")
    ap.add_argument("--site-url", default="", help="полный адрес сайта для карты сайта, например https://логин.github.io/")
    ap.add_argument("--goatcounter", default=os.environ.get("GOATCOUNTER_CODE", ""),
                    help="код сайта в GoatCounter (код.goatcounter.com) — включает счётчик посещений")
    args = ap.parse_args()
    code = args.goatcounter.strip().lower()
    counter = COUNTER.format(code=code) if re.fullmatch(r"[a-z0-9-]+", code) else ""
    p = args.prefix if args.prefix.endswith("/") else args.prefix + "/"
    base = (args.site_url or p).rstrip("/") + "/"
    out = pathlib.Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / "feeds").mkdir()

    site = B.Site(p, sights_limit=0)  # фото достопримечательностей — только из кэша, без запросов к Викиданным
    first, second, rest, _private = site.all_batches()  # служебные камеры — только в Blogger-черновиках
    posts = first + second + rest
    menu = (SRC / "menu.html").read_text(encoding="utf-8").replace("@@P@@", p)
    js = (SRC / "app.js").read_text(encoding="utf-8").replace("/*@@MENU_JS@@*/''", json.dumps(menu, ensure_ascii=False))
    css = (SRC / "style.css").read_text(encoding="utf-8")
    (out / "assets" / "app.js").write_text(js, encoding="utf-8")
    (out / "assets" / "style.css").write_text(css, encoding="utf-8")
    v = hashlib.md5((js + css).encode()).hexdigest()[:8]  # новая версия файлов — браузеры не берут старые из кэша

    def write(name, title, main, desc, url):
        (out / name).write_text(PAGE.format(title=html.escape(title), desc=html.escape(desc), url=url, p=p, v=v,
                                            menu=menu, main=main, counter=counter), encoding="utf-8")

    urls = [base]
    for post in posts:
        t = html.escape(post["title"])
        ru = post["title"].split(" | ")[0]
        write(f'{post["slug"]}.html', f"{ru} — Веб-камеры планеты", f'<article class="post"><h1 class="post-title" data-title="{t}">{t}</h1>'
              f'<div class="post-body">{post["content"]}</div></article>', describe(post), f'{base}{post["slug"]}.html')
        urls.append(f'{base}{post["slug"]}.html')
    write("index.html", "Веб-камеры планеты", '<div class="post"><div class="wp" data-t="home"><p class="ld">…</p></div></div>',
          "Прямые трансляции с веб-камер городов мира: страны, города, карты, погода и местное время.", base)
    write("search.html", "Поиск — Веб-камеры планеты", '<article class="post"><h1 class="post-title" data-title="Поиск | Search">Поиск</h1>'
          '<div class="post-body"><div class="wp" data-t="search"></div></div></article>', "Поиск страны, города или веб-камеры.", f"{base}search.html")
    write("404.html", "Страница не найдена — Веб-камеры планеты", '<div class="post"><h1 data-i18n="notFound">Страница не найдена</h1>'
          f'<p><a href="{p}">← <span class="ru">На главную</span><span class="en">Home</span></a></p></div>', "Страница не найдена.", base)

    # Ленты: индекс (страны и все камеры), новые камеры, камеры каждого города. Формат — как у feed() в скрипте темы
    def entry(post, body=None):
        return {"title": post["title"], "href": f'{p}{post["slug"]}.html', "html": post["content"] if body is None else body}

    def save(label, entries):
        (out / "feeds" / f"{label}.json").write_text(json.dumps({"entries": entries}, ensure_ascii=False, separators=(",", ":")),
                                                     encoding="utf-8")

    save("wp-index", [entry(x) for x in posts if "wp-index" in x["labels"]])
    cam_posts = [x for x in posts if "cam" in x["labels"]]
    by_city = {}
    for x in cam_posts:
        # для списка камер города хватает списка трансляций: по нему берутся превью YouTube и Rutube
        src = x["content"][x["content"].find('<ol class="wp-src">'):x["content"].find("</ol>") + 5]
        by_city.setdefault(x["labels"][1], []).append(entry(x, src))
    for label, entries in by_city.items():
        save(label, entries)
    with_thumb = [x for x in cam_posts if 'data-type="yt"' in x["content"]]
    save("cam", [entry(x, x["content"][x["content"].find('<ol class="wp-src">'):x["content"].find("</ol>") + 5])
                 for x in with_thumb[-12:][::-1]])

    # Топ по просмотрам: data/top.json готовит tools/fetch_top.py из счётчика GoatCounter (в сборке на GitHub)
    top_f = ROOT / "data" / "top.json"
    if top_f.exists():
        by_slug = {x["slug"]: x for x in posts if {"cam", "city", "country"} & set(x["labels"])}
        top = json.loads(top_f.read_text(encoding="utf-8"))
        for period in ("week", "all"):
            items, seen = [], set()
            for hit in top.get(period, []):
                slug = hit["path"].split("?")[0].rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")
                if slug in by_slug and slug not in seen:
                    seen.add(slug)
                    items.append(entry(by_slug[slug], ""))
            save(f"top-{period}", items[:10])

    # Карта сайта и robots.txt для поисковиков
    sm = ["<?xml version='1.0' encoding='UTF-8'?>", "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"]
    sm += [f"<url><loc>{html.escape(u)}</loc></url>" for u in urls]
    sm.append("</urlset>")
    (out / "sitemap.xml").write_text("\n".join(sm), encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # GitHub Pages отдаёт файлы как есть, без обработки Jekyll

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"Страниц: {len(posts) + 3}, лент: {len(by_city) + 2}, камер на сайте: {len(cam_posts)}, размер: {size:.0f} МБ → {out}")


if __name__ == "__main__":
    main()
