"""Тесты на отделение технологии от канцелярита вакансий.

Здесь ловится ровно тот класс ошибок, из-за которого команда `profile` четыре
раза выдавала мусор вместо спроса: правило выглядело работающим, а в топ
выходили Job Description и Location. Каждый тест — это тупик, в который уже
заходили; без них следующая правка отбора вернёт всё обратно.

    python3 -m scripts.scout.test_profile
"""

from __future__ import annotations

import sys

from . import profile

FAILS: list[str] = []


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def ok(cond, label):
    if not cond:
        FAILS.append(label)


def _rows(text: str, n: int, *, title: str = "Backend Engineer",
          location: str = "") -> list[dict]:
    """n вакансий разных компаний с одним и тем же текстом.

    Разных — потому что спрос считается по компаниям: один наниматель на пяти
    площадках не должен выглядеть как рынок.
    """
    return [{"title": title, "company": f"Company {i}", "description": text,
             "location": location} for i in range(n)]


# ── Форма слова ──────────────────────────────────────────────────────────────
def test_shape_knows_tech_spelling_from_ats_caps():
    for tok in ("k8s", "s3", "oauth2", "c++", "c#", "ci/cd", "gRPC", "mTLS",
                "PostgreSQL", "ClickHouse", "TypeScript"):
        ok(profile._shape_tech(tok), f"форма: {tok} должен считаться технологией")
    # Капс шапок ATS и Title Case через дефис — не форма технологии. На обоих
    # правило уже ломалось: JOB DESCRIPTION выходил в спрос с 4300 компаниями.
    for tok in ("JOB", "DESCRIPTION", "LOCATION", "POSITION", "Full-Stack",
                "End-to-End", "AI-Powered", "Backend", "Cloud"):
        ok(not profile._shape_tech(tok), f"форма: {tok} технологией не является")


def test_gender_marker_is_not_a_technology():
    # «m/w/d» и «h/f» — пометка пола в немецких и французских объявлениях.
    # Слэш в них тот же, что в «ci/cd», отличаются только длиной частей.
    ok(not profile._shape_tech("m/w/d"), "m/w/d — не технология")
    ok(not profile._shape_tech("h/f"), "h/f — не технология")
    ok(profile._shape_tech("ci/cd"), "ci/cd — технология")


# ── Спрос: что доходит до отчёта ─────────────────────────────────────────────
def test_ats_boilerplate_never_reaches_demand():
    text = ("JOB DESCRIPTION\n"
            "Position: Backend Engineer\n"
            "Location: Berlin, Germany\n"
            "- Experience with Kubernetes, Terraform and Kafka\n"
            "- Strong knowledge of Go\n")
    _, per_co, _ = profile.demand(_rows(text, 40))
    for junk in ("job", "position", "location", "description", "job description"):
        ok(junk not in per_co, f"шапка ATS в спросе: {junk}")
    for tech in ("kubernetes", "terraform"):
        ok(tech in per_co, f"технология потеряна: {tech}")


def test_enumeration_is_what_separates_tech_from_capitalized_words():
    # «Salary» стоит с заглавной посреди фразы не хуже «Kubernetes», и одного
    # признака имени собственного не хватало. Отличает их перечисление стека.
    text = ("- We offer a competitive Salary and a great Culture\n"
            "- Stack: Kubernetes, Terraform, Kafka, PostgreSQL\n")
    _, per_co, _ = profile.demand(_rows(text, 40))
    ok("salary" not in per_co, "Salary попал в спрос")
    ok("culture" not in per_co, "Culture попал в спрос")
    ok("terraform" in per_co, "terraform потерян")


def test_single_odd_spelling_does_not_make_a_word_a_technology():
    # Одно «UnD» на весь корпус объявляло немецкий союз технологией навсегда:
    # признак считался фактом «встретилось», а не долей.
    text = ("- Erfahrung mit Kubernetes und Kafka\n"
            "- Kenntnisse in Go und Docker\n")
    odd = _rows(text, 39) + [{"title": "Backend Engineer", "company": "Odd",
                              "description": "- Kubernetes, UnD, Kafka",
                              "location": ""}]
    _, per_co, _ = profile.demand(odd)
    ok("und" not in per_co, "«und» объявлен технологией одним написанием")


def test_location_of_the_vacancy_is_not_demand():
    # Порту и Лиссабон стоят в шапке через запятую ровно как стек.
    text = "- Stack: Go, Kubernetes\n- Offices in Porto, Lisbon\n"
    _, per_co, _ = profile.demand(
        _rows(text, 40, location="Porto, Lisbon, Portugal"))
    ok("porto" not in per_co, "город вакансии попал в спрос")
    ok("go" in per_co, "go потерян")


def test_resume_terms_are_measured_even_when_they_look_like_common_words():
    # «go» короче трёх букв, «backend» стоит в стоп-листе шума. Пока они не
    # проходили мимо стоп-листов, главный язык владельца уходил в «балласт»
    # с нулём вакансий на корпусе, где его требуют сотни компаний.
    text = "- Strong Go and backend experience\n- Kubernetes, Kafka\n"
    _, per_co, _ = profile.demand(_rows(text, 40))
    ok(per_co.get("go", 0) >= 40, f"спрос на go не измерен: {per_co.get('go')}")
    ok(per_co.get("backend", 0) >= 40, "спрос на backend не измерен")


def test_multiword_resume_term_survives_a_noisy_first_word():
    # «service» стоит в стоп-листе шума, и пока пара считалась внутри ветки
    # первого слова, «service mesh» молча уходил в «балласт».
    text = "- Experience with service mesh, Istio and Kubernetes\n"
    _, per_co, _ = profile.demand(_rows(text, 40))
    ok("service mesh" in per_co, "«service mesh» потерян из-за шумного слова")


# ── Резюме: чем подтверждён термин ───────────────────────────────────────────
def test_evidence_levels_tell_a_claim_from_a_proof():
    data = {
        "skills": {"groups": [{"items": ["Go (Golang)", "AWS", "Kubernetes"]}]},
        "experience": {"items": [{
            "stack": ["Go", "Kubernetes"],
            "summary": {"en": "Backend services", "ru": "Backend-сервисы"},
            "highlights": [{"en": "Ran Kubernetes clusters", "ru": "Вёл кластеры"}],
        }]},
    }
    ev = _evidence(data)
    eq(ev.get("go"), "работа", "go подтверждён стеком")
    eq(ev.get("golang"), "работа", "golang — тот же навык, что и go")
    eq(ev.get("aws"), "список", "aws заявлен, но ничем не подтверждён")


def test_localized_skill_item_does_not_leak_a_python_dict():
    # Разговорные языки записаны как {en: …, ru: …}. Через str() пункт уезжал
    # в поиск куском словаря: «{'en': 'russian» вместо «russian».
    data = {"skills": {"groups": [{"items": [
        {"en": "Russian (Native)", "ru": "Русский (родной)"}]}]},
        "experience": {"items": []}}
    ev = _evidence(data)
    ok(all("{" not in t and "'" not in t for t in ev),
       f"в терминах остался словарь: {sorted(ev)}")
    ok("russian" in ev, f"локализованный пункт не разобран: {sorted(ev)}")


def test_pet_project_is_neither_a_proof_of_work_nor_an_empty_claim():
    # «Дополнительная информация» не читалась вовсе, и пет-проекты выходили
    # в отчёт наравне с пустой строчкой ATS: список правок звал подтверждать
    # делом WebRTC и Python, уже описанные там прозой. Слить их с «делом»
    # тоже нельзя — это не коммерческий опыт, и в письме так подавать запрещено.
    data = {
        "skills": {"groups": [{"items": ["Go", "WebRTC", "Ansible"]}]},
        "experience": {"items": [{
            "stack": ["Go"],
            "highlights": [{"en": "Built backend services", "ru": "Делал сервисы"}],
        }]},
        "preferences": {"items": [
            {"en": "A side project in Go where I worked hands-on with WebRTC",
             "ru": "Пет-проект на Go, где я вплотную работал с WebRTC"}]},
    }
    ev = _evidence(data)
    eq(ev.get("webrtc"), "сам", "пет-проект прочитан как пустая заявка")
    eq(ev.get("go"), "работа", "работа не должна опускаться до пет-проекта")
    eq(ev.get("ansible"), "список", "нигде не названное стало подтверждённым")
    ok(profile.LEVELS["список"] < profile.LEVELS["сам"] < profile.LEVELS["дело"],
       "уровень «сам» должен стоять между заявкой и делом")


def test_same_skill_in_another_word_form_still_counts():
    # В списке навыков «Mentoring», в пункте опыта «Mentored ~10 interns» —
    # одно и то же, а точное совпадение по границам слова этого не видит,
    # и подтверждённый делом навык уезжал в «заявлено, но не подтверждено».
    data = {
        "skills": {"groups": [{"items": ["Mentoring", "Integration testing", "Rust"]}]},
        "experience": {"items": [{
            "stack": [],
            "highlights": [
                {"en": "Mentored ~10 interns", "ru": "Менторил ~10 стажёров"},
                {"en": "Built an integration-testing framework",
                 "ru": "Сделал фреймворк интеграционного тестирования"}],
        }]},
    }
    ev = _evidence(data)
    eq(ev.get("mentoring"), "дело", "«Mentored» не засчитан за «Mentoring»")
    eq(ev.get("integration testing"), "дело", "дефис вместо пробела потерял термин")
    eq(ev.get("rust"), "список", "короткое слово не должно ловиться по префиксу")


def test_spoken_language_is_not_a_claim_to_prove_with_a_bullet():
    # «English (B2 / Working proficiency)» растаскивалось на english, b2,
    # working, proficiency, и english с 191 компанией вставал третьим в списке
    # «подтверди делом» — совет, который к владению языком не применим.
    data = {
        "skills": {"groups": [
            {"title": {"en": "Platform", "ru": "Платформа"}, "items": ["Ansible"]},
            {"title": {"en": "Spoken languages", "ru": "Разговорные языки"},
             "items": [{"en": "English (B2 / Working proficiency)",
                        "ru": "Английский (B2 / рабочий уровень)"}]}]},
        "experience": {"items": [{"stack": [], "highlights": []}]},
    }
    ev = _evidence(data)
    eq(ev.get("english"), "язык", "разговорный язык подан как пустая заявка")
    eq(ev.get("b2"), "язык", "уровень языка подан как отдельный навык")
    eq(ev.get("ansible"), "список", "обычный навык пострадал от отсечки языков")


def test_short_terms_are_not_matched_by_prefix():
    # 🔴 Терпимость к форме слова обязана останавливаться на коротких словах:
    # «go» по префиксу поймало бы «going», «rust» — «rusty», и любой текст
    # опыта подтверждал бы половину списка навыков.
    ok(not profile._says("we are going to a meeting", "go"), "«go» поймал «going»")
    ok(not profile._says("a rusty old pipeline", "rust"), "«rust» поймал «rusty»")
    ok(not profile._says("the api is documented", "ai"), "«ai» поймал «api»")
    ok(profile._says("mentored ten interns", "mentoring"), "форма слова не опознана")


def _evidence(data: dict) -> dict[str, str]:
    """resume_evidence поверх словаря вместо файла: тест не должен зависеть
    от текущего содержимого резюме — оно меняется каждую неделю."""
    import tempfile
    import os
    try:
        import yaml
    except ImportError:
        return {}
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)
        ev, _ = profile.resume_evidence(path)
        return ev
    finally:
        os.unlink(path)


def main() -> int:
    # Тесты собираются АВТОМАТИЧЕСКИ — все `test_*` этого модуля, в порядке
    # определения. Ручной список означал, что забытое имя = тест, который не
    # запускается и потому «зелёный» всегда: 09.08.2026 так молча не работали
    # сразу две новые проверки, и обе ловили настоящие дефекты.
    import inspect as _inspect
    import sys as _sys
    mod = _sys.modules[__name__]
    tests = [f for _, f in _inspect.getmembers(mod, _inspect.isfunction)
             if f.__name__.startswith("test_") and f.__module__ == __name__
             and not any(pr.default is pr.empty
                         for pr in _inspect.signature(f).parameters.values())]
    tests.sort(key=lambda f: f.__code__.co_firstlineno)
    for fn in tests:
        fn()
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
