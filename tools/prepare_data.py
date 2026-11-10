"""Готовит справочники стран и городов из GeoNames и Викиданных.

Нужны файлы GeoNames (https://download.geonames.org/export/dump/) в папке --geodata:
countryInfo.txt, timeZones.txt, admin1CodesASCII.txt, cities1000.zip, alternateNamesV2.zip.

Результат: data/countries.json и data/cities.json.
Запуск: py tools/prepare_data.py --geodata ПАПКА [--min-pop 15000]
"""
import argparse
import csv
import io
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = "WebPlanetBuilder/0.1 (+https://webplanetcountry.blogspot.com/)"
SPARQL = "https://query.wikidata.org/sparql"

csv.field_size_limit(10_000_000)


def tsv_rows(f):
    for line in f:
        if not line.strip() or line.startswith("#"):
            continue
        yield line.rstrip("\n").split("\t")


def load_countries(geo):
    out = {}
    with open(geo / "countryInfo.txt", encoding="utf-8") as f:
        for r in tsv_rows(f):
            out[r[0]] = {
                "iso": r[0], "iso3": r[1], "name": r[4], "capital_en": r[5],
                "area": float(r[6] or 0), "population": int(r[7] or 0), "continent": r[8],
                "currency_code": r[10], "currency_en": r[11], "gid": int(r[16]) if r[16] else None,
            }
    return out


def load_timezones(geo):
    tz = {}
    with open(geo / "timeZones.txt", encoding="utf-8") as f:
        next(f)
        for r in tsv_rows(f):
            tz[r[1]] = {"cc": r[0], "gmt": float(r[2]), "dst": float(r[3])}
    return tz


def load_admin1(geo):
    out = {}
    with open(geo / "admin1CodesASCII.txt", encoding="utf-8") as f:
        for r in tsv_rows(f):
            out[r[0]] = {"en": r[1], "gid": int(r[3])}
    return out


def load_cities(geo):
    # Колонки таблицы geoname: https://download.geonames.org/export/dump/readme.txt
    with zipfile.ZipFile(geo / "cities1000.zip") as z:
        with z.open("cities1000.txt") as raw:
            for r in tsv_rows(io.TextIOWrapper(raw, encoding="utf-8")):
                yield {
                    "gid": int(r[0]), "name": r[1], "ascii": r[2], "lat": float(r[4]), "lon": float(r[5]),
                    "fcode": r[7], "cc": r[8], "a1": r[10], "pop": int(r[14] or 0), "tz": r[17],
                }


def load_altnames(geo, wanted):
    """ru/en-имена и ссылки на Википедию для нужных geonameid."""
    names = {}
    with zipfile.ZipFile(geo / "alternateNamesV2.zip") as z:
        with z.open("alternateNamesV2.txt") as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                p = line.split("\t", 8)
                lang = p[2]
                if lang not in ("ru", "en", "link"):
                    continue
                gid = int(p[1])
                if gid not in wanted:
                    continue
                pref, short, colloq, hist = (x == "1" for x in p[4:8])
                if colloq or hist:
                    continue
                e = names.setdefault(gid, {})
                if lang == "link":
                    url = p[3]
                    for host, key in (("en.wikipedia.org/wiki/", "wen"), ("ru.wikipedia.org/wiki/", "wru")):
                        if host in url and key not in e:
                            e[key] = urllib.parse.unquote(url.split(host, 1)[1]).replace("_", " ")
                    continue
                # приоритет: предпочтительное имя > обычное > краткое
                rank = 3 if pref else (1 if short else 2)
                if rank > e.get(lang + "_rank", 0):
                    e[lang] = p[3]
                    e[lang + "_rank"] = rank
    return names


def sparql(query):
    url = SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)["results"]["bindings"]
        except Exception as e:  # noqa: BLE001 — повторяем при сетевых сбоях
            print(f"  SPARQL повтор {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("Викиданные не ответили")


def wikidata_countries(gids):
    values = " ".join(f'"{g}"' for g in gids)
    base = f"VALUES ?gid {{ {values} }} ?item wdt:P1566 ?gid ."
    items = {}
    for b in sparql(f"""SELECT ?gid ?item ?ru ?en ?coord WHERE {{ {base}
        OPTIONAL {{ ?item rdfs:label ?ru FILTER(lang(?ru)='ru') }}
        OPTIONAL {{ ?item rdfs:label ?en FILTER(lang(?en)='en') }}
        OPTIONAL {{ ?item wdt:P625 ?coord }} }}"""):
        gid = int(b["gid"]["value"])
        if gid in items:
            continue
        it = {"qid": b["item"]["value"].rsplit("/", 1)[1]}
        for k in ("ru", "en"):
            if k in b:
                it[k] = b[k]["value"]
        if "coord" in b:  # Point(lon lat)
            lon, lat = b["coord"]["value"][6:-1].split()
            it["lat"], it["lon"] = round(float(lat), 4), round(float(lon), 4)
        items[gid] = it

    props = {"gov": "P122", "head_state": "P35", "head_gov": "P6", "capital": "P36",
             "languages": "P37", "currency": "P38", "sovereign": "P17"}
    for key, pid in props.items():
        print(f"  Викиданные: {key}")
        rows = sparql(f"""SELECT ?gid ?item ?v ?ru ?en WHERE {{ {base}
            ?item wdt:{pid} ?v .
            OPTIONAL {{ ?v rdfs:label ?ru FILTER(lang(?ru)='ru') }}
            OPTIONAL {{ ?v rdfs:label ?en FILTER(lang(?en)='en') }} }}""")
        for b in rows:
            gid = int(b["gid"]["value"])
            it = items.get(gid)
            if not it or b["item"]["value"].rsplit("/", 1)[1] != it["qid"]:
                continue
            vq = b["v"]["value"].rsplit("/", 1)[1]
            if key == "sovereign" and vq == it["qid"]:
                continue
            en = b.get("en", {}).get("value") or b.get("ru", {}).get("value") or vq
            ru = b.get("ru", {}).get("value") or en
            lst = it.setdefault(key, [])
            if all(x["qid"] != vq for x in lst):
                lst.append({"qid": vq, "ru": ru, "en": en})
        time.sleep(1)

    for key, pid in (("flag", "P41"), ("coa", "P94")):
        for b in sparql(f"SELECT ?gid ?item ?f WHERE {{ {base} ?item wdt:{pid} ?f . }}"):
            gid = int(b["gid"]["value"])
            it = items.get(gid)
            if it and key not in it and b["item"]["value"].rsplit("/", 1)[1] == it["qid"]:
                it[key] = urllib.parse.unquote(b["f"]["value"].rsplit("/", 1)[1])
        time.sleep(1)
    return items


def wikidata_cities(gids, cache_path):
    """QID и ru/en-названия городов из Викиданных по GeoNames ID (P1566). Кэшируется между запусками."""
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [g for g in gids if str(g) not in cache]
    for i in range(0, len(todo), 250):
        batch = todo[i:i + 250]
        values = " ".join(f'"{g}"' for g in batch)
        rows = sparql(f"""SELECT ?gid ?item ?ru ?en ?sl WHERE {{ VALUES ?gid {{ {values} }} ?item wdt:P1566 ?gid ; wikibase:sitelinks ?sl .
            OPTIONAL {{ ?item rdfs:label ?ru FILTER(lang(?ru)='ru') }}
            OPTIONAL {{ ?item rdfs:label ?en FILTER(lang(?en)='en') }} }}""")
        for g in batch:
            cache.setdefault(str(g), {})
        # Один GeoNames ID бывает у нескольких элементов (город, коммуна, исторический центр): берём самый «известный»
        for b in sorted(rows, key=lambda b: -int(b["sl"]["value"])):
            e = cache[b["gid"]["value"]]
            if "qid" in e:
                continue
            e["qid"] = b["item"]["value"].rsplit("/", 1)[1]
            for k in ("ru", "en"):
                if k in b:
                    e[k] = b[k]["value"]
        print(f"  Викиданные, города: {min(i + 250, len(todo))}/{len(todo)}", flush=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        time.sleep(0.5)
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--geodata", required=True, type=pathlib.Path)
    ap.add_argument("--min-pop", type=int, default=15000, help="минимальное население города (столицы берутся всегда)")
    args = ap.parse_args()
    geo = args.geodata
    DATA.mkdir(exist_ok=True)

    countries = load_countries(geo)
    tzs = load_timezones(geo)
    admin1 = load_admin1(geo)

    forced = set()
    cams_file = DATA / "cameras.json"
    if cams_file.exists():
        forced = {c["city_gid"] for c in json.loads(cams_file.read_text(encoding="utf-8"))}
    # города из списка камеры-онлайн.рф (tools/import_kamonline.py) остаются в справочнике, даже если они меньше порога
    sv_file = DATA / "streetview.json"
    if sv_file.exists():
        forced |= {int(g) for g in json.loads(sv_file.read_text(encoding="utf-8"))}

    cities = [c for c in load_cities(geo)
              if c["cc"] in countries and (c["pop"] >= args.min_pop or c["fcode"] == "PPLC" or c["gid"] in forced)]
    print(f"Городов: {len(cities)} (население от {args.min_pop}, плюс столицы и города камер)")
    missing = forced - {c["gid"] for c in cities}
    if missing:
        print(f"ВНИМАНИЕ: города камер не найдены в cities1000: {sorted(missing)}")

    used_a1 = {f'{c["cc"]}.{c["a1"]}' for c in cities}
    wanted = {c["gid"] for c in cities} | {admin1[k]["gid"] for k in used_a1 if k in admin1}
    wanted |= {c["gid"] for c in countries.values() if c["gid"]}
    print("Читаю alternateNamesV2 (несколько минут)...")
    alt = load_altnames(geo, wanted)

    print("Запрашиваю Викиданные по странам...")
    wd = wikidata_countries([c["gid"] for c in countries.values() if c["gid"]])

    out_c = []
    for iso, c in sorted(countries.items()):
        w = wd.get(c["gid"], {})
        a = alt.get(c["gid"], {})
        c.update({k: v for k, v in w.items()})
        c["ru"] = w.get("ru") or a.get("ru") or c["name"]
        c["en"] = c["name"]
        c["tz"] = sorted(({"id": k, **{x: v[x] for x in ("gmt", "dst")}} for k, v in tzs.items() if v["cc"] == iso),
                         key=lambda x: x["gmt"])
        out_c.append(c)

    print("Запрашиваю Викиданные по городам...")
    wdc = wikidata_cities([c["gid"] for c in cities], DATA / "wd_cities_cache.json")

    out_ci = []
    for c in cities:
        a = alt.get(c["gid"], {})
        w = wdc.get(str(c["gid"]), {})
        reg = admin1.get(f'{c["cc"]}.{c["a1"]}')
        ra = alt.get(reg["gid"], {}) if reg else {}
        tz = tzs.get(c["tz"], {})
        out_ci.append({
            "gid": c["gid"], "cc": c["cc"], "qid": w.get("qid", ""),
            "ru": w.get("ru") or a.get("ru") or "", "en": a.get("en") or c["name"], "ascii": c["ascii"],
            "lat": round(c["lat"], 5), "lon": round(c["lon"], 5), "pop": c["pop"], "fcode": c["fcode"],
            "tz": c["tz"], "gmt": tz.get("gmt"), "dst": tz.get("dst"),
            "region_en": reg["en"] if reg else "", "region_ru": ra.get("ru", ""),
            "wen": a.get("wen", ""), "wru": a.get("wru", ""),
        })

    (DATA / "countries.json").write_text(json.dumps(out_c, ensure_ascii=False, indent=0), encoding="utf-8")
    (DATA / "cities.json").write_text(json.dumps(out_ci, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with_ru = sum(1 for c in out_ci if c["ru"])
    print(f"Готово: стран {len(out_c)}, городов {len(out_ci)}, с русским названием {with_ru} ({with_ru * 100 // max(1, len(out_ci))}%)")


if __name__ == "__main__":
    main()
