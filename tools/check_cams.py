"""Проверяет трансляции из data/cameras.json: работает ли источник и разрешено ли встраивание.

YouTube проверяется только официальными способами:
  • oEmbed — существует ли видео и разрешил ли владелец встраивание (ключ не нужен);
  • YouTube Data API v3 — идёт ли эфир прямо сейчас (нужен бесплатный ключ: файл data/youtube_key.txt
    или переменная окружения YT_API_KEY). Один запрос проверяет 50 видео и стоит 1 единицу из 10 000 в сутки.
Страницы просмотра YouTube не запрашиваются: при частых запросах YouTube блокирует их (ошибка 429).
Если YouTube всё же ответит 429, проверка YouTube останавливается и честно об этом сообщает.

Остальные типы: iframe — разрешён ли показ во фрейме, hls — отдаёт ли сервер поток чужому сайту (CORS),
img — отдаётся ли картинка.

С --fix (нужен ключ) скрипт ещё и чинит базу:
  • закончившийся эфир YouTube заменяется текущим эфиром той же камеры на том же канале
    (по совпадению отличительных слов названия; 2–5 единиц квоты на канал);
  • камера без единого идущего эфира получает "off" и на сайт не выводится, но остаётся в базе;
  • камера с "off", у которой эфир снова идёт или нашлась замена, возвращается на сайт;
  • эфир канала (ytc), который сейчас не идёт, переносится в конец списка трансляций.
Источники других типов при --fix не удаляются: их сбои часто временные, о них скрипт только сообщает.
Перед изменением база копируется в data/cameras.backup.json. После --fix пересоберите посты: py tools/build_posts.py

Запуск: py tools/check_cams.py [--only-bad] [--fix] [--youtube-only]
"""
import argparse
import collections
import json
import os
import pathlib
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
SITE = "https://webplanetcountry.blogspot.com"


class RateLimited(Exception):
    pass


def get(url, headers=None, limit=65536):
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})}), timeout=20)
    return {k.lower(): v for k, v in r.headers.items()}, r.read(limit)


def oembed(vid):
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote("https://www.youtube.com/watch?v=" + vid)
    try:
        get(url)
        return "ok"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited from e
        return {401: "НЕЛЬЗЯ ВСТРАИВАТЬ", 403: "НЕЛЬЗЯ ВСТРАИВАТЬ", 404: "НЕ РАБОТАЕТ (видео удалено)"}.get(e.code, f"ОШИБКА {e.code}")


class YouTube:
    """Официальный YouTube Data API v3. Ключ не печатается и не попадает в сообщения об ошибках."""

    def __init__(self, key):
        self.key, self.units = key, 0

    def call(self, method, **params):
        url = f"https://www.googleapis.com/youtube/v3/{method}?" + urllib.parse.urlencode({**params, "key": self.key})
        self.units += 1
        try:
            return json.loads(get(url, limit=10_000_000)[1])
        except urllib.error.HTTPError as e:
            try:
                reason = json.loads(e.read())["error"]["errors"][0]["reason"]
            except Exception:  # noqa: BLE001
                reason = ""
            if e.code == 404:
                return {"items": []}
            if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded") or e.code == 429:
                raise RateLimited(reason or "429") from None
            raise SystemExit(f"Ошибка YouTube Data API: {e.code} {reason}") from None

    def videos(self, ids):
        out = {}
        ids = list(ids)
        for i in range(0, len(ids), 50):
            for it in self.call("videos", part="snippet,status", id=",".join(ids[i:i + 50]), maxResults=50).get("items", []):
                out[it["id"]] = it
        return out

    def channel_live(self, channel, pages=3):
        """Идущие сейчас эфиры канала: последние загрузки канала (туда попадают и эфиры) + проверка их состояния."""
        ids, token = [], None
        for _ in range(pages):
            p = {"part": "contentDetails", "playlistId": "UU" + channel[2:], "maxResults": 50}
            if token:
                p["pageToken"] = token
            r = self.call("playlistItems", **p)
            ids += [it["contentDetails"]["videoId"] for it in r.get("items", [])]
            token = r.get("nextPageToken")
            if not token:
                break
        return [{"id": k, "title": v["snippet"]["title"], "embeddable": v["status"].get("embeddable", False)}
                for k, v in self.videos(ids).items() if is_live(v)]


def is_live(item):
    return item["snippet"].get("liveBroadcastContent") == "live"


GENERIC = set("""live stream streaming webcam webcams cam cams camera cameras kamera kamery webkamera hd 4k uhd view
vivo directo direct diretta online ao en in the of and di del de la le el des du da und im am auf livecam
веб камера камеры онлайн прямой эфир трансляция""".split())


def tokens(t):
    t = re.sub(r"[^\w\s]", " ", (t or "").lower())
    return {w for w in t.split() if len(w) > 2 and w not in GENERIC and not w.isdigit()}


def match_channel(olds, lives, in_base):
    """olds: {старый_id: название}, lives: эфиры канала. → {старый_id: новый_id} для уверенных совпадений."""
    cands = [x for x in lives if x["embeddable"] and x["id"] not in in_base
             and not re.search(r"(?i)offline|replay|повтор", x["title"])]
    titles = list(olds.values()) + [x["title"] for x in lives]
    # слова, общие для большинства эфиров канала (город, страна, имя канала), не отличают камеры друг от друга
    df = collections.Counter(w for t in titles for w in tokens(t))
    common = {w for w, n in df.items() if len(titles) >= 3 and n / len(titles) > 0.5}
    pairs = []
    for o, ot in olds.items():
        a = tokens(ot) - common
        for x in cands:
            b = tokens(x["title"]) - common
            if tokens(ot) == tokens(x["title"]):
                sc = 2.0
            elif a and b:
                # все отличительные слова короткого названия есть в длинном: «Skarsvåg Live» ⊂ «Webcam from Skarsvåg»
                sc = max(len(a & b) / len(a | b), 0.6 if a <= b or b <= a else 0)
            else:
                sc = 0
            pairs.append((sc, o, x["id"]))
    out, used = {}, set()
    single = len(olds) == 1 and len(lives) == 1 and len(cands) == 1
    for sc, o, nid in sorted(pairs, reverse=True):
        if o in out or nid in used:
            continue
        if sc >= 0.6 or (single and not (tokens(olds[o]) - common)):
            out[o] = nid
            used.add(nid)
    return out


def fix_youtube(cams, yt):
    """Чинит YouTube-источники базы на месте; возвращает счётчики изменений."""
    stat = collections.Counter()
    ids = sorted({s["id"] for c in cams for s in c["sources"] if s["type"] == "yt"})
    info = yt.videos(ids)
    live = {i for i, v in info.items() if is_live(v) and v["status"].get("embeddable")}
    ended = {i: info[i]["snippet"] for i in ids if i in info and i not in live}
    by_ch = collections.defaultdict(dict)
    for i, sn in ended.items():
        by_ch[sn["channelId"]][i] = sn["title"]
    channels = sorted(set(by_ch) | {s["id"] for c in cams for s in c["sources"] if s["type"] == "ytc"})
    print(f"YouTube: в эфире {len(live)} из {len(ids)}, закончились {len(ended)}, удалены {len(ids) - len(info)}; "
          f"ищу текущие эфиры на {len(channels)} каналах…", flush=True)
    ch_live, repl = {}, {}
    for n, ch in enumerate(channels, 1):
        ch_live[ch] = yt.channel_live(ch)
        if ch in by_ch:
            repl.update(match_channel(by_ch[ch], ch_live[ch], set(ids)))
        if n % 50 == 0:
            print(f"  каналов {n}/{len(channels)}, квота {yt.units}", flush=True)

    today = date.today().isoformat()
    for c in cams:
        keep, later = [], []
        for s in c["sources"]:
            if s["type"] == "yt":
                if s["id"] in live:
                    keep.append(s)
                elif repl.get(s["id"]):
                    keep.append({**s, "id": repl[s["id"]]})
                    stat["эфир заменён текущим эфиром канала"] += 1
                    print(f"  замена: {c['ru']} — {s['id']} → {repl[s['id']]}")
                else:
                    stat["эфира нет, замены нет"] += 1
            elif s["type"] == "ytc":
                if ch_live.get(s["id"]):
                    keep.append(s)
                else:
                    later.append(s)  # канал может выйти в эфир позже: оставляем запасным в конце списка
                    stat["канал не в эфире (перенесён в конец списка)"] += 1
            else:
                keep.append(s)
        if keep:
            c["sources"] = keep + later
            if c.pop("off", None):
                stat["камера вернулась на сайт"] += 1
                print(f"  вернулась: {c['ru']}")
        elif not c.get("off"):
            c["off"] = today  # источники не трогаем: при следующей проверке эфир может найтись
            stat["камера скрыта: нет ни одного эфира"] += 1
            print(f"  скрыта: {c['ru']}")
    return stat


def frame_ok(h):
    xfo = h.get("x-frame-options", "").strip().upper()
    if xfo in ("DENY", "SAMEORIGIN"):
        return False
    m = re.search(r"frame-ancestors([^;]*)", h.get("content-security-policy", ""), re.I)
    return not m or any(x in m.group(1) for x in ("*", "https:", "blogspot"))


def check_other(s):
    u = s.get("src", "")
    if s["type"] == "link":
        return "ссылка (не проверяется)"
    if not u.startswith("https://"):
        return "НЕ РАБОТАЕТ: адрес не https, на сайте не покажется"
    try:
        if s["type"] == "iframe":
            h, _ = get(u, {"Referer": SITE + "/"})
            return "доступен" if frame_ok(h) else "НЕЛЬЗЯ ВСТРАИВАТЬ (запрет фрейма)"
        if s["type"] == "hls":
            h, body = get(u, {"Origin": SITE, "Referer": SITE + "/"}, 4096)
            if not body.lstrip().startswith(b"#EXTM3U"):
                return "НЕ РАБОТАЕТ: это не поток"
            return "доступен" if h.get("access-control-allow-origin") in ("*", SITE) else "НЕЛЬЗЯ ВСТРАИВАТЬ (нет CORS)"
        if s["type"] == "img":
            h, _ = get(u, {"Referer": SITE + "/"}, 2048)
            return "доступен" if h.get("content-type", "").startswith("image/") else "НЕ РАБОТАЕТ: не картинка"
    except urllib.error.HTTPError as e:
        return f"НЕ РАБОТАЕТ: HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 — сервер не ответил
        return f"НЕ РАБОТАЕТ: {type(e).__name__}"
    return "неизвестный тип"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-bad", action="store_true", help="показывать только проблемные трансляции")
    ap.add_argument("--fix", action="store_true", help="заменить закончившиеся эфиры YouTube и обновить пометки off")
    ap.add_argument("--youtube-only", action="store_true", help="не проверять источники других типов")
    args = ap.parse_args()
    key = os.environ.get("YT_API_KEY", "")
    kf = ROOT / "data" / "youtube_key.txt"
    if not key and kf.exists():
        key = kf.read_text(encoding="utf-8").strip()
    yt = YouTube(key) if key else None
    cams_file = ROOT / "data" / "cameras.json"
    cams = json.loads(cams_file.read_text(encoding="utf-8"))

    if args.fix:
        if not yt:
            raise SystemExit("Для --fix нужен ключ YouTube Data API: data/youtube_key.txt")
        try:
            stat = fix_youtube(cams, yt)
        except RateLimited:
            raise SystemExit("YouTube: суточная квота ключа исчерпана, база не изменена. Повторите завтра.") from None
        shutil.copyfile(cams_file, ROOT / "data" / "cameras.backup.json")
        cams_file.write_text(json.dumps(cams, ensure_ascii=False, indent=1), encoding="utf-8")
        for k, v in stat.items():
            print(f"  {k}: {v}")
        print(f"База обновлена (копия прежней — data/cameras.backup.json). На сайте камер: "
              f"{sum(1 for c in cams if not c.get('off'))} из {len(cams)}. Пересоберите посты: py tools/build_posts.py")

    jobs = [(c, s) for c in cams for s in c["sources"] if not (args.youtube_only and s["type"] not in ("yt", "ytc"))]
    yt_ids = sorted({s["id"] for _, s in jobs if s["type"] == "yt"})
    info = {}
    if yt:
        try:
            info = yt.videos(yt_ids)
            print(f"YouTube Data API: проверено {len(info)} из {len(yt_ids)} видео")
        except RateLimited:
            yt = None
            print("YouTube: квота ключа исчерпана, проверяю только встраивание через oEmbed.")
    if not yt:
        print("Ключа YouTube Data API нет: проверяю только встраивание, эфир не проверяется (см. описание в начале файла).")

    yt_stopped = False
    bad = 0
    for c, s in jobs:
        if s["type"] == "yt":
            if yt:
                v = info.get(s["id"])
                if not v:
                    r = "НЕ РАБОТАЕТ (видео удалено или скрыто)"
                else:
                    emb = v["status"].get("embeddable", False)
                    r = "в эфире" if is_live(v) and emb else ("НЕЛЬЗЯ ВСТРАИВАТЬ" if not emb else "НЕ В ЭФИРЕ")
            elif yt_stopped:
                r = "не проверено: YouTube ограничил запросы"
            else:
                try:
                    r = oembed(s["id"])
                    r = "встраивание разрешено" if r == "ok" else r
                    time.sleep(0.5)
                except RateLimited:
                    yt_stopped = True
                    r = "не проверено: YouTube ограничил запросы (429), повторите позже или добавьте ключ API"
        elif s["type"] == "ytc":
            r = "эфир канала (не проверяется)"
        else:
            r = check_other(s)
            time.sleep(0.3)
        problem = r.startswith(("НЕ", "НЕЛЬЗЯ"))
        bad += problem
        if problem or not args.only_bad:
            off = " [скрыта]" if c.get("off") else ""
            print(f"{'!!' if problem else '  '} {c['ru']}{off} [{s['type']} {s.get('id') or s.get('src')}]: {r}", flush=True)
    print(f"\nПроверено трансляций: {len(jobs)}, с проблемами: {bad}" + (f"; потрачено единиц квоты YouTube: {yt.units}" if yt else ""))


if __name__ == "__main__":
    main()
