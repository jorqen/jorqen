"""tgpost — достаёт настоящую ссылку отклика ИЗ ТЕЛА телеграм-поста.

Зачем это отдельный модуль. Агрегаторы отдают телеграм-вакансию так, что
контактом выглядит сам пост: `t.me/<канал>/<id>`. Но пост — это витрина, а не
наниматель. Настоящая ссылка спрятана под словом «Откликнуться», и часть
агрегаторов её ещё и подменяет: dreamoffer пишет на её месте «Доступно в
источнике», то есть в базе URL нет вовсе.

🔴 Живой случай 09.08.2026, замеченный владельцем: в карточке Авито контактом
стоял `t.me/rabota_golang/1236`. Настоящая ссылка вела на
`career.avito.com/vacancies/razrabotka/19383/` — и оказалась мёртвой (404), то
есть вакансия закрыта. По ссылке на пост этого не видно вообще никак: пост
остаётся на месте и выглядит живым.

Откуда берём. Веб-версия поста `t.me/s/<канал>/<id>` отдаёт разметку с живыми
`href`, включая те, что в тексте скрыты под словом-гиперссылкой. Логин не нужен,
это чтение публичного канала.

⚠️ Вырезать надо БЛОК КОНКРЕТНОГО ПОСТА. Страница `/s/` отдаёт ленту канала
целиком, и наивный поиск по всей странице приносит ссылку из соседней вакансии
— тихая подмена работодателя, которую человек заметит уже после отклика.
"""

from __future__ import annotations

import re

from .net import BlockedError, FetchError, fetch

# Домены, которые контактом работодателя НЕ являются: сам телеграм, соцсети и
# мессенджеры с каналами вакансий (правило из field-notes), плюс счётчики.
# Публичное имя: этот же список — единственный на проект — читает обход ссылок
# (`crawl`), вычитая из него сокращатели: контактом они не годятся, а дорогой
# вполне, и куда они ведут, обход как раз выясняет переходом.
NOT_CONTACT = (
    "t.me", "telegram.me", "telegram.org", "vk.com", "vk.ru", "ok.ru",
    "max.ru", "ord.vk.com", "instagram.com", "facebook.com", "youtube.com",
    "clck.ru",          # сокращатель Яндекса: куда ведёт, из ссылки не видно
)

_POST_BLOCK = 'data-post="{chan}/{ident}"'
_HREF = re.compile(r'href="(https?://[^"]+)"', re.I)


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "", re.I)
    return (m.group(1) if m else "").lower().removeprefix("www.")


def _is_contact(url: str, *, keep_shorteners: bool = False) -> bool:
    host = _host(url)
    if not host:
        return False
    banned = NOT_CONTACT
    if keep_shorteners:
        from .crawl import SHORTENERS  # noqa: PLC0415 — ленивый: crawl зовёт нас
        banned = tuple(d for d in NOT_CONTACT if d not in SHORTENERS)
    return not any(host == d or host.endswith("." + d) for d in banned)


def apply_links_from_post(page_html: str, channel: str, ident: int | str, *,
                          keep_shorteners: bool = False) -> list[str]:
    """Внешние ссылки ИЗ ОДНОГО поста — кандидаты в контакт отклика.

    Порядок сохраняется: в посте ссылка отклика обычно идёт первой, а хвост —
    это подписи каналов автора. Соцсети и сам телеграм отфильтрованы.

    `keep_shorteners` — оставить короткие ссылки. Контактом они не годятся
    (куда ведут, из адреса не видно), но обход ссылок умеет по ним пройти и
    посмотреть; для карточки же остаётся прежний строгий фильтр.
    """
    text = page_html or ""
    start = text.find(_POST_BLOCK.format(chan=channel, ident=ident))
    if start < 0:
        return []
    # До начала СЛЕДУЮЩЕГО поста: лента отдаётся одной страницей.
    nxt = text.find('data-post="', start + 10)
    block = text[start:nxt if nxt > 0 else len(text)]
    from .crawl import clean_url  # noqa: PLC0415 — ленивый: crawl зовёт нас

    out: list[str] = []
    for raw in _HREF.findall(block):
        # 🔴 Телеграм экранирует href ДВАЖДЫ: `&amp;amp;`. Одной распаковки мало
        # (живой случай 09.08.2026, пост job_web3/3757) — этим занимается
        # `clean_url`, он же снимает якорь.
        url = clean_url(raw)
        if _is_contact(url, keep_shorteners=keep_shorteners) and url not in out:
            out.append(url)
    return out


def fetch_apply_links(post_url: str, *, timeout: int = 20,
                      keep_shorteners: bool = False) -> tuple[list[str], str]:
    """(ссылки отклика, пояснение) по адресу поста `t.me/<канал>/<id>`.

    Пустой список — это не ошибка: у части постов контакт дан текстом (`@ник`
    или почта), и его достаёт `contacts`. Пояснение возвращается всегда, чтобы
    карточка могла честно сказать, почему контакта нет.
    """
    m = re.match(r"https?://t\.me/(?:s/)?([A-Za-z0-9_]+)/(\d+)", post_url or "")
    if not m:
        return [], "это не ссылка на пост телеграм-канала"
    chan, ident = m.group(1), m.group(2)
    try:
        page, _ = fetch(f"https://t.me/s/{chan}/{ident}", timeout=timeout, retries=0)
    except BlockedError:
        return [], "телеграм отдал стену вместо веб-версии поста"
    except FetchError as e:
        return [], f"веб-версия поста не открылась: {e.reason}"
    links = apply_links_from_post(page, chan, ident,
                                  keep_shorteners=keep_shorteners)
    if not links:
        return [], ("в посте нет внешних ссылок — контакт либо дан текстом "
                    "(@ник, почта), либо спрятан в callback-кнопке")
    return links, f"из тела поста t.me/{chan}/{ident}"
