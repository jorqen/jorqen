"""Тесты на то, что ломается тихо.

Здесь проверяется не «работает ли сеть», а разбор вилок и ключ дубля — места, где
ошибка не падает, а молча уезжает в карточку. Неверно разобранная вилка выглядит как
факт о зарплате и врёт пользователю уверенным тоном.

    python3 -m scripts.scout.test_scout
"""

from __future__ import annotations

import sys

from .model import Vacancy, dup_key, norm_currency
from .resolve import classify, find_targets
from .sources import parse_salary

FAILS: list[str] = []


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def test_salary():
    # Реальные строки с площадок, а не выдуманные примеры.
    cases = [
        ("до 500 000 ₽",            (None, 500000, "RUB")),
        ("от 300 000 до 490 000 ₽", (300000, 490000, "RUB")),
        ("400 000 ₽",               (400000, None, "RUB")),
        ("от 250 000 ₽",            (250000, None, "RUB")),
        ("200 000 — 250 000 ₽",     (200000, 250000, "RUB")),
        ("$3000 - $5000",           (3000, 5000, "USD")),
        ("2 800—12 500 USD",        (2800, 12500, "USD")),
        ("з/п не указана",          (None, None, None)),
        ("",                        (None, None, None)),
        (None,                      (None, None, None)),
    ]
    for text, (wf, wt, wc) in cases:
        f, t, c, _ = parse_salary(text)
        eq((f, t, c), (wf, wt, wc), f"parse_salary({text!r})")

    # Год публикации не должен превратиться в зарплату.
    f, t, _, _ = parse_salary("Опубликовано в 2026")
    eq((f, t), (None, None), "parse_salary не путает год с вилкой")


def test_zero_is_not_a_salary():
    """Ноль у careered означает «не указано». Нельзя показать «0–0 ₽» как условия."""
    v = Vacancy(source="careered", external_id="1", url="u", title="t",
                salary_from=None, salary_to=None, currency="RUB")
    eq(v.salary_str(), "", "нет вилки → пустая строка, а не «0–0»")


def test_salary_str():
    eq(Vacancy(source="s", external_id="1", url="u", title="t", salary_from=200000,
               salary_to=250000, currency="RUR").salary_str(),
       "200 000–250 000 RUB", "вилка форматируется с пробелами и нормализованной валютой")
    eq(Vacancy(source="s", external_id="1", url="u", title="t",
               salary_from=350000, currency="₽").salary_str(),
       "от 350 000 RUB", "открытая снизу вилка")


def test_currency():
    eq(norm_currency("RUR"), "RUB", "RUR → RUB")
    eq(norm_currency("₽"), "RUB", "знак рубля")
    eq(norm_currency(None), None, "пусто остаётся пустым")


def test_dup_key():
    """Ключ дубля — подсказка, а не автосклейка: он обязан быть консервативным."""
    a = dup_key("Т-Банк", "Senior Golang-разработчик")
    b = dup_key("Т-Банк", "Golang разработчик")
    eq(a, b, "грейд и порядок слов не должны разводить одну вакансию")
    c = dup_key("Ozon", "Golang разработчик")
    if a == c:
        FAILS.append("dup_key: разные компании склеились в один ключ")


def test_resolver_ignores_social():
    """Соцсеть из футера не может стать «лучшим путём отклика» — на этом уже обожглись."""
    html = """
    <a href="https://setka.ru/">Откликнуться</a>
    <a href="https://boards.greenhouse.io/acme/jobs/42">Apply</a>
    <form><button>Откликнуться</button></form>
    """
    targets = find_targets(html, "https://hh.ru/vacancy/1")
    kinds = {t.kind for t in targets}
    if "ats" not in kinds:
        FAILS.append("resolve: не распознал ATS-ссылку Greenhouse")
    submit = [t for t in targets if t.kind == "form-submit"]
    if not submit or submit[0].safe_to_open:
        FAILS.append("resolve: кнопка внутри <form> должна быть помечена как неотправляемая")


def test_classify():
    eq(classify("https://job-boards.greenhouse.io/x/jobs/1")[0], "ats", "greenhouse → ats")
    eq(classify("https://jobs.lever.co/acme/1")[0], "ats", "lever → ats")
    eq(classify("https://hh.ru/vacancy/1")[0], "aggregator", "hh → витрина")
    eq(classify("https://tbank.ru/career/")[0], "external", "сайт компании → external")


def main() -> int:
    for fn in (test_salary, test_zero_is_not_a_salary, test_salary_str, test_currency,
               test_dup_key, test_resolver_ignores_social, test_classify):
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
