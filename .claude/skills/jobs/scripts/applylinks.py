#!/usr/bin/env python3
"""Собрать ссылки для отклика с уже вписанным письмом.

Зачем скрипт: многострочное кириллическое письмо в query-параметре ломается тихо —
ссылка открывается, а тело пустое или обрезано на первом `&`. Здесь кодирование
делается один раз и сразу проверяется round-trip'ом (раскодировали обратно и
сравнили с оригиналом). Не прошло — падаем, а не отдаём битую ссылку.

Вход: JSON-файл со списком
    [{"slug": "...", "email": "hr@x.com" | null, "subject": "...", "body": "..."}]
Выход: JSON в stdout, по элементу на вакансию: mailto / tme_share / длины.

    python3 applylinks.py letters.json > links.json

Границы: скрипт только СОБИРАЕТ ссылки. Ничего не отправляет и никуда не ходит.
"""

import json
import sys
from urllib.parse import quote, urlencode, urlparse, parse_qs, unquote

# Практический потолок mailto:. Outlook рвёт около 2000 символов, часть клиентов
# и прокси — раньше. Письмо на 120-200 слов проходит, но впритык, поэтому длину
# показываем всегда, чтобы полный текст не потерялся молча.
MAILTO_SOFT_LIMIT = 2000


def _roundtrip_query(url: str, field: str, expected: str) -> None:
    """Проверить, что параметр раскодируется ровно в то, что в него клали."""
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    got = qs.get(field, [None])[0]
    if got != expected:
        raise SystemExit(
            f"round-trip провален для {field!r} в {url[:80]}...\n"
            f"  ожидали {len(expected)} символов, получили "
            f"{len(got) if got is not None else 'None'}"
        )


def build_mailto(email: str, subject: str, body: str) -> str:
    # quote() по умолчанию не трогает '/', а в теле письма он встречается в
    # ссылках — оставляем как есть, это валидно. Главное, что переводы строк,
    # '&', '?', '#' и кавычки уезжают в проценты.
    url = "mailto:" + quote(email) + "?" + urlencode(
        {"subject": subject, "body": body}, quote_via=quote
    )
    _roundtrip_query(url, "subject", subject)
    _roundtrip_query(url, "body", body)
    return url


def build_tme_share(text: str, url: str | None = None) -> str:
    """t.me/share открывает ВЫБОР ЧАТА с вписанным текстом.

    Адресата выбирает человек: прямой ссылки «написать конкретному @username
    с готовым текстом» в Telegram нет, и обещать её нельзя.
    """
    params = {"url": url or "", "text": text}
    share = "https://t.me/share/url?" + urlencode(params, quote_via=quote)
    _roundtrip_query(share, "text", text)
    return share


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)

    with open(sys.argv[1], encoding="utf-8") as fh:
        items = json.load(fh)

    out = []
    for it in items:
        row = {
            "slug": it["slug"],
            "body_chars": len(it["body"]),
            "body_words": len(it["body"].split()),
        }
        if it.get("email"):
            m = build_mailto(it["email"], it["subject"], it["body"])
            row["mailto"] = m
            row["mailto_chars"] = len(m)
            row["mailto_over_limit"] = len(m) > MAILTO_SOFT_LIMIT
        row["tme_share"] = build_tme_share(it["body"], it.get("vacancy_url"))
        out.append(row)

    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
