# Короткие команды сайта-резюме. Полная картина — AGENTS.md.
#
# Питон берём из .venv намеренно: генератор тянет weasyprint, python-docx и
# jinja2, и системный python упадёт не там, где причина.
PY := .venv/bin/python

.DEFAULT_GOAL := build
.PHONY: build test serve help

## build: собрать сайт и выгрузки из resume/resume.yaml
build:
	@scripts/build_resume_formats.sh

## test: гейт разбора выгрузок — читаем их так же, как прочитает ATS
test:
	@$(PY) -m scripts.run_tests

## serve: посмотреть собранный сайт на http://127.0.0.1:8000
serve:
	@$(PY) -m http.server 8000

## help: список команд
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  make /'
