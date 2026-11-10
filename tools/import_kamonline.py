"""Импорт с камеры-онлайн.рф: список городов (с точкой панорамы улиц из центра) и камеры этих городов.

Шаги (скачанные страницы хранятся в build/kamonline/, повторный запуск их не качает):
  1. список страниц городов с главной сайта (robots.txt сайта ничего не запрещает);
  2. скачивание — 2 потока с паузой;
  3. разбор: название и регион города, координаты центра, точка панорамы Google, камеры с подписями;
  4. привязка к нашим городам (data/cities.json); город, которого у нас нет, берётся из GeoNames
     (data/geonames/cities1000.zip) и дописывается в data/cities.json;
  5. data/streetview.json — точка панорамы для каждого города (страница города показывает панораму с неё);
  6. камеры: только исходные плееры (обёртка плеера самого сайта снимается), проверка встраивания,
     новые камеры — в data/cameras.json (уже известные трансляции пропускаются).

Запуск: py tools/import_kamonline.py [--no-cams]
"""
import argparse
import concurrent.futures as cf
import gzip
import hashlib
import html
import io
import json
import math
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import check_cams  # noqa: E402

DATA = ROOT / "data"
WORK = ROOT / "build" / "kamonline"
BASE = "https://xn----7sbbswbkfldgw7l.xn--p1ai/"  # камеры-онлайн.рф
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TODAY = date.today().isoformat()
# не камеры: карты, реклама, счётчики
NOT_CAM = re.compile(r"google\.com/maps|ad\.mail\.ru|yandex|yastatic|vkAuth|mradx|doubleclick|googletag")


def fetch(url):
    p = WORK / "pages" / (hashlib.sha1(url.encode()).hexdigest() + ".html.gz")
    if p.exists():
        return gzip.decompress(p.read_bytes()).decode("utf-8", "replace")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=40) as r:
                data = r.read()
            p.write_bytes(gzip.compress(data))
            time.sleep(0.5)
            return data.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — повтор с паузой
            time.sleep(3 * (attempt + 1))
    return None


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip()


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip(" .,;—-")


def source(u):
    """Адрес плеера → источник; обёртка плеера самого сайта снимается до исходного потока."""
    u = html.unescape(u.strip())
    if u.startswith("//"):
        u = "https:" + u
    if "/player/" in u and "file=" in u:
        u = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("file", [""])[0]) or u
    if u.startswith("http://"):
        u = "https://" + u[7:]  # на https-сайте работают только https-трансляции; проверка покажет, отвечает ли сервер
    m = re.search(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|live/)|youtu\.be/)([\w-]{11})", u)
    if m:
        return {"type": "yt", "id": m.group(1)}
    m = re.search(r"youtube\.com/embed/live_stream\?channel=(UC[\w-]{22})", u)
    if m:
        return {"type": "ytc", "id": m.group(1)}
    if re.search(r"\.m3u8(\?|$)", u):
        return {"type": "hls", "src": u}
    return {"type": "iframe", "src": u} if u.startswith("https://") else None


def src_key(s):
    """Ключ трансляции для поиска повторов: у Ivideon — сервер и камера, у остальных — адрес без лишних параметров."""
    u = s.get("id") or s.get("src", "")
    if "ivideon" in u:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(u)).query)
        return f'ivideon:{q.get("server", [""])[0]}:{q.get("camera", ["0"])[0]}'
    if "video_ext.php" in u:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(u)).query)
        return f'vk:{q.get("oid", [""])[0]}:{q.get("id", [""])[0]}'
    return s["type"] + ":" + re.sub(r"[?#].*$", "", u).rstrip("/").lower()


def parse(url, t):
    title = clean((re.search(r"<title>(.*?)</title>", t, re.S) or [0, ""])[1])
    m = re.match(r"Веб-камеры онлайн\s+(.+?)(?:\s*\(([^)]*)\))?\s*$", title)
    name, region = (m.group(1).strip(), (m.group(2) or "").strip()) if m else (title, "")
    c = re.search(r"широта и долгота:\s*(-?[\d.]+)[ ,]+(-?[\d.]+)", t)
    sv = re.search(r"streetview\?[^\"]*?location=\s*(-?[\d.]+),\s*(-?[\d.]+)(?:&amp;|&)heading=(-?\d+)", t)
    body = re.sub(r"<script.*?</script>", "", t, flags=re.S)
    cams = []
    for m in re.finditer(r'<iframe[^>]*?\ssrc="([^"]+)"[^>]*>\s*</iframe>\s*(?:</noindex>)?([^<]*)', body):
        if NOT_CAM.search(m.group(1)):
            continue
        s = source(m.group(1))
        if s:
            cams.append({"src": s, "caption": clean(m.group(2))})
    return {"url": url, "slug": url.rsplit("/", 1)[-1].removesuffix(".html"), "name": name, "region": region,
            "lat": float(c.group(1)) if c else None, "lon": float(c.group(2)) if c else None,
            "sv": [float(sv.group(1)), float(sv.group(2)), int(sv.group(3))] if sv else None, "cams": cams}


# ---------- города ----------
TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
              "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh shch _ y _ e yu ya".split()))


def translit(s):
    return "".join(TR.get(ch, ch) for ch in norm(s)).replace("_", "")


def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


class Cities:
    def __init__(self):
        self.ours = json.loads((DATA / "cities.json").read_text(encoding="utf-8"))
        self.by_gid = {c["gid"]: c for c in self.ours}
        self.grid = self._grid(self.ours)
        self.geo = None  # GeoNames cities1000 — загружается, только если понадобится

    @staticmethod
    def _grid(items):
        g = {}
        for c in items:
            g.setdefault((int(c["lat"] // 1), int(c["lon"] // 1)), []).append(c)
        return g

    @staticmethod
    def near(grid, lat, lon, radius):
        out = []
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for c in grid.get((int(lat // 1) + i, int(lon // 1) + j), []):
                    d = km(lat, lon, c["lat"], c["lon"])
                    if d <= radius:
                        out.append((d, c))
        return sorted(out, key=lambda x: x[0])

    @staticmethod
    def skel(s):
        """Скелет латинского написания: Babayevo = babaevo, Alzamay = alzamaj, Adygeysk = adigeisk."""
        return re.sub(r"[yjie]", "", re.sub(r"[^a-z]", "", translit(s)).replace("kh", "h").replace("ts", "c"))

    def named(self, c, name, slug):
        names = {norm(c.get("ru")), norm(c.get("en")), norm(c.get("ascii")), norm(c.get("name"))} - {""}
        return norm(name) in names or self.skel(slug) in {self.skel(n) for n in names}

    def by_name(self, rec):
        """Страница без координат: город с тем же названием, сначала в России, крупнейший."""
        if self.geo is None:
            self.load_geo()
        k = self.skel(rec["slug"])
        hits = [c for c in self.ours if norm(c.get("ru")) == norm(rec["name"]) or self.skel(c.get("ascii", "")) == k]
        if not hits:
            hits = [c for cell in self.geo.values() for c in cell if self.skel(c["ascii"]) == k]
        if not hits:
            return None
        ru = [c for c in hits if c["cc"] == "RU"]
        return max(ru or hits, key=lambda c: c["pop"])

    def add(self, c, rec):
        if c["gid"] in self.by_gid:
            return self.by_gid[c["gid"]]
        c = dict(c, ru=rec["name"], region_ru=rec["region"])
        c.pop("name", None)
        self.ours.append(c)
        self.by_gid[c["gid"]] = c
        self.grid.setdefault((int(c["lat"] // 1), int(c["lon"] // 1)), []).append(c)
        return c

    def load_geo(self):
        adm, tz = {}, {}
        for line in (DATA / "geonames" / "admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
            p = line.split("\t")
            adm[p[0]] = p[1]
        for line in (DATA / "geonames" / "timeZones.txt").read_text(encoding="utf-8").splitlines()[1:]:
            p = line.split("\t")
            tz[p[1]] = (float(p[2]), float(p[3]))
        items = []
        with zipfile.ZipFile(DATA / "geonames" / "cities1000.zip") as z, z.open("cities1000.txt") as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                p = line.rstrip("\n").split("\t")
                if p[7] == "PPLX" or int(p[0]) in self.by_gid:
                    continue
                g = tz.get(p[17], (None, None))
                items.append({"gid": int(p[0]), "cc": p[8], "qid": "", "ru": "", "en": p[1], "ascii": p[2], "name": p[1],
                              "lat": float(p[4]), "lon": float(p[5]), "pop": int(p[14] or 0), "fcode": p[7], "tz": p[17],
                              "gmt": g[0], "dst": g[1], "region_en": adm.get(f"{p[8]}.{p[10]}", ""), "region_ru": "",
                              "wen": "", "wru": ""})
        self.geo = self._grid(items)

    def find(self, rec):
        """→ (город, уверенно ли совпало название). Сначала наши города, затем GeoNames (новый город)."""
        lat, lon = rec["lat"], rec["lon"]
        if lat is None:
            c = self.by_name(rec)
            return (self.add(c, rec), True) if c else (None, False)
        cand = self.near(self.grid, lat, lon, 15)
        for d, c in cand:
            if self.named(c, rec["name"], rec["slug"]):
                return c, True
        if self.geo is None:
            self.load_geo()
        for d, c in self.near(self.geo, lat, lon, 10):
            if self.named(c, rec["name"], rec["slug"]):
                return self.add(c, rec), True
        close = [c for d, c in cand if d <= 5]
        return (max(close, key=lambda c: c["pop"]) if close else None), False


# ---------- названия камер ----------
WORDS = {"пляж": "Beach", "улица": "Street", "площадь": "Square", "набережная": "Embankment", "проспект": "Avenue",
         "парк": "Park", "мост": "Bridge", "вид": "View", "на": "of", "город": "City", "порт": "Port", "морской": "Sea",
         "центр": "Centre", "вокзал": "Station", "перекресток": "Crossroads", "перекрёсток": "Crossroads", "в": "in",
         "и": "and", "камера": "Camera", "панорама": "Panorama", "микрорайоне": "district", "курорт": "Resort",
         "трасса": "Highway", "озеро": "Lake", "река": "River", "гора": "Mountain", "храм": "Church", "собор": "Cathedral"}


def to_en(ru):
    out = []
    for w in re.findall(r"[\w’'-]+|[^\w\s]", ru):
        low = w.lower()
        if low in WORDS:
            out.append(WORDS[low])
        elif re.search(r"[а-яё]", low):
            t = translit(w)
            out.append(t[:1].upper() + t[1:])
        else:
            out.append(w)
    s = re.sub(r"\s+([,.;:)»])", r"\1", " ".join(out)).replace("( ", "(").replace("« ", "«")
    return s[:1].upper() + s[1:]


def check(s, yt_live):
    if s["type"] == "yt":
        return yt_live.get(s["id"], False)
    if s["type"] == "ytc":
        return False  # эфир канала без ключа не проверить — не берём
    u = s.get("src", "")
    if re.match(r"https://(vkvideo\.ru|vk\.com)/video_ext\.php\?|https://rutube\.ru/play/embed/|https://ok\.ru/videoembed/", u):
        return True  # официальные плееры видеоплатформ: в браузере встраиваются, роботу отвечают переадресацией
    return check_cams.check_other(s) in ("доступен",)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cams", action="store_true", help="только города и панорамы, без камер")
    args = ap.parse_args()
    (WORK / "pages").mkdir(parents=True, exist_ok=True)

    home = fetch(BASE + "index.html")
    pages = sorted({h for h in re.findall(r"""href=["']([a-z0-9\-]+\.html)["']""", home or "")}
                   - {"index.html", "otkaz.html", "cont.html"})
    print("страниц городов:", len(pages), flush=True)
    with cf.ThreadPoolExecutor(2) as ex:
        texts = list(ex.map(lambda h: fetch(BASE + h), pages))
    recs = [parse(BASE + h, t) for h, t in zip(pages, texts) if t]
    print("разобрано:", len(recs), "с камерами:", sum(1 for r in recs if r["cams"]), "камер:", sum(len(r["cams"]) for r in recs))

    # города и точки панорамы
    cities = Cities()
    before = len(cities.ours)
    sv_f = DATA / "streetview.json"
    sv = json.loads(sv_f.read_text(encoding="utf-8")) if sv_f.exists() else {}
    mapped, weak, lost = 0, 0, []
    for r in recs:
        c, sure = cities.find(r)
        r["city"] = c
        if c and sure:
            mapped += 1
            p = r["sv"] or ([r["lat"], r["lon"], 0] if r["lat"] is not None else [c["lat"], c["lon"], 0])
            sv[str(c["gid"])] = [round(p[0], 6), round(p[1], 6), p[2]]
        elif c:
            weak += 1
        else:
            lost.append(r["name"])
    added = cities.ours[before:]
    (DATA / "cities.json").write_text(json.dumps(cities.ours, ensure_ascii=False), encoding="utf-8")
    sv_f.write_text(json.dumps(sv, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"городов: найдено по названию {mapped}, только по месту {weak}, не найдено {len(lost)}; новых в справочнике: {len(added)}")
    print("  не найдены:", ", ".join(lost[:30]))
    print("  новые:", ", ".join(c["ru"] for c in added[:30]))
    print("точек панорамы в data/streetview.json:", len(sv))
    if args.no_cams:
        return

    # камеры
    cams = json.loads((DATA / "cameras.json").read_text(encoding="utf-8"))
    known = {src_key(s) for c in cams for s in c["sources"]}
    todo = [(r, cam) for r in recs if r["city"] for cam in r["cams"] if src_key(cam["src"]) not in known]
    yt_ids = sorted({cam["src"]["id"] for r, cam in todo if cam["src"]["type"] == "yt"})
    yt_live = {}
    key_f = DATA / "youtube_key.txt"
    if yt_ids and key_f.exists():
        try:
            yt = check_cams.YouTube(key_f.read_text(encoding="utf-8").strip())
            v = yt.videos(yt_ids)
            yt_live = {i: check_cams.is_live(x) and x["status"].get("embeddable", False) for i, x in v.items()}
        except check_cams.RateLimited:
            print("YouTube: квота ключа исчерпана, YouTube-камеры не добавлены")
    print(f"новых трансляций к проверке: {len(todo)} (уже в базе: {sum(len(r['cams']) for r in recs if r['city']) - len(todo)})", flush=True)
    with cf.ThreadPoolExecutor(8) as ex:
        verdict = list(ex.map(lambda x: check(x[1]["src"], yt_live), todo))
    taken = {(c["city_gid"], c["ru"]) for c in cams}
    new = 0
    for (r, cam), ok in zip(todo, verdict):
        k = src_key(cam["src"])
        if not ok or k in known:
            continue
        known.add(k)
        ru = cam["caption"] or "Вид на город"
        ru = ru[:1].upper() + ru[1:]
        name, n = ru, 2
        while (r["city"]["gid"], name) in taken:
            name, n = f"{ru} ({n})", n + 1
        taken.add((r["city"]["gid"], name))
        by = urllib.parse.urlparse(cam["src"].get("src", "")).netloc or "YouTube"
        cams.append({"city_gid": r["city"]["gid"], "ru": name, "en": to_en(name) if name != "Вид на город" else "City view",
                     "sources": [{**cam["src"], "by": by[:40]}], "found": f"catalog kamonline {TODAY}"})
        new += 1
    (DATA / "cameras.json").write_text(json.dumps(cams, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"камер добавлено: {new}, не прошли проверку встраивания: {sum(1 for v in verdict if not v)}")


if __name__ == "__main__":
    main()
