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
"""


# Колонки, добавленные после первой версии схемы. `CREATE TABLE IF NOT EXISTS`
# существующую таблицу не трогает вовсе, поэтому в уже собранной базе новое поле
# появляется только отдельным ALTER. Список, а не «пересоздать таблицу»: база живая,
# в ней полторы тысячи вакансий и все решения по ним, терять её нельзя.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("vacancy", "salary_period", "TEXT"),
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
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


def since_arg(value: str | None) -> str | None:
    """Понимает `3d`, `12h`, `2026-07-20` и полный ISO. Возвращает ISO в UTC."""
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
                payload: dict | None = None, error: str | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO detail (source, external_id, url, fetched_at, status, "
        "error, payload) VALUES (?,?,?,?,?,?,?)",
        (source, str(external_id), url, now(), status, error,
         json.dumps(payload, ensure_ascii=False, default=str) if payload else None),
    )


RETRY_BLOCKED_DAYS = 3


def have_details(conn, keys: list[tuple[str, str]], *,
                 retry_blocked_after_days: int = RETRY_BLOCKED_DAYS
                 ) -> set[tuple[str, str]]:
    """Какие из (source, external_id) уже обогащены и повторять их сейчас незачем.

    Успешные (`ok`/`generic`) — навсегда. Заблокированные антиботом — только на
    `retry_blocked_after_days`: пробовать их снова надо, но не КАЖДЫЙ прогон.
    Живьём это выглядело так: девять капчей LinkedIn съедали 45% лимита выжимок
    в каждом скане, вечно, вытесняя живые вакансии."""
    out: set[tuple[str, str]] = set()
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=retry_blocked_after_days)).isoformat()
    for source, ext in keys:
        row = conn.execute(
            "SELECT status, fetched_at FROM detail WHERE source=? AND external_id=?",
            (source, str(ext))).fetchone()
        if not row:
            continue
        if row["status"] in ("ok", "generic", "generic-empty"):
            out.add((source, str(ext)))
        elif row["status"] == "blocked" and (row["fetched_at"] or "") >= cutoff:
            out.add((source, str(ext)))
    return out


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


def upsert_negotiation(conn, *, title: str, company: str | None, status: str,
                       source: str, url: str | None = None, event_at: str | None = None,
                       note: str | None = None,
                       key_extra: str | None = None) -> tuple[str, str | None]:
    """Пишет статус отклика. Возвращает (что_случилось, прежний_статус):
    ('new', None) — строки не было; ('changed', старый) — статус сменился;
    ('same', статус) — ничего нового. По этому возврату sync печатает дельту.

    `key_extra` — страховка от схлопывания: когда настоящие вакансия и компания
    из письма не вычленились, в ключ добавляется Message-ID. Без него письма
    с шаблонной темой («Работодатель не готов пригласить вас») от разных
    работодателей становились ОДНОЙ строкой, и 45% статусов исчезали молча."""
    tk, ck = _neg_key(title), _neg_key(company)
    if key_extra:
        tk = f"{tk}#{_neg_key(key_extra)}"
    ts = now()
    row = conn.execute(
        "SELECT status FROM negotiation WHERE title_key=? AND company_key=?",
        (tk, ck)).fetchone()
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
    conn.execute(
        "UPDATE negotiation SET status=?, source=?, url=COALESCE(?, url), "
        "event_at=COALESCE(?, event_at), note=COALESCE(?, note), updated_at=? "
        "WHERE title_key=? AND company_key=?",
        (status, source, url, event_at, note, ts, tk, ck))
    return "changed", old


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
