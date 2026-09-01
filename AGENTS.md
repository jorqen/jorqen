# jorqen
Статический сайт-резюме на GitHub Pages; язык документации и коммитов — русский.
Карта: `resume/resume.yaml` — источник, `scripts/` — генератор и тесты, `assets/` — сайт.
Не добавляй сюда поиск работы: он полностью живёт в отдельном `~/Projects/kadr`.

## Инварианты
Резюме и контакты меняются только в `resume/resume.yaml`.
`index.html`, `en/`, `ru/` и выгрузки генерируются; вручную их не править.
EN и RU совпадают по смыслу и объёму; опыт отсортирован по дате начала.
PDF и DOCX должны совпадать по содержанию.
Репозиторий публичный: личное — только в игнорируемом `resume/resume.local.yaml`.
Красная сборка означает, что коммитить нечего.
Список тестов берётся обходом диска, не поддерживай ручной перечень.

## Проверка
Гейт любой правки: `make build && make test && ~/.nvm/versions/node/v22.18.0/bin/pyright`.
`make build` валидирует схему и собирает сайт с выгрузками.
`make test` проверяет выгрузки так, как их читает ATS; для PDF нужен `pdftotext` из poppler.

## Skills
UI-задачи: `better-accessibility`, `better-colors`, `better-layout`, `better-typography`, `better-ui`, `better-writing`.
Полный UI-обзор вызывай через `better-interface`; обзор изменений — через `interface-review`.
Правила skills: `.agents/skills/`; `.claude/` оставлен только для временной совместимости.
