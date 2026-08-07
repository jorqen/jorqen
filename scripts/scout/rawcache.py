"""rawcache — сырые ответы площадок за сутки, чтобы не ходить дважды.

Зачем. Правка парсера требует переразбора; переразбор требует повторного обхода;
повторный обход — это и лишний трафик к чужому сайту, и свежая антибот-стена
на ровном месте. С кэшем цикл «поправил регулярку → посмотрел, что вышло»
не стоит ни одного запроса, а прогон, перезапущенный в тот же день, идёт
из базы.

Что кэшируется: только успешные GET без тела. Стены и ошибки — НЕ кэшируются
никогда: положить стену в кэш значит закрепить поломку на сутки, и следующий
прогон получит её из базы, даже не сходив на площадку.

Срок жизни — календарные сутки UTC. Не «час» и не «навсегда»: вакансии живут
днями, а прогон бывает раз в день. Ключ включает url, потому что источник за
прогон качает десятки страниц пагинации (см. комментарий к таблице в store).

Атрибуция по источнику ведётся через `threading.local`: `run_collect` гоняет
площадки в пуле потоков, и contextvars туда не доезжают. Не проставили источник
— ключом станет хост, и это честный запасной вариант, а не потеря.
"""

from __future__ import annotations

import threading
import urllib.parse

from . import store

_local = threading.local()


def set_source(name: str | None) -> None:
    """Назвать источник для текущего потока (зовётся обёрткой в run_collect)."""
    _local.source = name


def current_source(url: str) -> str:
    name = getattr(_local, "source", None)
    if name:
        return str(name)
    try:
        return (urllib.parse.urlparse(url).hostname or "?").lower()
    except ValueError:
        return "?"


class Cache:
    """Кэш поверх таблицы `raw_cache`. Ставится в `net.set_cache`.

    Соединение открывается на каждую операцию, а не держится одно: обход идёт
    в потоках, а sqlite-соединение потокам не принадлежит. Цена — открытие
    файла; выгода — отсутствие класса ошибок «recursive use of cursors».
    """

    # Сколько дней держать. Читается ВСЕГДА только сегодняшний день (ключ
    # `fetched_on`), поэтому вчерашние строки нужны ровно для одного: переразбор
    # после правки парсера, когда прогон уже был. Два дня это покрывают, а
    # дальше кэш — чистый мусор.
    KEEP_DAYS = 2

    def __init__(self, db: str, *, read: bool = True, write: bool = True):
        self.db = db
        self.read = read
        self.write = write
        self.hits = 0
        self.misses = 0
        self.stored = 0
        self.pruned = self._prune()

    def _prune(self) -> int:
        """Выкинуть протухшие дни. Без этого кэш растёт БЕЗ ПРЕДЕЛА.

        `store.raw_cache_clear` существовал с самого начала, но его не звал
        никто — замер 08.08.2026: четыре источника кладут около трёх мегабайт,
        то есть волна примерно шестнадцать, и это каждый день. Ровно поэтому
        кэш и нельзя было включить по умолчанию. Чистка на старте делает его
        ограниченным сверху и снимает возражение.
        """
        from datetime import date, timedelta  # noqa: PLC0415

        edge = (date.today() - timedelta(days=self.KEEP_DAYS)).isoformat()
        try:
            with store.connect(self.db) as conn:
                return store.raw_cache_clear(conn, before=edge)
        except Exception:  # noqa: BLE001 — гигиена не имеет права ронять обход
            return 0

    def get(self, url: str):
        if not self.read:
            return None
        try:
            with store.connect(self.db) as conn:
                body = store.raw_cache_get(conn, current_source(url), url)
        except Exception:  # noqa: BLE001 — кэш не имеет права ронять обход
            return None
        if body is None:
            self.misses += 1
            return None
        self.hits += 1
        # Финальный URL не сохраняем отдельно: в кэш кладётся уже разрешённый
        # адрес, и отдать его же — правда. Обратное (вернуть исходный) сломало бы
        # резолвер отклика, который живёт именно на финальном адресе.
        return body, url

    def put(self, url: str, text: str, final: str | None = None) -> None:
        if not self.write:
            return
        try:
            with store.connect(self.db) as conn:
                store.raw_cache_put(conn, current_source(url), url, text)
            self.stored += 1
        except Exception:  # noqa: BLE001
            pass

    def line(self) -> str:
        tail = f", выкинуто протухших {self.pruned}" if self.pruned else ""
        return (f"кэш сырых ответов: попаданий {self.hits}, промахов {self.misses}, "
                f"сохранено {self.stored}{tail}")
