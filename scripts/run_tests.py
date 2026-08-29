"""run_tests — прогон ВСЕХ тестовых модулей, найденных на диске.

Зачем отдельный модуль, если тесты и так запускаются каждый сам. Затем, что
список модулей нельзя держать руками ни в одном месте.

🔴 Случай 16.08.2026, ради которого это написано (тогда — в сборщике вакансий,
который с 29.08.2026 живёт своим репозиторием). Чек-лист рабочего агента
утверждал «тесты — шесть отдельных модулей» и перечислял ПЯТЬ; модулей на диске
было девять. То есть агент, обязанный отчитаться зелёными тестами, не запускал
два самых больших тестовых файла репозитория, и все его отчёты «тесты зелёные»
были правдой только про половину.

Это не невнимательность, а класс ошибки: рукописный список того, что лежит на
диске, расходится с диском ВСЕГДА, вопрос только в сроке. Лечение одно —
перестать писать список руками.

Отсюда правило: **список тестов существует ровно в одном месте — в файловой
системе**, а все инструкции ссылаются на одну команду:

    .venv/bin/python -m scripts.run_tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Каталоги, где могут лежать тесты, и имя пакета для каждого. Это НЕ список
# тестов: внутри каталога модули берутся обходом диска. Каталог сейчас один —
# генератор резюме; форма словаря сохранена, потому что второй пакет здесь уже
# был и появится снова раньше, чем кажется.
PACKAGES = {
    "scripts": HERE,
}


def modules() -> list[str]:
    """Все тестовые модули на диске — полными именами (`scripts.test_resume_ats`).

    Источник истины — сам диск, по каждому каталогу из `PACKAGES`.
    """
    found: list[str] = []
    for package, directory in PACKAGES.items():
        found.extend(f"{package}.{name[:-3]}" for name in os.listdir(directory)
                     if name.startswith("test_") and name.endswith(".py"))
    return sorted(found)


def short_name(module: str) -> str:
    """Хвост полного имени — то, чем модуль зовут в фильтре и в отчёте."""
    return module.rsplit(".", 1)[-1]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    only = [a for a in argv if not a.startswith("-")]
    names = [m for m in modules()
             if not only or any(o in short_name(m) for o in only)]
    if not names:
        print(f"не нашлось ни одного тестового модуля по фильтру {only}",
              file=sys.stderr)
        return 2

    # Каждый модуль — своим процессом. Так падение одного (в том числе
    # SystemExit из его `main`) не уносит остальные, и время видно по каждому.
    failed: list[str] = []
    started = time.time()
    for name in names:
        cmd = [sys.executable, "-m", name]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
        tail = (proc.stdout or "").strip().splitlines()
        mark = "✅" if proc.returncode == 0 else "🔴"
        print(f"{mark} {short_name(name):<22} {time.time() - t0:5.1f}с  "
              f"{tail[-1][:70] if tail else ''}")
        if proc.returncode != 0:
            failed.append(short_name(name))
            for line in (tail[-25:] if tail else []):
                print(f"    {line}")
            err = (proc.stderr or "").strip().splitlines()
            for line in err[-10:]:
                print(f"    {line}", file=sys.stderr)

    print(f"\nмодулей {len(names)}, время {time.time() - started:.0f}с")
    if failed:
        print(f"🔴 КРАСНОЕ: {', '.join(failed)}")
        return 1
    print("✅ все тестовые модули зелёные")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
