"""Генератор постов для импорта в Blogger.

Читает data/countries.json, data/cities.json (их готовит prepare_data.py) и data/cameras.json,
пишет файлы импорта в import/ и подборку постов для локального предпросмотра в build/preview/.

Запуск: py tools/build_posts.py [--format classic|takeout] [--batch 500] [--prefix /2026/10/]
"""
import argparse
import hashlib
import html
import json
import pathlib
import re
import shutil
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BLOG_ID = "7135594099619710266"
BLOG_URL = "https://webplanetcountry.blogspot.com"

CONTINENTS = {"AF": ("Африка", "Africa"), "AS": ("Азия", "Asia"), "EU": ("Европа", "Europe"),
              "NA": ("Северная Америка", "North America"), "OC": ("Океания", "Oceania"),
              "SA": ("Южная Америка", "South America"), "AN": ("Антарктида", "Antarctica")}
DISPUTED = {"XK": ("Частично признанное государство", "Partially recognised state"),
            "TW": ("Частично признанное государство", "Partially recognised state"),
            "PS": ("Частично признанное государство", "Partially recognised state"),
            "EH": ("Спорная территория", "Disputed territory")}
RU_LETTERS = "АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ"
TRANSLIT = dict(zip(RU_LETTERS, "a b v g d e zh z i y k l m n o p r s t u f kh ts ch sh shch eh yu ya".split()))

esc = html.escape
# Внутри HTML-комментария нельзя писать сами знаки комментария, иначе он закроется раньше времени
HIDDEN_NOTE = ("<!-- Города без камер скрыты комментариями. Чтобы показать город, удалите открывающий и закрывающий "
               "знаки комментария в начале и в конце его строки. -->")


# Нетуристические камеры (кухни, стройки, дворы, мойки, пункты выдачи…) идут не на сайт, а в раздел «служебные»:
# отдельный файл импорта с черновиками, которые видит только автор блога.
# В data/cameras.json можно задать явно: "private": true — в служебные, false — на сайт, что бы ни говорили слова ниже.
PRIVATE = re.compile("|".join([
    r"додо|dodo|пицц|pizza|суши|япоки|пекарн|bakery|горяч\w* цех|на кухне|kitchen",
    r"\bЖК\b|жил\w* (комплекс|квартал|дом|район)|микрорайон «|стройплощад|строительн\w* площад|строительств|construction|residential",
    r"двор и парковк|во двор|двор дома|придомов|детск\w* площадк|playground|у парковки|на парковке|парковку",
    r"автомо[йе]|car wash|прачечн|постира|laundry|парикмах|барбершоп|barber|выдачи|pickup point|бильярд|billiard|jet wash",
    r"мини-магазин|витрин\w* магазин|\bТЦ\b|\bТРЦ\b|\bТРК\b|торгов\w* (комплекс|центр)|shopping (mall|cent)|легород",
    r"офис|склад|малярн|техцентр|автосервис|автоподъ|автосалон|клиник|\bАЗС\b|тренажерн|школ\w* бокса|спортивн\w* школ",
    r"бассейн\w* спортивного|бассейн «(сатурн|волна)»|комплекс бассейнов|^бассейн$|каток школы|давао|davao|агдао|agdao",
    r"радио|\bfm\b|мусоровоз|спецтехник|стоянк|wildberries|вайлдберриз|\bозон\b|ozon|сдэк|cdek|аптек|оптик|"
    r"магазин цвет|отдел 1с|автомойк|шиномонтаж",
]), re.I)


def is_private(cam):
    if "private" in cam:
        return bool(cam["private"])
    return bool(PRIVATE.search(cam["ru"]) or PRIVATE.search(cam["en"]))


def slugify(s):
    s = s.replace("'", "").replace("’", "").replace("ʻ", "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def clean_ru(s):
    return re.sub(r"\s*\([^)]*\)$", "", s or "").strip()


def ru_key(s):
    return (s or "").lower().replace("ё", "е")


def letter_ru(name):
    """Буква для списков: кириллица, затем латиница (у городов без русского названия), иначе «#»."""
    c = (name or "#")[0].upper().replace("Ё", "Е")
    if c in RU_LETTERS:
        return c
    c = unicodedata.normalize("NFKD", c)[0]
    return c if "A" <= c <= "Z" else "#"


def num(n):
    return f"{int(n):,}".replace(",", " ")


def utc(off):
    sign = "+" if off >= 0 else "−"
    h, m = divmod(round(abs(off) * 60), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def tz_text(gmt, dst, lang):
    if gmt is None:
        return ""
    s = utc(gmt)
    if dst is not None and dst != gmt:
        s += (", летом " if lang == "ru" else ", summer ") + utc(dst)
    return s


def commons(file, width=320):
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(file) + f"?width={width}"


def names(lst):
    return lambda lang: ", ".join(x[lang] for x in (lst or []))


class Site:
    def __init__(self, prefix, with_sights=True, sights_limit=200):
        self.prefix = prefix
        self.with_sights = with_sights
        sc = DATA / "sights_cache.json"
        self.sights = json.loads(sc.read_text(encoding="utf-8")) if sc.exists() else {}
        self.countries = json.loads((DATA / "countries.json").read_text(encoding="utf-8"))
        self.cities = json.loads((DATA / "cities.json").read_text(encoding="utf-8"))
        cams_file = DATA / "cameras.json"
        # "off" — у камеры сейчас нет ни одного рабочего источника (ставит проверка): на сайт не выводится
        cams = [c for c in json.loads(cams_file.read_text(encoding="utf-8")) if not c.get("off")] if cams_file.exists() else []
        self.cams = [c for c in cams if not is_private(c)]
        self.private = [c for c in cams if is_private(c)]

        self.by_cc = {}
        used = set()
        for c in self.countries:
            c["ru"] = clean_ru(c["ru"])
            s = slugify(c["en"]) or c["iso"].lower()
            while s in used:
                s += "-" + c["iso"].lower()
            used.add(s)
            c["slug"] = s
            self.by_cc[c["iso"]] = c

        self.city_by_gid = {}
        taken = set()
        # Сначала крупные города: им достаются короткие ссылки без уточнений
        for ci in sorted(self.cities, key=lambda x: (x["fcode"] != "PPLC", -x["pop"])):
            ci["ru"] = clean_ru(ci["ru"]) or ci["en"]
            co = self.by_cc[ci["cc"]]
            base = co["slug"] + "-" + (slugify(ci["en"]) or slugify(ci["ascii"]) or str(ci["gid"]))
            s = base
            if s in taken:
                s = base + "-" + (slugify(ci["region_en"]) or str(ci["gid"]))
            if s in taken:
                s = base + "-" + str(ci["gid"])
            taken.add(s)
            ci["slug"] = s
            self.city_by_gid[ci["gid"]] = ci
        self.city_by_qid = {c["qid"]: c for c in self.cities if c.get("qid")}

        self.cams_by_city = {}
        for cam in self.cams + self.private:
            ci = self.city_by_gid.get(cam["city_gid"])
            if not ci:
                raise SystemExit(f"Город {cam['city_gid']} камеры «{cam['en']}» не найден — перезапустите prepare_data.py")
            cam["city"] = ci
            # Ссылка «страна-город-камера»: хвост камеры не длиннее 50 знаков, повтор получает номер
            tail = slugify(cam["en"])[:50].rstrip("-") or "webcam"
            s, n = ci["slug"] + "-" + tail, 2
            while s in taken:
                s, n = f'{ci["slug"]}-{tail}-{n}', n + 1
            taken.add(s)
            cam["slug"] = s
            if not is_private(cam):
                self.cams_by_city.setdefault(ci["gid"], []).append(cam)
        ncams = {}
        for g, v in self.cams_by_city.items():
            ncams[self.city_by_gid[g]["cc"]] = ncams.get(self.city_by_gid[g]["cc"], 0) + len(v)
        for co in self.countries:
            co["ncams"] = ncams.get(co["iso"], 0)
        self.sights_top = set(sorted(self.cams_by_city, key=lambda g: -len(self.cams_by_city[g]))[:sights_limit])

    def url(self, slug):
        return f"{self.prefix}{slug}.html"

    # ---------- общие фрагменты ----------
    def crumbs(self, parts):
        out = [f'<a href="{self.url("countries")}" data-en="Countries">Страны</a>']
        for i, (ru, en, slug) in enumerate(parts):
            out.append("<span>›</span>")
            if slug and i < len(parts) - 1:
                out.append(f'<a href="{self.url(slug)}" data-en="{esc(en)}">{esc(ru)}</a>')
            else:
                out.append(f'<span data-en="{esc(en)}">{esc(ru)}</span>')
        return '<nav class="crumbs">' + " ".join(out) + "</nav>"

    @staticmethod
    def desc_slots():
        return ('<div class="wp-desc ru"><!-- СВОЙ ТЕКСТ (RU): впишите сюда абзацы <p>…</p>, и они заменят текст из Викигида --></div>\n'
                '<div class="wp-desc en"><!-- OWN TEXT (EN): put <p>…</p> here to replace the Wikivoyage text --></div>')

    @staticmethod
    def facts(rows):
        out = []
        for lang in ("ru", "en"):
            items = "".join(f"<dt>{esc(r[0] if lang == 'ru' else r[1])}</dt><dd>{r[2](lang)}</dd>" for r in rows if r[2](lang))
            out.append(f'<dl class="facts {lang}">{items}</dl>')
        return "\n".join(out)

    def city_li(self, ci, with_country=False, geo=False):
        co = self.by_cc[ci["cc"]]
        extra = f' data-k="{esc(co["ru"])}|{esc(co["en"])}"' if with_country else ""
        cams = self.cams_by_city.get(ci["gid"])
        # координаты и число камер — для карты городов на странице страны (только у городов с камерами)
        if geo and cams:
            extra += f' data-lat="{round(ci["lat"], 4)}" data-lon="{round(ci["lon"], 4)}" data-n="{len(cams)}"'
        line = f'<li><a href="{self.url(ci["slug"])}" data-en="{esc(ci["en"])}"{extra}>{esc(ci["ru"])}</a></li>'
        return line if cams else f"<!-- {line} -->"

    # ---------- страницы ----------
    def country_post(self, co):
        cities = sorted((c for c in self.cities if c["cc"] == co["iso"]), key=lambda c: ru_key(c["ru"]))
        capital = co.get("capital") or []

        def capital_html(lang):
            out = []
            for cap in capital:
                ci = self.city_by_qid.get(cap["qid"])
                out.append(f'<a href="{self.url(ci["slug"])}">{esc(cap[lang])}</a>' if ci else esc(cap[lang]))
            return ", ".join(out) or esc(co["capital_en"] if lang == "en" else "")

        def currency(lang):
            cur = co.get("currency") or []
            txt = ", ".join(esc(x[lang]) for x in cur) or esc(co["currency_en"])
            return f'{txt} ({co["currency_code"]})' if co["currency_code"] else txt

        def tz(lang):
            zones = co.get("tz") or []
            if not zones:
                return ""
            if len(zones) == 1:
                return tz_text(zones[0]["gmt"], zones[0]["dst"], lang)
            lo, hi = min(z["gmt"] for z in zones), max(z["gmt"] for z in zones)
            if lo == hi:
                return tz_text(zones[0]["gmt"], zones[0]["dst"], lang)
            n = len({z["gmt"] for z in zones})
            return f"{utc(lo)} … {utc(hi)} ({'часовых поясов' if lang == 'ru' else 'time zones'}: {n})"

        cont = CONTINENTS.get(co["continent"], ("", ""))
        rows = [
            ("Форма правления", "Government", lambda l: esc("; ".join(x[l] for x in co.get("gov") or []))),
            ("Глава государства", "Head of state", lambda l: esc(names(co.get("head_state"))(l))),
            ("Глава правительства", "Head of government", lambda l: esc(names(co.get("head_gov"))(l))),
            ("Столица", "Capital", capital_html),
            ("Официальные языки", "Official languages", lambda l: esc(names(co.get("languages"))(l))),
            ("Валюта", "Currency", currency),
            ("Население", "Population", lambda l: num(co["population"]) if co["population"] else ""),
            ("Площадь", "Area", lambda l: num(co["area"]) + (" км²" if l == "ru" else " km²") if co["area"] else ""),
            ("Часовой пояс", "Time zone", tz),
            ("Часть света", "Continent", lambda l: cont[0] if l == "ru" else cont[1]),
            ("Код ISO 3166", "ISO 3166 code", lambda l: co["iso"]),
        ]
        note = ""
        if co["iso"] in DISPUTED:
            ru, en = DISPUTED[co["iso"]]
            note = f'<p class="note ru">{ru}</p><p class="note en">{en}</p>'
        elif co.get("sovereign"):
            s = co["sovereign"][0]
            note = f'<p class="note ru">Территория: {esc(s["ru"])}</p><p class="note en">Territory of {esc(s["en"])}</p>'
        flags = ""
        if co.get("flag") or co.get("coa"):
            imgs = []
            if co.get("flag"):
                imgs.append(f'<img src="{commons(co["flag"])}" alt="Флаг — {esc(co["ru"])}" loading="lazy">')
            if co.get("coa"):
                imgs.append(f'<img class="coa" src="{commons(co["coa"], 200)}" alt="Герб — {esc(co["ru"])}" loading="lazy">')
            flags = '<div class="flags">' + "".join(imgs) + "</div>"
        lat, lon = co.get("lat", ""), co.get("lon", "")
        body = "\n".join([
            f'<div class="wp" data-t="country" data-cc="{co["iso"]}" data-qid="{co.get("qid", "")}" data-slug="{co["slug"]}" data-lat="{lat}" data-lon="{lon}"></div>',
            self.crumbs([(co["ru"], co["en"], co["slug"])]),
            note, flags,
            self.facts(rows),
            '<p class="srcnote ru">Данные: Викиданные и GeoNames, октябрь 2026.</p><p class="srcnote en">Data: Wikidata and GeoNames, October 2026.</p>',
            '<h2 data-en="About">Описание</h2>',
            self.desc_slots(),
            '<h2 data-en="Cities with webcams">Города с веб-камерами</h2>',
            HIDDEN_NOTE,
            '<ul class="az">',
            *[self.city_li(c, geo=True) for c in cities],
            "</ul>",
        ])
        return {"slug": co["slug"], "title": f'{co["ru"]} | {co["en"]}', "labels": ["country"], "content": body}

    def slides_html(self, ci):
        """Для городов с камерами фото вписываются в пост заранее (кэш data/sights_cache.json), остальные грузятся вживую."""
        key = str(ci["gid"])
        if ci["gid"] not in self.cams_by_city or not self.with_sights:
            return ""
        # Запрос к Викиданным — секунды на город, поэтому заранее вписываем фото только крупнейшим «камерным» городам,
        # остальные получат слайд-шоу вживую в браузере
        if key not in self.sights and ci["gid"] not in self.sights_top:
            return ""
        if key not in self.sights:
            self.sights[key] = fetch_sights(ci["lat"], ci["lon"], ci.get("qid", ""))
            (DATA / "sights_cache.json").write_text(json.dumps(self.sights, ensure_ascii=False), encoding="utf-8")
        return "".join(
            f'\n<img src="{esc(s["src"])}" alt="{esc(s["ru"])}" data-en="{esc(s["en"])}" data-href="{esc(s["aru"])}" '
            f'data-href-en="{esc(s["aen"])}" data-credit="{esc(s["dru"])}" data-credit-en="{esc(s["den"])}" loading="lazy">'
            for s in self.sights[key]) + ("\n" if self.sights[key] else "")

    def city_post(self, ci):
        co = self.by_cc[ci["cc"]]
        region = lambda l: esc(ci["region_ru"] or ci["region_en"]) if l == "ru" else esc(ci["region_en"])
        rows = [
            ("Страна", "Country", lambda l: f'<a href="{self.url(co["slug"])}">{esc(co[l])}</a>'),
            ("Регион", "Region", region),
            ("Население", "Population", lambda l: num(ci["pop"]) if ci["pop"] else ""),
            ("Часовой пояс", "Time zone", lambda l: f'{esc(ci["tz"])} ({tz_text(ci["gmt"], ci["dst"], l)})' if ci["tz"] else ""),
            ("Координаты", "Coordinates", lambda l: f'{ci["lat"]}, {ci["lon"]}'),
        ]
        body = "\n".join([
            f'<div class="wp" data-t="city" data-gid="{ci["gid"]}" data-qid="{ci.get("qid", "")}" data-cc="{ci["cc"]}" data-slug="{ci["slug"]}" '
            f'data-country="{co["slug"]}" data-ru="{esc(ci["ru"])}" data-en="{esc(ci["en"])}" data-lat="{ci["lat"]}" data-lon="{ci["lon"]}" '
            f'data-tz="{ci["tz"]}" data-wen="{esc(ci["wen"])}" data-n="{len(self.cams_by_city.get(ci["gid"], []))}"></div>',
            self.crumbs([(co["ru"], co["en"], co["slug"]), (ci["ru"], ci["en"], ci["slug"])]),
            '<div class="wp-wx"><!-- местное время и погода --></div>',
            '<h2 data-en="Sights">Достопримечательности</h2>',
            '<div class="wp-slides"><!-- СВОИ ФОТО: <img src="https://..." alt="Подпись" data-en="Caption" data-href="ссылка на источник" data-credit="пояснение"> -->'
            + self.slides_html(ci) + '</div>',
            '<h2 data-en="About">Описание</h2>',
            self.desc_slots(),
            '<h2 data-en="Webcams">Веб-камеры</h2>',
            '<div class="wp-cams"><p class="stub ru">Веб-камер в этом городе пока нет.</p><p class="stub en">No webcams in this city yet.</p></div>',
            self.facts(rows),
        ])
        return {"slug": ci["slug"], "title": f'{ci["ru"]}, {co["ru"]} | {ci["en"]}, {co["en"]}', "labels": ["city"], "content": body}

    def cam_post(self, cam):
        ci = cam["city"]
        co = self.by_cc[ci["cc"]]
        lat, lon = cam.get("lat", ci["lat"]), cam.get("lon", ci["lon"])
        srcs = []
        for s in cam["sources"]:
            q = f' <span class="q">{esc(s["q"])}</span>' if s.get("q") else ""
            by = esc(s.get("by", ""))
            if s["type"] == "yt":
                who = f" · {by}" if by and by != "YouTube" else ""
                srcs.append(f'<li><a href="https://www.youtube.com/watch?v={s["id"]}" data-type="yt" data-id="{s["id"]}">YouTube{who}{q}</a></li>')
            elif s["type"] == "ytc":
                srcs.append(f'<li><a href="https://www.youtube.com/channel/{s["id"]}/live" data-type="ytc" data-id="{s["id"]}">'
                            f'<span class="ru">Эфир канала</span><span class="en">Channel live</span> · {by}{q}</a></li>')
            elif s["type"] == "link":
                srcs.append(f'<li><a href="{esc(s["src"])}" data-type="link" target="_blank" rel="noopener nofollow">'
                            f'<span class="ru">На сайте источника</span><span class="en">On the source site</span> · {by} ↗</a></li>')
            else:
                kind = s["type"]
                extra = f' data-refresh="{s["refresh"]}"' if s.get("refresh") else ""
                label = esc(s.get("label") or by or kind)
                if kind == "img":
                    label = f'<span class="ru">Фото</span><span class="en">Photo</span> · {label}'
                srcs.append(f'<li><a href="{esc(s.get("href") or s["src"])}" data-type="{kind}" data-src="{esc(s["src"])}"{extra}>{label}{q}</a></li>')
        body = "\n".join([
            f'<div class="wp" data-t="cam" data-slug="{cam["slug"]}" data-city="{ci["slug"]}" data-country="{co["slug"]}" '
            f'data-lat="{lat}" data-lon="{lon}" data-tz="{ci["tz"]}" data-wen="{esc(cam.get("wen", ""))}"></div>',
            self.crumbs([(co["ru"], co["en"], co["slug"]), (ci["ru"], ci["en"], ci["slug"]), (cam["ru"], cam["en"], None)]),
            '<div class="wp-player"><!-- плеер --></div>',
            '<!-- Трансляции: первая — основная. Порядок можно менять, переставляя строки. -->',
            '<ol class="wp-src">', *srcs, "</ol>",
            '<div class="wp-wx"><!-- местное время и погода --></div>',
            self.desc_slots(),
            '<h2 data-en="More webcams in this city">Другие камеры города</h2>',
            '<div class="wp-more"><!-- другие камеры --></div>',
        ])
        post = {"slug": cam["slug"], "title": f'{cam["ru"]} — {ci["ru"]} | {cam["en"]} — {ci["en"]}',
                "labels": ["cam", ci["slug"], co["slug"]], "content": body}
        if is_private(cam):
            # Черновик без меток города: даже если его случайно опубликовать, в списки города он не попадёт
            post.update(labels=["служебные", co["slug"]], draft=True)
        return post

    def cam_index_li(self, cam):
        """Строка индекса камер (по нему работают поиск, «Ближайшие камеры» и списки городов). Страну скрипт берёт по data-iso."""
        ci = cam["city"]
        yt = next((s["id"] for s in cam["sources"] if s["type"] == "yt"), "")
        # Превью Rutube — официальный адрес обложки трансляции
        rt = next((m.group(1) for s in cam["sources"] if s["type"] == "iframe"
                   for m in [re.search(r"rutube\.ru/play/embed/([0-9a-f]{32})", s["src"])] if m), "")
        lat, lon = cam.get("lat", ci["lat"]), cam.get("lon", ci["lon"])
        return (f'<li><a href="{self.url(cam["slug"])}" data-en="{esc(cam["en"])}"' + (f' data-yt="{yt}"' if yt else "")
                + (f' data-rt="{rt}"' if rt and not yt else "")
                + f' data-lat="{round(float(lat), 4)}" data-lon="{round(float(lon), 4)}" data-iso="{ci["cc"]}" '
                f'data-c="{esc(ci["ru"])}|{esc(ci["en"])}" data-ch="{self.url(ci["slug"])}">{esc(cam["ru"])}</a></li>')

    def menu_posts(self):
        posts = []
        lis = [f'<li><a href="{self.url(c["slug"])}" data-en="{esc(c["en"])}" data-cc="{c["iso"]}"'
               + (f' data-n="{c["ncams"]}"' if c["ncams"] else "") + f'>{esc(c["ru"])}</a></li>'
               for c in sorted(self.countries, key=lambda c: ru_key(c["ru"]))]
        posts.append({"slug": "countries", "title": "Страны мира | Countries of the world", "labels": ["page", "wp-index"], "content": "\n".join([
            '<div class="wp" data-t="countries"></div>',
            '<p data-en="All countries and territories of the world (ISO 3166). The number shows how many webcams we have there.">'
            'Все страны и территории мира по стандарту ISO 3166. Число — сколько у нас там веб-камер.</p>',
            '<ul class="az">', *lis, "</ul>"])})

        # Индекс камер делится на части не больше ~250 КБ: большой пост Blogger может не принять
        cams = sorted(self.cams, key=lambda c: ru_key(c["ru"]))
        parts, cur, size = [], [], 0
        for c in cams:
            li = self.cam_index_li(c)
            if cur and size + len(li.encode()) > 250_000:
                parts.append(cur)
                cur, size = [], 0
            cur.append(li)
            size += len(li.encode())
        parts.append(cur)
        for i, part in enumerate(parts):
            slug = "webcams" if i == 0 else f"webcams-{i + 1}"
            title = "Все веб-камеры | All webcams" if i == 0 else f"Все веб-камеры, часть {i + 1} | All webcams, part {i + 1}"
            posts.append({"slug": slug, "title": title, "labels": ["page", "wp-index"], "content": "\n".join([
                '<div class="wp" data-t="cams" data-all="1"></div>',
                '<!-- Добавляя камеру, добавьте её строку в конец любой части: по ним работают поиск, «Города» и «Ближайшие камеры». -->',
                '<ul class="az">', *part, "</ul>"])})

        groups = {}
        for ci in self.cities:
            groups.setdefault(letter_ru(ci["ru"]), []).append(ci)
        letter_links = []
        letter_posts = []
        for L in list(RU_LETTERS) + [chr(c) for c in range(ord("A"), ord("Z") + 1)] + ["#"]:
            if L not in groups:
                continue
            if L in RU_LETTERS:
                slug, ru_t, en_t = "cities-" + TRANSLIT[L], f"Города на букву {L}", f"Cities: {L} (Russian names)"
            elif L != "#":
                slug, ru_t, en_t = "cities-latin-" + L.lower(), f"Города на латинскую букву {L}", f"Cities: {L}"
            else:
                slug, ru_t, en_t = "cities-other", "Города: другие названия", "Cities: other names"
            letter_links.append(f'<a class="btn" href="{self.url(slug)}">{L}</a>')
            items = sorted(groups[L], key=lambda c: ru_key(c["ru"]))
            letter_posts.append({"slug": slug, "title": f"{ru_t} | {en_t}", "labels": ["page"], "content": "\n".join([
                '<div class="wp" data-t="cities"></div>',
                HIDDEN_NOTE,
                '<ul class="az">', *[self.city_li(c, True) for c in items], "</ul>"])})
        with_cams = sorted((self.city_by_gid[g] for g in self.cams_by_city), key=lambda c: ru_key(c["ru"]))
        posts.append({"slug": "cities", "title": "Города мира | Cities of the world", "labels": ["page"], "content": "\n".join([
            '<div class="wp" data-t="cities" data-all="1"></div>',
            '<p data-en="Cities with webcams. Full lists by letter:">Города, где есть веб-камеры. Полные списки по буквам:</p>',
            '<p class="tools-row">' + " ".join(letter_links) + "</p>",
            '<ul class="az">', *[self.city_li(c, True) for c in with_cams], "</ul>"])})
        posts.append({"slug": "top", "title": "Топ по просмотрам | Most viewed", "labels": ["page"], "content":
                      '<div class="wp" data-t="top"></div>\n<p data-en="The most viewed pages of the site.">Самые просматриваемые страницы сайта.</p>'})
        return posts + letter_posts

    def all_batches(self):
        """Посты в порядке импорта: служебные и страны, затем города с камерами и камеры, затем остальные города."""
        first = self.menu_posts() + [self.country_post(c) for c in sorted(self.countries, key=lambda c: c["slug"])]
        # Город и сразу его камеры: если импорт прервётся на середине, импортированное уже связано между собой
        cam_cities = sorted((self.city_by_gid[g] for g in self.cams_by_city), key=lambda c: -len(self.cams_by_city[c["gid"]]))
        second = []
        for c in cam_cities:
            second.append(self.city_post(c))
            second.extend(self.cam_post(x) for x in self.cams_by_city[c["gid"]])
        rest = sorted((c for c in self.cities if c["gid"] not in self.cams_by_city),
                      key=lambda c: (c["fcode"] != "PPLC", -c["pop"]))
        private = [self.cam_post(x) for x in sorted(self.private, key=lambda c: (c["city"]["slug"], ru_key(c["ru"])))]
        return first, second, [self.city_post(c) for c in rest], private


SKIP_TYPES = ("Q515 Q1549591 Q5119 Q15284 Q2983893 Q123705 Q928830 Q55488 Q1248784 Q3914 Q16917 Q4830453 Q1093829 "
              "Q7930989 Q3957 Q532 Q484170 Q747074 Q5 Q5503 Q13406463")
SIGHT_BAD = re.compile(r"станци|station|аэропорт|airport|школ|school|больниц|hospital|компани|company|список|list of", re.I)


def fetch_sights(lat, lon, qid, limit=10):
    """Главные достопримечательности рядом с городом по Викиданным (тот же отбор, что в скрипте темы)."""
    import time
    import urllib.request
    q = f"""SELECT ?item ?ru ?en ?dru ?den ?img ?sl ?her ?aru ?aen WHERE {{
      SERVICE wikibase:around {{ ?item wdt:P625 ?loc . bd:serviceParam wikibase:center "Point({lon} {lat})"^^geo:wktLiteral ; wikibase:radius "8" . }}
      ?item wdt:P18 ?img ; wikibase:sitelinks ?sl . FILTER(?sl >= 4)
      FILTER NOT EXISTS {{ ?item wdt:P585|wdt:P580|wdt:P576|wdt:P159|wdt:P1454|wdt:P1082 [] }}
      FILTER NOT EXISTS {{ ?item wdt:P31 ?t . VALUES ?t {{ {" ".join("wd:" + x for x in SKIP_TYPES.split())} }} }}
      OPTIONAL {{ ?item wdt:P1435 ?her }}
      OPTIONAL {{ ?item rdfs:label ?ru FILTER(lang(?ru) = "ru") }} OPTIONAL {{ ?item rdfs:label ?en FILTER(lang(?en) = "en") }}
      OPTIONAL {{ ?item schema:description ?dru FILTER(lang(?dru) = "ru") }} OPTIONAL {{ ?item schema:description ?den FILTER(lang(?den) = "en") }}
      OPTIONAL {{ ?aru schema:about ?item ; schema:isPartOf <https://ru.wikipedia.org/> . }}
      OPTIONAL {{ ?aen schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }}
    }} ORDER BY DESC(?sl) LIMIT 40"""
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": q, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": "WebPlanetBuilder/0.1 (+https://webplanetcountry.blogspot.com/)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                rows = json.load(r)["results"]["bindings"]
            break
        except Exception:  # noqa: BLE001 — повтор при перегрузке сервиса
            time.sleep(5 * (attempt + 1))
    else:
        return []
    seen, out = set(), []
    for b in rows:
        iid = b["item"]["value"].rsplit("/", 1)[1]
        img = b["img"]["value"]
        if iid in seen or img in seen or iid == qid:
            continue
        seen.update((iid, img))
        g = lambda k: b.get(k, {}).get("value", "")
        ru, en = g("ru") or g("en"), g("en") or g("ru")
        if not ru or SIGHT_BAD.search(ru + " " + g("dru")):
            continue
        out.append({"s": int(b["sl"]["value"]) + (25 if "her" in b else 0), "src": img.replace("http:", "https:", 1) + "?width=1280",
                    "ru": ru, "en": en, "dru": g("dru"), "den": g("den"),
                    "aru": g("aru") or b["item"]["value"], "aen": g("aen") or b["item"]["value"]})
    out.sort(key=lambda x: -x["s"])
    time.sleep(1)
    return out[:limit]


def post_id(slug):
    return str(int(hashlib.sha1(slug.encode()).hexdigest()[:15], 16))


def write_feed(path, posts, fmt, prefix, start):
    xe = lambda s: html.escape(s, quote=True)
    out = ["<?xml version='1.0' encoding='UTF-8'?>"]
    if fmt == "takeout":
        out.append("<feed xmlns='http://www.w3.org/2005/Atom' xmlns:blogger='http://schemas.google.com/blogger/2018'>")
        out.append(f"<id>tag:blogger.com,1999:blog-{BLOG_ID}</id><title>Веб камеры планеты</title>")
    else:
        out.append("<feed xmlns='http://www.w3.org/2005/Atom' xmlns:openSearch='http://a9.com/-/spec/opensearchrss/1.0/' "
                   "xmlns:gd='http://schemas.google.com/g/2005' xmlns:thr='http://purl.org/syndication/thread/1.0' "
                   "xmlns:georss='http://www.georss.org/georss'>")
        out.append(f"<id>tag:blogger.com,1999:blog-{BLOG_ID}.archive</id><updated>{start:%Y-%m-%dT%H:%M:%S}.000+00:00</updated>"
                   "<title type='text'>Веб камеры планеты</title><generator version='7.00' uri='http://www.blogger.com'>Blogger</generator>")
    for i, p in enumerate(posts):
        ts = (start + timedelta(seconds=p["n"])).strftime("%Y-%m-%dT%H:%M:%S")
        pid = f"tag:blogger.com,1999:blog-{BLOG_ID}.post-{post_id(p['slug'])}"
        cats = "".join(f"<category scheme='http://www.blogger.com/atom/ns#' term='{xe(l)}'/>" for l in p["labels"])
        draft = "<app:control xmlns:app='http://purl.org/atom/app#'><app:draft>yes</app:draft></app:control>" if p.get("draft") else ""
        if fmt == "takeout":
            out.append(f"<entry><id>{pid}</id><blogger:type>POST</blogger:type>"
                       f"<blogger:status>{'DRAFT' if p.get('draft') else 'LIVE'}</blogger:status>"
                       f"<author><name>webplanet</name><blogger:type>BLOGGER</blogger:type></author>"
                       f"<title>{xe(p['title'])}</title><content type='html'>{xe(p['content'])}</content>"
                       f"<blogger:created>{ts}.000Z</blogger:created><published>{ts}.000Z</published><updated>{ts}.000Z</updated>"
                       f"{cats}<blogger:filename>{prefix}{p['slug']}.html</blogger:filename></entry>")
        else:
            out.append(f"<entry><id>{pid}</id><published>{ts}.000+00:00</published><updated>{ts}.000+00:00</updated>"
                       f"<category scheme='http://schemas.google.com/g/2005#kind' term='http://schemas.google.com/blogger/2008/kind#post'/>{cats}"
                       f"<title type='text'>{xe(p['title'])}</title><content type='html'>{xe(p['content'])}</content>"
                       f"<link rel='alternate' type='text/html' href='{BLOG_URL}{prefix}{p['slug']}.html' title='{xe(p['title'])}'/>"
                       f"<author><name>webplanet</name></author>{draft}</entry>")
    out.append("</feed>")
    path.write_text("\n".join(out), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["classic", "takeout"], default="classic")
    ap.add_argument("--batch", type=int, default=500, help="постов в одном файле импорта")
    ap.add_argument("--prefix", default="/2026/10/", help="путь постов: /ГГГГ/ММ/ — должен совпадать с датой публикации")
    ap.add_argument("--preview-sample", type=int, default=40, help="сколько городов без камер положить в предпросмотр")
    ap.add_argument("--no-sights", action="store_true", help="не вписывать фото достопримечательностей в посты городов с камерами")
    ap.add_argument("--sights-limit", type=int, default=200, help="скольким городам с камерами вписать фото заранее")
    args = ap.parse_args()
    y, m = map(int, args.prefix.strip("/").split("/"))
    start = datetime(y, m, 1, tzinfo=timezone.utc)

    site = Site(args.prefix, with_sights=not args.no_sights, sights_limit=args.sights_limit)
    first, second, rest, private = site.all_batches()
    n = 0
    for p in first + second + rest + private:
        p["n"] = n
        n += 1
    n -= len(private)

    out = ROOT / "import"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    files = [("0001-razdely-i-strany.xml", first)]
    for i in range(0, len(second), args.batch):
        files.append((f"{len(files) + 1:04d}-kamery-i-ih-goroda.xml", second[i:i + args.batch]))
    for i in range(0, len(rest), args.batch):
        files.append((f"{len(files) + 1:04d}-goroda.xml", rest[i:i + args.batch]))
    lines = []
    for name, posts in files:
        if posts:
            write_feed(out / name, posts, args.format, args.prefix, start)
            size = (out / name).stat().st_size / 1e6
            lines.append(f"{name}\t{len(posts)} постов\t{size:.1f} МБ\t{posts[0]['title'].split(' | ')[0]} … {posts[-1]['title'].split(' | ')[0]}")
    (out / "ОПИСЬ.txt").write_text(
        f"Формат: {args.format}. Всего постов: {n}. Импортируйте файлы по порядку номеров.\n\n" + "\n".join(lines) + "\n", encoding="utf-8")

    # Служебные камеры — отдельная папка: черновики, которые видит только автор блога
    priv = ROOT / "import-sluzhebnye"
    if priv.exists():
        shutil.rmtree(priv)
    priv.mkdir()
    for i in range(0, len(private), args.batch):
        write_feed(priv / f"{i // args.batch + 1:02d}-sluzhebnye-kamery-chernoviki.xml", private[i:i + args.batch],
                   args.format, args.prefix, start)

    # Подборка для локального предпросмотра
    prev = ROOT / "build" / "preview"
    if prev.exists():
        shutil.rmtree(prev)
    prev.mkdir(parents=True)
    sample = first + second + rest[:args.preview_sample]
    (prev / "posts.json").write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    # Конструктор камеры со встроенным списком городов
    rows = [[c["slug"], c["ru"], c["en"], c["cc"], c["lat"], c["lon"], c["tz"], c["pop"]] for c in site.cities]
    cos = {c["iso"]: [c["ru"], c["en"], c["slug"]] for c in site.countries}
    js = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = (ROOT / "tools" / "cam-builder.tpl.html").read_text(encoding="utf-8")
    (ROOT / "Конструктор камеры.html").write_text(
        tpl.replace("@@PREFIX@@", args.prefix).replace("@@CITIES@@", js(rows)).replace("@@COUNTRIES@@", js(cos)), encoding="utf-8")
    print(f"Постов: {n} (разделы и страны {len(first)}, камеры с городами {len(second)}, остальные города {len(rest)}); файлов: {len(lines)}")
    print(f"Служебных камер (черновики в import-sluzhebnye/): {len(private)}; на сайте камер: {len(site.cams)}")


if __name__ == "__main__":
    main()
