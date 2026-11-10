"""Ищет новые прямые трансляции веб-камер на YouTube через официальный YouTube Data API v3
и добавляет подходящие в data/cameras.json.

Ключ: файл data/youtube_key.txt (одна строка) или переменная окружения YT_API_KEY.
Квота: один поиск стоит 100 единиц из 10 000 бесплатных в сутки, проверка 50 найденных видео — 1 единицу.
Скрипт тратит не больше --budget единиц за запуск и запоминает, где остановился (data/youtube_search_state.json):
следующий запуск продолжит со следующих запросов. Квота обнуляется в полночь по тихоокеанскому времени.

Что попадает в базу: только идущий сейчас эфир, который владелец разрешил встраивать, с признаками веб-камеры
в названии или описании и без признаков новостей, музыки, игр, богослужений и т. п.
Город определяется по координатам трансляции (если автор их указал) или по названию города в заголовке.

Запуск: py tools/find_youtube.py [--budget 6000] [--geodata ПАПКА_GEONAMES] [--dry-run]
С --geodata (папка с cities1000.zip) камеры привязываются и к небольшим городкам; после этого
перезапустите prepare_data.py, чтобы новые города появились в справочнике.
"""
import argparse
import http.client
import io
import json
import math
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE = DATA / "youtube_search_state.json"

# Запросы по словам «веб-камера» на разных языках и поиск по регионам (центр, радиус до 1000 км)
QUERIES = ["webcam", "live cam", "live camera 24/7", "веб-камера онлайн", "webcam en vivo", "câmera ao vivo",
           "webcam in diretta", "webcam en direct", "webcam live Deutschland", "kamera na żywo", "ライブカメラ",
           "라이브캠", "canlı kamera", "webcam live Norge", "live beach cam", "live harbor cam", "ski webcam live",
           "city live cam", "камера онлайн трансляция", "plane spotting live", "live wildlife cam", "webcam Österreich live",
           "webcam España en directo", "live cam Thailand", "live cam Bali", "webcam Hrvatska uživo", "webcam Ελλάδα live",
           "webcam live Italia", "webcam Schweiz live", "webcam Nederland live", "webcam live Sverige", "webcam Suomi live",
           "webcam živě Česko", "webcam v živo Slovenija", "webcam ao vivo Portugal", "câmera ao vivo praia",
           "cámara en vivo playa México", "cámara en vivo Argentina", "live cam Ireland", "live cam Scotland",
           "live cam Florida beach", "live cam New York City", "live cam Hawaii", "live cam Alaska", "live cam Canada",
           "live cam Australia beach", "live cam New Zealand", "live cam Philippines", "live cam Vietnam", "live cam Seoul",
           "ライブカメラ 富士山", "ライブカメラ 海", "live cam Dubai", "live cam Israel", "live safari cam Africa",
           "kamera na żywo morze", "webcam live România", "webcam live България", "canlı kamera İstanbul",
           "веб камера Беларусь онлайн", "веб камера Казахстан онлайн", "веб-камера Крым онлайн", "веб камера Сочи онлайн",
           "веб камера Санкт-Петербург онлайн", "веб камера Москва онлайн", "live cam Iceland", "live cam Norway fjord",
           "webcam live Montenegro", "live cam Malta", "live cam Cyprus", "live cam Caribbean beach", "live cam Brazil Rio"]
CENTERS = [("Западная Европа", 48.5, 4.0), ("Центральная Европа", 49.0, 15.0), ("Скандинавия", 61.0, 13.0),
           ("Средиземноморье", 40.0, 15.0), ("Иберия", 40.0, -4.0), ("Балканы и Греция", 41.5, 22.5),
           ("Восточная Европа", 52.0, 30.0), ("Европейская Россия", 56.0, 42.0), ("Сибирь", 55.0, 83.0),
           ("Восток США", 39.0, -77.0), ("Юг США", 31.0, -88.0), ("Запад США", 37.0, -119.0), ("Центр США", 40.0, -98.0),
           ("Канада", 46.0, -72.0), ("Мексика", 20.5, -99.0), ("Карибы", 18.0, -70.0), ("Бразилия", -22.0, -45.0),
           ("Аргентина и Чили", -34.0, -64.0), ("Япония", 36.0, 138.0), ("Корея", 36.5, 127.8), ("Юго-Восточная Азия", 12.0, 102.0),
           ("Индонезия", -7.0, 112.0), ("Австралия", -32.0, 147.0), ("Новая Зеландия", -41.0, 174.0),
           ("Ближний Восток", 31.0, 37.0), ("Турция", 39.0, 32.0), ("Индия", 21.0, 78.0), ("Южная Африка", -30.0, 25.0),
           ("Северная Африка", 32.0, 3.0), ("Исландия", 64.5, -19.0)]
CAM_WORDS = re.compile(r"(?i)\bcam\b|webcam|livecam|live ?cam|camera|kamera|câmera|cámara|caméra|telecamera|камер|ライブカメラ|"
                       r"라이브캠|cctv|실시간|panoram|view|beach|harbou?r|\bport\b|airport|spotting|nest|feeder|aquarium|\bzoo\b|"
                       r"\bski\b|volcan|street|square|plaza|piazza|marina|\bbay\b|webkamera|uživo|canlı")
NOT_CAM = re.compile(r"(?i)news|новост|notícias|noticias|nachrichten|music|музык|radio|радио|lo-?fi|chill|relax|sleep|asmr|"
                     r"\bgame|gaming|gameplay|minecraft|fortnite|roblox|\bgta\b|podcast|подкаст|interview|church|worship|"
                     r"богослуж|литург|служба|\bmass\b|misa\b|messe\b|quran|bible|prayer|молитв|sermon|concert|концерт|"
                     r"\bmatch\b|матч|football|soccer|\bnba\b|\bnfl\b|\btv\b|\bтв\b|trading|bitcoin|crypto|forex|tarot|"
                     r"караоке|karaoke|cartoon|мульт|anime|vtuber|replay|повтор|offline|highlights|election|выбор|"
                     r"shopping|auction|giveaway|q&a|talk show|ток-шоу|stream of|playing|let's play|around the world|"
                     r"multi-?cams?|cameras from")
REGIONS = {
    "US": "USA, U.S., United States, America, Alabama, Alaska, Arizona, Arkansas, California, Colorado, Connecticut, "
          "Delaware, Florida, Hawaii, Idaho, Illinois, Indiana, Iowa, Kansas, Kentucky, Louisiana, Maine, Maryland, "
          "Massachusetts, Michigan, Minnesota, Mississippi, Missouri, Montana, Nebraska, Nevada, New Hampshire, New Jersey, "
          "New Mexico, North Carolina, North Dakota, Ohio, Oklahoma, Oregon, Pennsylvania, Rhode Island, South Carolina, "
          "South Dakota, Tennessee, Texas, Utah, Vermont, Virginia, Washington State, West Virginia, Wisconsin, Wyoming, "
          "New York, "
          "FL, CA, NC, SC, NJ, NY, TX",
    "GB": "UK, U.K., England, Scotland, Wales, Northern Ireland, Britain, Great Britain",
    "CA": "Ontario, Quebec, Québec, British Columbia, Alberta, Nova Scotia, Newfoundland, Manitoba, Saskatchewan, New Brunswick",
    "AU": "Queensland, New South Wales, NSW, Victoria, Tasmania, Western Australia, South Australia",
    "ES": "Canary Islands, Tenerife, Mallorca, Majorca, Ibiza, Andalucía, Andalusia, Catalonia, Catalunya",
    "IT": "Italia, Sicily, Sicilia, Sardinia, Sardegna, Tuscany, Toscana",
    "DE": "Deutschland, Bavaria, Bayern", "AT": "Österreich", "CH": "Schweiz, Suisse, Svizzera", "JP": "日本",
    "MX": "México, Baja California, Baja California Sur, Quintana Roo", "BR": "Brasil", "NL": "Nederland", "NO": "Norge", "SE": "Sverige", "HR": "Hrvatska", "GR": "Ελλάδα",
}
US_STATES = {"FL": "Florida", "CA": "California", "NC": "North Carolina", "SC": "South Carolina", "NJ": "New Jersey",
             "NY": "New York", "TX": "Texas", "Washington State": "Washington"}
US_STATES.update({s: s for s in REGIONS["US"].split(", ")[4:] if len(s) > 2 and s not in US_STATES.values()})


def load_key():
    key = os.environ.get("YT_API_KEY", "")
    kf = DATA / "youtube_key.txt"
    if not key and kf.exists():
        key = kf.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("Нет ключа YouTube Data API: сохраните его одной строкой в data/youtube_key.txt")
    return key


class Quota(Exception):
    pass


class Api:
    COST = {"search": 100}

    def __init__(self, key, budget):
        self.key, self.budget, self.spent = key, budget, 0

    def call(self, method, **params):
        cost = self.COST.get(method, 1)
        if self.spent + cost > self.budget:
            raise Quota("исчерпан бюджет этого запуска")
        url = f"https://www.googleapis.com/youtube/v3/{method}?" + urllib.parse.urlencode({**params, "key": self.key})
        self.spent += cost
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                try:
                    reason = json.loads(e.read())["error"]["errors"][0]["reason"]
                except Exception:  # noqa: BLE001
                    reason = str(e.code)
                if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                    raise Quota("YouTube: суточная квота ключа исчерпана, продолжите завтра") from None
                raise SystemExit(f"Ошибка YouTube Data API: {e.code} {reason}") from None
            except (urllib.error.URLError, http.client.HTTPException, OSError):
                time.sleep(5 * (attempt + 1))  # обрыв связи — повторяем тот же запрос
        raise Quota("нет связи с YouTube, найденное сохранено — запустите позже")


def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


class Places:
    """Города для привязки: data/cities.json и, если дана папка GeoNames, все города от 1000 жителей."""

    def __init__(self, geodata):
        self.cities = [{"gid": c["gid"], "cc": c["cc"], "names": {c["en"], c["ascii"], c.get("wen") or ""} - {""},
                        "lat": c["lat"], "lon": c["lon"], "pop": c["pop"], "region": c.get("region_en", "")}
                       for c in json.loads((DATA / "cities.json").read_text(encoding="utf-8"))]
        if geodata:
            known = {c["gid"] for c in self.cities}
            adm = {}
            af = pathlib.Path(geodata) / "admin1CodesASCII.txt"
            if af.exists():
                for line in af.read_text(encoding="utf-8").splitlines():
                    p = line.split("\t")
                    adm[p[0]] = p[1]
            with zipfile.ZipFile(pathlib.Path(geodata) / "cities1000.zip") as z, z.open("cities1000.txt") as f:
                for line in io.TextIOWrapper(f, encoding="utf-8"):
                    p = line.rstrip("\n").split("\t")
                    if int(p[0]) in known or p[7] == "PPLX":  # районы больших городов — не отдельные города
                        continue
                    self.cities.append({"gid": int(p[0]), "cc": p[8], "names": {p[1], p[2]}, "lat": float(p[4]),
                                        "lon": float(p[5]), "pop": int(p[14] or 0), "region": adm.get(f"{p[8]}.{p[10]}", "")})
        self.grid = {}
        self.by_name = {}
        for c in self.cities:
            self.grid.setdefault((int(c["lat"] // 1), int(c["lon"] // 1)), []).append(c)
            if c["pop"] >= 50000:
                for n in c["names"]:
                    if len(n) > 3:
                        self.by_name.setdefault(n.lower(), []).append(c)
        # Названия стран и крупных регионов: подсказка страны в заголовке и не город сами по себе («Florida», «Jamaica»).
        # Значение — (страна, штат): штат США должен совпасть с регионом города («Sandy, NJ» — не Сэнди в Юте)
        self.regions = {}
        for c in json.loads((DATA / "countries.json").read_text(encoding="utf-8")):
            for n in (c["en"], c["name"]):
                self.regions[n.lower()] = (c["iso"], "")
        for cc, names in REGIONS.items():
            for n in names.split(", "):
                state = US_STATES.get(n, n) if cc == "US" and (n in US_STATES or n in US_STATES.values()) else ""
                self.regions[n.lower()] = (cc, state)

    def by_coords(self, lat, lon):
        best = None
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for c in self.grid.get((int(lat // 1) + i, int(lon // 1) + j), []):
                    d = km(lat, lon, c["lat"], c["lon"])
                    # ближайший заметный город: посёлок-спутник рядом уступает большому городу чуть дальше
                    score = d - 6 * math.log10(max(c["pop"], 1000))
                    if d <= 30 and (not best or score < best[0]):
                        best = (score, c)
        return best[1] if best else None

    def by_title(self, title):
        """Город по заголовку — только если в заголовке есть и страна (или штат, регион) этого города
        и упомянут ровно один город: «Venice Beach» без «Italy» в Венецию не попадёт."""
        t = title.lower()
        has = lambda n, s: n in s and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", s)
        # «Miramar Beach», «Sandy Hook», «Panama City Beach» — другие места, названные по городу
        other = r"(?!\w)\s+(beach|bay|hook|island|lake|park|harbou?r|point|key)\b"
        found = [(n, cs) for n, cs in self.by_name.items() if n not in self.regions and has(n, t)
                 and not re.search(r"(?<!\w)" + re.escape(n) + other, t)]
        # «Panama City Beach» поглощает «Panama City»; два разных города — заголовок неоднозначен
        found = [(n, cs) for n, cs in found if not any(n != m and n in m for m, _ in found)]
        rest = t
        for n, _ in found:
            rest = rest.replace(n, " ")
        hints = [n for n in self.regions if has(n, rest)]
        hints = [n for n in hints if not any(n != m and n in m for m in hints)]  # «Baja California», а не «California»
        if not hints or len(found) != 1:
            return None
        ok = lambda c: any(self.regions[h][0] == c["cc"] and self.regions[h][1] in ("", c.get("region", "")) for h in hints)
        cs = sorted((c for c in found[0][1] if ok(c)), key=lambda c: -c["pop"])
        return cs[0] if cs and (len(cs) == 1 or cs[0]["pop"] >= 5 * cs[1]["pop"]) else None


def clean_title(t):
    """Название камеры из заголовка: без эмодзи, хэштегов, «LIVE», «Webcam», «4K» и хвоста после «|»."""
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", t)
    first = t = re.split(r"\s[|•·]\s|\s#|\s//\s", t)[0]
    t = re.sub(r"[【】\[\]]", " ", t)
    t = re.sub(r"(?i)\blive\s+(web)?cam(era)?\b|\bwebcams?\b|\blivecam\b", " ", t)
    t = re.sub(r"(?i)\b(live ?stream(ing)?|live now|live|now|en vivo|ao vivo|en direct|in diretta|4k|uhd|full hd|hd|24/7|24h)\b", " ", t)
    t = re.sub(r"\(\s*\)|^\s*from\b", " ", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–:,.@")
    if len(t) < 3:
        t = first.strip()
    letters = [ch for ch in t if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.7:
        t = t.title()
    return t[:90] or "Webcam"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=6000, help="сколько единиц квоты потратить за этот запуск")
    ap.add_argument("--geodata", help="папка GeoNames с cities1000.zip (для привязки к небольшим городам)")
    ap.add_argument("--pages", type=int, default=3, help="сколько страниц результатов брать на один запрос")
    ap.add_argument("--dry-run", action="store_true", help="только показать найденное, базу не менять")
    args = ap.parse_args()
    api = Api(load_key(), args.budget)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"done": [], "pages": {}}
    jobs = [("q", q) for q in QUERIES] + [("geo", name) for name, _, _ in CENTERS]
    centers = {name: (lat, lon) for name, lat, lon in CENTERS}
    if all(f"{k}:{v}" in state["done"] for k, v in jobs):
        # всё пройдено — начинаем круг заново: появляются новые эфиры
        state = {"done": [], "pages": {}, "rejected": state.get("rejected", []), "found": state.get("found", {})}

    found = state.pop("found", {})  # найденное прошлым запуском, который оборвался до разбора
    stop = None
    try:
        for kind, val in jobs:
            jid = f"{kind}:{val}"
            if jid in state["done"]:
                continue
            p = {"part": "id", "type": "video", "eventType": "live", "maxResults": 50, "videoEmbeddable": "true"}
            if kind == "q":
                p["q"] = val
            else:
                lat, lon = centers[val]
                p.update(location=f"{lat},{lon}", locationRadius="1000km", q="webcam|live cam|camera|камера|kamera")
            token = state["pages"].get(jid)
            for _ in range(args.pages):
                if token:
                    p["pageToken"] = token
                r = api.call("search", **p)
                for it in r.get("items", []):
                    found.setdefault(it["id"]["videoId"], jid)
                token = r.get("nextPageToken")
                if not token:
                    break
                state["pages"][jid] = token  # если квота кончится, следующий запуск продолжит с этой страницы
            state["done"].append(jid)
            state["pages"].pop(jid, None)
            if not args.dry_run:  # квота уже потрачена: найденное сохраняем сразу, на случай обрыва
                STATE.write_text(json.dumps({**state, "found": found}, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{jid}: всего найдено {len(found)}, потрачено {api.spent}", flush=True)
    except Quota as e:
        stop = str(e)

    cams = json.loads((DATA / "cameras.json").read_text(encoding="utf-8"))
    # "rejected" в файле состояния — видео, которые убрали вручную (неверный город и т. п.): больше не добавляются
    have = {s["id"] for c in cams for s in c["sources"] if s["type"] == "yt"} | set(state.get("rejected", []))
    new_ids = [i for i in found if i not in have]
    info = {}
    for i in range(0, len(new_ids), 50):
        try:
            r = api.call("videos", part="snippet,status,recordingDetails", id=",".join(new_ids[i:i + 50]), maxResults=50)
        except Quota:
            api.budget += 50  # проверку найденного доводим до конца: она почти бесплатна
            r = api.call("videos", part="snippet,status,recordingDetails", id=",".join(new_ids[i:i + 50]), maxResults=50)
        for it in r.get("items", []):
            info[it["id"]] = it

    places = Places(args.geodata)
    added, skipped = [], {"не эфир/нельзя встраивать": 0, "не похоже на камеру": 0, "город не определён": 0}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for vid, it in info.items():
        sn, st = it["snippet"], it["status"]
        if sn.get("liveBroadcastContent") != "live" or not st.get("embeddable"):
            skipped["не эфир/нельзя встраивать"] += 1
            continue
        title, desc = sn.get("title", ""), sn.get("description", "")[:600]
        if NOT_CAM.search(title) or not CAM_WORDS.search(title + " " + desc):
            skipped["не похоже на камеру"] += 1
            continue
        loc = it.get("recordingDetails", {}).get("location") or {}
        city = places.by_coords(loc["latitude"], loc["longitude"]) if "latitude" in loc else places.by_title(title)
        if not city:
            skipped["город не определён"] += 1
            continue
        name = clean_title(title)
        cam = {"city_gid": city["gid"], "ru": name, "en": name,
               "sources": [{"type": "yt", "id": vid, "by": sn.get("channelTitle", "")}], "found": f"youtube {today}"}
        if "latitude" in loc:
            cam.update(lat=round(loc["latitude"], 5), lon=round(loc["longitude"], 5))
        added.append(cam)

    print(f"\nНайдено эфиров: {len(found)}, новых для базы: {len(new_ids)}, добавлено камер: {len(added)}")
    for k, v in skipped.items():
        print(f"  пропущено ({k}): {v}")
    names = {c["gid"]: next(iter(c["names"])) for c in places.cities}
    for c in added if args.dry_run else added[:30]:
        print("  +", c["en"], "—", names.get(c["city_gid"]), "(по координатам)" if "lat" in c else "(по названию)")
    if stop:
        print("Остановлено:", stop)
    print(f"Потрачено единиц квоты: {api.spent}")
    if args.dry_run:
        return
    cams += added
    (DATA / "cameras.json").write_text(json.dumps(cams, ensure_ascii=False, indent=1), encoding="utf-8")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    known = {c["gid"] for c in json.loads((DATA / "cities.json").read_text(encoding="utf-8"))}
    if any(c["city_gid"] not in known for c in added):
        print("Есть камеры в городах, которых нет в справочнике: перезапустите prepare_data.py, затем build_posts.py")


if __name__ == "__main__":
    main()
