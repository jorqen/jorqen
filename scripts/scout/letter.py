"""Черновик сопроводительного письма — подбором фактов, а не пересказом их моделью.

Зачем модуль существует. Письмо в каждой карточке я писал заново: перечитывал
требования вакансии, вспоминал подходящие эпизоды из резюме, пересказывал их
своими словами. На восьмидесяти карточках это восемьдесят пересказов ОДНИХ И ТЕХ
ЖЕ фактов — работа, которую владелец справедливо назвал делом алгоритма: «всё,
что можно переложить на алгоритм, нужно переложить» (09.08.2026).

Что здесь машинного, а что остаётся модели:

* **машинное** — сопоставить требования вакансии с эпизодами резюме, выбрать
  три самых близких из РАЗНЫХ мест работы, собрать текст по канону, проследить
  за длиной, тире и подписью;
* **модель** — прочитать черновик, поправить связки под конкретную вакансию,
  добавить то, чего в резюме нет (вопрос про визу, оговорку про грейд), и
  решить, отправлять ли вообще.

Факты берутся ТОЛЬКО из `resume.yaml` (`highlights` мест работы) — то есть из
единственного источника правды о кандидате. Ничего не выдумывается: если
эпизода в резюме нет, его не будет и в письме.

🔴 Канон писем — `.claude/skills/jobs/references/letter-guide.md`, и он здесь не
пересказан, а исполнен: без длинного тире, без markdown, без «у меня этого нет»,
резюме одной ссылкой, подпись отдельной строкой. Проверяет результат тот же
`lint-letter`, что и письма, написанные руками.
"""

from __future__ import annotations

import hashlib
import re

# Термины, по которым эпизод резюме признаётся отвечающим требованию. Ключ —
# то, что ищем в требовании вакансии; значения — как это же понятие может быть
# записано в резюме. Список намеренно короткий: он ловит стек и инженерные
# понятия, а не пытается разобрать смысл фразы.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "go": ("go", "golang"),
    "python": ("python",),
    "php": ("php",),
    "postgres": ("postgres", "postgresql", "sql"),
    "mysql": ("mysql",),
    "clickhouse": ("clickhouse",),
    "redis": ("redis",),
    "kafka": ("kafka",),
    "rabbitmq": ("rabbitmq", "rabbit"),
    "mqtt": ("mqtt",),
    "grpc": ("grpc",),
    "rest": ("rest", "api"),
    "kubernetes": ("kubernetes", "k8s"),
    "docker": ("docker", "контейнер"),
    "aws": ("aws", "облак", "cloud"),
    "ci": ("ci/cd", "ci", "cd", "pipeline", "gitops", "argocd"),
    "observability": ("observability", "наблюдаемост", "prometheus", "grafana",
                      "loki", "trace", "трейс", "метрик", "monitoring", "мониторинг"),
    "highload": ("highload", "высоконагруж", "нагрузк", "rps", "латентн",
                 "latency", "p99", "throughput"),
    "microservices": ("микросервис", "microservice", "гексагональ", "границ сервис"),
    "payments": ("платеж", "платёж", "payment", "биллинг", "billing", "транзакц",
                 "финанс", "fintech", "финтех", "крипт", "crypto"),
    "distributed": ("распределён", "распределен", "distributed", "идемпотент",
                    "консистент", "согласован"),
    "security": ("mtls", "tls", "сертификат", "шифров", "безопасн", "security"),
    "websocket": ("websocket", "webrtc", "realtime", "реальном времени"),
    "team": ("code review", "ревью", "менторю", "координир", "владельц",
             "решения по", "архитектурн"),
}


def _word(needle: str, text: str) -> bool:
    """Есть ли слово в тексте. По ГРАНИЦАМ, а не подстрокой.

    🔴 Подстрочный поиск ловит чужое: «ci» сидит внутри «ACID», «go» — внутри
    «algorithm» и «Django». На требовании «Понимание принципов ACID» это
    добавляло вакансии несуществующее понятие CI/CD, и письмо собиралось из
    эпизодов про пайплайны (09.08.2026).

    Хвост слова при этом разрешён: в резюме «высоконагруженный», в вакансии
    «высоконагруженных» — это одно и то же, и требовать полного совпадения
    значит потерять половину русских формулировок.
    """
    return re.search(rf"(?<![\w/]){re.escape(needle)}", text) is not None


def _terms(text: str) -> set[str]:
    """Понятия, встреченные в тексте. Ключи `_SYNONYMS`, а не слова текста."""
    low = (text or "").lower()
    return {key for key, words in _SYNONYMS.items()
            if any(_word(w, low) for w in words)}


def _sentences(text: str) -> list[str]:
    """Эпизод режем на предложения: в письмо идёт факт, а не абзац резюме."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) > 30]


def highlights(resume: dict) -> list[dict]:
    """Эпизоды из резюме: [{текст, компания, понятия}]. Русская сторона.

    Берём `highlights` каждого места работы — они уже написаны как факты с
    цифрами, и переписывать их не нужно. Порядок сохраняем: в резюме сверху
    свежее, и в письме свежее тоже ценнее.
    """
    out: list[dict] = []
    # В resume.yaml раздел — это объект с заголовком и списком `items`, а не
    # сам список: заголовки там локализованы, и обойти это нельзя.
    exp = resume.get("experience") or {}
    jobs = exp.get("items") if isinstance(exp, dict) else exp
    for job in (jobs or []):
        if not isinstance(job, dict):
            continue
        # Название бывает и строкой, и локализованной парой {en, ru}: резюме
        # двуязычное, и часть полей переведена, а часть нет.
        raw = job.get("company") or job.get("organization") or ""
        if isinstance(raw, dict):
            raw = raw.get("ru") or raw.get("en") or ""
        company = str(raw).strip()
        for h in (job.get("highlights") or []):
            text = h.get("ru") if isinstance(h, dict) else h
            text = str(text or "").strip()
            if len(text) < 40:
                continue
            out.append({"text": text, "company": company, "terms": _terms(text)})
    return out


def pick(reqs: list[str], pool: list[dict], *, want: int = 3,
         seed: str = "") -> list[dict]:
    """Эпизоды под требования вакансии: самые близкие, из РАЗНЫХ мест работы.

    Разные места обязательны. Три факта об одном проекте читаются как «умеет
    ровно одно», а канон требует показать глубину, а не повтор; к тому же
    письмо, собранное всегда из одних и тех же строк, выглядит шаблоном — а
    шаблон в письме хуже отсутствия письма.

    `seed` (обычно адрес вакансии) разводит одинаковые по весу эпизоды: при
    равном совпадении выбор устойчив для одной вакансии и различается между
    вакансиями.
    """
    need = set()
    for r in reqs:
        need |= _terms(r)
    if not need:
        return []
    salt = hashlib.sha1((seed or "").encode()).hexdigest()

    # 🔴 Совпадения весят РАЗНО. «Go» стоит почти в каждом эпизоде резюме и
    # поэтому не различает их вовсе, а «highload» или «payments» — в единицах,
    # и именно они отвечают на вопрос «почему этот кандидат». Считая совпадения
    # штуками, генератор выбирал под требования «Go, highload, PostgreSQL, ACID»
    # фреймворк интеграционного тестирования: у него тоже был «go».
    freq: dict[str, int] = {}
    for h in pool:
        for t in h["terms"]:
            freq[t] = freq.get(t, 0) + 1

    scored = []
    for i, h in enumerate(pool):
        hit = need & h["terms"]
        if not hit:
            continue
        # Вклад понятия обратен его частоте. «Go» стоит в двадцати эпизодах из
        # тридцати пяти и не различает их вовсе; «highload» — в трёх, и именно
        # он отвечает на вопрос «почему этот кандидат». Линейная поправка тут не
        # работает: два частых совпадения складывались в больший вес, чем одно
        # редкое, и письмо про высокие нагрузки собиралось из эпизодов про CI.
        weight = sum(1.0 / max(freq.get(t, 1), 1) for t in hit)
        # Добавка устойчива для вакансии и мала: она разводит РАВНЫЕ эпизоды,
        # а не переставляет сильный ниже слабого.
        jitter = int(salt[i % len(salt)], 16) / 1000.0
        scored.append((weight + jitter, i, h))
    scored.sort(key=lambda x: (-x[0], x[1]))

    # Из разных мест работы — но не любой ценой: три факта об одном проекте
    # читаются как «умеет ровно одно», а заметно более слабый эпизод ради
    # разнообразия компаний хуже повтора. Порог: вдвое.
    # 🔴 Слабые эпизоды НЕ добираются ради количества. Канон прямо разрешает
    # письмо из двух абзацев: «если закрывать нечего — третий просто не
    # пишется». Пока добор шёл до `want` любой ценой, под требования про высокие
    # нагрузки в письмо попадал эпизод про фреймворк тестирования — он совпал
    # одним лишь словом «Go» (09.08.2026).
    best = scored[0][0]
    strong = [(s, i, h) for s, i, h in scored if s >= best * 0.4]

    out, used = [], set()
    for _score, _i, h in strong:
        if h["company"] in used and len(out) >= want - 1:
            continue
        out.append(h)
        used.add(h["company"])
        if len(out) >= want:
            break
    return out


def _lead(reqs: list[str], chosen: list[dict]) -> str:
    """Первая фраза: чем именно вакансия совпала. Без «меня зовут» и «о себе»."""
    need = set()
    for r in reqs:
        need |= _terms(r)
    top = (chosen[0]["terms"] & need) if chosen else set()
    labels = {
        "payments": "Платежи и транзакционная корректность",
        "highload": "Высоконагруженные сервисы",
        "observability": "Наблюдаемость и разбор проблем в проде",
        "distributed": "Распределённые системы и гарантии доставки",
        "microservices": "Микросервисы и границы между ними",
        "kafka": "Очереди и событийная обработка",
        "kubernetes": "Сервисы в Kubernetes",
        "security": "Защищённые каналы и работа с сертификатами",
        "go": "Бэкенд на Go",
    }
    for key in ("payments", "highload", "distributed", "observability",
                "microservices", "kafka", "security", "kubernetes", "go"):
        if key in top:
            return f"{labels[key]} — то, чем я занимаюсь сейчас, поэтому начну с этого."
    return "Начну с того, что ближе всего к вашей задаче."


def draft(*, title: str, reqs: list[str], resume: dict, url: str = "") -> str:
    """Черновик письма. Пусто — если сопоставлять не с чем.

    Возвращается ГОТОВЫЙ текст, а не заготовка с пропусками: заготовка с
    дырами — это тот же шаблон, только заполнять его пришлось бы модели.
    """
    pool = highlights(resume)
    chosen = pick(reqs, pool, seed=url)
    if not chosen:
        return ""
    role = (title or "").strip().rstrip(".")
    lines = [f"Здравствуйте! Откликаюсь на позицию {role}.", ""]
    lines += [_lead(reqs, chosen), ""]
    for h in chosen:
        # Из эпизода берём первое предложение: оно несёт факт, остальное —
        # уточнения, которые в письме превращаются в воду.
        body = _sentences(h["text"])
        text = body[0] if body else h["text"]
        where = f"В {h['company']}: " if h["company"] else ""
        lines += [f"{where}{text}", ""]
    if url:
        lines.append(f"Вакансия: {url} · резюме: https://jorqen.link")
    else:
        lines.append("Резюме: https://jorqen.link")
    lines += ["", "Матвей"]
    text = "\n".join(lines)
    # Канон: длинного тире в письме не бывает. Ставим его нигде, но эпизоды
    # приезжают из резюме, где оно встречается.
    return text.replace(" — ", ": ").replace("—", "")
