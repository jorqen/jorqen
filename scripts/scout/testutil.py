"""Общее для тестовых модулей. Только то, что НЕ зависит от их состояния.

`eq`/`ok` живут в каждом модуле своими: они пишут в собственный список `FAILS`,
и общий список слил бы отчёты четырёх независимых прогонов в один. А вот
`patched` состояния не имеет вовсе — и лежал тремя одинаковыми копиями, из-за
чего в `test_scout` его просто не было, и проверки там писались обходными
путями.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def fresh(hours: int = 1) -> str:
    """Момент ВНУТРИ окна свежести.

    Фиксированная дата в фикстуре — мина: тест зеленеет сегодня и краснеет
    через неделю сам по себе, без единой правки кода.
    """
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def stale(days: int = 30) -> str:
    """Момент ЗА окном свежести. Тот же довод, что у `fresh`."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()



class patched:
    """Точечная подмена атрибута модуля на время теста."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *a):
        setattr(self.obj, self.name, self.old)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Подделки сети. Общие, потому что нужны нескольким тестовым модулям, а
# копия подделки — это копия допущений о том, как отвечает площадка.
# ──────────────────────────────────────────────────────────────────────────────

class _FakeJSON:
    """Подменяет sources.fetch_json и запоминает, какие URL спросили.

    Смысл теста именно в этом: у трёх источников три разных способа
    пагинации и ровно один настоящий серверный фильтр — их и надо стеречь."""

    def __init__(self, routes: dict, default=None):
        self.routes, self.default, self.asked = routes, default, []

    def __call__(self, url, **kw):
        self.asked.append(url)
        for frag, payload in self.routes.items():
            if frag in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        if self.default is None:
            raise AssertionError(f"фикстуры под {url} нет")
        return self.default
def _with_fake_json(fake, fn):
    from . import sources as S
    real = S.fetch_json
    S.fetch_json = fake
    try:
        return fn()
    finally:
        S.fetch_json = real
class _FakeFetch:
    """Подменяет sources.fetch фикстурами страниц и СЧИТАЕТ спрошенные URL.

    Считать обязательно: с пагинацией «источник отработал» и «источник обошёл
    выдачу целиком» — разные вещи, и отличает их только список запросов.
    Фрагменты проверяются в порядке объявления, поэтому «page=2» надо класть
    ВЫШЕ общего фрагмента.
    """

    def __init__(self, pages: dict):
        self.pages, self.asked = pages, []

    def __call__(self, url, **kw):
        self.asked.append(url)
        for frag, payload in self.pages.items():
            if frag in url:
                return payload, url
        raise AssertionError(f"фикстуры под {url} нет")
def _with_fake_fetch(pages: dict, fn, *, keep_pause: bool = False):
    """Подменяет sources.fetch фикстурами страниц (hh и habr читают HTML).

    Заодно глушится пауза между страницами: тест не должен спать по-настоящему,
    но ВЫЗОВЫ паузы считаются — вежливость к площадке проверяется отдельным тестом.

    И глушатся ОБА API-пути, у которых есть запасной разбор HTML:

      * hh  — src_hh выбирает путь по наличию токена в `.auth/`, то есть на
        машине с токеном фикстуры HTML не читались бы вовсе, а на машине без
        него читались;
      * habr — src_habr сначала пробует фронтовый JSON и ушёл бы в сеть мимо
        фикстур.

    Тест, зелёный или красный в зависимости от того, авторизован ли хозяин
    ноутбука и жив ли чужой API, не проверяет ничего. Заодно это прогоняет
    ровно тот откат на HTML, который случится в бою, когда API отвалится."""
    from . import hhapi as A
    from . import sources as S
    from .net import FetchError
    real_fetch, real_pause = S.fetch, S._pause
    real_usable, real_habr_api = A.usable, S.src_habr_api
    fake = pages if isinstance(pages, _FakeFetch) else _FakeFetch(pages)
    naps: list[float] = []
    S.fetch = fake
    A.usable = lambda env=None: False

    def _no_api(ctx):
        raise FetchError(S.HABR_API, "в тесте API выключен")

    S.src_habr_api = _no_api
    if not keep_pause:
        # `gate` подменённая пауза принимает и игнорирует: тест считает ВЫЗОВЫ
        # (проверка вежливости), а не спит. Без параметра подмена падает
        # TypeError на источниках, которые различают частоту и отступ.
        S._pause = lambda seconds=S.PAGE_PAUSE, *, gate=True: (
            naps.append(seconds), seconds)[1]
    try:
        result = fn()
    finally:
        S.fetch, S._pause = real_fetch, real_pause
        A.usable, S.src_habr_api = real_usable, real_habr_api
    fake.naps = naps
    return result
def _careered_entry(jid, title, lo, hi, period, posted=None):
    return {"kind": "job", "id": jid, "posted_at": posted or fresh(),
            "features": [{"key": "name", "value": title},
                         {"key": "company", "value": "Acme"},
                         {"key": "salary_from", "value": lo},
                         {"key": "salary_to", "value": hi},
                         {"key": "salary_currency", "value": "USD"},
                         {"key": "salary_period", "value": period},
                         {"key": "location", "value": "Remote"}]}
