"""Сборка командной строки: все подкоманды scout и их флаги.

Выделено из `cli.py` 07.08.2026 переездом БЕЗ изменения поведения — файл дорос
до 2745 строк и стал самым большим в сборщике. Здесь только объявление
интерфейса; вся работа команд осталась в `cli.py`.

Импорт команд ЛЕНИВЫЙ, внутри `build_parser`, и это не стиль, а необходимость:
`cli` импортирует отсюда `build_parser`, и импорт `cli` на уровне модуля замкнул
бы цикл. Заодно так интерфейс собирается только когда его действительно строят.
"""

from __future__ import annotations

import argparse

# ──────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    # Импорт ЛЕНИВЫЙ и полный список — намеренно. Ленивый, потому что `cli`
    # импортирует `build_parser` отсюда, и обратный импорт на уровне модуля
    # замкнул бы цикл. Полный поимённо, а не звёздочкой: имя команды, забытое
    # здесь, обязано падать при сборке интерфейса, а не через NameError у
    # пользователя, набравшего ровно эту подкоманду.
    from . import cookiesrc, store  # noqa: PLC0415
    from .cli import (  # noqa: PLC0415
        DEFAULT_LIMIT, DEFAULT_MAX_ENRICH, RAW_SOURCES,
        cmd_ats, cmd_auth, cmd_brief, cmd_browse, cmd_budget, cmd_card,
        cmd_channel, cmd_check_links, cmd_collect, cmd_coverage, cmd_crawl, cmd_detail,
        cmd_doctor, cmd_dups, cmd_employer, cmd_funnel, cmd_tg_wave, cmd_enrich, cmd_habr_sync, cmd_hh_auth, cmd_hh_sync,
        cmd_mail_ingest, cmd_mail_read, cmd_mail_sync, cmd_mark, cmd_new,
        cmd_profile, cmd_raw, cmd_render, cmd_research, cmd_resolve, cmd_reveal,
        cmd_lint_cards, cmd_lint_letter, cmd_pending_reveals, cmd_scan, cmd_shortlist, cmd_status, cmd_wavedoc, cmd_tg, cmd_tg_auth, cmd_tg_dm,
        cmd_tg_fetch, cmd_tg_mirror, cmd_tg_reparse, cmd_tg_rollback, cmd_wave,
    )

    # --db принимается и до, и после подкоманды: писать `collect --db ...` естественнее,
    # чем `--db ... collect`, и спотыкаться об это на каждом запуске незачем.
    #
    # default=SUPPRESS здесь обязателен. Подпарсер разбирает свои аргументы в ОТДЕЛЬНОЕ
    # пространство имён и потом копирует его поверх основного — со ЗНАЧЕНИЯМИ ПО
    # УМОЛЧАНИЮ включительно. С обычным default `scout --db свой.db status` молча
    # затирался на `.scout/scout.db`, то есть команда работала не с той базой, что
    # просили (и `--db` до подкоманды не работал вовсе). SUPPRESS кладёт атрибут
    # только когда флаг реально передан, поэтому копировать поверх нечего.
    # Значение по умолчанию живёт ТОЛЬКО на верхнем парсере, и объявлять его надо
    # отдельным вызовом: `parents=[...]` копирует не описание аргумента, а сам объект
    # Action, поэтому `p.set_defaults(db=...)` поменял бы default и у подкоманд —
    # ровно то, что мы здесь чиним.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS,
                        help="путь к SQLite (по умолчанию .scout/scout.db)")

    p = argparse.ArgumentParser(prog="scout", description="Сборщик вакансий")
    p.add_argument("--db", default=store.DEFAULT_DB,
                   help="путь к SQLite (по умолчанию .scout/scout.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="обойти площадки", parents=[common])
    c.add_argument("--query", default="Golang")
    c.add_argument("--also", nargs="*", default=["Go разработчик", "Backend Go"],
                   help="дополнительные формулировки: одна не покрывает всё")
    c.add_argument("--days", type=int, default=3, help="окно по публикации-или-обновлению")
    c.add_argument("--area", default="113", help="113 — вся РФ, 1 — Москва, 2 — СПб")
    c.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"нижняя граница глубины обхода одной площадки (по "
                        f"умолчанию {DEFAULT_LIMIT}; 0 — сколько отдаёт площадка). "
                        f"Только ПОДНИМАЕТ проверенные потолки источников, "
                        f"обрезание всегда названо строкой в сводке")
    c.add_argument("--sources", help="через запятую; по умолчанию все")
    c.add_argument("--workers", type=int, default=8)
    c.add_argument("--ru-only", action="store_true", help="без зарубежных источников")
    c.add_argument("--ats-all", action="store_true",
                   help="нести все роли с ATS-досок, включая заведомо чужие профессии")
    c.add_argument("--no-browser", action="store_true",
                   help="не запускать площадки, которым нужен настоящий браузер "
                        "(glassdoor, levels); в покрытии они будут «ПРОПУЩЕН», "
                        "а не пропадут")
    c.add_argument("--no-store", action="store_true", help="не писать в базу (для облака)")
    c.add_argument("--raw-cache", choices=["write", "read", "off"], default="write",
                   help="кэш сырых ответов площадок: write (по умолчанию) — ходить "
                        "в сеть и складывать; read — брать из кэша (переразбор после "
                        "правки парсера без единого запроса к площадке); off — "
                        "не трогать вовсе. Кэш ограничен сверху: строки старше двух "
                        "дней чистятся на старте, читается только сегодняшний день")
    c.add_argument("--auth-wait", type=int, default=0, metavar="СЕК",
                   help="площадке нужен вход — открыть окно и ждать СЕК секунд, "
                        "НЕ останавливая обход остальных. Продлеваемые сессии "
                        "поднимаются без окна. 0 (по умолчанию) — не открывать")
    c.add_argument("--with-items", action="store_true", help="выгрузить вакансии в JSON")
    c.add_argument("--format", choices=["text", "json"], default="text")
    c.set_defaults(func=cmd_collect)

    n = sub.add_parser("new", help="дельта: что появилось с указанного момента", parents=[common])
    n.add_argument("--since", default="3d",
                   help="3d, 12h, 2026-07-20, ISO или auto (с прошлого прогона, но не уже суток)")
    n.add_argument("--by", choices=["seen", "published"], default="seen",
                   help="seen — чего не было в базе; published — по дате площадки")
    n.add_argument("--sources")
    n.add_argument("--limit", type=int, default=200,
                   help="0 — без ограничения; при усечении это ВСЕГДА сказано в шапке")
    n.add_argument("--strict", action="store_true",
                   help="ненулевой код возврата, если выдача обрезана по --limit")
    n.add_argument("--include-decided", action="store_true",
                   help="показать и то, по чему уже принято решение")
    n.add_argument("--format", choices=["text", "json"], default="text")
    n.set_defaults(func=cmd_new)

    v = sub.add_parser("coverage", help="кто отработал в последнем прогоне", parents=[common])
    v.set_defaults(func=cmd_coverage)

    r = sub.add_parser("resolve", help="куда ведёт кнопка «Откликнуться»", parents=[common])
    r.add_argument("url")
    r.add_argument("--no-follow", action="store_true", help="не проходить редиректы")
    r.add_argument("--format", choices=["text", "json"], default="text")
    r.set_defaults(func=cmd_resolve)

    from .crawl import DEADLINE, GAP, MAX_DEPTH, MAX_PAGES, PER_HOST  # noqa: PLC0415

    cr = sub.add_parser("crawl", help="обойти ВСЕ ссылки вакансии и построить карту: "
                                      "лучший контакт, живость, работодатель, "
                                      "непройденное",
                        parents=[common])
    cr.add_argument("urls", nargs="+", metavar="url",
                    help="пост t.me/<канал>/<id> — тогда старт по всем его "
                         "ссылкам; любой другой адрес — старт с него самого")
    cr.add_argument("--depth", type=int, default=MAX_DEPTH,
                    help=f"переходов от стартовой ссылки (по умолчанию {MAX_DEPTH}); "
                         f"всё, что глубже, названо в разделе «не пошли»")
    cr.add_argument("--max-pages", type=int, default=MAX_PAGES,
                    help=f"потолок страниц на весь обход (по умолчанию {MAX_PAGES})")
    cr.add_argument("--per-host", type=int, default=PER_HOST,
                    help=f"потолок страниц на один хост (по умолчанию {PER_HOST}): "
                         f"без него пагинация одного сайта съедает весь обход")
    cr.add_argument("--gap", type=float, default=GAP,
                    help=f"зазор между запросами К ОДНОМУ хосту, сек (по умолчанию "
                         f"{GAP}); меньше ставить незачем — rabota.ru закрыла нам "
                         f"TLS после ~25 запросов за 20 минут")
    cr.add_argument("--deadline", type=float, default=DEADLINE,
                    help=f"потолок времени на весь обход, сек (по умолчанию {DEADLINE:g})")
    cr.add_argument("--render", action="store_true",
                    help="раскрывать SPA-каркасы браузером 🌐 — дорого, поэтому "
                         "по флагу; без него каркас честно помечен в карте")
    cr.add_argument("--save", action="store_true",
                    help="записать найденные маршруты и живость в базу (вакансия "
                         "ищется по url) — их подхватят `brief`, `card` и `reveal`")
    cr.add_argument("--force", action="store_true",
                    help="с --save: переобойти, даже если факты в базе уже есть")
    cr.add_argument("--format", choices=["text", "json"], default="text")
    cr.set_defaults(func=cmd_crawl)

    tr = sub.add_parser("tg-rollback", help="вернуть чатам «непрочитано» и точки "
                                            "возобновления после неудачного прогона",
                        parents=[common])
    tr.add_argument("--file", default=".scout/tg/rollback-2026-08-04.json")
    tr.add_argument("--apply", action="store_true",
                    help="без него — только предпросмотр")
    tr.add_argument("--force", action="store_true",
                    help="отмотать и те чаты, у которых водяной знак уже есть "
                         "(по умолчанию они не трогаются, чтобы не потерять "
                         "прогресс, набранный после отката)")
    tr.set_defaults(func=cmd_tg_rollback)

    tp = sub.add_parser("tg-reparse", help="пересчитать телеграм-вакансии по "
                                           "сохранённому тексту поста (после правки "
                                           "парсера; сеть не трогается)",
                        parents=[common])
    tp.add_argument("--apply", action="store_true", help="без него — предпросмотр")
    tp.set_defaults(func=cmd_tg_reparse)

    tm = sub.add_parser("tg-mirror", help="переслать посты вакансий в СВОЙ приватный "
                                          "канал (ссылка переживёт удаление оригинала)",
                        parents=[common])
    tm.add_argument("--apply", action="store_true",
                    help="без него — предпросмотр, не пересылается НИЧЕГО")
    tm.add_argument("--limit", type=int, default=200, help="потолок за один заход")
    tm.add_argument("--since", help="окно по first_seen (3d, 2026-08-01)")
    tm.add_argument("--list-chats", action="store_true",
                    help="показать каналы, куда можно писать, и их id")
    tm.set_defaults(func=cmd_tg_mirror)

    cd = sub.add_parser("card", help="скелет карточки: деньги, флаги, таблица "
                                     "«требование → что у тебя» (фит и письмо "
                                     "пишет модель)", parents=[common])
    cd.add_argument("urls", nargs="+")
    # Блок «сколько просить» по умолчанию считается ТОЛЬКО по базе: карточки
    # собираются пачкой, и поход в сеть на каждую — это и минуты, и свежая
    # антибот-стена. С флагом справочники рынка спрашиваются живьём и ложатся
    # в суточный кэш, дальше пачка берёт их оттуда.
    cd.add_argument("--write", action="store_true",
                    help="разложить карточки по каталогам волны "
                         ".jobs/<дата>/companies/<слаг>/ вместо печати; "
                         "безымянный работодатель уходит в _hidden/")
    cd.add_argument("--no-crawl", action="store_true",
                    help="не обходить ссылки вакансии. По умолчанию обход ИДЁТ "
                         "(глубина 1, до 10 страниц на вакансию) и кладёт в карточку "
                         "проверенный контакт и живость; результат кэшируется, так "
                         "что пересборка карточки бесплатна")
    cd.add_argument("--date", help="дата волны (по умолчанию сегодня)")
    cd.add_argument("--force", action="store_true",
                    help="--write: перезаписать существующие файлы (по умолчанию "
                         "НЕ трогает: там уже может лежать фит и письмо)")
    cd.add_argument("--refresh", action="store_true",
                    help="--write: обновить ФАКТЫ существующей карточки (деньги, "
                         "маршруты, живость, флаги, требования), сохранив фит и "
                         "письмо. Ради этого и разделены разделы: факты стареют, "
                         "суждение — нет")
    cd.add_argument("--fetch-market", action="store_true",
                    help="спросить справочники зарплат (levels.fyi, dreamoffer) "
                         "живьём, а не брать из базы")
    cd.set_defaults(func=cmd_card)

    rs = sub.add_parser("research", help="кэш вердиктов ресёрча: раскрытый "
                                         "работодатель, живость, право на работу",
                        parents=[common])
    rs.add_argument("action", choices=["get", "set"])
    rs.add_argument("url")
    rs.add_argument("--employer", help="настоящий работодатель за заглушкой")
    rs.add_argument("--liveness", choices=["alive", "dead", "unknown"])
    rs.add_argument("--rtw", help="что сказано про право на работу")
    rs.add_argument("--verdict", help="итог: годится / нет и почему")
    rs.add_argument("--evidence", help="чем подтверждено")
    rs.set_defaults(func=cmd_research)

    bg = sub.add_parser("budget", help="СМЕТА волны до её начала: сколько строк, "
                                       "сколько токенов, влезает ли в потолок",
                        parents=[common])
    bg.add_argument("--days", type=int, default=3, help="окно дельты")
    bg.add_argument("--top", type=int, default=30, help="сколько строк в топе")
    bg.add_argument("--brief", type=int, default=None,
                    help="на скольких вакансиях считать досье (по умолчанию = --top)")
    bg.add_argument("--cards", type=int, default=None,
                    help="сколько карточек заложить (по умолчанию = --top)")
    bg.add_argument("--cap", type=int, default=500_000, help="потолок волны в токенах")
    bg.set_defaults(func=cmd_budget)

    wv = sub.add_parser("wave", help="ВЕСЬ конвейер одной командой: сбор → картина "
                                     "волны → что делать дальше", parents=[common])
    wv.add_argument("--days", type=int, default=3)
    wv.add_argument("--top", type=int, default=40, help="строк шорт-листа в картине")
    wv.add_argument("--verbose", action="store_true", help="показать вывод scan")
    for flag, kw in (("--no-telegram", {}), ("--no-mail", {}), ("--no-hh", {}),
                     ("--no-habr", {}), ("--no-browser", {})):
        wv.add_argument(flag, action="store_true", **kw)
    wv.add_argument("--limit", type=int, default=400)
    wv.add_argument("--max-enrich", type=int, default=400)
    wv.add_argument("--enrich-workers", type=int, default=4)
    wv.add_argument("--report-rows", type=int, default=0)
    wv.add_argument("--mail-days", type=int, default=30)
    # Те же флаги кук, что у scan: run_scan читает args.cookies_from и args.cache,
    # и без них команда падала бы AttributeError на первом же этапе.
    cookiesrc.add_cookie_args(wv)
    wv.set_defaults(func=cmd_wave)

    ch = sub.add_parser("channel", help="найти careers-страницу/ATS/HR-почту "
                                        "работодателя зондированием (без модели)",
                        parents=[common])
    ch.add_argument("company", nargs="?",
                    help="название компании; не нужно с --from-shortlist")
    ch.add_argument("--from-shortlist", dest="from_shortlist", action="store_true",
                    help="взять ВСЕ компании топа, у которых канала ещё нет "
                         "(список считает shortlist, а не человек глазами)")
    ch.add_argument("--days", type=int, default=3, help="--from-shortlist: окно волны")
    ch.add_argument("--top", type=int, default=30, help="--from-shortlist: глубина топа")
    ch.add_argument("--site", help="домен компании, если он известен")
    ch.add_argument("--timeout", type=int, default=12)
    ch.add_argument("--render", action="store_true",
                    help="добрать настоящим браузером, если stdlib увидел каркас SPA")
    ch.add_argument("--save", action="store_true", help="записать лучший в кэш")
    ch.set_defaults(func=cmd_channel)

    bf = sub.add_parser("brief", help="сводка по вакансиям для карточки: выжимка, "
                                      "стаж, история компании, канал найма — одним вызовом",
                        parents=[common])
    bf.add_argument("urls", nargs="+")
    bf.add_argument("--chars", type=int, default=900, help="длина описания в выжимке")
    bf.set_defaults(func=cmd_brief)

    sl = sub.add_parser("shortlist", help="дельта → строка на вакансию: дедуп, "
                                          "сверка с историей, разбор стажа (для карточек)",
                        parents=[common])
    sl.add_argument("--since", default="3d",
                    help="окно (3d, 2026-08-01, auto — с прошлого прогона, но не уже суток); пусто — вся база")
    sl.add_argument("--by", choices=["seen", "published"], default="seen")
    sl.add_argument("--sources", help="через запятую")
    sl.add_argument("--limit", type=int, default=0, help="0 — без ограничения")
    sl.add_argument("--format", choices=["table", "json"], default="table")
    sl.add_argument("--simhash-bits", type=int, default=3,
                    help="порог третьего слоя дедупа: макс. расстояние Хэмминга "
                         "между описаниями (3 из 64 ≈ 95%% совпадения). "
                         "Отрицательное — выключить слой")
    sl.set_defaults(func=cmd_shortlist)

    pr = sub.add_parser("profile", help="спрос рынка против резюме: пробелы, "
                                        "неподтверждённые заявки, балласт, воронка "
                                        "откликов — всё по своей базе, без сети",
                        parents=[common])
    pr.add_argument("--days", type=int, default=90,
                    help="окно по дате публикации/первой встречи (по умолчанию 90)")
    pr.add_argument("--all", action="store_true", help="вся база, без окна")
    pr.add_argument("--top", type=int, default=25, help="длина каждой таблицы")
    pr.add_argument("--min-companies", type=int, default=3,
                    help="сколько РАЗНЫХ компаний должны просить термин, чтобы он "
                         "считался спросом, а не разовым требованием")
    pr.set_defaults(func=cmd_profile)

    em = sub.add_parser("employer", help="кэш прямых каналов найма работодателей",
                        parents=[common])
    em.add_argument("action", choices=["list", "get", "set"], nargs="?", default="list")
    em.add_argument("company", nargs="?")
    em.add_argument("channel", nargs="?")
    em.add_argument("--kind", choices=["careers", "ats", "email", "telegram", "none"],
                    default=None)
    em.add_argument("--evidence", default=None,
                    help="чем подтверждено, что канал принадлежит этой компании")
    em.set_defaults(func=cmd_employer)

    rv = sub.add_parser("reveal", help="раскрыть прямой контакт hirehi (СПИСЫВАЕТ "
                                       "лимит раскрытий; разрешение пользователя "
                                       "30.07.2026)", parents=[common])
    rv.add_argument("urls", nargs="+", metavar="url",
                    help="страницы вакансий hirehi.ru (только они и принимаются)")
    rv.add_argument("--limit", type=int, default=5,
                    help="потолок раскрытий за прогон (по умолчанию 5): каждое "
                         "списывает лимит площадки; уже раскрытое берётся из базы "
                         "без клика")
    rv.add_argument("--from-browser", action="store_true",
                    help="взять сессию из браузера пользователя вместо .auth/hirehi.json "
                         "(какой браузер — задаётся общим --cookies-from). ВНИМАНИЕ: "
                         "ротация refresh-токена разлогинит живую вкладку hirehi")
    rv.add_argument("--dry-run", action="store_true",
                    help="показать ПЛАН: на что лимит спишется и почему, на что нет. "
                         "Ни одного клика, браузер не поднимается. Обход ссылок при "
                         "этом делается — он бесплатный, и его результат остаётся в базе")
    rv.add_argument("--no-crawl", action="store_true",
                    help="не обходить ссылки вакансии перед раскрытием. По умолчанию "
                         "обход ИДЁТ: он часто находит контакт даром (careers-страница, "
                         "ATS, почта найма), и тогда невосполнимый лимит не тратится")
    rv.set_defaults(func=cmd_reveal)

    w = sub.add_parser("raw", help="страница источника без парсера", parents=[common])
    w.add_argument("source", choices=list(RAW_SOURCES))
    w.add_argument("--query", default="Golang")
    w.add_argument("--days", type=int, default=3)
    w.add_argument("--area", default="113")
    w.add_argument("--out")
    w.add_argument("--render", action="store_true",
                   help="забрать страницу браузером (SPA: geekjob, hirehi, shadowhint)")
    w.set_defaults(func=cmd_raw)

    a = sub.add_parser("auth", help="сессии площадок в .auth/ (вход делает пользователь); "
                                    "refresh — продлить то, что продлевается без человека; "
                                    "import — забрать куки из браузеров в единый профиль; "
                                    "export — сессия строкой для СЕКРЕТОВ окружения "
                                    "облачной рутины (предъявительский доступ, никому "
                                    "не показывать)",
                       parents=[common])
    # careered продлевается настоящим браузером на постоянном профиле — здесь
    # нужен тот же выбор браузера, что у `render`/`browse`. Импорт локальный:
    # ядро обязано подниматься без playwright, а render тянет его лениво.
    from .render import add_browser_args  # noqa: PLC0415
    add_browser_args(a)
    a.add_argument("action", choices=["status", "login", "check", "refresh", "import",
                                      "secure", "push-browser", "export"],
                   nargs="?", default="status")
    a.add_argument("platform", nargs="?")
    a.add_argument("--all", action="store_true",
                   help="login: одно окно с вкладкой на каждую площадку")
    a.add_argument("--from", dest="from_", choices=["yandex", "chrome", "claude", "all"],
                   default="all", help="import: из какого браузера брать куки")
    a.add_argument("--domains", nargs="*",
                   help="import: домены площадок поимённо (по умолчанию встроенный "
                        "allowlist; `*` не поддерживается)")
    a.add_argument("--list", action="store_true",
                   help="import: показать домены и число кук, не записывая")
    a.add_argument("--from-browser", dest="from_browser", nargs="?", const="auto",
                   default=None, metavar="БРАУЗЕР",
                   help="refresh: забрать вход из ПОВСЕДНЕВНОГО браузера "
                        "(yandex|chrome|auto) вместо окна. У ротационных площадок "
                        "(hirehi) ротация уедет к нам и живая вкладка там "
                        "разлогинится — поэтому только явным флагом")
    a.add_argument("--force", action="store_true",
                   help="login: открыть окно, даже если проба считает вход живым "
                        "(она видит наличие куки, а не то, принимают ли её)")
    a.add_argument("--wait", type=int, default=0, metavar="СЕК",
                   help="login: ждать входа СЕК секунд, опрашивая страницу, вместо "
                        "Enter (нужно, когда команду запускают не из терминала)")
    a.set_defaults(func=cmd_auth)

    tw = sub.add_parser("tg-wave", parents=[common],
                        help="ОДИН пост о волне в свой приватный канал: сколько "
                             "новых вакансий + файл со всеми. По умолчанию "
                             "предпросмотр, отправка только с --apply")
    tw.add_argument("--days", type=int, default=3, help="окно волны")
    tw.add_argument("--top", type=int, default=10,
                    help="сколько строк показать в самом посте (остальное в файле)")
    tw.add_argument("--date", help="дата волны (по умолчанию сегодня)")
    tw.add_argument("--apply", action="store_true",
                    help="отправить (без него — только предпросмотр)")
    tw.add_argument("--force", action="store_true",
                    help="отправить повторно волну, которая уже уходила в канал")
    tw.add_argument("--via", choices=("auto", "bot", "user"), default="auto",
                    help="чем слать: bot — через TG_BOT_TOKEN (так работает "
                         "облачная рутина), user — от своего аккаунта через "
                         "telethon, auto — ботом, если есть токен")
    tw.set_defaults(func=cmd_tg_wave)

    fn = sub.add_parser("funnel", parents=[common],
                        help="что происходит с откликами: сколько ушло, сколько "
                             "ответили, за сколько дней, и какие молчат слишком долго")
    fn.add_argument("--tail-days", type=int, default=None, metavar="ДН",
                    help="сколько дней молчания считать хвостом (по умолчанию 14)")
    fn.set_defaults(func=cmd_funnel)

    tl = sub.add_parser("tails", parents=[common],
                        help="только хвосты: отклики, молчащие дольше срока. "
                             "Возвращает 1, когда есть что разобрать — годится в рутину")
    tl.add_argument("--tail-days", type=int, default=None, metavar="ДН",
                    help="порог молчания в днях (по умолчанию 14)")
    tl.set_defaults(func=cmd_funnel, tails_only=True)

    dp = sub.add_parser("dups", parents=[common],
                        help="СОСТАВ схлопнутых групп: что именно склеил дедуп "
                             "и где внутри группы разошлись компания, грейд или "
                             "роль. Возвращает 1, когда есть что посмотреть")
    dp.add_argument("--since", default="3d",
                    help="окно: дата, «3d» или auto. Шире — «30d»: аудит по всей "
                         "базе честнее, но идёт минуты")
    dp.add_argument("--by", choices=("seen", "published"), default="seen",
                    help="по какой дате брать окно")
    dp.add_argument("--sample", type=int, default=12,
                    help="сколько подозрительных групп распечатать составом")
    dp.add_argument("--simhash-bits", type=int, default=None, metavar="N",
                    help="порог слоя описаний: выше — разводит, -1 выключает")
    dp.set_defaults(func=cmd_dups)

    doc = sub.add_parser("doctor", parents=[common],
                         help="что на этой машине сломано: окружение, база, "
                              "профиль браузера, ключи, сессии, права .auth/. "
                              "В сеть НЕ ходит — живость сессий это `auth status`")
    doc.set_defaults(func=cmd_doctor)

    lc = sub.add_parser("lint-cards", parents=[common],
                        help="формальная проверка карточек волны: есть ли раздел "
                             "«Отклик», не осталось ли заглушек и предупреждений")
    lc.add_argument("path", nargs="?", default=".jobs",
                    help="каталог волны или один файл (по умолчанию .jobs)")
    lc.set_defaults(func=cmd_lint_cards)

    ll = sub.add_parser("lint-letter", parents=[common],
                        help="проверить сопроводительное по формальной части канона: "
                             "тире, слова-метки генератора, разметка, длина, переносы")
    ll.add_argument("file", nargs="?", default="-",
                    help="файл с письмом; без аргумента — читает stdin")
    ll.set_defaults(func=cmd_lint_letter)

    wd = sub.add_parser("wavedoc", parents=[common],
                        help="скелет главного документа волны из базы: таблица "
                             "отобранного, покрытие, отсев. Суждение НЕ пишет")
    wd.add_argument("--days", type=int, default=3, help="окно волны")
    wd.add_argument("--top", type=int, default=30, help="строк в таблице отобранного")
    wd.add_argument("--date", help="дата волны (по умолчанию сегодня)")
    wd.add_argument("--write", action="store_true",
                    help="записать в .jobs/<дата>.md вместо печати")
    wd.add_argument("--force", action="store_true",
                    help="перезаписать существующий файл (по умолчанию НЕ трогает: "
                         "там уже может лежать дописанное суждение)")
    wd.set_defaults(func=cmd_wavedoc)

    pr = sub.add_parser("pending-reveals", parents=[common],
                        help="долги по раскрытию контактов: где лимит кончился "
                             "и надо вернуться")
    pr.add_argument("--resolve", action="store_true",
                    help="попытаться добыть контакт БЕЗ лимита: обход дубля "
                         "вакансии на другой площадке и зонд сайта компании")
    pr.set_defaults(func=cmd_pending_reveals)

    m = sub.add_parser("mark", help="зафиксировать решение по вакансии", parents=[common])
    m.add_argument("source")
    m.add_argument("id")
    m.add_argument("--state", required=True,
                   choices=["applied", "rejected", "skipped", "skip", "shortlist", "interview"],
                   help="skip — синоним skipped")
    m.add_argument("--note")
    m.set_defaults(func=cmd_mark)

    st = sub.add_parser("status", help="поиск по базе (title+company) с показом решений",
                        parents=[common])
    st.add_argument("--query", required=True, help="подстрока названия или компании")
    st.add_argument("--limit", type=int, default=50)
    st.set_defaults(func=cmd_status)

    # ── ats: порт бывших scripts/ats/*.sh ────────────────────────────────────
    at = sub.add_parser("ats", help="доски работодателей: check / jobs / sniff",
                        parents=[common])
    ats_sub = at.add_subparsers(dest="ats_cmd", required=True)

    ac = ats_sub.add_parser("check", help="живость токена на всех ATS сразу, с названием компании")
    ac.add_argument("tokens", nargs="+")
    ac.set_defaults(func=cmd_ats)

    aj = ats_sub.add_parser("jobs", help="вакансии доски со структурным матчем локаций "
                                         "(secondaryLocations, offices, заголовок)")
    aj.add_argument("board", help="<ats>:<token>, например greenhouse:gitlab или ashby:ruby-labs")
    aj.add_argument("--country", help="код страны (TR, RU, CY, …) или свободный текст")
    aj.add_argument("--grep", help="регулярка по заголовку роли")
    aj.add_argument("--locations", action="store_true",
                    help="показать распределение локаций доски")
    aj.set_defaults(func=cmd_ats)

    an = ats_sub.add_parser("sniff", help="вычислить ATS по careers-странице компании")
    an.add_argument("urls", nargs="+")
    an.set_defaults(func=cmd_ats)

    # ── detail / enrich ──────────────────────────────────────────────────────
    d = sub.add_parser("detail", help="нормализованная выжимка страницы вакансии",
                       parents=[common])
    d.add_argument("url")
    d.add_argument("--json", dest="format", action="store_const", const="json",
                   default="text")
    d.add_argument("--render", action="store_true",
                   help="брать HTML из браузера (SPA); только для generic-случаев — "
                        "для hh/habr/ATS это будет сказано в notes")
    cookiesrc.add_cookie_args(d)
    d.set_defaults(func=cmd_detail)

    e = sub.add_parser("enrich", help="выжимки по дельте из базы, с хранением "
                                      "(второй раз не качает)", parents=[common])
    e.add_argument("--since", default="3d",
                   help="окно дельты: 3d, 12h, ISO, auto")
    e.add_argument("--source", help="через запятую: hh,habr,…")
    e.add_argument("--max", type=int, default=DEFAULT_MAX_ENRICH,
                   help=f"потолок выжимок за прогон (по умолчанию "
                        f"{DEFAULT_MAX_ENRICH}; 0 — без потолка). Отрезанное "
                        f"не теряется: выжимки кэшируются в базе")
    e.add_argument("--workers", type=int, default=8)
    e.add_argument("--refresh", action="store_true", help="перекачать уже обогащённые")
    e.add_argument("--include-decided", action="store_true")
    e.set_defaults(func=cmd_enrich)

    # ── tg / check-links ─────────────────────────────────────────────────────
    t = sub.add_parser("tg", help="разбор телеграм-дампа: счётчики, теги, кандидаты",
                       parents=[common])
    t.add_argument("file")
    t.add_argument("--since", help="ISO-дата: сообщения старше — только в счётчик")
    t.add_argument("--full", action="store_true", help="тела целиком, а не первые ~15 строк")
    t.add_argument("--save", action="store_true",
                   help="разобрать дамп в вакансии и положить в базу "
                        "(переразбор старых дампов; водяной знак не двигает)")
    t.set_defaults(func=cmd_tg)

    cl = sub.add_parser("check-links", help="предфлайт живости ATS-ссылок "
                                            "(Ashby ротирует UUID!)", parents=[common])
    cl.add_argument("urls", nargs="+")
    cl.set_defaults(func=cmd_check_links)

    # ── tg-auth / tg-fetch: Telegram-архив без MCP ───────────────────────────
    ta = sub.add_parser("tg-auth", help="сессия Telegram (Telethon; вход делает "
                                        "пользователь в терминале)", parents=[common])
    ta.add_argument("action", choices=["login", "status"], nargs="?", default="status")
    ta.set_defaults(func=cmd_tg_auth)

    tf = sub.add_parser("tg-fetch", help="выкачать непрочитанное из архива Telegram "
                                         "и прогнать парсер tg", parents=[common])
    tf.add_argument("--archive-only", action="store_true", default=True,
                    help="только архивные диалоги (так по умолчанию)")
    tf.add_argument("--all-folders", dest="archive_only", action="store_false",
                    help="обойти и основную папку тоже")
    tf.add_argument("--no-mark", action="store_true", help="не отмечать прочитанным")
    tf.add_argument("--out", help="куда класть дампы (по умолчанию .scout/tg/<дата>)")
    tf.set_defaults(func=cmd_tg_fetch)

    td = sub.add_parser("tg-dm", help="личная переписка с человеком: последние N "
                                      "сообщений (только чтение, БЕЗ отметки "
                                      "прочитанным)", parents=[common])
    td.add_argument("peer", help="@ник, ник или числовой id собеседника")
    td.add_argument("--limit", type=int, default=50,
                    help="сколько последних сообщений показать (по умолчанию 50)")
    td.set_defaults(func=cmd_tg_dm)

    # ── render / hh-sync / mail-sync / scan ──────────────────────────────────
    rn = sub.add_parser("render", help="страница через браузер: SPA и авторизованные "
                                       "площадки (exness, wantapply, ecom.tech)",
                        parents=[common])
    rn.add_argument("url")
    rn.add_argument("--session", help="оверрайд: отдельная сессия площадки из .auth/ "
                                      "(hh, shadowhint, …); по умолчанию единый профиль")
    rn.add_argument("--session-file", help="оверрайд: конкретный storage_state-файл "
                                           "(например .auth/browser.json)")
    rn.add_argument("--wait", type=float, default=3.0,
                    help="секунды ожидания после networkidle (SPA дорисовываются)")
    rn.add_argument("--html", dest="mode", action="store_const", const="html",
                    default="text", help="сырой HTML вместо чистого текста")
    rn.add_argument("--text", dest="mode", action="store_const", const="text",
                    help="чистый текст (по умолчанию)")
    cookiesrc.add_cookie_args(rn)
    rn.set_defaults(func=cmd_render)

    br = sub.add_parser("browse", help="видимое окно с куками пользователя для "
                                       "ручного дебага (только чтение)", parents=[common])
    br.add_argument("url")
    br.add_argument("--keep", action="store_true",
                    help="держать окно открытым до Enter")
    br.add_argument("--wait", type=float, default=3.0)
    cookiesrc.add_cookie_args(br)
    br.set_defaults(func=cmd_browse)

    ha = sub.add_parser("hh-auth", help="пользовательский токен API hh "
                                        "(один раз; дальше обновляется сам)",
                        parents=[common])
    ha.add_argument("action", choices=["login", "status"], nargs="?",
                    default="status")
    ha.add_argument("--visible", action="store_true",
                    help="видимое окно: нужно, если сессии hh нет и требуется "
                         "вход — логин и капчу проходит человек, не скрипт")
    ha.add_argument("--no-confirm", action="store_true",
                    help="не жать «Proceed» на экране согласия самому "
                         "(с --visible: нажмёшь руками)")
    cookiesrc.add_cookie_args(ha)
    ha.set_defaults(func=cmd_hh_auth)

    hs = sub.add_parser("hh-sync", help="статусы откликов из кабинета hh "
                                        "(отказы/приглашения) → таблица negotiation",
                        parents=[common])
    hs.add_argument("--max-pages", type=int, default=25)
    cookiesrc.add_cookie_args(hs)
    hs.set_defaults(func=cmd_hh_sync)

    hb = sub.add_parser("habr-sync", help="статусы откликов из кабинета Хабр Карьеры "
                                          "(отказы/просмотры) → таблица negotiation",
                        parents=[common])
    hb.add_argument("--max-pages", type=int, default=40)
    cookiesrc.add_cookie_args(hb)
    hb.set_defaults(func=cmd_habr_sync)

    ms = sub.add_parser("mail-sync", help="статусы откликов из почты "
                                          "(IMAP, только чтение)", parents=[common])
    ms.add_argument("--days", type=int, default=30, help="окно поиска писем")
    ms.set_defaults(func=cmd_mail_sync)

    mr = sub.add_parser("mail-read", help="полный текст писем по подстроке: "
                                          "тема/отправитель/тело (IMAP, только чтение)",
                        parents=[common])
    mr.add_argument("query", help="подстрока, регистр не важен")
    mr.add_argument("--days", type=int, default=30, help="окно поиска писем")
    mr.add_argument("--limit", type=int, default=10, help="сколько писем показать")
    mr.set_defaults(func=cmd_mail_read)

    mi = sub.add_parser("mail-ingest", help="принять JSON-выгрузку писем (Gmail MCP) → "
                                            "классификатор → таблица статусов",
                        parents=[common])
    mi.add_argument("file", help="путь к JSON-файлу или '-' для stdin")
    mi.set_defaults(func=cmd_mail_ingest)

    sc = sub.add_parser("scan", help="весь конвейер одной командой: collect → tg → "
                                     "enrich → hh-sync → habr-sync → mail-sync → "
                                     "сводный отчёт", parents=[common])
    sc.add_argument("--days", type=int, default=3, help="окно свежести площадок и дельты")
    sc.add_argument("--no-telegram", action="store_true")
    sc.add_argument("--no-mail", action="store_true")
    sc.add_argument("--no-hh", action="store_true")
    sc.add_argument("--no-habr", action="store_true")
    sc.add_argument("--no-browser", action="store_true",
                    help="без площадок, которым нужен браузер; в покрытии они "
                         "останутся строкой «ПРОПУЩЕН»")
    sc.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"нижняя граница глубины обхода площадки (по умолчанию "
                         f"{DEFAULT_LIMIT}; 0 — сколько отдаёт площадка)")
    sc.add_argument("--max-enrich", type=int, default=DEFAULT_MAX_ENRICH,
                    help=f"потолок выжимок за прогон (по умолчанию "
                         f"{DEFAULT_MAX_ENRICH}; 0 — без потолка). Кэшируются: "
                         f"отрезанное достанется следующему прогону")
    sc.add_argument("--enrich-workers", type=int, default=8,
                    help="потоки выжимок; пауза между запросами к ОДНОМУ хосту "
                         "держится независимо от их числа")
    sc.add_argument("--report-rows", type=int, default=0,
                    help="строк в таблице дельты отчёта (0 — вся дельта, так "
                         "по умолчанию); при обрезании это сказано в отчёте")
    sc.add_argument("--mail-days", type=int, default=30,
                    help="окно почты (шире окна площадок: ответы приходят с лагом)")
    cookiesrc.add_cookie_args(sc)
    sc.set_defaults(func=cmd_scan)

    return p
