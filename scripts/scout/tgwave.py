"""tgwave — ОДИН пост о прошедшей волне в свой приватный канал.

Зачем. Регулярный прогон живёт в облачной рутине, а её результат до сих пор
можно было увидеть только открыв ноутбук и запустив `shortlist`. Пост в
собственном канале снимает это: телефон показывает, сколько нового, и держит
рядом файл со всем списком.

ЧТО ИМЕННО ПОСТИТСЯ (требование владельца 08.08.2026): **один пост** —
количество новых вакансий и файл со всеми ними. Не сводка по площадкам, не
строка на вакансию, не серия сообщений.

🔴 Границы. Этот модуль ПИШЕТ в Telegram, поэтому список разрешённого
исчерпывающий, как у `tgmirror`:

* **только в один канал** — тот, чей id лежит в `.auth/telegram.env`
  (`TG_MIRROR_CHAT`), то есть в приватный канал самого владельца;
* **только один пост на волну** — повтор той же даты требует `--force`;
* **никому не отвечаем и ни на что не откликаемся**, ни в канале, ни в личке;
* **по умолчанию предпросмотр**: без явного `--apply` не уходит ничего.

Почему это отдельный модуль, а не ручка в `tgmirror`. У того инвариант жёстче
и проверяется тестом: ему позволена ровно одна операция — `forward_messages`
уже существующего поста, и «ничего не сочиняется» там сказано буквально. Здесь
сочиняется — значит, и границы должны стоять отдельно, а не размывать те.

Содержимое файла берётся у `shortlist` целиком. Второй способ ответить на
вопрос «что нового» завёлся бы собственным форматом и разошёлся бы с командой,
которой владелец пользуется, — в проекте это уже проходили на фильтре ролей.
"""

from __future__ import annotations

import os
import sys

from . import shortlist, store

# Псевдо-источник в таблице `tg_mirror`: она хранит «что уже уехало в канал», и
# волна — такая же запись, только вместо id поста стоит дата. Отдельная таблица
# ради одной строки в волну не нужна, а ключ (source, external_id) разводит их
# с настоящими пересылками навсегда: канала с именем «wave» не бывает.
WAVE_SOURCE = "wave"


def build(db: str, *, days: int, date: str, top: int = 10,
          simhash_bits: int = shortlist.SIMHASH_MAX_DIST) -> tuple[str, str]:
    """(текст поста, содержимое файла). В сеть не ходит — только база.

    Окно разбирает `store.since_arg`, а не f-строка: `build` ждёт готовую дату,
    и «3d» он молча принимал за границу «строка 3d», под которую не подходит
    ничего. Пост при этом выглядел исправным и сообщал «0 новых вакансий».
    """
    res = shortlist.build(db, since=store.since_arg(f"{days}d", db=db),
                          by="seen", limit=0, simhash_bits=simhash_bits)
    rows, st = res["rows"], res["stats"]
    n = len(rows)

    head = [f"Волна {date}: {n} новых вакансий"]
    if n:
        # Деньги и удалёнка — единственные два признака, ради которых стоит
        # открыть файл прямо сейчас. Всё остальное решается за компьютером.
        paid = sum(1 for g in rows if g.get("salary_from") or g.get("salary_to"))
        remote = sum(1 for g in rows if g.get("remote"))
        head.append(f"с вилкой {paid}, с удалёнкой {remote}, "
                    f"схлопнуто дублей {st.get('collapsed', 0)}")
        head.append("")
        head.append(f"Первые {min(top, n)} по совпадению с профилем:")
        for i, g in enumerate(rows[:top], 1):
            money = shortlist._money(g)
            head.append(f"{i}. {(g.get('title') or '')[:60]} — "
                        f"{(g.get('company') or 'работодатель не раскрыт')[:32]}"
                        + (f" · {money}" if money and money != "—" else ""))
        head.append("")
        head.append("Полный список — в файле. Отбор и письма — не здесь: "
                    "это суждение, его делает не скрипт.")
    else:
        head.append("Ничего нового. Либо окно узкое, либо всё уже отработано.")
    return "\n".join(head), shortlist.render(res, fmt="table")


def _target(env: dict) -> str | None:
    return (env.get("TG_MIRROR_CHAT") or "").strip() or None


def run(db: str, *, days: int, date: str, top: int = 10, apply: bool = False,
        force: bool = False, out_dir: str = ".scout") -> int:
    """Предпросмотр или отправка. Предпросмотр НИЧЕГО телеграмного не трогает.

    Импорт `tgclient` стоит ПОСЛЕ выхода по `apply=False` намеренно: telethon
    опционален (инвариант 3), и предпросмотр обязан работать на машине, где его
    нет вовсе. Раньше импорт стоял в начале функции и это свойство держалось
    случайно — только потому, что `tgclient` не тянет telethon на уровне модуля.
    """
    text, table = build(db, days=days, date=date, top=top)
    path = os.path.join(out_dir, f"wave-{date}.md")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(table if table.endswith("\n") else table + "\n")

    print(text)
    print()
    print(f"файл со всеми вакансиями: {path}")

    if not apply:
        print("\n(предпросмотр — не отправлено; `--apply`, чтобы отправить)")
        return 0

    from .tgclient import ENV_PATH, read_env  # noqa: PLC0415 — telethon опционален

    env = read_env()
    chat = _target(env or {})
    if not chat:
        print(f"\nКанал не назначен: в {ENV_PATH} нет TG_MIRROR_CHAT. "
              f"Как завести — `scout tg-mirror` без аргументов.", file=sys.stderr)
        return 2

    with store.connect(db) as conn:
        was = store.mirror_of(conn, WAVE_SOURCE, date)
    if was and not force:
        print(f"\nВолна {date} уже отправлена (сообщение {was['mirror_message_id']}). "
              f"`--force`, если надо повторить.", file=sys.stderr)
        return 0

    from .tgclient import _connect  # noqa: PLC0415
    client = _connect(env)
    try:
        if not client.is_user_authorized():
            print("сессия Telegram не жива: `scout tg-auth login`", file=sys.stderr)
            return 2
        # Файл и текст уходят ОДНИМ сообщением: подпись к документу, а не пост
        # плюс отдельный документ. Требование владельца — «единый пост».
        msg = client.send_file(int(chat), path, caption=text[:1024],
                               force_document=True)
        with store.connect(db) as conn:
            store.save_mirror(conn, WAVE_SOURCE, date, str(chat), int(msg.id))
        print(f"\nотправлено в {chat}, сообщение {msg.id}")
    finally:
        client.disconnect()
    return 0


def cli(args) -> int:
    date = getattr(args, "date", None) or store.now()[:10]
    return run(args.db, days=getattr(args, "days", 3), date=date,
               top=getattr(args, "top", 10),
               apply=getattr(args, "apply", False),
               force=getattr(args, "force", False))
