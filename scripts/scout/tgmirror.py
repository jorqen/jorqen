"""tgmirror — пересылка постов вакансий в СВОЙ приватный канал.

Зачем. Пост в чужом канале живёт ровно столько, сколько его там держат: вакансию
закрыли — пост удалили, и ссылка из карточки ведёт в никуда вместе со всем текстом.
Пересланная копия в собственном канале переживает удаление оригинала, а `forward`
сохраняет ссылку на источник — видно, откуда пришло.

🔴 **Границы, которые здесь жёстче, чем в остальном сборщике.** Это единственный
модуль, который ПИШЕТ в Telegram, поэтому список разрешённого исчерпывающий:

* **только `forward_messages`** — пересылка уже существующего поста;
* **только в один канал** — тот, чей id лежит в `.auth/telegram.env`;
* **ничего не сочиняется**: ни подписей, ни комментариев, ни своих сообщений;
* **никому не отвечаем и ни на что не откликаемся** — ни в канале, ни в личке;
* **по умолчанию `--dry-run`**: без явного `--apply` не уходит ни одно сообщение.

Канал создаёт ПОЛЬЗОВАТЕЛЬ руками и сам кладёт его id в `.auth/telegram.env`.
Скрипт каналов не создаёт: создание канала — это действие от имени человека,
и оно должно остаться его решением, а не побочным эффектом прогона.
"""

from __future__ import annotations

import sys

from . import store
from .tgclient import ENV_PATH, SESSION_PATH, _connect, read_env

HOWTO = f"""Зеркало выключено: в {ENV_PATH} нет TG_MIRROR_CHAT.

Как включить (делает пользователь, один раз):
  1. В Telegram создай ПРИВАТНЫЙ канал (без публичной ссылки).
  2. Отправь в него любое сообщение — иначе канала не будет в списке диалогов.
  3. Узнай его id:  python3 -m scripts.scout tg-mirror --list-chats
  4. Допиши в {ENV_PATH}:

       TG_MIRROR_CHAT=-1001234567890

Дальше `scout tg-mirror --apply` перешлёт туда посты вакансий из базы.
В канал не пишется ничего, кроме пересланных постов."""


def _mirror_chat(env: dict) -> str | None:
    return (env.get("TG_MIRROR_CHAT") or "").strip() or None


def list_chats() -> int:
    """Каналы, куда пользователь может писать, — чтобы узнать id своего."""
    env = read_env()
    if env is None:
        print(HOWTO, file=sys.stderr)
        return 2
    client = _connect(env)
    try:
        if not client.is_user_authorized():
            print("сессия Telegram не жива: `scout tg-auth login`", file=sys.stderr)
            return 2
        print("# каналы, где ты можешь публиковать (кандидаты в зеркало):")
        for folder in (0, 1):
            for d in client.iter_dialogs(folder=folder):
                e = getattr(d, "entity", None)
                if e is None or not getattr(e, "broadcast", False):
                    continue
                rights = getattr(e, "admin_rights", None)
                if not (getattr(e, "creator", False) or
                        (rights and getattr(rights, "post_messages", False))):
                    continue
                private = "приватный" if not getattr(e, "username", None) \
                    else f"ПУБЛИЧНЫЙ (@{e.username})"
                print(f"  {d.id:<16} {(d.name or '')[:40]:<40} {private}")
    finally:
        client.disconnect()
    print("\nПриватный канал предпочтительнее: зеркало — личный архив, "
          "а не публикация.")
    return 0


def run(db: str, *, limit: int = 200, apply: bool = False,
        since: str | None = None) -> int:
    """Пересылает ещё не зеркалированные телеграм-вакансии. Возвращает код."""
    env = read_env()
    if env is None:
        print(HOWTO, file=sys.stderr)
        return 2
    chat = _mirror_chat(env)
    if not chat:
        print(HOWTO, file=sys.stderr)
        return 2
    import os  # noqa: PLC0415

    if not os.path.exists(SESSION_PATH):
        print(f"Сессии нет ({SESSION_PATH}): `scout tg-auth login`", file=sys.stderr)
        return 2

    with store.connect(db) as conn:
        done = store.mirrored(conn)
        sql = ("SELECT source, external_id, title, url FROM vacancy "
               "WHERE source LIKE 'tg:%' AND url <> ''")
        params: list = []
        if since:
            sql += " AND first_seen >= ?"
            params.append(since)
        sql += " ORDER BY first_seen DESC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    todo = [r for r in rows if (r["source"], r["external_id"]) not in done][:limit]

    print(f"# tg-mirror: телеграм-вакансий {len(rows)}, уже зеркалировано "
          f"{len(rows) - len([r for r in rows if (r['source'], r['external_id']) not in done])}, "
          f"к пересылке {len(todo)}"
          + (f" (потолок {limit})" if len(rows) - len(done) > limit else ""))
    if not apply:
        for r in todo[:10]:
            print(f"  · {r['title'][:56]:<56} {r['url']}")
        print(f"\nЭто предпросмотр — не переслано НИЧЕГО. "
              f"Переслать: `scout tg-mirror --apply`")
        return 0
    if not todo:
        return 0

    client = _connect(env)
    sent = failed = 0
    try:
        if not client.is_user_authorized():
            print("сессия Telegram не жива: `scout tg-auth login`", file=sys.stderr)
            return 2
        target = client.get_entity(int(chat))
        for r in todo:
            # Канал-источник восстанавливаем из `source` (`tg:<канал>`): по нему
            # Telethon находит сущность и пересылает сообщение по его id.
            slug = r["source"].split(":", 1)[1]
            try:
                peer = client.get_entity(int(slug) if slug.lstrip("-").isdigit()
                                         else slug)
                msgs = client.forward_messages(target, int(r["external_id"]), peer)
                mid = getattr(msgs, "id", None) or getattr(msgs[0], "id", None)
                if mid is None:
                    # Пересылка прошла, а идентификатора нет — записать нечего,
                    # и молчать нельзя: без записи следующий прогон перешлёт
                    # тот же пост второй раз.
                    raise RuntimeError("Telegram не вернул id пересланного сообщения")
                with store.connect(db) as conn:
                    store.save_mirror(conn, r["source"], r["external_id"], chat, mid)
                sent += 1
            except Exception as e:  # noqa: BLE001 — один пост не рвёт зеркало
                failed += 1
                print(f"  ⚠️  {r['url']}: {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        client.disconnect()
    print(f"переслано {sent}, не вышло {failed}")
    return 0 if not failed else 1


def cli(args) -> int:
    if args.list_chats:
        return list_chats()
    return run(args.db, limit=args.limit, apply=args.apply,
               since=store.since_arg(args.since) if args.since else None)
