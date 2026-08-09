"""Тесты разбора досок работодателей (`atsapi`).

Сеть не трогается: фикстуры — обрезанные реальные ответы.

Здесь живёт регрессия на дефект, который стоил каждой доске Workable ровно одной
вакансии. Разделителем ячеек стоял `\\s*`, а `\\s` матчит перевод строки, поэтому
первый матч начинался на СТРОКЕ ЗАГОЛОВКА таблицы, проезжал сквозь `|---|` и
подхватывал ссылку `[View]` уже из первой строки данных: доска отдавала фантом
`title="Title"` с чужим id, а настоящая первая вакансия исчезала. Отловить это
глазами было почти нельзя — фантом отсеивался фильтром по названию профессии,
и в сводке всё выглядело нормально.

    python3 -m scripts.scout.test_atsapi
"""

from __future__ import annotations

import sys

from . import atsapi

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(f"{name}: {detail}" if detail else name)


# Шапка + разделитель + две строки данных — минимум, на котором дефект виден.
WORKABLE_MD = """\
# Acme — All Open Positions

> Last updated: 2026-07-30

| Title | Department | Location | Type | Salary | Posted | Details |
|-------|-----------|----------|------|--------|--------|---------|
| SEO Senior Associate | Channel Marketing | Athens, Greece (Hybrid) | Full-time | — | 2026-06-15 | [View](https://apply.workable.com/acme/jobs/view/B40901E4AB.md) |
| Backend Engineer (Go) | Platform | Remote | Full-time | €70k – €90k | 2026-07-29 | [View](https://apply.workable.com/acme/jobs/view/9D3D73F77D.md) |
"""


def test_workable_header_is_not_a_vacancy() -> None:
    rows = list(atsapi._WORKABLE_ROW.finditer(WORKABLE_MD))
    check("workable: ровно две строки данных", len(rows) == 2,
          f"разобрано {len(rows)}: {[m.group('title') for m in rows]}")
    titles = [m.group("title") for m in rows]
    check("workable: шапка таблицы не стала вакансией", "Title" not in titles,
          f"титулы: {titles}")
    check("workable: первая вакансия настоящая", titles[0] == "SEO Senior Associate",
          f"первый титул: {titles[0]!r}")
    check("workable: вторая вакансия настоящая", titles[1] == "Backend Engineer (Go)",
          f"второй титул: {titles[1]!r}")


def test_workable_ids_belong_to_their_own_rows() -> None:
    """Id не должен «съезжать» на соседнюю строку.

    Именно это и происходило: B40901E4AB принадлежит SEO Senior Associate, но
    доставался фантому «Title», а сама вакансия пропадала вместе со своим id.
    """
    rows = {m.group("title"): m.group("sc") for m in atsapi._WORKABLE_ROW.finditer(WORKABLE_MD)}
    check("workable: id первой строки на месте", rows.get("SEO Senior Associate") == "B40901E4AB",
          f"получено {rows.get('SEO Senior Associate')!r}")
    check("workable: id второй строки на месте", rows.get("Backend Engineer (Go)") == "9D3D73F77D",
          f"получено {rows.get('Backend Engineer (Go)')!r}")


def test_workable_cells_do_not_leak_across_lines() -> None:
    """Ячейки не должны собираться из разных строк файла."""
    by_title = {m.group("title"): m for m in atsapi._WORKABLE_ROW.finditer(WORKABLE_MD)}
    m = by_title.get("SEO Senior Associate")
    check("workable: разбор первой строки состоялся", m is not None)
    if m:
        check("workable: департамент свой", m.group("dept") == "Channel Marketing",
              f"получено {m.group('dept')!r}")
        check("workable: локация своя", m.group("loc") == "Athens, Greece (Hybrid)",
              f"получено {m.group('loc')!r}")
        check("workable: дата своя", m.group("posted") == "2026-06-15",
              f"получено {m.group('posted')!r}")
        for group in ("title", "dept", "loc", "typ", "salary", "posted"):
            check(f"workable: в ячейке {group} нет перевода строки",
                  "\n" not in (m.group(group) or ""),
                  f"получено {m.group(group)!r}")


def test_workable_separator_line_is_not_a_vacancy() -> None:
    """Строка `|---|---|` не должна разбираться как вакансия даже в одиночку."""
    only_separator = "|-------|-----------|----------|------|--------|--------|---------|\n"
    check("workable: разделитель не вакансия",
          not list(atsapi._WORKABLE_ROW.finditer(only_separator)))


def test_workable_empty_board_yields_nothing() -> None:
    head = "# Acme — All Open Positions\n\n| Title | Department |\n|---|---|\n"
    check("workable: доска без строк данных даёт ноль",
          not list(atsapi._WORKABLE_ROW.finditer(head)))


def main() -> int:
    # Тесты собираются АВТОМАТИЧЕСКИ — все `test_*` этого модуля, в порядке
    # определения. Ручной список означал, что забытое имя = тест, который не
    # запускается и потому «зелёный» всегда: 09.08.2026 так молча не работали
    # сразу две новые проверки, и обе ловили настоящие дефекты.
    import inspect as _inspect
    import sys as _sys
    mod = _sys.modules[__name__]
    tests = [f for _, f in _inspect.getmembers(mod, _inspect.isfunction)
             if f.__name__.startswith("test_") and f.__module__ == __name__
             and not any(pr.default is pr.empty
                         for pr in _inspect.signature(f).parameters.values())]
    tests.sort(key=lambda f: f.__code__.co_firstlineno)
    for fn in tests:
        fn()
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
