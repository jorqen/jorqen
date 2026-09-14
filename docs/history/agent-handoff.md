# Снимок handoff до миграции оснастки

Источник: `.claude/HANDOFF.md`, перенесён 13.09.2026 при переходе на
`AGENTS.md` и `.agents/`. Это историческая запись, а не действующая инструкция.

## Состояние на момент записи

Репозиторий стал одночастным 29.08.2026: механика поиска работы переехала в
`~/Projects/kadr` (приватный, `git@github.com:jorqen/kadr.git`). Здесь остались
`resume/resume.yaml`, генератор выгрузок, сайт и его оснастка.

После отделения были зелёными `scripts/build_resume_formats.sh`,
`.venv/bin/python -m scripts.run_tests` (1 модуль, 17 тестов) и pyright без
ошибок.

## Зафиксированные остатки

- `.venv` содержал зависимости уехавшего сборщика (playwright, telethon,
  trafilatura, imap-tools); генератору нужны только babel, jinja2, jsonschema,
  python-docx, pillow, pyyaml и weasyprint. Пересборка окружения не входила в
  тот handoff.
- В игнорируемом `.idea/` оставались ссылки на модули прежней раскладки; на
  сборку это не влияло.
