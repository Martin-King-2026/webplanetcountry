"""Исправляет в data/cities.json русские названия-муниципалитеты:
«Киришское городское поселение» → «Кириши», «городской округ Торжок» → «Торжок».
Запуск: py tools/fix_city_names.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from prepare_data import DATA, fix_ru  # noqa: E402


def main():
    f = DATA / "cities.json"
    cities = json.loads(f.read_text(encoding="utf-8"))
    n = 0
    for c in cities:
        new = fix_ru(c.get("ru", ""), c.get("wru", ""))
        if new != c.get("ru"):
            print(f'  {c["ru"]} → {new}')
            c["ru"] = new
            n += 1
    f.write_text(json.dumps(cities, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("исправлено названий:", n)


if __name__ == "__main__":
    main()
