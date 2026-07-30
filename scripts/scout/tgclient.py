"""Telegram-архив без MCP: Telethon-клиент, который только читает.

Зачем. Джоб-каналы, группы и боты лежат у пользователя в АРХИВЕ Telegram, и скилл
`jobs` разбирал их через MCP-коннектор — который падал и уносил с собой весь этап.
Здесь тот же обход делается своим клиентом: выкачать непрочитанное, сложить дампы
в формате MCP (их уже умеет разбирать `scout tg`), отметить прочитанным.

Границы, которые не двигаются:

* **Вход выполняет пользователь.** Телефон, код из Telegram и пароль 2FA вводятся
  руками в терминале — это промпты самого Telethon, скрипт их не перехватывает
  и никуда не сохраняет. Скрипт хранит только сессию после входа.
* **Только чтение и mark-as-read.** Ни отправки сообщений, ни удаления, ни нажатий
  callback-кнопок (нажатие — это действие от имени пользователя). URL-кнопки
  не нажимаются тоже — их адреса просто выписываются в дамп.
* **Сессия не покидает машину.** `.auth/telegram.session` — предъявительский доступ
  к аккаунту, лежит под gitignore рядом с куками площадок.

Telethon — опциональная зависимость: без него команды печатают инструкцию,
остальной сборщик работает как работал.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import timezone

from .auth import AUTH_DIR

ENV_PATH = os.path.join(AUTH_DIR, "telegram.env")
# Telethon сам дописывает расширение .session — передаём путь без него.
SESSION_BASE = os.path.join(AUTH_DIR, "telegram")
SESSION_PATH = SESSION_BASE + ".session"

# Сколько уже прочитанных сообщений захватить перед непрочитанными. Перехлёст
# страхует от гонки «прочитал в клиенте, но дамп ещё не снят»: граница read/unread
# у Telegram живёт на сервере и может сдвинуться между прогонами.
OVERLAP = 3
# Потолок на чат: разумный дамп вместо бесконечной прокрутки канала с историей.
MAX_PER_CHAT = 500

ENV_HOWTO = f"""Нет файла {ENV_PATH} — Telethon нужен api_id/api_hash твоего аккаунта.

Как получить (один раз, ~2 минуты):
  1. Зайди на https://my.telegram.org (вход по своему номеру — код придёт в Telegram).
  2. Раздел «API development tools» → создай приложение (название любое).
  3. Скопируй App api_id и App api_hash.
  4. Положи их в {ENV_PATH} в виде:

       TG_API_ID=1234567
       TG_API_HASH=0123456789abcdef0123456789abcdef

Файл остаётся на этой машине: .auth/ в .gitignore. После этого:
  python3 -m scripts.scout tg-auth login
"""

TELETHON_HOWTO = """Нужен Telethon — он используется только для чтения твоего Telegram-архива.
  .venv/bin/pip install telethon
Без него остальной сборщик работает; телеграм-этап пропускается."""


def read_env(path: str = ENV_PATH) -> dict[str, str] | None:
    """KEY=VALUE построчно; # — комментарий. Нет файла — None (не исключение:
    отсутствие кредов — штатный случай, на него печатается инструкция).

    Права чинятся на месте: в этих файлах лежат App Password и api_hash — то есть
    предъявительский доступ. Всё, что создаёт сам код, ставится в 0600, но эти два
    файла заводит руками пользователь, и они приезжали с 0644."""
    if not os.path.exists(path):
        return None
    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        os.chmod(path, 0o600)
        print(f"права на {path} были {mode:04o} — исправлены на 0600 "
              f"(там предъявительский секрет)", file=sys.stderr)
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _require_telethon():
    try:
        from telethon.sync import TelegramClient  # noqa: PLC0415
    except ImportError:
        print(TELETHON_HOWTO, file=sys.stderr)
        raise SystemExit(3)
    return TelegramClient


def _client(env: dict[str, str]):
    TelegramClient = _require_telethon()
    try:
        api_id = int(env["TG_API_ID"])
        api_hash = env["TG_API_HASH"]
    except (KeyError, ValueError):
        print(f"В {ENV_PATH} нужны TG_API_ID (число) и TG_API_HASH.\n\n{ENV_HOWTO}",
              file=sys.stderr)
        raise SystemExit(2)
    os.makedirs(AUTH_DIR, exist_ok=True)
    return TelegramClient(SESSION_BASE, api_id, api_hash)


# ──────────────────────────────────────────────────────────────────────────────
# tg-auth
# ──────────────────────────────────────────────────────────────────────────────

def _connect(env: dict[str, str]):
    """Подключение БЕЗ авторизации. Именно connect(), а не `with client:` —
    контекстный менеджер Telethon зовёт start(), а start() на неавторизованной
    сессии интерактивно спрашивает телефон. Спрашивать имеет право только
    `tg-auth login`; status и fetch обязаны молча сказать «сессия не жива»."""
    client = _client(env)
    client.connect()
    return client


def cmd_login() -> int:
    """Интерактивный вход. Телефон/код/2FA вводит пользователь в промптах Telethon —
    скрипт эти значения не видит и не хранит, у него остаётся только сессия."""
    env = read_env()
    if env is None:
        print(ENV_HOWTO, file=sys.stderr)
        return 2
    client = _client(env)
    try:
        # start() без аргументов = Telethon сам спрашивает телефон, код и пароль
        # у СТОЯЩЕГО ЗА ТЕРМИНАЛОМ ЧЕЛОВЕКА. Никаких phone=/password= здесь
        # не появится: автоматизация входа — это механика захвата аккаунта.
        client.start()
        me = client.get_me()
        name = " ".join(x for x in (me.first_name, me.last_name) if x)
        print(f"\nВход выполнен: {name}" + (f" (@{me.username})" if me.username else ""))
    except (EOFError, KeyboardInterrupt):
        print("\nотменено", file=sys.stderr)
        return 1
    finally:
        client.disconnect()
    os.chmod(SESSION_PATH, 0o600)
    print(f"Сессия сохранена: {SESSION_PATH} (в .gitignore, с машины не уезжает)")
    return 0


def cmd_status() -> int:
    """Жива ли сессия. Только connect + get_me — никаких побочных эффектов
    и никаких интерактивных вопросов."""
    env = read_env()
    if env is None:
        print(ENV_HOWTO, file=sys.stderr)
        return 2
    if not os.path.exists(SESSION_PATH):
        print(f"Сессии нет ({SESSION_PATH}). Заведи: python3 -m scripts.scout tg-auth login")
        return 1
    client = _connect(env)
    try:
        if not client.is_user_authorized():
            print("Сессия есть, но НЕ АВТОРИЗОВАНА (вход не завершён или отозван). "
                  "Перезайди: python3 -m scripts.scout tg-auth login")
            return 1
        me = client.get_me()
        name = " ".join(x for x in (me.first_name, me.last_name) if x)
        print(f"Сессия жива: {name}" + (f" (@{me.username})" if me.username else ""))
    finally:
        client.disconnect()
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# tg-fetch: архив → дампы формата MCP
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ChatResult:
    title: str
    dump_path: str | None = None
    messages: int = 0
    marked: bool = False
    error: str | None = None
    topics: int = 0            # 0 = обычный чат, >0 — форум, пройдено топиков
    truncated: bool = False    # упёрлись в MAX_PER_CHAT — хвост НЕ выкачан


@dataclass
class FetchSummary:
    visited: int = 0
    dumped: int = 0
    marked: int = 0
    failed: int = 0
    truncated: int = 0
    chats: list[ChatResult] = field(default_factory=list)


def _slug(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip()
    return re.sub(r"[\s]+", "-", s)[:60] or "chat"


def _author(msg, chat_title: str) -> str:
    sender = getattr(msg, "sender", None)
    if sender is not None:
        name = " ".join(x for x in (getattr(sender, "first_name", None),
                                    getattr(sender, "last_name", None)) if x)
        name = name or getattr(sender, "title", None) or ""
        username = getattr(sender, "username", None)
        if username:
            name = f"{name} (@{username})" if name else f"@{username}"
        if name:
            # Двоеточие в имени сломало бы разбор «Автор: текст» в tg.py.
            return name.replace(":", "")
    return chat_title.replace(":", "") or "?"


def _file_line(msg) -> str | None:
    """Строка про вложение: имя файла и размер.

    В личке это половина смысла: вопрос «отправлял ли он уже резюме» решается
    именно вложением, а в тексте сообщения от него не остаётся ничего —
    `_format_message` показал бы «(медиа без текста)» и молча потерял факт.
    Файл НЕ скачивается: берутся только метаданные, уже приехавшие с сообщением.
    """
    f = getattr(msg, "file", None)
    if f is None:
        return None
    name = getattr(f, "name", None) or f"без имени ({getattr(f, 'mime_type', '?')})"
    size = getattr(f, "size", None)
    size_s = f", {size // 1024} КБ" if isinstance(size, int) and size else ""
    return f"  [файл] {name}{size_s}"


def _format_message(msg, chat_title: str) -> str:
    """Формат MCP-дампа: `[#id] [ISO] Автор: текст` + строки кнопок и скрытых ссылок.

    Кнопки — главный источник ссылок отклика у джоб-ботов: текст поста говорит
    «Откликнуться», а URL живёт в KeyboardButtonUrl и в голом тексте не виден.
    Callback-кнопки не нажимаются и в дамп не пишутся — у них нет URL."""
    from telethon.tl import types as t  # noqa: PLC0415

    date = msg.date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    body = (msg.message or "").rstrip() or "(медиа без текста)"
    lines = [f"[#{msg.id}] [{date}] {_author(msg, chat_title)}: {body}"]

    # Скрытые гиперссылки: текст «тут» с URL внутри entity.
    for ent in (msg.entities or []):
        if isinstance(ent, t.MessageEntityTextUrl) and ent.url:
            lines.append(f"  [link] {ent.url}")

    fl = _file_line(msg)
    if fl:
        lines.append(fl)

    markup = getattr(msg, "reply_markup", None)
    for row in (getattr(markup, "rows", None) or []):
        for btn in (row.buttons or []):
            if isinstance(btn, t.KeyboardButtonUrl):
                lines.append(f"  [button] {btn.text} url={btn.url}")
    return "\n".join(lines)


def _collect_messages(client, entity, read_max: int, *,
                      reply_to: int | None = None) -> tuple[list, bool]:
    """Непрочитанные (id > read_max) плюс OVERLAP уже прочитанных до границы.
    Возвращает (сообщения по возрастанию id, упёрлись ли в потолок).

    Признак обрезки нужен ради одного: при `mark=True` отмеченный прочитанным
    хвост теряется НАВСЕГДА и молча. Раньше потолок MAX_PER_CHAT срабатывал
    беззвучно, и чат помечался прочитанным целиком."""
    kw = {"reply_to": reply_to} if reply_to is not None else {}
    unread = list(client.iter_messages(entity, min_id=read_max, limit=MAX_PER_CHAT, **kw))
    truncated = len(unread) >= MAX_PER_CHAT
    overlap = list(client.iter_messages(entity, max_id=read_max + 1, limit=OVERLAP, **kw)) \
        if read_max > 0 else []
    seen: set[int] = set()
    out = []
    for m in sorted(overlap + unread, key=lambda m: m.id):
        if m.id not in seen:
            seen.add(m.id)
            out.append(m)
    return out, truncated


def _fetch_forum(client, dialog, out_lines: list[str]) -> tuple[int, int, bool]:
    """Форум-супергруппа: обход по топикам. Возвращает (сообщений, топиков, обрезка).

    В свежем TL-слое (Telethon 1.44) запрос топиков живёт в messages, а не в
    channels — на channels.GetForumTopicsRequest уже один раз упали живьём."""
    from telethon.tl import functions as fn  # noqa: PLC0415

    entity = dialog.entity
    res = client(fn.messages.GetForumTopicsRequest(
        peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))
    total, topics_hit, truncated = 0, 0, False
    for topic in res.topics:
        unread = getattr(topic, "unread_count", 0) or 0
        if unread <= 0:
            continue
        read_max = getattr(topic, "read_inbox_max_id", 0) or 0
        msgs, cut = _collect_messages(client, entity, read_max, reply_to=topic.id)
        truncated = truncated or cut
        if not msgs:
            continue
        topics_hit += 1
        out_lines.append(f"=== топик: {topic.title} ===")
        for m in msgs:
            out_lines.append(_format_message(m, dialog.name or ""))
            out_lines.append("")
        total += len(msgs)
    return total, topics_hit, truncated


def _mark_read(client, dialog, max_id: int) -> None:
    """Отметить прочитанным. Единственная «пишущая» операция модуля — и она
    ровно та, которую требует скилл: прочитанный чат не разбирается по второму разу."""
    client.send_read_acknowledge(dialog.entity, max_id=max_id or None)


# ──────────────────────────────────────────────────────────────────────────────
# tg-dm: личная переписка, ТОЛЬКО чтение
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DMResult:
    peer: str                    # как показывать собеседника
    kind: str                    # user | bot | group | channel
    me: str                      # кто «я» в этой переписке — иначе не видно, кто писал
    messages: int = 0
    lines: list[str] = field(default_factory=list)
    truncated: bool = False      # упёрлись в --limit: переписка длиннее показанного


def _peer_kind(entity) -> str:
    if getattr(entity, "bot", False):
        return "bot"
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False) or hasattr(entity, "participants_count"):
        return "group"
    return "user" if hasattr(entity, "first_name") else "chat"


def _peer_name(entity) -> str:
    name = " ".join(x for x in (getattr(entity, "first_name", None),
                                getattr(entity, "last_name", None)) if x)
    name = name or getattr(entity, "title", None) or ""
    username = getattr(entity, "username", None)
    if username:
        return f"{name} (@{username})" if name else f"@{username}"
    return name or str(getattr(entity, "id", "?"))


def _resolve_peer(client, target: str):
    """`@ник`, `ник` или числовой id → сущность Telegram.

    Числовой id отдаётся Telethon именно числом: строку «777000» он попытается
    искать как имя пользователя и не найдёт ничего."""
    from telethon import errors  # noqa: PLC0415

    t = target.strip()
    key: str | int = int(t) if re.fullmatch(r"-?\d+", t) else t.lstrip("@")
    try:
        return client.get_entity(key)
    except (ValueError, TypeError) as e:
        raise LookupError(
            f"не нашёл диалог «{target}»: {e}. Ник пишется как @nickname или nickname, "
            f"id — числом. Собеседника, с которым не было ни одного сообщения "
            f"и чей ник скрыт, найти нельзя.") from e
    except errors.RPCError as e:
        raise LookupError(f"Telegram не отдал диалог «{target}»: "
                          f"{type(e).__name__}: {e}") from e


def read_dm(target: str, limit: int = 50) -> DMResult:
    """Последние `limit` сообщений личной переписки в формате дампа.

    Зачем: перед тем как советовать написать рекрутёру, надо знать, что в этой
    переписке уже было — не отправлено ли резюме, не был ли уже отказ.

    ТОЛЬКО ЧТЕНИЕ, и здесь это строже, чем в tg-fetch. Прочитанным ничего
    не помечается: в личке отметка видна собеседнику («просмотрено»), то есть
    это действие ОТ ИМЕНИ пользователя, а такие скрипт не совершает. По той же
    причине не ставится «печатает…» и не скачиваются вложения — только их имена.
    """
    env = read_env()
    if env is None:
        print(ENV_HOWTO, file=sys.stderr)
        raise SystemExit(2)
    if not os.path.exists(SESSION_PATH):
        print(f"Сессии нет ({SESSION_PATH}). Заведи: python3 -m scripts.scout tg-auth login",
              file=sys.stderr)
        raise SystemExit(2)

    client = _connect(env)
    try:
        if not client.is_user_authorized():
            print("Сессия не авторизована — перезайди: python3 -m scripts.scout tg-auth login",
                  file=sys.stderr)
            raise SystemExit(2)
        entity = _resolve_peer(client, target)
        me = client.get_me()
        res = DMResult(peer=_peer_name(entity), kind=_peer_kind(entity),
                       me=_peer_name(me))
        # iter_messages отдаёт от свежих к старым; читаем как переписку — сверху вниз.
        msgs = list(client.iter_messages(entity, limit=max(1, limit)))
        res.truncated = len(msgs) >= max(1, limit)
        for m in reversed(msgs):
            res.lines.append(_format_message(m, res.peer))
            res.lines.append("")
        res.messages = len(msgs)
        return res
    finally:
        client.disconnect()


def render_dm(res: DMResult, target: str, limit: int) -> str:
    """Дамп + шапка. Шапка нужна из-за одной вещи: в формате `Автор: текст`
    исходящие и входящие различаются только именем, а решение «писать или нет»
    зависит ровно от того, писал ли пользователь сам."""
    head = [f"# tg-dm: {res.peer} [{res.kind}] — сообщений {res.messages}",
            f"# я в этой переписке: {res.me}",
            "# только чтение: прочитанным НЕ помечено (в личке отметка видна собеседнику)"]
    if res.kind not in ("user", "bot"):
        head.append(f"# ⚠️  это не личная переписка, а {res.kind} — "
                    f"«я» здесь один из многих участников")
    if res.truncated:
        # Ровно `limit` сообщений может значить и «столько всего», и «упёрлись
        # в потолок». Отличить нельзя, поэтому предупреждаем в обе стороны:
        # молчаливое «вот вся переписка» здесь дороже лишнего предупреждения.
        head.append(f"# ⚠️  показаны последние {limit} сообщений; если переписка длиннее, "
                    f"хвост не виден — `--limit {limit * 2}` покажет больше")
    if not res.messages:
        head.append("# переписки нет: диалог найден, но сообщений в нём ноль")
    return "\n".join(head + [""] + res.lines).rstrip() + "\n"


def fetch(out_dir: str, *, archive_only: bool = True, mark: bool = True) -> FetchSummary:
    """Обходит диалоги с непрочитанным, пишет дампы, отмечает прочитанным СРАЗУ
    после успешного дампа каждого чата (по одному, как требует скилл: упавший
    на середине прогон не оставляет «прочитанного, но не разобранного»)."""
    env = read_env()
    if env is None:
        print(ENV_HOWTO, file=sys.stderr)
        raise SystemExit(2)
    if not os.path.exists(SESSION_PATH):
        print(f"Сессии нет ({SESSION_PATH}). Заведи: python3 -m scripts.scout tg-auth login",
              file=sys.stderr)
        raise SystemExit(2)

    os.makedirs(out_dir, exist_ok=True)
    summary = FetchSummary()

    client = _connect(env)
    try:
        if not client.is_user_authorized():
            print("Сессия не авторизована — перезайди: python3 -m scripts.scout tg-auth login",
                  file=sys.stderr)
            raise SystemExit(2)

        # folder=1 — архив (folder_id в терминах Telegram API). Основная папка = 0.
        folders = [1] if archive_only else [1, 0]
        for folder in folders:
            for dialog in client.iter_dialogs(folder=folder):
                if (dialog.unread_count or 0) <= 0:
                    continue
                summary.visited += 1
                cr = ChatResult(title=dialog.name or str(dialog.id))
                try:
                    lines: list[str] = []
                    is_forum = bool(getattr(dialog.entity, "forum", False))
                    if is_forum:
                        n, topics, cut = _fetch_forum(client, dialog, lines)
                        cr.topics, cr.truncated = topics, cut
                    else:
                        read_max = getattr(dialog.dialog, "read_inbox_max_id", 0) or 0
                        msgs, cr.truncated = _collect_messages(client, dialog.entity,
                                                               read_max)
                        for m in msgs:
                            lines.append(_format_message(m, cr.title))
                            lines.append("")
                        n = len(msgs)
                    cr.messages = n
                    if n:
                        path = os.path.join(out_dir, f"{_slug(cr.title)}-{dialog.id}.txt")
                        with open(path, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines).rstrip() + "\n")
                        cr.dump_path = path
                        summary.dumped += 1
                    if cr.truncated:
                        summary.truncated += 1
                        if cr.dump_path:
                            with open(cr.dump_path, "a", encoding="utf-8") as f:
                                f.write(f"\n[!] ДАМП ОБРЕЗАН по потолку {MAX_PER_CHAT} "
                                        f"сообщений на чат/топик — хвост НЕ выкачан "
                                        f"и чат НЕ отмечен прочитанным.\n")
                    # Отмечаем сразу, по одному — даже если сообщений в дампе ноль
                    # (непрочитанными могут числиться сервисные записи).
                    # НО НЕ при обрезке: отметить прочитанным то, чего мы не забрали,
                    # значит потерять хвост навсегда и молча.
                    if mark and not cr.truncated:
                        _mark_read(client, dialog, max_id=dialog.dialog.top_message)
                        cr.marked = True
                        summary.marked += 1
                except Exception as e:  # noqa: BLE001 — один чат не роняет обход
                    cr.error = f"{type(e).__name__}: {e}"
                    summary.failed += 1
                summary.chats.append(cr)
    finally:
        client.disconnect()
    return summary
