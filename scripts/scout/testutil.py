"""Общее для тестовых модулей. Только то, что НЕ зависит от их состояния.

`eq`/`ok` живут в каждом модуле своими: они пишут в собственный список `FAILS`,
и общий список слил бы отчёты четырёх независимых прогонов в один. А вот
`patched` состояния не имеет вовсе — и лежал тремя одинаковыми копиями, из-за
чего в `test_scout` его просто не было, и проверки там писались обходными
путями.
"""

from __future__ import annotations


class patched:
    """Точечная подмена атрибута модуля на время теста."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *a):
        setattr(self.obj, self.name, self.old)
        return False
