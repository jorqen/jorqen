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

import html as H
import re

from .net import BlockedError, FetchError, fetch

# Домены, которые контактом работодателя НЕ являются: сам телеграм, соцсети и
# мессенджеры с каналами вакансий (правило из field-notes), плюс счётчики.
_NOT_CONTACT = (
    "t.me", "telegram.me", "telegram.org", "vk.com", "vk.ru", "ok.ru",
    "max.ru", "ord.vk.com", "instagram.com", "facebook.com", "youtube.com",
    "clck.ru",          # сокращатель Яндекса: куда ведёт, из ссылки не видно
)

_POST_BLOCK = 'data-post="{chan}/{ident}"'
_HREF = re.compile(r'href="(https?://[^"]+)"', re.I)


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "", re.I)
    return (m.group(1) if m else "").lower().removeprefix("www.")


def _is_contact(url: str) -> bool:
    host = _host(url)
    return bool(host) and not any(
        host == d or host.endswith("." + d) for d in _NOT_CONTACT)


def apply_links_from_post(page_html: str, channel: str, ident: int | str) -> list[str]:
    """Внешние ссылки ИЗ ОДНОГО поста — кандидаты в контакт отклика.

    Порядок сохраняется: в посте ссылка отклика обычно идёт первой, а хвост —
    это подписи каналов автора. Соцсети и сам телеграм отфильтрованы.
    """
    text = page_html or ""
    start = text.find(_POST_BLOCK.format(chan=channel, ident=ident))
    if start < 0:
        return []
    # До начала СЛЕДУЮЩЕГО поста: лента отдаётся одной страницей.
    nxt = text.find('data-post="', start + 10)
    block = text[start:nxt if nxt > 0 else len(text)]
    out: list[str] = []
    for raw in _HREF.findall(block):
        url = H.unescape(raw)
        if _is_contact(url) and url not in out:
            out.append(url)
    return out


def fetch_apply_links(post_url: str, *, timeout: int = 20) -> tuple[list[str], str]:
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
    links = apply_links_from_post(page, chan, ident)
    if not links:
        return [], ("в посте нет внешних ссылок — контакт либо дан текстом "
                    "(@ник, почта), либо спрятан в callback-кнопке")
    return links, f"из тела поста t.me/{chan}/{ident}"
