"""Собирает тему Blogger: вставляет style.css, app.js и меню в theme.tpl.xml и проверяет XML.

Запуск: py tools/build_theme.py [--prefix /2026/10/]
"""
import argparse
import json
import pathlib
import sys
import xml.dom.minidom

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "theme" / "src"
OUT = ROOT / "theme" / "webplanet-theme.xml"


def main():
    ap = argparse.ArgumentParser()
    # Основной сайт теперь на GitHub Pages: меню темы Blogger ведёт туда. Для меню по постам самого блога: --prefix /2026/10/
    ap.add_argument("--prefix", default="https://martin-king-2026.github.io/webplanetcountry/",
                    help="куда ведёт меню: адрес основного сайта или путь постов блога")
    args = ap.parse_args()

    css = (SRC / "style.css").read_text(encoding="utf-8")
    js = (SRC / "app.js").read_text(encoding="utf-8")
    menu = (SRC / "menu.html").read_text(encoding="utf-8").replace("@@P@@", args.prefix)
    for name, text in (("style.css", css), ("app.js", js), ("menu.html", menu)):
        if "]]>" in text:
            sys.exit(f"{name}: содержит ']]>', это сломает CDATA в теме")

    tpl = (SRC / "theme.tpl.xml").read_text(encoding="utf-8")
    # Меню идёт и в виджет HTML1, и в скрипт: если Blogger не выведет виджет, скрипт вставит меню сам
    js = js.replace("/*@@MENU_JS@@*/''", json.dumps(menu, ensure_ascii=False).replace("</", "<\\/"))
    out = tpl.replace("/*@@CSS@@*/", css).replace("/*@@JS@@*/", js).replace("@@MENU@@", menu)

    try:
        xml.dom.minidom.parseString(out.encode("utf-8"))
    except Exception as e:  # noqa: BLE001 — показываем любую ошибку разбора
        sys.exit(f"Тема не является корректным XML: {e}")

    OUT.write_text(out, encoding="utf-8")
    print(f"OK: {OUT} ({len(out.encode('utf-8')) // 1024} КБ)")


if __name__ == "__main__":
    main()
