"""Самые просматриваемые страницы из счётчика GoatCounter → data/top.json
(для «Топа по просмотрам» и блока «Популярное» на главной).

Нужны переменные окружения:
  GOATCOUNTER_CODE  — код сайта в GoatCounter (адрес вида код.goatcounter.com);
  GOATCOUNTER_TOKEN — API-ключ GoatCounter с правом «Read statistics».
На GitHub код задаётся переменной репозитория, а ключ — только секретом (Settings → Secrets and variables → Actions):
в файлах и на сайте его быть не должно. Без них скрипт ничего не делает, сборка сайта идёт как обычно.

Запуск: py tools/fetch_top.py
"""
import datetime as dt
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SINCE = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)  # «за всё время» — с запуска сайта


def hits(code, token, start, end):
    q = urllib.parse.urlencode({"start": start.strftime("%Y-%m-%dT%H:00:00Z"), "end": end.strftime("%Y-%m-%dT%H:00:00Z"), "limit": 100})
    req = urllib.request.Request(f"https://{code}.goatcounter.com/api/v0/stats/hits?{q}",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return [{"path": h["path"], "count": h["count"]} for h in data.get("hits", []) if not h.get("event")]


def main():
    code = os.environ.get("GOATCOUNTER_CODE", "").strip().lower()
    token = os.environ.get("GOATCOUNTER_TOKEN", "").strip()
    if not code or not token:
        print("Счётчик не настроен (нет GOATCOUNTER_CODE или GOATCOUNTER_TOKEN): топ не обновляется")
        return
    if not re.fullmatch(r"[a-z0-9-]+", code):
        print("GOATCOUNTER_CODE — только латиница, цифры и дефис, без .goatcounter.com")
        return
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    try:
        top = {"week": hits(code, token, now - dt.timedelta(days=7), now), "all": hits(code, token, SINCE, now)}
    except urllib.error.HTTPError as e:
        # ключ в сообщение не попадает: печатаем только код ответа
        print(f"GoatCounter ответил {e.code}" + (" — проверьте API-ключ и его право «Read statistics»" if e.code in (401, 403) else ""))
        return
    except Exception as e:  # noqa: BLE001 — сеть; сайт соберётся и без топа
        print(f"GoatCounter недоступен: {type(e).__name__}")
        return
    (ROOT / "data" / "top.json").write_text(json.dumps(top, ensure_ascii=False), encoding="utf-8")
    print(f"Топ: за неделю {len(top['week'])} страниц, за всё время {len(top['all'])}")


if __name__ == "__main__":
    main()
