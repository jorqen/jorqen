"""SQLite-состояние сборщика: что видели, когда и что с этим сделали.

Зачем вообще состояние. У сайтов нет «непрочитанного» — каждый заход отдаёт всю выдачу
заново, и без памяти скилл разбирает одни и те же вакансии по второму кругу, тратя
на это контекст. База даёт то, чего не даёт ни одна площадка: **дельту**.

База лежит в `.scout/` и в `.gitignore` — репозиторий публичный.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .model import Vacancy

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


@contextmanager
def connect(path: str = DEFAULT_DB):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
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
    """Возвращает (новых, обновлённых). Новизна считается по (source, external_id)."""
    ts = now()
    new = updated = 0
    for v in vacancies:
        row = v.to_row()
        exists = conn.execute(
            "SELECT 1 FROM vacancy WHERE source=? AND external_id=?",
            (v.source, v.external_id),
        ).fetchone()
        if exists:
            conn.execute(
                "UPDATE vacancy SET url=?, title=?, company=?, salary_from=?, salary_to=?, "
                "currency=?, salary_gross=?, location=?, remote=?, published_at=?, updated_at=?, "
                "employer_url=COALESCE(?, employer_url), tags=?, description=?, raw=?, "
                "dup_key=?, last_seen=? WHERE source=? AND external_id=?",
                (row["url"], row["title"], row["company"], row["salary_from"], row["salary_to"],
                 row["currency"], row["salary_gross"], row["location"], row["remote"],
                 row["published_at"], row["updated_at"], row["employer_url"], row["tags"],
                 row["description"], row["raw"], row["dup_key"], ts, v.source, v.external_id),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO vacancy (source, external_id, url, title, company, salary_from, "
                "salary_to, currency, salary_gross, location, remote, published_at, updated_at, "
                "employer_url, tags, description, raw, dup_key, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (v.source, v.external_id, row["url"], row["title"], row["company"],
                 row["salary_from"], row["salary_to"], row["currency"], row["salary_gross"],
                 row["location"], row["remote"], row["published_at"], row["updated_at"],
                 row["employer_url"], row["tags"], row["description"], row["raw"],
                 row["dup_key"], ts, ts),
            )
            new += 1
    return new, updated


def query(conn, *, since: str | None = None, first_seen_since: str | None = None,
          sources: list[str] | None = None, exclude_decided: bool = True,
          limit: int | None = None) -> list[dict]:
    """Выборка для отчёта.

    `since` — по дате публикации-ИЛИ-обновления (решение пользователя от 25.07.2026:
    достаточно любой из двух — поднятая работодателем вакансия так же свежа, как новая).
    `first_seen_since` — «чего не было в прошлый заход», это и есть дельта.
    """
    sql = ["SELECT v.*, d.state AS decision, d.note AS decision_note "
           "FROM vacancy v LEFT JOIN decision d "
           "ON d.source=v.source AND d.external_id=v.external_id WHERE 1=1"]
    params: list = []
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
    sql.append("ORDER BY COALESCE(v.published_at, v.first_seen) DESC")
    if limit:
        sql.append("LIMIT ?")
        params.append(limit)
    return [dict(r) for r in conn.execute(" ".join(sql), params).fetchall()]


def decide(conn, source: str, external_id: str, state: str, note: str | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO decision (source, external_id, state, note, updated_at) "
        "VALUES (?,?,?,?,?)",
        (source, str(external_id), state, note, now()),
    )


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
