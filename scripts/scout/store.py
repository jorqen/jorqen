"""SQLite-состояние сборщика: что видели, когда и что с этим сделали.

Зачем вообще состояние. У сайтов нет «непрочитанного» — каждый заход отдаёт всю выдачу
заново, и без памяти скилл разбирает одни и те же вакансии по второму кругу, тратя
на это контекст. База даёт то, чего не даёт ни одна площадка: **дельту**.

База лежит в `.scout/` и в `.gitignore` — репозиторий публичный.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .model import SUMMARY_ID, Vacancy
from .net import PAGE_GONE

DEFAULT_DB = os.environ.get("SCOUT_DB") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".scout", "scout.db",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancy (
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT,
    salary_from   INTEGER,
    salary_to     INTEGER,
    currency      TEXT,
    salary_gross  INTEGER,
    salary_period TEXT,                  -- hour | month | year | NULL (площадка не назвала)
    location      TEXT,
    remote        INTEGER,
    published_at  TEXT,
    updated_at    TEXT,
    employer_url  TEXT,
    tags          TEXT,
    description   TEXT,
    raw           TEXT,
    dup_key       TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS ix_vacancy_first_seen ON vacancy(first_seen);
CREATE INDEX IF NOT EXISTS ix_vacancy_published  ON vacancy(published_at);
CREATE INDEX IF NOT EXISTS ix_vacancy_dup        ON vacancy(dup_key);

-- Что скилл (или пользователь) решил по вакансии. Отдельной таблицей, чтобы
-- пересборка выдачи никогда не затирала решения.
CREATE TABLE IF NOT EXISTS decision (
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    state       TEXT NOT NULL,          -- applied | rejected | skipped | shortlist | interview
    note        TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);

-- Выжимки страниц вакансий (`scout enrich`). Храним, чтобы не качать по второму
-- разу: страница вакансии не дельта, она не меняется настолько часто.
CREATE TABLE IF NOT EXISTS detail (
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url         TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    status      TEXT NOT NULL,           -- ok | generic | generic-empty | error | blocked
    error       TEXT,
    payload     TEXT,                    -- JSON-выжимка (detail.Detail)
    -- Что было на странице (net.PAGE_*), отдельно от того, вышла ли выжимка.
    -- Это ДВА разных вопроса, и одной колонкой они не отвечаются: страница
    -- может разобраться прекрасно и при этом сообщать «вакансия снята» (hh
    -- с флагом archived) — status='ok', page_state='gone'. NULL — состояние
    -- не записано (строка из базы, собранной до появления колонки).
    page_state  TEXT,
    PRIMARY KEY (source, external_id)
);

-- Статусы откликов из внешних систем: hh-кабинет (`hh-sync`) и почта (`mail-sync`).
-- Отдельно от decision: decision — что решили МЫ, negotiation — что ответили НАМ.
-- Уникальность по (title_key, company_key): у письма нет vacancy_id, а вакансия
-- одна и та же приходит из hh и из почты — нормализованная пара их склеивает.
CREATE TABLE IF NOT EXISTS negotiation (
    title_key   TEXT NOT NULL,           -- норм. название (норм. текст, lower)
    company_key TEXT NOT NULL,           -- норм. компания; '' если неизвестна
    title       TEXT NOT NULL,
    company     TEXT,
    status      TEXT NOT NULL,           -- rejection | invitation | interview | viewed
                                         -- | not_viewed | pending | applied | other
    source      TEXT NOT NULL,           -- hh | mail
    url         TEXT,
    event_at    TEXT,                    -- дата события по данным площадки/письма
    note        TEXT,
    first_seen  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (title_key, company_key)
);

-- Кэш прямого канала найма работодателя: найденное однажды не ищется снова.
-- Поиск careers-страницы/ATS/HR-почты — самый дорогой этап ресёрча (живой
-- прогон 04.08.2026: 1,7 млн токенов на 18 компаний), и результат по компании
-- меняется редко. Ключ — нормализованное имя компании, чтобы попадать из любой
-- площадки, где она названа по-своему.
CREATE TABLE IF NOT EXISTS employer_channel (
    company_key TEXT PRIMARY KEY,        -- норм. имя компании (shortlist.norm)
    company     TEXT NOT NULL,
    channel     TEXT NOT NULL,           -- URL careers-страницы / ATS / почта / @ник
    kind        TEXT,                    -- careers | ats | email | telegram | none
    evidence    TEXT,                    -- чем подтверждено, что это канал этой компании
    checked_at  TEXT NOT NULL
);

-- Журнал прогонов. Существует ровно ради одного вопроса: обошли ли мы всё?
CREATE TABLE IF NOT EXISTS run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    query       TEXT,
    args        TEXT
);
CREATE TABLE IF NOT EXISTS run_source (
    run_id     INTEGER NOT NULL,
    source     TEXT NOT NULL,
    status     TEXT NOT NULL,           -- ok | error | skipped
    found      INTEGER DEFAULT 0,
    new        INTEGER DEFAULT 0,
    error      TEXT,
    elapsed_ms INTEGER,
    PRIMARY KEY (run_id, source)
);

-- Зеркало телеграм-постов в приватном канале пользователя.
--
-- Зачем. Пост в чужом канале живёт ровно столько, сколько его там держат:
-- вакансию закрыли — пост удалили, и ссылка из карточки ведёт в никуда, вместе
-- со всем описанием. Пересланная копия в собственном канале переживает удаление
-- оригинала.
CREATE TABLE IF NOT EXISTS tg_mirror (
    source            TEXT NOT NULL,    -- tg:<канал>
    external_id       TEXT NOT NULL,    -- id исходного сообщения
    mirror_chat_id    TEXT NOT NULL,
    mirror_message_id INTEGER NOT NULL,
    mirrored_at       TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);

-- Решения о дублях: «эти две записи — одна вакансия» или «разные».
--
-- Зачем хранить, а не считать заново. Сравнение описаний — единственный слой
-- дедупа, который может ОШИБИТЬСЯ в сторону склейки, а склейка стоит потерянной
-- вакансии. Записанное решение позволяет, во-первых, не пересчитывать его
-- каждую волну, во-вторых — увидеть и поправить: пересчёт молча меняет выдачу
-- от прогона к прогону, а запись можно прочитать и оспорить.
CREATE TABLE IF NOT EXISTS dup_decision (
    key_a      TEXT NOT NULL,           -- «источник:id», меньший лексикографически
    key_b      TEXT NOT NULL,
    verdict    TEXT NOT NULL,           -- same | different
    reason     TEXT,                    -- чем решено: simhash 0.94 / грейд / руками
    decided_by TEXT,                    -- auto | human
    decided_at TEXT NOT NULL,
    PRIMARY KEY (key_a, key_b)
);

-- Маршруты отклика по вакансии: сайт работодателя, ATS, агрегатор, бот.
-- Знание «куда откликаться ближе к работодателю» жило только в голове модели
-- и умирало вместе с контекстом каждой волны. Здесь оно лежит рядом с вакансией.
CREATE TABLE IF NOT EXISTS apply_option (
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url         TEXT NOT NULL,
    publisher   TEXT,                   -- employer | ats | aggregator | telegram
    is_direct   INTEGER,                -- 1 = прямой канал работодателя
    note        TEXT,                   -- откуда узнали этот маршрут
    rank        INTEGER DEFAULT 0,      -- порядок обнаружения: устойчивый выбор best
    liveness    TEXT,                   -- ЖИВА | МЕРТВА | НЕИЗВЕСТНО — по факту обхода
    state       TEXT,                   -- net.PAGE_*: как ответила страница
    found_at    TEXT NOT NULL,
    PRIMARY KEY (source, external_id, url)
);

-- Кэш вердиктов ресёрча: что модель ВЫЯСНИЛА про вакансию, а не что отдала
-- площадка. Раскрытие скрытого работодателя, подтверждение живости и чтение
-- права на работу — самые дорогие проверки волны, и повторять их в следующей
-- незачем: их результат меняется медленнее, чем идут волны.
CREATE TABLE IF NOT EXISTS research (
    source            TEXT NOT NULL,
    external_id       TEXT NOT NULL,
    employer_revealed TEXT,             -- настоящий работодатель за заглушкой
    liveness          TEXT,             -- alive | dead | unknown
    rtw               TEXT,             -- что сказано про право на работу
    verdict           TEXT,             -- итог: годится / нет / почему
    evidence          TEXT,             -- чем подтверждено
    checked_at        TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);

-- Кэш сырых ответов площадок за день.
--
-- Зачем. Правка парсера требует переразбора, переразбор — повторного обхода,
-- а повторный обход это и лишний трафик к чужому сайту, и новая антибот-стена
-- на ровном месте. С кэшем отладка парсера идёт по уже скачанному: площадка
-- об этом не знает, а прогон в тот же день не стоит ни одного запроса.
--
-- Ключ тройной, а не (источник, дата), как просилось изначально: один источник
-- за прогон качает десятки страниц пагинации, и без url в ключе двадцатая
-- страница затирала бы первую — кэш «работал» бы, отдавая одну страницу вместо
-- двадцати. Это была бы тихая потеря 95% выдачи.
CREATE TABLE IF NOT EXISTS raw_cache (
    source     TEXT NOT NULL,
    fetched_on TEXT NOT NULL,           -- YYYY-MM-DD
    url        TEXT NOT NULL,
    body       TEXT NOT NULL,
    stored_at  TEXT NOT NULL,
    PRIMARY KEY (source, fetched_on, url)
);
CREATE INDEX IF NOT EXISTS ix_raw_cache_day ON raw_cache(fetched_on);

-- Свой водяной знак по чатам Telegram: докуда уже разобрано.
--
-- Зачем своя таблица, когда у Telegram есть «непрочитанное». Read-state — это
-- состояние ЧЕЛОВЕКА, а не сборщика: пользователь открыл канал с телефона —
-- и граница окна уехала, а прогон молча пропустил всё, что он пролистал.
-- Обратно её не вернуть: Telegram умеет только «пометить диалог непрочитанным»
-- целиком, без указания сообщения (см. tgclient.cmd_rollback).
--
-- Поэтому выборкой управляет ЭТА таблица, а отметка «прочитано» остаётся
-- отметкой для человека и ни на что не влияет.
CREATE TABLE IF NOT EXISTS tg_watermark (
    chat_id         TEXT PRIMARY KEY,   -- abs(dialog.id), как в именах дампов
    chat_title      TEXT,
    username        TEXT,               -- @ник канала, если публичный
    last_message_id INTEGER NOT NULL,   -- разобрано ВКЛЮЧИТЕЛЬНО до этого id
    updated_at      TEXT NOT NULL
);
"""


# Колонки, добавленные после первой версии схемы. `CREATE TABLE IF NOT EXISTS`
# существующую таблицу не трогает вовсе, поэтому в уже собранной базе новое поле
# появляется только отдельным ALTER. Список, а не «пересоздать таблицу»: база живая,
# в ней полторы тысячи вакансий и все решения по ним, терять её нельзя.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("vacancy", "salary_period", "TEXT"),
    ("detail", "page_state", "TEXT"),
    ("apply_option", "liveness", "TEXT"),
    ("apply_option", "state", "TEXT"),
]


def migrate(conn) -> list[str]:
    """Дотягивает старую базу до текущей схемы. Возвращает список добавленных колонок.

    Существующие строки получают NULL — и это правда: период у них НЕ известен,
    он был выброшен парсером, а не сохранён. Домысливать его задним числом нельзя.
    """
    applied: list[str] = []
    for table, column, decl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols or column in cols:
            continue          # таблицы нет — её создаст SCHEMA; колонка есть — работы нет
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        applied.append(f"{table}.{column}")
    return applied


@contextmanager
def connect(path: str = DEFAULT_DB):
    # Каталог создаём, только если он в пути ЕСТЬ. `os.path.dirname` для
    # «scout.db» и для «:memory:» отдаёт пустую строку, и makedirs("") падает
    # FileNotFoundError — то есть база в текущем каталоге и база в памяти были
    # недоступны обе. Первое ломало `scout --db scout.db <команда>`, второе —
    # любой тест, которому база не нужна вовсе.
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def last_run_at(db: str = DEFAULT_DB) -> str | None:
    """Когда сборщик ходил в последний раз. ISO или None, если не ходил вовсе.

    Берётся из журнала прогонов, а не из mtime отчётов: файл отчёта можно
    удалить, переместить и скопировать, а строка в `run` — факт обхода.
    """
    try:
        with connect(db) as conn:
            row = conn.execute(
                "SELECT started_at FROM run WHERE finished_at IS NOT NULL "
                "ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:  # noqa: BLE001 — нет базы это не ошибка вызова
        return None
    return row["started_at"] if row else None


def since_arg(value: str | None, *, db: str = DEFAULT_DB) -> str | None:
    """Понимает `3d`, `12h`, `2026-07-20`, полный ISO и `auto`. Возвращает ISO в UTC.

    `auto` — «с прошлого прогона», и это ответ на шаг, который до сих пор делала
    модель рассуждением (SKILL.md: непрочитанное в Telegram → дата последнего
    отчёта → спросить человека). Оба источника машинные, и решение по ним
    воспроизводимо.

    ⚠️ Окно НИКОГДА не уже суток. Прогон мог упасть на середине, и взять окно
    ровно от его начала значит оставить дыру, которую следующее узкое окно уже
    не закроет, — а в отчёте она выглядит как «новых вакансий не было».
    Перекрытие стоит дублей, дедуп их схлопывает; экономия стоила бы вакансий.
    """
    if value and value.strip().lower() == "auto":
        last = last_run_at(db)
        if not last:
            return since_arg("3d")
        try:
            dt = datetime.fromisoformat(last)
        except ValueError:
            return since_arg("3d")
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        edge = min(dt, datetime.now(timezone.utc) - timedelta(days=1))
        return edge.astimezone(timezone.utc).isoformat()
    if not value:
        return None
    v = value.strip().lower()
    if v.endswith("d") and v[:-1].isdigit():
        return (datetime.now(timezone.utc) - timedelta(days=int(v[:-1]))).isoformat()
    if v.endswith("h") and v[:-1].isdigit():
        return (datetime.now(timezone.utc) - timedelta(hours=int(v[:-1]))).isoformat()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"не понимаю дату {value!r}; ожидаю 3d, 12h, 2026-07-20 или ISO") from e
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def start_run(conn, query: str, args: dict) -> int:
    cur = conn.execute(
        "INSERT INTO run (started_at, query, args) VALUES (?,?,?)",
        (now(), query, json.dumps(args, ensure_ascii=False, default=str)),
    )
    return cur.lastrowid


def finish_run(conn, run_id: int) -> None:
    conn.execute("UPDATE run SET finished_at=? WHERE id=?", (now(), run_id))


def record_source(conn, run_id: int, source: str, status: str, *,
                  found: int = 0, new: int = 0, error: str | None = None,
                  elapsed_ms: int = 0) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO run_source "
        "(run_id, source, status, found, new, error, elapsed_ms) VALUES (?,?,?,?,?,?,?)",
        (run_id, source, status, found, new, error, elapsed_ms),
    )


def upsert(conn, vacancies: list[Vacancy]) -> tuple[int, int]:
    """Возвращает (новых, обновлённых). Новизна считается по (source, external_id).

    Служебные сводки источников (`external_id == "_summary"`) ПИШУТСЯ, но в счёт
    не идут: это не вакансии. Пока они считались, строка отчёта противоречила
    сама себе — «найдено 6273, новых 6294», где лишняя двадцать одна штука ровно
    равна числу отработавших площадок.
    """
    ts = now()
    new = updated = 0
    for v in vacancies:
        counts = v.external_id != SUMMARY_ID
        row = v.to_row()
        exists = conn.execute(
            "SELECT 1 FROM vacancy WHERE source=? AND external_id=?",
            (v.source, v.external_id),
        ).fetchone()
        if exists:
            conn.execute(
                "UPDATE vacancy SET url=?, title=?, company=?, salary_from=?, salary_to=?, "
                "currency=?, salary_gross=?, salary_period=?, location=?, remote=?, "
                "published_at=?, updated_at=?, "
                "employer_url=COALESCE(?, employer_url), tags=?, description=?, raw=?, "
                "dup_key=?, last_seen=? WHERE source=? AND external_id=?",
                (row["url"], row["title"], row["company"], row["salary_from"], row["salary_to"],
                 row["currency"], row["salary_gross"], row["salary_period"], row["location"],
                 row["remote"], row["published_at"], row["updated_at"],
                 row["employer_url"], row["tags"],
                 row["description"], row["raw"], row["dup_key"], ts, v.source, v.external_id),
            )
            updated += counts
        else:
            conn.execute(
                "INSERT INTO vacancy (source, external_id, url, title, company, salary_from, "
                "salary_to, currency, salary_gross, salary_period, location, remote, "
                "published_at, updated_at, "
                "employer_url, tags, description, raw, dup_key, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (v.source, v.external_id, row["url"], row["title"], row["company"],
                 row["salary_from"], row["salary_to"], row["currency"], row["salary_gross"],
                 row["salary_period"], row["location"], row["remote"],
                 row["published_at"], row["updated_at"],
                 row["employer_url"], row["tags"], row["description"], row["raw"],
                 row["dup_key"], ts, ts),
            )
            new += counts
    return new, updated


def _query_sql(*, since, first_seen_since, sources, exclude_decided,
               include_summary) -> tuple[list[str], list]:
    sql = ["FROM vacancy v LEFT JOIN decision d "
           "ON d.source=v.source AND d.external_id=v.external_id WHERE 1=1"]
    params: list = []
    if not include_summary:
        # `_summary` — служебная строка отчёта ATS, а не вакансия: у неё пустой url
        # и она портит и счётчики, и таблицы. enrich её и так отбрасывал, а `new`
        # печатал строкой с пустой ссылкой.
        sql.append("AND v.external_id <> '_summary' AND v.url <> ''")
    if since:
        sql.append("AND (COALESCE(v.published_at,'') >= ? OR COALESCE(v.updated_at,'') >= ?)")
        params += [since, since]
    if first_seen_since:
        sql.append("AND v.first_seen >= ?")
        params.append(first_seen_since)
    if sources:
        sql.append(f"AND v.source IN ({','.join('?' * len(sources))})")
        params += sources
    if exclude_decided:
        # Отработанное не показываем: отклик уже ушёл или вакансия отвергнута.
        sql.append("AND (d.state IS NULL OR d.state IN ('shortlist','interview'))")
    return sql, params


def count_undated(conn, *, sources: list[str] | None = None,
                  exclude_decided: bool = True) -> dict[str, int]:
    """Строки без ОБЕИХ дат, по источникам.

    Окно `since` фильтрует по публикации-ИЛИ-обновлению, и строка, у которой
    нет ни той ни другой, не попадает НИ В ОДНО окно. Живьём: geekjob (91)
    и relocateme (13) не существовали для `new --since --by published`,
    и заметить это было нечем — площадки значились «ok» в покрытии."""
    sql, params = _query_sql(since=None, first_seen_since=None, sources=sources,
                             exclude_decided=exclude_decided,
                             include_summary=False)
    sql.append("AND v.published_at IS NULL AND v.updated_at IS NULL")
    rows = conn.execute("SELECT v.source, COUNT(*) " + " ".join(sql)
                        + " GROUP BY v.source", params).fetchall()
    return {r[0]: r[1] for r in rows}


def count(conn, *, since: str | None = None, first_seen_since: str | None = None,
          sources: list[str] | None = None, exclude_decided: bool = True,
          include_summary: bool = False) -> int:
    """Сколько строк ВСЕГО подходит под фильтр — без учёта limit.

    Существует ради одного: `new --limit 200` печатал «200 вакансий» там, где
    в дельте было 1505, и вызывающий терял 1305 штук, не узнав об этом."""
    sql, params = _query_sql(since=since, first_seen_since=first_seen_since,
                             sources=sources, exclude_decided=exclude_decided,
                             include_summary=include_summary)
    return conn.execute("SELECT COUNT(*) " + " ".join(sql), params).fetchone()[0]


def query(conn, *, since: str | None = None, first_seen_since: str | None = None,
          sources: list[str] | None = None, exclude_decided: bool = True,
          limit: int | None = None, include_summary: bool = False,
          order: str = "date") -> list[dict]:
    """Выборка для отчёта.

    `since` — по дате публикации-ИЛИ-обновления (решение пользователя от 25.07.2026:
    достаточно любой из двух — поднятая работодателем вакансия так же свежа, как новая).
    `first_seen_since` — «чего не было в прошлый заход», это и есть дельта.

    `order='salary'` поднимает вакансии с указанной вилкой — так лимит на выжимки
    тратится на то, по чему можно писать карточку, а не на ежедневные перепубликации
    агрегатора.
    """
    sql, params = _query_sql(since=since, first_seen_since=first_seen_since,
                             sources=sources, exclude_decided=exclude_decided,
                             include_summary=include_summary)
    head = "SELECT v.*, d.state AS decision, d.note AS decision_note "
    if order == "salary":
        sql.append("ORDER BY (v.salary_from IS NULL AND v.salary_to IS NULL), "
                   "COALESCE(v.published_at, v.first_seen) DESC")
    else:
        sql.append("ORDER BY COALESCE(v.published_at, v.first_seen) DESC")
    if limit:
        sql.append("LIMIT ?")
        params.append(limit)
    return [dict(r) for r in conn.execute(head + " ".join(sql), params).fetchall()]


def vacancy_exists(conn, source: str, external_id: str) -> bool:
    return conn.execute("SELECT 1 FROM vacancy WHERE source=? AND external_id=?",
                        (source, str(external_id))).fetchone() is not None


def decide(conn, source: str, external_id: str, state: str, note: str | None = None) -> None:
    """Пишет решение. Повторный mark БЕЗ --note не затирает прежнюю заметку:
    раньше `mark hh 111 --state skip` стирал «отклик 30.07», записанный минутой
    ранее, — потеря без единого предупреждения."""
    conn.execute(
        "INSERT INTO decision (source, external_id, state, note, updated_at) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(source, external_id) DO UPDATE SET "
        "state=excluded.state, note=COALESCE(excluded.note, decision.note), "
        "updated_at=excluded.updated_at",
        (source, str(external_id), state, note, now()),
    )


def search(conn, needle: str, limit: int = 50) -> list[dict]:
    """Поиск по названию и компании с показом решений — защита от повторного
    отклика туда, где уже отказ. Регистронезависимо для латиницы (LIKE в SQLite
    кириллицу по регистру не сводит — ищи в том регистре, в каком писала площадка,
    или дважды)."""
    pat = f"%{needle}%"
    rows = conn.execute(
        "SELECT v.source, v.external_id, v.title, v.company, v.url, v.salary_from, "
        "v.salary_to, v.currency, v.salary_gross, v.salary_period, v.first_seen, v.last_seen, "
        "d.state AS decision, d.note AS decision_note, d.updated_at AS decided_at "
        "FROM vacancy v LEFT JOIN decision d "
        "ON d.source=v.source AND d.external_id=v.external_id "
        "WHERE v.title LIKE ? OR v.company LIKE ? "
        "ORDER BY d.state IS NULL, v.last_seen DESC LIMIT ?",
        (pat, pat, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def save_detail(conn, source: str, external_id: str, url: str, status: str,
                payload: dict | None = None, error: str | None = None,
                page_state: str | None = None) -> None:
    """Итог захода на страницу вакансии. Пустая выжимка НЕ затирает добытую.

    `INSERT OR REPLACE` здесь терял знание: `enrich --refresh` перекачивает уже
    обогащённое, и страница, которая сегодня отдаёт 404 или стену, стирала
    вчерашнюю полную выжимку. Дальше `have_details` не пускала вакансию
    в закачку две недели — писать карточку по ней было уже нечем. В базе на
    10.08.2026 таких строк без payload 477 при 869 с payload.

    Разделение то же, что в `save_research`: `status`, `error`, `url` и
    `fetched_at` описывают ПОСЛЕДНЮЮ попытку и обновляются всегда, а `payload`
    и `page_state` — это знание, и пустотой оно не перезаписывается.
    """
    conn.execute(
        "INSERT INTO detail (source, external_id, url, fetched_at, status, "
        "error, payload, page_state) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source, external_id) DO UPDATE SET "
        "url=excluded.url, fetched_at=excluded.fetched_at, status=excluded.status, "
        "error=excluded.error, payload=COALESCE(excluded.payload, detail.payload), "
        "page_state=COALESCE(excluded.page_state, detail.page_state)",
        (source, str(external_id), url, now(), status, error,
         json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
         page_state),
    )


RETRY_BLOCKED_DAYS = 3

# Через сколько дней перепроверять вакансию, признанную снятой.
#
# Не «никогда», хотя снятая вакансия обратно не открывается: «снята» — это наш
# ВЫВОД по признакам страницы (`net.classify_page`), а не факт от площадки.
# Ошибиться в нём можно двумя способами, и оба не редкость: временный 404 на
# переезде страницы и наш же маркер, поймавший живую вакансию. Записать такой
# вывод навсегда значит вычеркнуть живую вакансию молча и без права на пересмотр
# — ровно та тихая потеря, ради которой всё это и писалось.
#
# Дольше, чем стена: стену снимает человек заходом в браузер, и ждать его больше
# нескольких дней незачем, а здесь ждать нечего и некого. Две недели — это одна
# лишняя закачка в полмесяца, если вакансия правда снята, и самоизлечение
# записи, если мы ошиблись.
RETRY_GONE_DAYS = 14

# Статусы, после которых качать нечего: выжимка есть, она в базе.
_DONE_STATUSES = ("ok", "generic", "generic-empty")


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _detail_rows(conn, keys: list[tuple[str, str]]):
    """(ключ, строка detail) по тем ключам, что вообще есть в базе."""
    for source, ext in keys:
        row = conn.execute(
            "SELECT status, page_state, fetched_at FROM detail "
            "WHERE source=? AND external_id=?", (source, str(ext))).fetchone()
        if row:
            yield (source, str(ext)), row


def have_details(conn, keys: list[tuple[str, str]], *,
                 retry_blocked_after_days: int = RETRY_BLOCKED_DAYS,
                 retry_gone_after_days: int = RETRY_GONE_DAYS
                 ) -> set[tuple[str, str]]:
    """Какие из (source, external_id) уже обогащены и повторять их сейчас незачем.

    Успешные (`ok`/`generic`) — навсегда. Заблокированные антиботом — только на
    `retry_blocked_after_days`: пробовать их снова надо, но не КАЖДЫЙ прогон.
    Живьём это выглядело так: девять капчей LinkedIn съедали 45% лимита выжимок
    в каждом скане, вечно, вытесняя живые вакансии.

    Снятые (`page_state='gone'`) — на `retry_gone_after_days`, по той же причине
    и с тем же механизмом: страница отвечает «такой вакансии нет», выжимки из
    неё не выйдет никогда, а качалась она каждый прогон и занимала место
    в `--max-enrich`, вытесняя живые. Почему отложенно, а не навсегда —
    см. `RETRY_GONE_DAYS`."""
    out: set[tuple[str, str]] = set()
    blocked_cutoff = _cutoff(retry_blocked_after_days)
    gone_cutoff = _cutoff(retry_gone_after_days)
    for key, row in _detail_rows(conn, keys):
        fetched = row["fetched_at"] or ""
        if row["status"] in _DONE_STATUSES:
            out.add(key)
        elif row["status"] == "blocked" and fetched >= blocked_cutoff:
            out.add(key)
        elif row["page_state"] == PAGE_GONE and fetched >= gone_cutoff:
            out.add(key)
    return out


def gone_details(conn, keys: list[tuple[str, str]], *,
                 retry_after_days: int = RETRY_GONE_DAYS) -> set[tuple[str, str]]:
    """Какие ключи пропускаются ИМЕННО как снятые — для честной строки отчёта.

    Без этого числа отложенный повтор работает молча, а молчащий пропуск
    неотличим от потерянной вакансии: в отчёте «уже было в базе» растёт, а по
    какой причине — не видно. Заодно это единственный сигнал, что маркеры
    снятой вакансии начали срабатывать на живых: сотня «снятых» за прогон
    видна сразу.

    Разобравшаяся выжимка снятой вакансии (hh отдал страницу с флагом archived)
    сюда НЕ попадает: она лежит в базе успешной, качать её незачем и через две
    недели — это не «пропущено», а «есть»."""
    cutoff = _cutoff(retry_after_days)
    return {key for key, row in _detail_rows(conn, keys)
            if row["page_state"] == PAGE_GONE
            and row["status"] not in _DONE_STATUSES
            and (row["fetched_at"] or "") >= cutoff}


def blocked_details(conn, since: str | None = None) -> list[dict]:
    """Стены, ждущие повтора, — для честной строки в отчёте."""
    sql = "SELECT * FROM detail WHERE status='blocked'"
    params: list = []
    if since:
        sql += " AND fetched_at >= ?"
        params.append(since)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_detail_payload(conn, source: str, external_id: str) -> dict | None:
    row = conn.execute(
        "SELECT payload FROM detail WHERE source=? AND external_id=? AND payload IS NOT NULL",
        (source, str(external_id))).fetchone()
    return json.loads(row["payload"]) if row else None


def _neg_key(s: str | None) -> str:
    """Ключ для склейки negotiation-строк: нижний регистр, схлопнутые пробелы.

    Без лемматизации и без выбрасывания слов — ключ должен быть консервативным:
    лучше две строки про одну вакансию, чем письмо про Go-вакансию, склеенное
    с письмом про Java-вакансию той же компании."""
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip().lower()


# Статусы, означающие «работодатель ОТВЕТИЛ». Всё остальное (not_viewed,
# pending, applied, other) — «ответа ещё нет».
_ANSWERED = frozenset({"rejection", "invitation", "interview", "viewed"})


def _regression(old: str, new: str, old_event: str | None,
                new_event: str | None) -> bool:
    """Откат из «ответ есть» в «ответа ещё нет» — событие, которого не бывает.

    Полной машины переходов (списка разрешённых пар) здесь нет намеренно.
    `negotiation` — не наше состояние, а ЗЕРКАЛО чужого: что ответил
    работодатель. Этими переходами управляем не мы, и запрещать их не за что:
    «отказ → приглашение» в жизни случается, и потерять такую новость дороже,
    чем стерпеть странную пару.

    А вот откат в незнание событием не бывает вовсе: работодатель не может
    «разпросмотреть» резюме и не может забрать отказ обратно в «ожидание». Он
    бывает только рассинхроном источников — список кабинета показывает старое
    состояние отклика, а письмо с отказом уже пришло и записано. Живая цена
    такого затирания: `status --query`, заведённый ради вопроса «сюда уже
    отказали?», отвечает «ждём ответа» там, где отказ пришёл неделю назад.

    Исключение — ДАТА. Событие свежее сохранённого это не рассинхрон, а новый
    отклик на ту же вакансию: ключ строки — название плюс компания, и повторный
    отклик через полгода выглядит ровно так. Сравниваем по дню: у hh дата
    приходит из списка, у почты — из письма, до минут они всё равно не сходятся.
    """
    if new in _ANSWERED or old not in _ANSWERED:
        return False
    return not ((new_event or "")[:10] > (old_event or "")[:10])


def upsert_negotiation(conn, *, title: str, company: str | None, status: str,
                       source: str, url: str | None = None, event_at: str | None = None,
                       note: str | None = None,
                       key_extra: str | None = None) -> tuple[str, str | None]:
    """Пишет статус отклика. Возвращает (что_случилось, прежний_статус):
    ('new', None) — строки не было; ('changed', старый) — статус сменился;
    ('same', статус) — ничего нового; ('kept', статус) — переход ОТКЛОНЁН
    (см. `_regression`) либо строку успел поменять кто-то другой. По этому
    возврату sync печатает дельту; 'kept' у него попадает в «без изменений» —
    так и есть, строка не менялась, — а причина остаётся в `note`.

    `key_extra` — страховка от схлопывания: когда настоящие вакансия и компания
    из письма не вычленились, в ключ добавляется Message-ID. Без него письма
    с шаблонной темой («Работодатель не готов пригласить вас») от разных
    работодателей становились ОДНОЙ строкой, и 45% статусов исчезали молча."""
    tk, ck = _neg_key(title), _neg_key(company)
    if key_extra:
        tk = f"{tk}#{_neg_key(key_extra)}"
    ts = now()
    row = conn.execute(
        "SELECT status, event_at, note, url FROM negotiation "
        "WHERE title_key=? AND company_key=?", (tk, ck)).fetchone()
    # Ключ (название, компания) склеивает hh с почтой по одной вакансии — ради
    # этого он и такой. Но у одного работодателя бывают ДВЕ вакансии с
    # одинаковым названием, и тогда тот же ключ склеивает разные отклики: отказ
    # по одной молча заменялся приглашением по другой, а `status --query`
    # отвечает по этой таблице на вопрос «сюда уже отказали?».
    #
    # Расходимся только по ДОКАЗАННОМУ различию — когда обе стороны назвали
    # адрес и адреса разные. Почта адрес почти никогда не называет (3 строки
    # из 106 на 10.08.2026), поэтому склейка hh+почта продолжает работать.
    if row is not None and url and row["url"] and row["url"] != url:
        tk = f"{tk}#{_neg_key(url)}"
        row = conn.execute(
            "SELECT status, event_at, note, url FROM negotiation "
            "WHERE title_key=? AND company_key=?", (tk, ck)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO negotiation (title_key, company_key, title, company, status, "
            "source, url, event_at, note, first_seen, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tk, ck, title, company, status, source, url, event_at, note, ts, ts))
        return "new", None
    old = row["status"]
    if old == status:
        # Дату события всё равно обновляем: «просмотрено вчера» и «просмотрено сегодня»
        # — один статус, но свежая дата нужна отчёту.
        conn.execute(
            "UPDATE negotiation SET event_at=COALESCE(?, event_at), url=COALESCE(?, url) "
            "WHERE title_key=? AND company_key=?", (event_at, url, tk, ck))
        return "same", old
    if _regression(old, status, row["event_at"], event_at):
        # Отказ не применяем МОЛЧА: расхождение источников — это факт, и через
        # неделю «почему hh показывает одно, а база другое» иначе не восстановить.
        mark = f"[регресс отклонён] {source}: {status}" + (f" {event_at[:10]}"
                                                           if event_at else "")
        old_note = row["note"] or ""
        if mark not in old_note:
            conn.execute(
                "UPDATE negotiation SET note=? WHERE title_key=? AND company_key=?",
                ((f"{old_note}; {mark}" if old_note else mark)[:400], tk, ck))
        return "kept", old
    # `AND status=?` — переход происходит только из того состояния, которое мы
    # прочитали. Идея из соседнего проекта: между SELECT и UPDATE строку может
    # поменять другой процесс (mail-sync в одном терминале и scan в другом —
    # это две РАЗНЫХ сессии sqlite), и тогда наш UPDATE затрёт чужую свежую
    # запись своим устаревшим решением. rowcount == 1 говорит, что этого не было.
    cur = conn.execute(
        "UPDATE negotiation SET status=?, source=?, url=COALESCE(?, url), "
        "event_at=COALESCE(?, event_at), note=COALESCE(?, note), updated_at=? "
        "WHERE title_key=? AND company_key=? AND status=?",
        (status, source, url, event_at, note, ts, tk, ck, old))
    return ("changed", old) if cur.rowcount == 1 else ("kept", old)


def search_negotiations(conn, needle: str, limit: int = 50) -> list[dict]:
    """Статусы откликов по подстроке названия/компании/темы.

    Ровно тот вопрос, ради которого таблица заведена: «сюда уже отказали?».
    До этого `status --query` смотрел только в vacancy+decision, и 79 отказов
    из кабинета плюс все почтовые статусы были недостижимы командой, созданной,
    чтобы не откликнуться второй раз туда, где уже отказ."""
    pat = f"%{needle}%"
    rows = conn.execute(
        "SELECT * FROM negotiation WHERE title LIKE ? OR company LIKE ? OR note LIKE ? "
        "ORDER BY COALESCE(event_at, updated_at) DESC LIMIT ?",
        (pat, pat, pat, limit)).fetchall()
    return [dict(r) for r in rows]


def negotiations(conn, *, updated_since: str | None = None) -> list[dict]:
    sql = "SELECT * FROM negotiation"
    params: list = []
    if updated_since:
        sql += " WHERE updated_at >= ?"
        params.append(updated_since)
    sql += " ORDER BY updated_at DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ──────────────────────────────────────────────────────────────────────────────
# Маршруты отклика и кэш ресёрча
# ──────────────────────────────────────────────────────────────────────────────

def save_apply_options(conn, source: str, external_id: str,
                       options: list[dict]) -> int:
    """Пишет маршруты. Возвращает, сколько записано.

    Дописывание, а не удаление старых: маршрут, найденный в прошлой волне и
    пропавший из выдачи сегодня, всё ещё рабочий — терять его незачем.

    Поля обхода (`liveness`, `state`) переписываются ТОЛЬКО непустыми: их знает
    один `crawl`, а строку по тому же адресу пишет ещё и дешёвый `gather`,
    которому про живость известно ничего. С прежним `INSERT OR REPLACE`
    ближайший `brief` затирал результат обхода в NULL — то есть дорогая
    проверка жила до первой следующей команды.
    """
    ts = now()
    for i, o in enumerate(options):
        conn.execute(
            "INSERT INTO apply_option (source, external_id, url, publisher, "
            "is_direct, note, rank, liveness, state, found_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source, external_id, url) DO UPDATE SET "
            "  publisher = excluded.publisher, is_direct = excluded.is_direct, "
            "  note = COALESCE(excluded.note, apply_option.note), "
            "  rank = excluded.rank, "
            "  liveness = COALESCE(excluded.liveness, apply_option.liveness), "
            "  state = COALESCE(excluded.state, apply_option.state), "
            "  found_at = excluded.found_at",
            (source, str(external_id), o["url"], o.get("publisher"),
             1 if o.get("is_direct") else 0, o.get("note"),
             int(o.get("rank", i)), o.get("liveness") or None,
             o.get("state") or None, ts))
    return len(options)


def apply_options(conn, source: str, external_id: str) -> list[dict]:
    """Маршруты в УСТОЙЧИВОМ порядке. Без явного ORDER BY по rank выбор «лучшего»
    менялся от прогона к прогону на одних и тех же данных: SQL порядок строк
    не гарантирует, а два маршрута одного ранга различались только им."""
    rows = conn.execute(
        "SELECT url, publisher, is_direct, note, rank, liveness, state "
        "FROM apply_option WHERE source=? AND external_id=? "
        "ORDER BY is_direct DESC, rank, url",
        (source, str(external_id))).fetchall()
    # 🔴 Кто публикует — ФУНКЦИЯ ОТ АДРЕСА, а не факт наблюдения: она считается
    # по реестру витрин, и реестр пополняется. Записанное в базе значение
    # устаревает молча. Живой счёт 09.08.2026: реестр научился видеть
    # `jooble.org`, `adzuna.*` и `jobviewtrack.com`, а в карточках они остались
    # «[employer, прямой]» — то есть человеку по-прежнему обещали прямой канал
    # в компанию. Живость и заметки — наоборот, наблюдения, и берутся из базы
    # как есть.
    from .applyopt import classify  # noqa: PLC0415 — цикл импорта иначе
    out = []
    for r in rows:
        publisher, direct = classify(r["url"])
        out.append({"url": r["url"], "publisher": publisher, "is_direct": direct,
                    "note": r["note"], "rank": r["rank"],
                    "liveness": r["liveness"], "state": r["state"]})
    return out


def save_mirror(conn, source: str, external_id: str, chat_id: str,
                message_id: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO tg_mirror (source, external_id, mirror_chat_id, "
        "mirror_message_id, mirrored_at) VALUES (?,?,?,?,?)",
        (source, str(external_id), str(chat_id), int(message_id), now()))


def mirrored(conn) -> set[tuple[str, str]]:
    """Что уже зеркалировано — чтобы не пересылать по второму разу."""
    return {(r["source"], r["external_id"])
            for r in conn.execute("SELECT source, external_id FROM tg_mirror")}


def mirror_of(conn, source: str, external_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM tg_mirror WHERE source=? AND external_id=?",
                       (source, str(external_id))).fetchone()
    return dict(row) if row else None


def _dup_pair(a: str, b: str) -> tuple[str, str]:
    """Пара ключей в устойчивом порядке: решение о дубле симметрично, и хранить
    его дважды (a,b) и (b,a) значит получить два расходящихся ответа."""
    return (a, b) if a <= b else (b, a)


def save_dup_decision(conn, key_a: str, key_b: str, verdict: str, *,
                      reason: str | None = None, by: str = "auto") -> None:
    a, b = _dup_pair(key_a, key_b)
    conn.execute(
        "INSERT INTO dup_decision (key_a, key_b, verdict, reason, decided_by, "
        "decided_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(key_a, key_b) DO UPDATE SET verdict=excluded.verdict, "
        "reason=excluded.reason, decided_by=excluded.decided_by, "
        "decided_at=excluded.decided_at "
        # Решение ЧЕЛОВЕКА автоматика перебить не может: иначе следующий прогон
        # молча отменит правку, ради которой человека и позвали.
        "WHERE dup_decision.decided_by <> 'human' OR excluded.decided_by = 'human'",
        (a, b, verdict, reason, by, now()))


def dup_decisions(conn) -> dict[tuple[str, str], dict]:
    return {(r["key_a"], r["key_b"]): dict(r)
            for r in conn.execute("SELECT * FROM dup_decision")}


def dup_decision(conn, key_a: str, key_b: str) -> dict | None:
    a, b = _dup_pair(key_a, key_b)
    row = conn.execute("SELECT * FROM dup_decision WHERE key_a=? AND key_b=?",
                       (a, b)).fetchone()
    return dict(row) if row else None


def save_research(conn, source: str, external_id: str, **fields) -> None:
    """Вердикт ресёрча. Пустые поля НЕ затирают уже записанные.

    Это принципиально: волна, выяснившая только живость, не должна стирать
    раскрытого работодателя, добытого прошлой волной, — иначе кэш будет
    терять ровно то, ради чего заведён.
    """
    cols = ("employer_revealed", "liveness", "rtw", "verdict", "evidence")
    vals = {c: fields.get(c) for c in cols}
    conn.execute(
        "INSERT INTO research (source, external_id, employer_revealed, liveness, "
        "rtw, verdict, evidence, checked_at) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source, external_id) DO UPDATE SET "
        + ", ".join(f"{c}=COALESCE(excluded.{c}, research.{c})" for c in cols)
        + ", checked_at=excluded.checked_at",
        (source, str(external_id), vals["employer_revealed"], vals["liveness"],
         vals["rtw"], vals["verdict"], vals["evidence"], now()))


def research(conn, source: str, external_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM research WHERE source=? AND external_id=?",
                       (source, str(external_id))).fetchone()
    return dict(row) if row else None


# ──────────────────────────────────────────────────────────────────────────────
# Кэш сырых ответов
# ──────────────────────────────────────────────────────────────────────────────

def raw_cache_get(conn, source: str, url: str, *, on: str | None = None) -> str | None:
    day = on or datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT body FROM raw_cache WHERE source=? AND fetched_on=? AND url=?",
        (source, day, url)).fetchone()
    return row["body"] if row else None


def raw_cache_put(conn, source: str, url: str, body: str, *,
                  on: str | None = None) -> None:
    day = on or datetime.now(timezone.utc).date().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO raw_cache (source, fetched_on, url, body, stored_at) "
        "VALUES (?,?,?,?,?)", (source, day, url, body, now()))


def raw_cache_stats(conn, *, on: str | None = None) -> dict:
    day = on or datetime.now(timezone.utc).date().isoformat()
    rows = conn.execute(
        "SELECT source, COUNT(*) n, SUM(LENGTH(body)) b FROM raw_cache "
        "WHERE fetched_on=? GROUP BY source ORDER BY n DESC", (day,)).fetchall()
    return {"day": day, "sources": [dict(r) for r in rows],
            "pages": sum(r["n"] for r in rows),
            "bytes": sum(r["b"] or 0 for r in rows)}


def raw_cache_clear(conn, *, before: str | None = None) -> int:
    """Чистка. Без `before` — весь кэш; с ним — всё старше указанного дня."""
    if before:
        cur = conn.execute("DELETE FROM raw_cache WHERE fetched_on < ?", (before,))
    else:
        cur = conn.execute("DELETE FROM raw_cache")
    return cur.rowcount


# ──────────────────────────────────────────────────────────────────────────────
# Водяной знак Telegram
# ──────────────────────────────────────────────────────────────────────────────

def tg_watermarks(conn) -> dict[str, int]:
    """{chat_id: last_message_id} — докуда разобран каждый чат."""
    return {str(r["chat_id"]): int(r["last_message_id"])
            for r in conn.execute("SELECT chat_id, last_message_id FROM tg_watermark")}


def set_tg_watermark(conn, chat_id: str | int, last_message_id: int, *,
                     chat_title: str | None = None,
                     username: str | None = None) -> None:
    """Двигает знак ВПЕРЁД. Назад — только явным `tg-rollback`.

    Монотонность здесь не педантизм: `fetch` отмечает чат сразу после дампа,
    а прогон может упасть на следующем чате и быть перезапущен. Если бы знак
    принимал любое значение, повторный проход по чату, где на этот раз ничего
    не выкачалось (сеть моргнула, `max_id` вышел нулём), откатил бы его в ноль
    и следующий прогон выкачал бы канал целиком — сотни лишних строк и
    потерянная граница.
    """
    conn.execute(
        "INSERT INTO tg_watermark (chat_id, chat_title, username, last_message_id, "
        "updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET "
        "chat_title=COALESCE(excluded.chat_title, tg_watermark.chat_title), "
        "username=COALESCE(excluded.username, tg_watermark.username), "
        "last_message_id=MAX(excluded.last_message_id, tg_watermark.last_message_id), "
        "updated_at=excluded.updated_at",
        (str(chat_id), chat_title, username, int(last_message_id), now()),
    )


def seed_tg_watermarks(conn, chats: dict, *, force: bool = False) -> tuple[int, int]:
    """Стартовые значения из файла отката. Возвращает (проставлено, пропущено).

    `resume_from_id` в файле — это id, С КОТОРОГО надо продолжить минус один,
    то есть ровно «последнее разобранное». Кладём его как есть.

    Уже существующий знак по умолчанию НЕ трогаем: засеивание — разовая
    операция, и повторный запуск не должен отматывать назад прогресс, набранный
    после неё. `force=True` — осознанный откат руками.
    """
    seeded = skipped = 0
    have = tg_watermarks(conn)
    for rec in chats.values():
        cid = str(rec.get("chat_id") or "")
        if not cid:
            continue
        if cid in have and not force:
            skipped += 1
            continue
        resume = int(rec.get("resume_from_id") or 0)
        if force:
            conn.execute("DELETE FROM tg_watermark WHERE chat_id=?", (cid,))
        set_tg_watermark(conn, cid, resume, chat_title=rec.get("title"))
        seeded += 1
    return seeded, skipped


def last_run(conn) -> dict | None:
    row = conn.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    out = dict(row)
    out["sources"] = [dict(r) for r in conn.execute(
        "SELECT * FROM run_source WHERE run_id=? ORDER BY status, source", (row["id"],)
    ).fetchall()]
    return out


def stats(conn) -> dict:
    g = lambda q: conn.execute(q).fetchone()[0]
    return {
        "vacancies": g("SELECT COUNT(*) FROM vacancy"),
        "sources": g("SELECT COUNT(DISTINCT source) FROM vacancy"),
        "decided": g("SELECT COUNT(*) FROM decision"),
        "runs": g("SELECT COUNT(*) FROM run"),
    }
