"""Готовит папку github/ — ровно то, что загружается в репозиторий GitHub: генератор, тема и данные.
Страницы собирает сам GitHub (файл .github/workflows/pages.yml).

Репозиторий на GitHub открыт всем, поэтому сюда НЕ попадают: ключ YouTube (data/youtube_key.txt),
служебные камеры (они остаются только в Blogger-черновиках) и рабочие файлы.

Запуск: py tools/export_github.py
"""
import json
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from build_posts import is_private  # noqa: E402

OUT = ROOT / "github"
FILES = {
    ".github/workflows/pages.yml": "tools/github/pages.yml",
    "tools/build_posts.py": "tools/build_posts.py",
    "tools/build_static.py": "tools/build_static.py",
    "tools/fetch_top.py": "tools/fetch_top.py",
    "theme/src/app.js": "theme/src/app.js",
    "theme/src/style.css": "theme/src/style.css",
    "theme/src/menu.html": "theme/src/menu.html",
    "data/countries.json": "data/countries.json",
    "data/cities.json": "data/cities.json",
    "data/sights_cache.json": "data/sights_cache.json",
    "data/streetview.json": "data/streetview.json",
}
README = """# Веб-камеры планеты

Сайт собирается автоматически: после каждой загрузки файлов GitHub запускает `tools/build_static.py`
(вкладка **Actions**) и публикует готовые страницы на GitHub Pages.

- `data/cameras.json` — база камер: город, названия, трансляции (первая — основная).
- `data/cities.json`, `data/countries.json` — справочники городов и стран.
- `theme/src/` — оформление и скрипт сайта.

Чтобы обновить сайт, загрузите изменённые файлы через «Add file → Upload files» — через 3–5 минут он пересоберётся.
"""


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    for dst, src in FILES.items():
        for attempt in range(5):  # Windows отпускает только что удалённую папку не сразу
            try:
                (OUT / dst).parent.mkdir(parents=True, exist_ok=True)
                break
            except PermissionError:
                time.sleep(1)
        shutil.copyfile(ROOT / src, OUT / dst)
    cams = json.loads((ROOT / "data" / "cameras.json").read_text(encoding="utf-8"))
    public = [c for c in cams if not is_private(c)]
    (OUT / "data" / "cameras.json").write_text(json.dumps(public, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "README.md").write_text(README, encoding="utf-8")

    # Проверка: в открытый репозиторий не уходит ничего секретного
    key = (ROOT / "data" / "youtube_key.txt")
    secret = key.read_text(encoding="utf-8").strip() if key.exists() else ""
    for f in OUT.rglob("*"):
        if f.is_file():
            if secret and secret in f.read_text(encoding="utf-8", errors="ignore"):
                sys.exit(f"СТОП: в {f} найден ключ YouTube")
            if f.stat().st_size > 25_000_000:
                sys.exit(f"СТОП: {f} больше 25 МБ — через браузер GitHub его не загрузить")
    files = sorted(f for f in OUT.rglob("*") if f.is_file())
    print(f"Папка {OUT}: файлов {len(files)}, камер {len(public)} (служебных не включено: {len(cams) - len(public)})")
    for f in files:
        print(f"  {f.relative_to(OUT).as_posix():32} {f.stat().st_size / 1e6:6.2f} МБ")


if __name__ == "__main__":
    main()
