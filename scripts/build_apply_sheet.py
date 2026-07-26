#!/usr/bin/env python3
"""Собирает единый рабочий лист для откликов из карточек скана.

Зачем скрипт, а не копипаст: письма живут в карточках, и если продублировать
их руками, две копии разойдутся — отправлено будет не то, что правил.
Здесь письма **вытягиваются из карточек программно**, поэтому лист всегда
соответствует источнику. Поправил письмо в карточке — перегенерируй лист.

Запуск:  .venv/bin/python scripts/build_apply_sheet.py 2026-07-25
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN = sys.argv[1] if len(sys.argv) > 1 else "2026-07-25"
SRC = ROOT / ".jobs" / SCAN
OUT = SRC / "00-KUDA-OTKLIKATSYA.md"

# Одна вакансия на компанию — самая релевантная. Порядок = приоритет отклика.
# card=None → письма пока нет, только контакт.
PRIMARY = [
    ("Клируэй Текнолоджис", "Team Lead Go (PKI, TLS/ГОСТ)", "450 000–600 000 ₽ + бонусы до 6 окладов",
     "Москва: офис / гибрид / удалёнка на выбор", "https://hh.ru/vacancy/135501327",
     "01-clearway-teamlead-go.md", "Сертификаты для K8s/Istio — Sber Tech и АТОМ дословно"),
    ("Persona / Gradient", "Senior Backend Engineer (Golang)", "от 450 000 ₽ на руки",
     "Удалёнка", "https://hh.ru/vacancy/135399543",
     "17-persona-gradient-450k.md", "Писать В СУЩЕСТВУЮЩИЙ ЧАТ — ты уже откликался и напоминал 15.07"),
    ("Canonical", "Senior SWE — Python/Golang — Kubernetes", "не указана (политика компании)",
     "Home based EMEA", "https://job-boards.greenhouse.io/canonical/jobs/7774649",
     "24-canonical-emea-go.md", "Строят K8s-операторы. ⚠️ Эссе писать самому — в форме запрет на AI"),
    ("МТС / MWS Cloud Platform", "Go-разработчик", "350 000–600 000 ₽ (из поста, не подтверждено)",
     "Удалёнка / Москва", "https://job.mts.ru/vacancy/487966481431137071",
     "13-mts-mws-cloud-platform.md", "В письме указана команда AI automation — иначе попадёшь в Network, где отказ"),
    ("Avito", "Старший бэкенд-разработчик (Quality)", "от 400 000 ₽ на руки",
     "Удалёнка РФ / Москва / Н. Новгород", "https://career.avito.com/vacancies/razrabotka/19689/",
     "02-avito-quality.md", "Гэп: LLM в проде, RAG, token economics"),
    ("Яндекс Финтех", "Go-разработчик в платёжный бэкенд", "не раскрыта",
     "Удалёнка РФ / Мск / СПб / Белград", "https://getmatch.ru/vacancies/35366-go-razrabotchik-v-komandu-platiozhnogo-bekenda",
     "04-yandex-fintech-payments.md", "Лучший фит по содержанию за весь скан"),
    ("VK", "Старший Go-разработчик в инфраструктуру AdBlogger", "от 300 000 ₽ на руки",
     "Удалёнка РФ / Москва", "https://team.vk.company/vacancy/45364/",
     "03-vk-adblogger-infra.md", "Гэпов нет вообще"),
    ("Сбер", "Go Developer (Cloud Infra)", "не раскрыта",
     "Гибрид, Москва", "https://hirehi.ru/development/go-developer-67961",
     "05-sber-gigachat-go.md", "Операторы и контроллеры K8s. Ты ex-Sber. ⚠️ Искать на rabota.sber.ru"),
    ("MAGNIT TECH", "Senior Golang-разработчик", "от 300 000 ₽ на руки",
     "Удалёнка РФ / Мск / СПб / Краснодар", "https://getmatch.ru/vacancies/24686-senior-golang-razrabotchik-platformennaia-komanda",
     "08-magnit-tech-golang.md", "Ты строил ценовую платформу для «Бристоля». Спросить, что за команда"),
    ("Exness", "Platform Engineer (Golang)", "не указана + налоговая льгота до 50%",
     "Лимассол, релокация с семьёй", "https://job-boards.eu.greenhouse.io/internalhiring/jobs/4932377101",
     "12-exness-platform-golang.md", "K8s on-prem, Helm, ArgoCD, Kafka, Prometheus"),
    ("adjoe (Bertelsmann)", "Senior Go Backend (Supply Integrations)", "не указана",
     "Гамбург, гибрид; виза + релокационный бонус", "https://adjoe.io/careers/open-positions/edd0b04a-2b9f-46b4-8fe8-46cf61bbb58d/",
     "16-adjoe-hamburg-go.md", "Единственная, где проходишь ОБА порога: 5 лет backend и 3 года Go"),
    ("EPAM", "Senior Golang Developer (плагины к HashiCorp Vault)", "не указана",
     "Гибрид София; релокация для находящихся в Турции", "https://careers.epam.com/en/vacancy/senior-golang-developer-relocation-to-bulgaria-bltjvdr8n25quwiwu2g_en",
     "22-epam-bulgaria-vault-go.md", "Vault = секреты и сертификаты, твой mTLS-слой. Порог 5+ лет Go совпадает"),
    ("Sezzle", "Senior Site Reliability Engineer", "$5 000–9 500/мес gross USD",
     "Türkiye, Remote", "https://job-boards.greenhouse.io/sezzle/jobs/7612268003",
     "23-sezzle-turkey-usd.md", "⚠️ Проверить в форме вопрос про work authorization. Первый этап — тест Wonderlic"),
    ("Andersen → inDrive", "Go Developer (Senior)", "€2 375–3 875/мес + до $1 000/мес бонусом",
     "Worldwide remote", "https://people-andersenlab.com/ru/vacancy/2509967",
     "21-andersen-indrive-go.md", "Сначала спросить: ограничение по стране — про проживание или гражданство?"),
    ("Alpheya", "Senior Backend Developer (LLM agent platform)", "не раскрыта",
     "Полная удалёнка; контракт 6 мес", "https://apply.workable.com/alpheya/",
     "15-alpheya-llm-agent-platform.md", "Единственная, где Go вокруг LLM — ядро роли"),
    ("Wheely", "Senior Backend Engineer, Maps", "не указана",
     "Никосия, релокация + виза", "https://wantapply.com/",
     "18-wheely-nicosia-go-shop.md", "«We are a Go shop». Гэп: год работы с геоданными"),
    ("Kaspersky", "Lead Go Developer (OSMP)", "не указана",
     "Москва + релокационная поддержка", "https://careers.kaspersky.ru/vacancy/25712",
     "14-kaspersky-osmp-lead-go.md", "Требования совпадают дословно. Просят 6 лет Go при 4,5"),
    ("CTRL2GO Solutions", "TeamLead группы backend-разработки (Golang)", "не указана",
     "Мск / Казань / удалёнка", "https://hh.ru/vacancy/135556780",
     "10-ctrl2go-teamlead.md", "Телеметрия в реальном времени — твой АТОМ. Гэп: математические методы"),
    ("Mayflower", "Go/PHP Backend Developer", "~5 000–6 000 € (не подтверждено)",
     "Лимассол; виза на 3 года, жильё, билеты семье", "https://mayflower.recruitee.com/o/gophp-backend-developer-1",
     "09-mayflower-limassol.md", "🔴 PHP как основной язык — настоящий гэп. Country в форме = Russia"),
    ("Civo", "Go Developer — AI & ML", "не указана",
     "Fully remote, без страновых ограничений, 4-дневка", "https://careers.civo.com/jobs/4608451-go-developer-ai-ml",
     "25-civo-go-ai-ml.md", "Kubernetes Operators в требованиях — твой K8s-ресурс дословно. Гэп: деплой ML"),
    ("Mirantis", "Software Engineer, Infrastructure (Go)", "не указана",
     "Удалённо по ЕС", "https://jobs.smartrecruiters.com/Mirantis/744000139667439",
     None, "K8s controller/reconciler, Cluster API, Temporal"),
    ("Workato", "Senior Backend Engineer (GO, API Gateways)", "не указана",
     "Тбилиси / Никосия / София / Барселона / Берлин", "https://www.workato.com/careers?gh_jid=8574597002",
     None, "Go + TLS/mTLS handshake, TCP lifecycle, HTTP/2-3 — сетевой слой твоего брокера"),
    ("poolside", "Member of Engineering (Compute)", "не указана",
     "Remote EMEA", "https://jobs.ashbyhq.com/poolside/1f2a733a-a3fe-48fe-bf61-1425eddb30f6",
     None, "Go + GPU-планирование, inference serving. Go-инфраструктура вокруг LLM"),
    ("Synthflow AI", "Senior SWE (Go) — Real-Time Engine", "не указана",
     "Global Remote, ограничений нет", "https://jobs.ashbyhq.com/synthflow/7fd58cad-4ea0-40ad-b56a-d8605aab2f5c",
     None, "WebRTC и RTP из твоего пет-проекта здесь в требованиях"),
    ("Cloud.ru", "Golang Developer_Kubernetes (DBaaS)", "не указана (оценка 307–441K)",
     "Москва", "https://career.habr.com/vacancies/1000167180",
     None, "Kubebuilder, проектирование CRD, написание операторов, DDD, ADR"),
    ("БЮРО 1440", "Golang-разработчик", "не указана",
     "Удалёнка + Москва", "https://career.habr.com/vacancies/1000166881",
     None, "Go всего от 2 лет при 5 годах общего — самый мягкий порог среди сильных"),
    ("Ozon", "Golang-разработчик (платформа алертинга и инцидентов)", "не указана",
     "Гибрид, Москва", "https://hirehi.ru/development/golang-razrabotchik-67174",
     None, "HA, балансировщики, rate-limiters, PromQL, Thanos. Отказ был по WMS — это другая команда"),
    ("Vonage", "Senior Software Engineer (Go)", "не указана",
     "Польша", "https://job-boards.greenhouse.io/vonage",
     None, "«Сильный Go ЛИБО глубокий JVM с переходом на Go» — твоё Java-полугодие засчитывается"),
    ("DATATRONiQ", "(Senior) Developer Backend (Golang)", "€70 000–80 000/год",
     "Берлин, гибрид", "https://join.com/companies/datatroniq/16357609-senior-developer-backend-golang",
     None, "Industrial IoT + AI. Вилка открыта — редкость для зарубежа"),
]

# Другие вакансии тех же компаний — выбор остаётся за пользователем.
ALTERNATIVES = [
    ("Клируэй Текнолоджис", [
        ("Технический лидер команды разработки (Go)", "450 000–600 000 ₽", "https://hh.ru/vacancy/134645258"),
        ("Руководитель команды разработки (Go)", "450 000–600 000 ₽", "https://hh.ru/vacancy/135457389"),
        ("Solution Architect", "350 000–450 000 ₽", "https://hh.ru/vacancy/135031875"),
        ("Senior DevOps engineer", "300 000–420 000 ₽", "https://hh.ru/vacancy/135467382"),
        ("Golang разработчик — НЕ лидерская, запасной ход", "250 000–350 000 ₽", "https://hh.ru/vacancy/135228801"),
    ]),
    ("Canonical (~30 Go-ролей всего)", [
        ("Golang System SWE — Containers / Virtualisation", "—", "https://job-boards.greenhouse.io/canonical/jobs/4960407"),
        ("Golang SWE, Commercial Systems — мягкий порог по грейду", "—", "https://job-boards.greenhouse.io/canonical/jobs/4827747"),
        ("Lead Golang SWE, Commercial Systems — закрывается 20.08", "—", "https://job-boards.greenhouse.io/canonical/jobs/5692058"),
        ("Staff SWE, Identity Management (Go)", "—", "https://job-boards.greenhouse.io/canonical/jobs/3880952"),
        ("C, Golang SWE — dqlite (Raft/SQLite)", "—", "https://job-boards.greenhouse.io/canonical/jobs/4124053"),
    ]),
    ("Sezzle", [
        ("Software Engineer II (Turkey) — проходишь с запасом", "$2 800–6 000/мес", "https://job-boards.greenhouse.io/sezzle/jobs/6503001003"),
        ("Senior Payments Engineer — содержательно твоё, но порог 7+ лет", "$5 000–9 500/мес", "https://job-boards.greenhouse.io/sezzle/jobs/7779221003"),
        ("Senior Software Engineer (Turkey) — порог 8+ лет", "$5 000–9 500/мес", "https://job-boards.greenhouse.io/sezzle/jobs/6275711003"),
    ]),
    ("Exness", [
        ("Backend Software Engineer, Golang", "—", "https://job-boards.eu.greenhouse.io/internalhiring/jobs/4792957101"),
        ("Backend SWE, Golang (Workplace Experience)", "—", "https://job-boards.eu.greenhouse.io/internalhiring/jobs/4756942101"),
    ]),
    ("Andersen (12 Go-вакансий)", [
        ("Go Developer, финплатформа — потолок выше", "€2 375–4 550/мес", "https://people-andersenlab.com/ru/vacancy/2509769"),
        ("Go Architect", "—", "https://people-andersenlab.com/ru/vacancy/2510050"),
        ("Lead Go", "—", "https://people-andersenlab.com/ru/vacancy/2510049"),
    ]),
    ("Сбер", [
        ("Senior Golang-разработчик", "от 350 000 ₽", "https://www.rabota.ru/vacancy/54335063/"),
        ("Lead Go Developer (GigaChat, B2C)", "—", "https://developers.sber.ru/kak-v-sbere/vacancies/golang_ii"),
        ("Senior Go (GigaChat)", "—", "https://hh.ru/vacancy/135440444"),
        ("Senior Golang / TechLead (Platform V Synapse)", "—", "https://rabota.sber.ru/"),
    ]),
    ("adjoe", [
        ("Senior Go Backend Developer (Dashboards Team)", "—", "https://adjoe.io/careers/open-positions/b7f98905-900a-43dd-93f3-3237a9ecfd75/"),
    ]),
    ("Магнит (другое юрлицо)", [
        ("Team Lead Golang (рекомендации)", "—", "https://magnit.tech/vacancies/1485"),
        ("Team Lead Golang (AdTech)", "—", "https://magnit.tech/vacancies/1243"),
        ("Team Lead Golang", "—", "https://magnit.tech/vacancies/2066"),
    ]),
    ("poolside", [
        ("Member of Engineering (Agent Sandboxes)", "—", "https://jobs.ashbyhq.com/poolside/9a4f25e5-d387-46b6-8c0f-ebc8b8837de6"),
        ("Member of Engineering (Infrastructure)", "—", "https://jobs.ashbyhq.com/poolside/ade02c95-890f-4f1d-9ca6-05076b6fe687"),
    ]),
    ("Mirantis", [
        ("Senior AI Infrastructure & Platform Ops Engineer", "—", "https://jobs.smartrecruiters.com/Mirantis/744000139219499"),
    ]),
    ("Kaspersky", [
        ("Senior System Architect (MXDR)", "—", "https://careers.kaspersky.ru/vacancy/25709"),
        ("Golang Developer (Container Security)", "—", "https://careers.kaspersky.ru/vacancy/23956"),
    ]),
    ("Cloud.ru", [
        ("Технический лидер Go (Evolution IaaS)", "—", "https://career.habr.com/vacancies/1000167157"),
        ("Middle/Senior Golang (CNAPP)", "—", "https://career.habr.com/vacancies/1000167179"),
    ]),
]


def letter_from(card_name):
    """Вытаскивает письмо из ```-блока карточки. Копий не делаем — только чтение."""
    if not card_name:
        return None
    p = SRC / card_name
    if not p.exists():
        return None
    blocks = re.findall(r"^```\s*$\n(.*?)^```\s*$", p.read_text(encoding="utf-8"), re.S | re.M)
    return blocks[0].strip() if blocks else None


def main():
    L = []
    L.append("# Куда откликаться — рабочий лист\n")
    L.append(f"Скан {SCAN}. **Одна вакансия на компанию** — самая релевантная; остальные "
             "позиции тех же работодателей вынесены в конец, выбор за тобой.\n")
    L.append("> Письма здесь **вытянуты из карточек автоматически** "
             "(`scripts/build_apply_sheet.py`). Правишь письмо — правь в карточке "
             "и перегенерируй лист, тогда копии не разойдутся.\n")
    L.append("---\n")
    L.append("## Таблица\n")
    L.append("| # | Компания | Позиция | Деньги | Формат | Контакт | Ключевое |")
    L.append("|---|---|---|---|---|---|---|")
    for i, (co, role, money, fmt, url, card, note) in enumerate(PRIMARY, 1):
        L.append(f"| {i} | **{co}** | {role} | {money} | {fmt} | [откликнуться]({url}) | {note} |")
    L.append("")
    L.append("---\n")
    L.append("## Сопроводительные письма\n")
    missing = []
    for i, (co, role, money, fmt, url, card, note) in enumerate(PRIMARY, 1):
        letter = letter_from(card)
        L.append(f"### {i}. {co} — {role}\n")
        L.append(f"**Контакт:** {url}\n")
        if letter:
            L.append("```")
            L.append(letter)
            L.append("```\n")
        else:
            missing.append(f"{i}. {co}")
            L.append("_Письмо ещё не написано — скажи, и напишу под эту вакансию._\n")
    L.append("---\n")
    L.append("## Другие вакансии тех же компаний — выбор за тобой\n")
    L.append("Сюда вынесено то, что я не стал предлагать вторым откликом в ту же компанию. "
             "Если основная позиция не подойдёт по грейду или деньгам — заходи отсюда.\n")
    for co, alts in ALTERNATIVES:
        L.append(f"**{co}**\n")
        L.append("| Позиция | Деньги | Ссылка |")
        L.append("|---|---|---|")
        for role, money, url in alts:
            L.append(f"| {role} | {money} | [открыть]({url}) |")
        L.append("")
    if missing:
        L.append("---\n")
        L.append("## Без письма\n")
        L.append(", ".join(missing) + " — скажи, какие нужны, напишу.\n")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"готово: {OUT.relative_to(ROOT)}")
    print(f"вакансий: {len(PRIMARY)}, писем: {len(PRIMARY) - len(missing)}, без письма: {len(missing)}")
    print(f"альтернатив в {len(ALTERNATIVES)} компаниях: {sum(len(a) for _, a in ALTERNATIVES)}")


if __name__ == "__main__":
    main()
