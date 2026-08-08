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

ДВА СПОСОБА ОТПРАВКИ, и выбор между ними не косметический:

* **от своего аккаунта** (telethon, `.auth/telegram.session`) — локально;
* **ботом** (Bot API, чистый urllib) — в облачной рутине.

Разница по сути. Сессия аккаунта — предъявительский доступ ко ВСЕЙ переписке,
поэтому `.auth/` с машины не уезжает (инвариант 4) и облаку недоступна в
принципе. Токен бота — доступ только к тем чатам, куда бота позвали, и
отзывается одной командой в @BotFather. Это единственный способ дать облачному
прогону голос, не отдавая ему аккаунт.

🔴 Токен в вывод не попадает никогда: он лежит в URL запроса, а логи облачной
сессии видны. За этим следит `_redact` и тест.

Содержимое файла берётся у `shortlist` целиком. Второй способ ответить на
вопрос «что нового» завёлся бы собственным форматом и разошёлся бы с командой,
которой владелец пользуется, — в проекте это уже проходили на фильтре ролей.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

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


# ── Отправка ботом: только stdlib, для облачной рутины ───────────────────────

BOT_API = "https://api.telegram.org"
# Потолок Telegram на подпись к документу. Пост длиннее уходит обрезанным, и
# это лучше, чем ошибка 400 и молчание: число новых вакансий стоит в первой
# строке, а весь список всё равно в файле.
CAPTION_MAX = 1024


def _redact(text: str, token: str) -> str:
    """Токен из сообщения вон. Он лежит в URL, а URL попадает в текст ошибок."""
    if not token:
        return text
    out = text.replace(token, "<токен>")
    head = token.split(":")[0]
    # Числовая часть до двоеточия — id самого бота, она не секрет. Но если
    # токен пришёл покалеченным (без двоеточия), `head` равен всему токену,
    # и вторая замена обязана его добить.
    return out if head == token else out.replace(head + ":", "<токен>:")


def bot_creds(env: dict | None = None) -> tuple[str, str] | None:
    """(токен, чат) для Bot API или None, если ботом слать нечем.

    Окружение процесса ГЛАВНЕЕ файла намеренно: в облаке `.auth/telegram.env`
    нет вовсе, а локально переменной удобно перебить файл на один прогон.

    `TG_MIRROR_CHAT` принимается как запасной адрес — это тот же самый
    приватный канал владельца, второй раз его прописывать незачем. 🔴 Бота в
    этот канал надо позвать руками: Telegram не даёт боту писать туда, куда его
    не добавили, и это ровно та защита, из-за которой список каналов здесь
    исчерпывающий.
    """
    src = dict(env or {})
    src.update(os.environ)
    token = (src.get("TG_BOT_TOKEN") or "").strip()
    chat = (src.get("TG_BOT_CHAT") or src.get("TG_MIRROR_CHAT") or "").strip()
    return (token, chat) if token and chat else None


def _multipart(fields: dict[str, str], name: str, blob: bytes) -> tuple[bytes, str]:
    """Тело multipart/form-data и его Content-Type. Без внешних пакетов.

    Имя файла чистится от кавычек и переводов строки: оно уходит в заголовок
    Content-Disposition, и чужой символ там ломает разбор на стороне Telegram.
    Своё имя всегда «wave-ДАТА.md», но функция не должна зависеть от того, что
    её зовёт только один вызывающий.
    """
    boundary = "scoutwave" + os.urandom(16).hex()
    safe = "".join(c for c in name if c not in '"\r\n\\') or "wave.md"
    out = bytearray()
    for key, value in fields.items():
        out += (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n").encode()
    out += (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{safe}"\r\n'
            f"Content-Type: text/markdown\r\n\r\n").encode()
    out += blob + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def send_bot(token: str, chat: str, path: str, caption: str, *,
             timeout: int = 60) -> int:
    """Файл с подписью ОДНИМ сообщением. Возвращает id сообщения.

    parse_mode не ставится намеренно: в подписи стоят названия вакансий и
    компаний — чужой текст, в котором `_`, `*` и `[` встречаются постоянно.
    С разметкой Telegram отвечал бы 400 на ровном месте, а «жирное слово» тут
    ничего не стоит.
    """
    with open(path, "rb") as f:
        blob = f.read()
    body, ctype = _multipart({"chat_id": chat, "caption": caption[:CAPTION_MAX]},
                             os.path.basename(path), blob)
    req = urllib.request.Request(f"{BOT_API}/bot{token}/sendDocument",
                                 data=body, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Тело ответа Telegram объясняет отказ словами («chat not found»,
        # «bot was blocked by the user»). Без него в облаке остаётся голое
        # «HTTP Error 400», по которому чинить нечего.
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("description") or ""
        except Exception:
            pass
        raise RuntimeError(_redact(
            f"Telegram отказал: HTTP {e.code}"
            + (f" — {detail}" if detail else ""), token)) from None
    except urllib.error.URLError as e:
        raise RuntimeError(_redact(f"до Telegram не достучались: {e.reason}",
                                   token)) from None
    if not payload.get("ok"):
        raise RuntimeError(_redact(
            f"Telegram отказал: {payload.get('description') or payload}", token))
    return int(payload["result"]["message_id"])


NO_CHANNEL = """Слать некуда — ни бота, ни своей сессии.

Ботом (так работает облачная рутина: своей сессии там нет и быть не может):
  1. @BotFather → /newbot → скопировать токен.
  2. Добавить бота в свой приватный канал и дать право писать.
  3. TG_BOT_TOKEN=<токен> и TG_BOT_CHAT=<id канала> в окружение прогона.

От своего аккаунта (локально): `scout tg-mirror` без аргументов расскажет,
как назначить TG_MIRROR_CHAT в .auth/telegram.env."""


def run(db: str, *, days: int, date: str, top: int = 10, apply: bool = False,
        force: bool = False, out_dir: str = ".scout", via: str = "auto") -> int:
    """Предпросмотр или отправка. Предпросмотр НИЧЕГО телеграмного не трогает.

    Импорт `tgclient` стоит ПОСЛЕ выхода по `apply=False` намеренно: telethon
    опционален (инвариант 3), и предпросмотр обязан работать на машине, где его
    нет вовсе. Раньше импорт стоял в начале функции и это свойство держалось
    случайно — только потому, что `tgclient` не тянет telethon на уровне модуля.

    `via`: `auto` — ботом, если есть токен, иначе своим аккаунтом; `bot` и
    `user` требуют названный способ и не подменяют его молча. Молчаливая
    подмена здесь опасна ровно в одну сторону: рутина, у которой отвалился
    токен, отправила бы пост «от владельца», если бы рядом случайно оказалась
    живая сессия.
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

    # Файла `.auth/telegram.env` в облаке нет — это штатный случай, а не отказ:
    # ботовые креды приезжают окружением. Поэтому чтение файла не должно ронять
    # ботовый путь, и `read_env` здесь только источник значений по умолчанию.
    try:
        from .tgclient import read_env  # noqa: PLC0415 — telethon опционален
        env = read_env() or {}
    except Exception as e:  # noqa: BLE001 — файла может не быть, это штатно
        # Молчать здесь нельзя: без этой строки поломка чтения файла выглядит
        # как «канал не назначен», и чинить пойдут не то. Отсутствие файла —
        # штатный случай (в облаке его нет вовсе), поэтому не отказ, а заметка.
        print(f"\n.auth/telegram.env не прочитан ({type(e).__name__}: {e}) — "
              f"беру только окружение", file=sys.stderr)
        env = {}
    creds = bot_creds(env)
    if via == "bot" and not creds:
        print("\n--via bot, но TG_BOT_TOKEN/TG_BOT_CHAT не заданы.\n\n" + NO_CHANNEL,
              file=sys.stderr)
        return 2
    use_bot = creds is not None if via == "auto" else via == "bot"
    if not use_bot and not _target(env):
        print("\n" + NO_CHANNEL, file=sys.stderr)
        return 2

    # Защита от повтора стоит ДО выбора транспорта: «один пост на волну» —
    # свойство волны, а не способа отправки. Иначе перезапуск рутины с другим
    # транспортом положил бы в канал второй тот же пост.
    with store.connect(db) as conn:
        was = store.mirror_of(conn, WAVE_SOURCE, date)
    if was and not force:
        print(f"\nВолна {date} уже отправлена (сообщение {was['mirror_message_id']}). "
              f"`--force`, если надо повторить.", file=sys.stderr)
        return 0

    if use_bot:
        token, chat = creds
        try:
            msg_id = send_bot(token, chat, path, text)
        except RuntimeError as e:
            print(f"\n{e}", file=sys.stderr)
            return 2
    else:
        chat = _target(env)
        from .tgclient import _connect  # noqa: PLC0415
        client = _connect(env)
        try:
            if not client.is_user_authorized():
                print("сессия Telegram не жива: `scout tg-auth login`", file=sys.stderr)
                return 2
            # Файл и текст уходят ОДНИМ сообщением: подпись к документу, а не
            # пост плюс отдельный документ. Требование владельца — «единый пост».
            msg_id = int(client.send_file(int(chat), path, caption=text[:CAPTION_MAX],
                                          force_document=True).id)
        finally:
            client.disconnect()

    with store.connect(db) as conn:
        store.save_mirror(conn, WAVE_SOURCE, date, str(chat), msg_id)
    print(f"\nотправлено в {chat} ({'ботом' if use_bot else 'от аккаунта'}), "
          f"сообщение {msg_id}")
    return 0


def cli(args) -> int:
    date = getattr(args, "date", None) or store.now()[:10]
    return run(args.db, days=getattr(args, "days", 3), date=date,
               top=getattr(args, "top", 10),
               apply=getattr(args, "apply", False),
               force=getattr(args, "force", False),
               via=getattr(args, "via", "auto"))
