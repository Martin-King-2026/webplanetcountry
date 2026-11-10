"""Страница служебных камер для компьютера: «Служебные камеры.html» в папке проекта.
Открывается двойным щелчком, без Blogger и интернета для самой страницы (трансляции, конечно, грузятся из сети).
В папку для GitHub она не попадает: там только то, что перечислено в export_github.py.

Запуск: py tools/build_private_page.py (build_posts.py запускает его сам)
"""
import json
import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from build_posts import cam_kind, clean_ru, is_private  # noqa: E402

OUT = ROOT / "Служебные камеры.html"


def main():
    cams = json.loads((ROOT / "data" / "cameras.json").read_text(encoding="utf-8"))
    cities = {c["gid"]: c for c in json.loads((ROOT / "data" / "cities.json").read_text(encoding="utf-8"))}
    countries = {c["iso"]: c for c in json.loads((ROOT / "data" / "countries.json").read_text(encoding="utf-8"))}
    rows = []
    for c in cams:
        if c.get("off") or not is_private(c):
            continue
        ci = cities.get(c["city_gid"], {})
        co = countries.get(ci.get("cc"), {})
        rows.append({"ru": c["ru"], "en": c["en"], "kind": cam_kind(c),
                     "city": clean_ru(ci.get("ru")) or ci.get("en", ""), "country": clean_ru(co.get("ru")) or co.get("en", ""),
                     "sources": [{k: v for k, v in s.items() if k in ("type", "id", "src", "by", "refresh")} for s in c["sources"]]})
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = (ROOT / "tools" / "private-page.tpl.html").read_text(encoding="utf-8")
    OUT.write_text(tpl.replace("/*@@DATA@@*/[]", data).replace("@@DATE@@", date.today().strftime("%d.%m.%Y")), encoding="utf-8")
    print(f"Служебные камеры: {len(rows)} → {OUT.name}")


if __name__ == "__main__":
    main()
