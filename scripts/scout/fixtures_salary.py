"""Корпус живых строк зарплат со всех площадок — и ожидания, проставленные ГЛАЗАМИ.

Зачем он есть. Разбор вилки — место, где ошибка не падает, а печатается уверенным
тоном: «3 ₽» вместо «3 000 000 ₽», доллар вместо канадского доллара, «от 90 000»
вместо «90 000–130 000». Такие баги ловились по одному, поштучно, по жалобе — и
каждый следующий находился ровно тем же способом. Корпус переводит поиск из
«заметил в отчёте» в «упало в тесте».

Откуда строки. Выкопаны из .scout/scout.db (поля raw.salary_raw, raw.salary_formatted,
raw.item.salaryText, raw.salaryLabel, raw.vacancy_info.salary.raw, detail.payload.salary)
и из архива телеграм-каналов .scout/tg/*/*.txt — то есть это в точности то, что
приезжает в parse_salary в живом прогоне, вплоть до неразрывных пробелов, нулевой
ширины и эмодзи. Ничего не выдумано и не «причёсано».

Как проставлены ожидания. РУКАМИ, чтением строки, а НЕ прогоном текущего парсера.
Это принципиально: снять ожидания парсером — значит забетонировать его сегодняшние
ошибки и получить тест, который зелен ровно тогда, когда врёт.

Формат записи:

    (площадка, строка, от, до, валюта, gross, период)

* валюта — код ровно в том виде, в каком его обязан вернуть parse_salary: «RUR»
  остаётся «RUR» (в общий вид его приводит model.norm_currency, а не разбор);
* gross — True «до вычета/gross/гросс/before taxes», False «на руки/net/нетто»,
  None — площадка не сказала, и додумывать за неё нельзя;
* период — hour | day | week | month | year | None. День и неделя появились
  06.08.2026 вместе с model.PERIOD_SUFFIX: раньше «1000 EUR per day» честно
  оставалось без периода, но «честно» здесь означало «неотличимо от месячной»;
* период SKIP — строка называет несколько разных периодов сразу, и однозначного
  ответа нет даже глазами. На 06.08.2026 таких строк в корпусе не осталось:
  период привязан к ОСНОВНОЙ вилке (sources._period_scope), и «16000–20000 PLN
  gross; B2B: 116–142 PLN/hour» — это не «почасовая», а «период не назван».

Отдельно про строки, которые разбор обязан НЕ ОТДАТЬ, хотя прочитал их без
единой ошибки. Их здесь четыре сорта, и ожидание у всех одинаковое — пустая
вилка:

* опечатка площадки («€43,000 – €53,75»): верх ниже низа физически невозможен;
* невероятный порядок величины («5 520 000 000 USD/мес», «150000250K ₽»,
  «28000-35000 EUR per day» — годовая сумма с дневным периодом);
* справочная вилка («ср. рын. зп 300 000 ₽ – 370 000 ₽») — это чужой обзор
  рынка, а не предложение работодателя;
* валюта, которую нельзя определить («45 000 kr» без страны) — здесь пустой
  остаётся валюта, а не сумма.

Во всех четырёх случаях факт уходит в sources.salary_note и оттуда в raw
вакансии. Проверяет это test_scout.test_salary_note_*, потому что в кортеже
корпуса колонки под примечание нет и заводить её ради четырёх строк незачем.

Файл только с данными: ни импортов, ни логики — чтобы его нельзя было «починить»,
подогнав под парсер.
"""

from __future__ import annotations

# Период не определить однозначно даже глазами — в этой строке названо несколько.
# Сейчас не используется ни одной строкой и оставлен намеренно: это единственный
# честный способ пометить строку, у которой ответа нет, — и он должен быть под
# рукой, а не изобретаться заново тем, кто наткнётся на такую строку следующим.
SKIP = "?"

Case = tuple[str, str, "int | None", "int | None", "str | None", "bool | None", "str | None"]

CASES: tuple[Case, ...] = (
    # ── habr (Хабр Карьера) ───────────────────────────────────────────────────
    ("habr", "от 250 000 до 270 000 ₽", 250000, 270000, "RUB", None, None),
    ("habr", "от 300 000 до 490 000 ₽", 300000, 490000, "RUB", None, None),
    ("habr", "до 250 000 ₽", None, 250000, "RUB", None, None),
    ("habr", "до 45 000 ₽", None, 45000, "RUB", None, None),
    ("habr", "от 300 000 ₽", 300000, None, "RUB", None, None),
    ("habr", "от 350 000 ₽", 350000, None, "RUB", None, None),
    ("habr", "от 3000 до 4000 $", 3000, 4000, "USD", None, None),
    ("habr", "от 1600 до 2240 $", 1600, 2240, "USD", None, None),
    ("habr", "от 2000 до 3000 €", 2000, 3000, "EUR", None, None),
    ("habr", "70 000 ₽", 70000, None, "RUB", None, None),
    ("habr", "92 000 ₽", 92000, None, "RUB", None, None),
    ("habr", "от 5000 до 15 000 $", 5000, 15000, "USD", None, None),

    # ── hh.ru ─────────────────────────────────────────────────────────────────
    ("hh", "350 000–450 000 RUB/мес net", 350000, 450000, "RUB", False, "month"),
    ("hh", "450 000–600 000 RUB/мес net", 450000, 600000, "RUB", False, "month"),
    ("hh", "250 000–300 000 RUB/мес gross", 250000, 300000, "RUB", True, "month"),
    ("hh", "100 000–140 000 RUB/мес gross", 100000, 140000, "RUB", True, "month"),
    ("hh", "до 250 000 RUB/мес net", None, 250000, "RUB", False, "month"),
    ("hh", "до 300 000 RUB/мес net", None, 300000, "RUB", False, "month"),
    ("hh", "до 700 000 RUB/мес gross", None, 700000, "RUB", True, "month"),
    ("hh", "до 300 000 RUB/мес gross", None, 300000, "RUB", True, "month"),
    ("hh", "от 400 000 RUB/мес gross", 400000, None, "RUB", True, "month"),
    ("hh", "от 290 000 RUB/мес gross", 290000, None, "RUB", True, "month"),
    ("hh", "от 330 000 RUB/мес net", 330000, None, "RUB", False, "month"),
    ("hh", "от 500 000 RUB/мес net", 500000, None, "RUB", False, "month"),
    ("hh", "от 6 000 USD/мес gross", 6000, None, "USD", True, "month"),
    ("hh", "от 4 000 USD/мес net", 4000, None, "USD", False, "month"),

    # ── getmatch ──────────────────────────────────────────────────────────────
    ("getmatch", "3 000–5 500 USD net", 3000, 5500, "USD", False, None),
    ("getmatch", "12 000–18 000 USD net", 12000, 18000, "USD", False, None),
    ("getmatch", "7 500–9 000 USD gross", 7500, 9000, "USD", True, None),
    ("getmatch", "8 000–10 000 USD gross", 8000, 10000, "USD", True, None),
    ("getmatch", "340 000–400 000 RUB gross", 340000, 400000, "RUB", True, None),
    ("getmatch", "400 000–550 000 RUB gross", 400000, 550000, "RUB", True, None),
    ("getmatch", "500 000–750 000 RUB net", 500000, 750000, "RUB", False, None),
    ("getmatch", "300 000–400 000 RUB net", 300000, 400000, "RUB", False, None),
    ("getmatch", "5 000–5 000 EUR gross", 5000, 5000, "EUR", True, None),
    ("getmatch", "7 500–10 000 EUR gross", 7500, 10000, "EUR", True, None),

    # ── geekjob: суммы через K, включая дробные ───────────────────────────────
    ("geekjob", "от 350K ₽", 350000, None, "RUB", None, None),
    ("geekjob", "от 400K ₽", 400000, None, "RUB", None, None),
    ("geekjob", "350K — 500K ₽", 350000, 500000, "RUB", None, None),
    ("geekjob", "120K — 150K ₽", 120000, 150000, "RUB", None, None),
    ("geekjob", "до 250K ₽", None, 250000, "RUB", None, None),
    ("geekjob", "180K ₽", 180000, None, "RUB", None, None),
    ("geekjob", "15K ₽", 15000, None, "RUB", None, None),
    ("geekjob", "1.5K ₽", 1500, None, "RUB", None, None),
    ("geekjob", "2.3K — 2.8K ₽", 2300, 2800, "RUB", None, None),
    ("geekjob", "3.5K — 5.5K ₽", 3500, 5500, "RUB", None, None),
    ("geekjob", "от 700 $", 700, None, "USD", None, None),
    ("geekjob", "от 3K $", 3000, None, "USD", None, None),
    ("geekjob", "от 5K $", 5000, None, "USD", None, None),
    ("geekjob", "5K — 12K $", 5000, 12000, "USD", None, None),
    ("geekjob", "4K — 10K $", 4000, 10000, "USD", None, None),
    ("geekjob", "3.5K — 4.5K $", 3500, 4500, "USD", None, None),
    ("geekjob", "3.8K — 4.2K $", 3800, 4200, "USD", None, None),
    ("geekjob", "4.5K — 6K $", 4500, 6000, "USD", None, None),
    ("geekjob", "от 800 €", 800, None, "EUR", None, None),

    # ── hirehi ────────────────────────────────────────────────────────────────
    ("hirehi", "от 400 000 ₽", 400000, None, "RUB", None, None),
    ("hirehi", "до 480 412 ₽", None, 480412, "RUB", None, None),
    ("hirehi", "до 4 000 ₽", None, 4000, "RUB", None, None),
    ("hirehi", "66 440 ₽", 66440, None, "RUB", None, None),
    ("hirehi", "350 000 ₽", 350000, None, "RUB", None, None),
    ("hirehi", "от 1 092 626 ₽", 1092626, None, "RUB", None, None),
    ("hirehi", "от 1 601 374 ₽", 1601374, None, "RUB", None, None),
    ("hirehi", "1 167 274 ₽", 1167274, None, "RUB", None, None),

    # ── careered: валюта кодом, период через слэш ─────────────────────────────
    ("careered", "от 2 900 EUR/мес", 2900, None, "EUR", None, "month"),
    ("careered", "от 2 100 EUR/мес", 2100, None, "EUR", None, "month"),
    ("careered", "35 000–40 000 EUR/год", 35000, 40000, "EUR", None, "year"),
    ("careered", "50 000–75 000 EUR/год", 50000, 75000, "EUR", None, "year"),
    ("careered", "до 180 000 RUB/мес", None, 180000, "RUB", None, "month"),
    ("careered", "до 240 000 RUB/мес", None, 240000, "RUB", None, "month"),
    ("careered", "30–130 USD/час", 30, 130, "USD", None, "hour"),
    ("careered", "20–70 USD/час", 20, 70, "USD", None, "hour"),
    ("careered", "62 400–140 400 USD/год", 62400, 140400, "USD", None, "year"),
    ("careered", "30 000–100 000 USD/год", 30000, 100000, "USD", None, "year"),
    ("careered", "5 000–8 000 USD/мес", 5000, 8000, "USD", None, "month"),
    ("careered", "3 600–4 100 USD/мес", 3600, 4100, "USD", None, "month"),
    ("careered", "80 000–120 000 GBP/год", 80000, 120000, "GBP", None, "year"),
    ("careered", "60 000–60 000 GBP/год", 60000, 60000, "GBP", None, "year"),
    ("careered", "300 000–350 000 RUB/мес", 300000, 350000, "RUB", None, "month"),
    ("careered", "120 000–230 000 RUB/мес", 120000, 230000, "RUB", None, "month"),
    ("careered", "1 750–3 500 EUR/мес", 1750, 3500, "EUR", None, "month"),
    ("careered", "154 000–247 800 PLN/год", 154000, 247800, "PLN", None, "year"),
    ("careered", "228 900–425 100 PLN/год", 228900, 425100, "PLN", None, "year"),
    ("careered", "от 50 000 EUR/год", 50000, None, "EUR", None, "year"),
    ("careered", "от 200 000 RUB/мес", 200000, None, "RUB", None, "month"),
    ("careered", "45–55 EUR/час", 45, 55, "EUR", None, "hour"),
    ("careered", "16 000–22 000 PLN/мес", 16000, 22000, "PLN", None, "month"),
    ("careered", "до 8 000 EUR/мес", None, 8000, "EUR", None, "month"),
    # Площадка отдала явную чушь. Пять миллиардов долларов в месяц не платит
    # никто и никогда — такую вилку разбор обязан НЕ ОТДАТЬ: она встала бы первой
    # строкой отчёта и выглядела бы лучшим предложением за всю выдачу. Ожидание
    # изменено 06.08.2026; до этого стояло «вернуть ровно то, что написано»,
    # и это было ошибкой — читать написанное и верить написанному не одно и то же.
    #
    # А вот 300 000 ₽/час (≈$3 750) отдаётся: это тоже чушь, но чушь ВОЗМОЖНАЯ,
    # и выбрасывать по подозрению разбор права не имеет. Порог намеренно там,
    # где кончается физика, а не там, где кончается правдоподобие.
    ("careered", "до 300 000 RUB/час", None, 300000, "RUB", None, "hour"),
    ("careered", "5 520 000 000–5 520 000 000 USD/мес", None, None, "USD", None, "month"),
    ("careered", "2 900–2 900 RUB/час", 2900, 2900, "RUB", None, "hour"),
    ("careered", "от 3 500 USD/мес", 3500, None, "USD", None, "month"),
    ("careered", "от 5 500 USD/мес", 5500, None, "USD", None, "month"),
    # Валюты, которых в нашей таблице не было: дирхам, иена, бат, канадский доллар.
    ("careered", "27 000–32 000 AED/мес", 27000, 32000, "AED", None, "month"),
    ("careered", "9 000 000–15 000 000 JPY/год", 9000000, 15000000, "JPY", None, "year"),
    ("careered", "6 000 000–9 000 000 JPY/год", 6000000, 9000000, "JPY", None, "year"),
    ("careered", "250 000–250 000 JPY/мес", 250000, 250000, "JPY", None, "month"),
    ("careered", "40 000–60 000 THB/мес", 40000, 60000, "THB", None, "month"),
    ("careered", "60 000–100 000 THB/мес", 60000, 100000, "THB", None, "month"),
    ("careered", "137 000–186 000 CAD/год", 137000, 186000, "CAD", None, "year"),
    ("careered", "89 900–167 100 CAD/год", 89900, 167100, "CAD", None, "year"),
    ("careered", "3 000–4 000 CAD/мес", 3000, 4000, "CAD", None, "month"),
    ("careered", "до 6 000 USD/мес", None, 6000, "USD", None, "month"),
    ("careered", "160–180 PLN/час", 160, 180, "PLN", None, "hour"),
    ("careered", "от 40 USD/час", 40, None, "USD", None, "hour"),
    ("careered", "до 65 000 EUR/год", None, 65000, "EUR", None, "year"),
    ("careered", "до 155 PLN/час", None, 155, "PLN", None, "hour"),
    ("careered", "21 000–24 000 GBP/мес", 21000, 24000, "GBP", None, "month"),

    # ── jobsdb (Таиланд) ──────────────────────────────────────────────────────
    ("jobsdb", "฿45,000 – ฿65,000 per month", 45000, 65000, "THB", None, "month"),
    ("jobsdb", "฿18,000 – ฿22,000 per month", 18000, 22000, "THB", None, "month"),
    ("jobsdb", "฿15,000 per month", 15000, None, "THB", None, "month"),
    ("jobsdb", "30-45k THB", 30000, 45000, "THB", None, None),
    ("jobsdb", "THB 150000 - 250000 per month, Negotiable", 150000, 250000, "THB", None, "month"),

    # ── remotive ──────────────────────────────────────────────────────────────
    ("remotive", "$80k - $100k", 80000, 100000, "USD", None, None),
    ("remotive", "$170k - $200k", 170000, 200000, "USD", None, None),
    ("remotive", "$90 - $150 /hour", 90, 150, "USD", None, "hour"),
    ("remotive", "$120 - $170 /hour", 120, 170, "USD", None, "hour"),

    # ── dreamoffer ────────────────────────────────────────────────────────────
    ("dreamoffer", "1000 EUR", 1000, None, "EUR", None, None),
    ("dreamoffer", "30000 EUR", 30000, None, "EUR", None, None),
    ("dreamoffer", "200000-300000 USD per year", 200000, 300000, "USD", None, "year"),
    ("dreamoffer", "65000-70000 USD per year", 65000, 70000, "USD", None, "year"),
    ("dreamoffer", "40000-50000 EUR", 40000, 50000, "EUR", None, None),
    ("dreamoffer", "50000-60000 EUR per year", 50000, 60000, "EUR", None, "year"),
    ("dreamoffer", "от 150к RUB в зависимости от грейда/занятость/скиллов", 150000, None, "RUB", None, None),
    ("dreamoffer", "от 3 000 $ до 5 000 $", 3000, 5000, "USD", None, None),
    ("dreamoffer", "143800-231900 USD", 143800, 231900, "USD", None, None),
    ("dreamoffer", "50-120 USD per hour", 50, 120, "USD", None, "hour"),
    ("dreamoffer", "75000-85000 GBP per year", 75000, 85000, "GBP", None, "year"),
    ("dreamoffer", "от 250 000₽ до 300 000₽", 250000, 300000, "RUB", None, None),
    # «per day» и «per week». До 06.08.2026 период здесь оставался None, и
    # дневная ставка 1000 EUR стояла в таблице неотличимо от месячной вилки —
    # то есть выглядела нищей и отбрасывалась не глядя.
    ("dreamoffer", "1000 EUR per day", 1000, None, "EUR", None, "day"),
    ("dreamoffer", "300-400 EUR per day", 300, 400, "EUR", None, "day"),
    ("dreamoffer", "50000-75000 EUR per week", 50000, 75000, "EUR", None, "week"),
    ("dreamoffer", "900-1200 PLN per day", 900, 1200, "PLN", None, "day"),
    ("dreamoffer", "900-1000 SEK per day", 900, 1000, "SEK", None, "day"),
    ("dreamoffer", "40000 EUR per year", 40000, None, "EUR", None, "year"),
    ("dreamoffer", "от 180 000 ₽", 180000, None, "RUB", None, None),
    ("dreamoffer", "от 250 000 до 350 000 ₽", 250000, 350000, "RUB", None, None),
    ("dreamoffer", "125000 USD", 125000, None, "USD", None, None),
    ("dreamoffer", "50-62 GBP per hour", 50, 62, "GBP", None, "hour"),
    ("dreamoffer", "609985 SEK", 609985, None, "SEK", None, None),
    ("dreamoffer", "320000 PLN per year", 320000, None, "PLN", None, "year"),
    ("dreamoffer", "до 180к рублей на руки", None, 180000, "RUB", False, None),
    ("dreamoffer", "250 000 ₽ – 350 000 ₽ на руки", 250000, 350000, "RUB", False, None),
    ("dreamoffer", "68000 RUR", 68000, None, "RUR", None, None),
    # Справочная вилка, а не предложение. Работодатель здесь как раз ОТКАЗАЛСЯ
    # называть деньги («по итогам собеседования»), а числа взяты из чужого обзора
    # рынка. Ожидание изменено 06.08.2026: до этого 300–370 тысяч приезжали в
    # отчёт обычной вилкой и выглядели предложением, которого никто не делал.
    ("dreamoffer", "по итогам собеседования (ср. рын. зп 300 000 ₽ – 370 000 ₽)", None, None, None, None, None),
    ("dreamoffer", "120-144 тыс. ₽ / мес (после налогов)", 120000, 144000, "RUB", False, "month"),
    ("dreamoffer", "300 000 ₽ – 370 000 ₽", 300000, 370000, "RUB", None, None),
    ("dreamoffer", "до 260 000 ₽", None, 260000, "RUB", None, None),
    ("dreamoffer", "до 1700 RUB/час", None, 1700, "RUB", None, "hour"),
    ("dreamoffer", "ср. рын. зп 230 000 ₽ – 300 000 ₽", None, None, None, None, None),
    ("dreamoffer", "785000-1100000 NOK", 785000, 1100000, "NOK", None, None),
    ("dreamoffer", "200 000 – 270 000 руб.", 200000, 270000, "RUB", None, None),
    ("dreamoffer", "15000-20000 CZK", 15000, 20000, "CZK", None, None),
    ("dreamoffer", "2535 EUR per month", 2535, None, "EUR", None, "month"),
    ("dreamoffer", "5500-7500 EUR per month", 5500, 7500, "EUR", None, "month"),
    ("dreamoffer", "5000$ to 7000$", 5000, 7000, "USD", None, None),
    ("dreamoffer", "from 3500$ gross", 3500, None, "USD", True, None),
    ("dreamoffer", "2900 ₽ в час gross", 2900, None, "RUB", True, "hour"),
    ("dreamoffer", "4000 GBP", 4000, None, "GBP", None, None),
    ("dreamoffer", "от 4 500 до 6 000 $", 4500, 6000, "USD", None, None),
    # $7 600 в день — на грани, но не за гранью: столько стоит день топового
    # консультанта, и порог правдоподобия ($20 000/день) отсекает не это.
    ("dreamoffer", "7600 USD per day", 7600, None, "USD", None, "day"),
    ("dreamoffer", "244090-366140 PLN", 244090, 366140, "PLN", None, None),
    ("dreamoffer", "54000 - 72000 €/Año", 54000, 72000, "EUR", None, None),
    ("dreamoffer", "46000 GBP per month", 46000, None, "GBP", None, "month"),
    ("dreamoffer", "40000-100000 GBP", 40000, 100000, "GBP", None, None),
    ("dreamoffer", "€6k-€8,5k", 6000, 8500, "EUR", None, None),
    ("dreamoffer", "250 000 - 300 000 руб.", 250000, 300000, "RUB", None, None),
    ("dreamoffer", "от 3500$ net", 3500, None, "USD", False, None),
    ("dreamoffer", "180 000 ₽", 180000, None, "RUB", None, None),
    ("dreamoffer", "от 3 500 $", 3500, None, "USD", None, None),
    ("dreamoffer", "$30 - $90 per hour", 30, 90, "USD", None, "hour"),
    ("dreamoffer", "от 250 до 350 тыс руб", 250000, 350000, "RUB", None, None),
    ("dreamoffer", "от 150 до 200 тыс руб", 150000, 200000, "RUB", None, None),
    ("dreamoffer", "от 150 тыс. руб. до 170 тыс. руб.", 150000, 170000, "RUB", None, None),
    ("dreamoffer", "2500$", 2500, None, "USD", None, None),
    ("dreamoffer", "$3 500 до $5 500", 3500, 5500, "USD", None, None),
    ("dreamoffer", "493-5588 EUR per hour", 493, 5588, "EUR", None, "hour"),
    ("dreamoffer", "150000-187000 PLN per year", 150000, 187000, "PLN", None, "year"),
    ("dreamoffer", "300 000 — 350 000 ₽ gross", 300000, 350000, "RUB", True, None),
    ("dreamoffer", "12400 PLN per month", 12400, None, "PLN", None, "month"),
    ("dreamoffer", "$85 per hour", 85, None, "USD", None, "hour"),
    ("dreamoffer", "3000 EUR - 5000 EUR Gross", 3000, 5000, "EUR", True, None),
    ("dreamoffer", "651000-956900 DKK per year", 651000, 956900, "DKK", None, "year"),
    ("dreamoffer", "18000-25000 USD per month", 18000, 25000, "USD", None, "month"),
    ("dreamoffer", "от 1600 руб.", 1600, None, "RUB", None, None),
    ("dreamoffer", "$5500 на руки", 5500, None, "USD", False, None),
    ("dreamoffer", "от 150к RUB", 150000, None, "RUB", None, None),
    ("dreamoffer", "916100-1145100 DKK", 916100, 1145100, "DKK", None, None),
    ("dreamoffer", "7000-15000 PLN per month", 7000, 15000, "PLN", None, "month"),
    ("dreamoffer", "83000 CHF", 83000, None, "CHF", None, None),
    # 4,8 миллиарда крон — это ≈$450 млн, и период площадка не назвала вовсе.
    # Настоящая вилка тут потеряна безвозвратно (479 225–5 180? 4 792 255?), и
    # выдавать за неё сумму, которую невозможно заработать, нельзя. Ожидание
    # изменено 06.08.2026 вместе с careered-миллиардами: болезнь одна и та же.
    ("dreamoffer", "4792255180 NOK", None, None, "NOK", None, None),

    # ── shadowhint ────────────────────────────────────────────────────────────
    ("shadowhint", "от 50 000 до 150 000 ₽", 50000, 150000, "RUB", None, None),
    ("shadowhint", "от 300 000₽ до 400 000₽", 300000, 400000, "RUB", None, None),
    ("shadowhint", "от 400 000 до 500 000 ₽ net (на руки)", 400000, 500000, "RUB", False, None),
    ("shadowhint", "от $3500 net (USD/USDT)", 3500, None, "USD", False, None),
    ("shadowhint", "$400", 400, None, "USD", None, None),
    ("shadowhint", "от 300000 ₽ за месяц", 300000, None, "RUB", None, "month"),
    ("shadowhint", "4,000 USD", 4000, None, "USD", None, None),
    ("shadowhint", "от 127 500 ₽", 127500, None, "RUB", None, None),
    ("shadowhint", "350000 - 450000 ₽", 350000, 450000, "RUB", None, None),
    ("shadowhint", "$4 500–6 500 net в месяц", 4500, 6500, "USD", False, "month"),
    ("shadowhint", "$5 000–8 000 в месяц", 5000, 8000, "USD", None, "month"),
    ("shadowhint", "от $3 500 до $5 500", 3500, 5500, "USD", None, None),
    ("shadowhint", "от 350000 ₽", 350000, None, "RUB", None, None),
    ("shadowhint", "$30–40/час для удалённых сотрудников", 30, 40, "USD", None, "hour"),
    ("shadowhint", "Middle+ от 150 000 ₽", 150000, None, "RUB", None, None),
    ("shadowhint", "до 350 000 ₽", None, 350000, "RUB", None, None),
    ("shadowhint", "$97,600 - $139,000 / year", 97600, 139000, "USD", None, "year"),
    ("shadowhint", "$181,000 - $226,000 / year (£110,416 - £138,020 / year)", 181000, 226000, "USD", None, "year"),
    ("shadowhint", "от 258 000 ₽ /на руки", 258000, None, "RUB", False, None),
    ("shadowhint", "ЗП: от 150 до 200 тыс руб", 150000, 200000, "RUB", None, None),

    # ── wantapply ─────────────────────────────────────────────────────────────
    ("wantapply", "€5,300 - €8,000", 5300, 8000, "EUR", None, None),
    ("wantapply", "€64,080 - €80,100", 64080, 80100, "EUR", None, None),
    ("wantapply", "1000$", 1000, None, "USD", None, None),
    ("wantapply", "2500-3200$", 2500, 3200, "USD", None, None),
    ("wantapply", "4000$-6000$", 4000, 6000, "USD", None, None),
    ("wantapply", "£90,000 – £120,000", 90000, 120000, "GBP", None, None),
    ("wantapply", "$80k–$120k", 80000, 120000, "USD", None, None),
    ("wantapply", "2000$ - 4000$", 2000, 4000, "USD", None, None),
    ("wantapply", "€73,000–€93,000", 73000, 93000, "EUR", None, None),
    ("wantapply", "$1000-$3000", 1000, 3000, "USD", None, None),
    ("wantapply", "3000-4000$ gross", 3000, 4000, "USD", True, None),
    ("wantapply", "19.800K – 24.000 PLN", 19800, 24000, "PLN", None, None),
    ("wantapply", "5000€ - 6500€", 5000, 6500, "EUR", None, None),
    ("wantapply", "60000€ - 90 000€", 60000, 90000, "EUR", None, None),
    ("wantapply", "5000€", 5000, None, "EUR", None, None),
    ("wantapply", "$4000–$7000", 4000, 7000, "USD", None, None),
    ("wantapply",
     "от 350 000 до 400 000 ₽ (компания готова рассматривать пожелания кандидатов по зп, ориентировочно $4,000 - $5,000 net)",
     350000, 400000, "RUB", False, None),
    ("wantapply", "$4,000–8,000", 4000, 8000, "USD", None, None),
    ("wantapply", "4000$-6000$ net", 4000, 6000, "USD", False, None),
    ("wantapply", "$1500", 1500, None, "USD", None, None),
    # Две вилки в одной строке: договор найма и B2B — два разных предложения
    # одной вакансии. В поля идёт первая (основная форма занятости), вторая — в
    # salary_note. Период у первой НЕ назван: «/hour» относится ко второй, и
    # раньше он утекал на первую, превращая 16 000–20 000 PLN в почасовую.
    ("wantapply", "UiP: 16000 - 20000 PLN gross; B2B: 116-142 PLN/hour+VAT", 16000, 20000, "PLN", True, None),
    ("wantapply", "$4,000 - $6,000", 4000, 6000, "USD", None, None),
    ("wantapply", "$96,000 - $180,000", 96000, 180000, "USD", None, None),
    ("wantapply", "€35,000 – €50,000", 35000, 50000, "EUR", None, None),
    # У площадки опечатка в верхней границе («€53,75» вместо «€53,750»). Верх
    # ниже низа — это невозможно, значит верить верхней границе нельзя. И чинить
    # её догадкой тоже нельзя: 53,75 могло быть и 53 750, и 537 500. Отдаём одну
    # границу, факт — в salary_note. Ожидание изменено 06.08.2026: раньше здесь
    # стояло 5375, то есть в колонку денег уезжала «вилка 43 000 – 5 375 EUR».
    ("wantapply", "€43,000 – €53,75", 43000, None, "EUR", None, None),
    ("wantapply", "100 000 £/€", 100000, None, "GBP", None, None),
    ("wantapply", "1 000 – 3 000€", 1000, 3000, "EUR", None, None),
    ("wantapply", "2-3K$", 2000, 3000, "USD", None, None),
    ("wantapply", "up to $4000 gross", None, 4000, "USD", True, None),
    ("wantapply", "7–50$ / hour", 7, 50, "USD", None, "hour"),
    ("wantapply", "up to 4500$", None, 4500, "USD", None, None),
    ("wantapply", "500 - 1000$", 500, 1000, "USD", None, None),
    ("wantapply", "43,000€ - 69,000€", 43000, 69000, "EUR", None, None),
    ("wantapply", "$5,000 - 12,000", 5000, 12000, "USD", None, None),
    ("wantapply", "$1600-2400", 1600, 2400, "USD", None, None),
    ("wantapply", "$100,000 and up", 100000, None, "USD", None, None),
    ("wantapply", "4000€ - 5500€ (B2B )", 4000, 5500, "EUR", None, None),
    ("wantapply", "2800$ – 3200$", 2800, 3200, "USD", None, None),
    ("wantapply", "£50,000 - £70,000 / year", 50000, 70000, "GBP", None, "year"),
    ("wantapply", "€50,000 - €80,000 / year", 50000, 80000, "EUR", None, "year"),
    ("wantapply", "8,000-12,500 €", 8000, 12500, "EUR", None, None),
    ("wantapply", "$3000–6000 gross", 3000, 6000, "USD", True, None),
    ("wantapply", "20-30$ / hour", 20, 30, "USD", None, "hour"),
    ("wantapply", "up to 5000$ gross", None, 5000, "USD", True, None),
    ("wantapply", "$20–30 / час", 20, 30, "USD", None, "hour"),
    ("wantapply", "100 000-150 000$", 100000, 150000, "USD", None, None),
    ("wantapply", "5000-6000€", 5000, 6000, "EUR", None, None),
    ("wantapply", "$90k-150k/year + stock options", 90000, 150000, "USD", None, "year"),
    ("wantapply", "от $4,500 до $6,500 net", 4500, 6500, "USD", False, None),
    ("wantapply", "400-450K ₽", 400000, 450000, "RUB", None, None),
    ("wantapply", "110 000$", 110000, None, "USD", None, None),
    ("wantapply", "$90K - $140K", 90000, 140000, "USD", None, None),
    ("wantapply", "80 000$– 120 000$", 80000, 120000, "USD", None, None),
    ("wantapply", "25$ per hour", 25, None, "USD", None, "hour"),
    ("wantapply", "20-25$ per hour gross", 20, 25, "USD", True, "hour"),
    ("wantapply", "£90 000 - £110 000", 90000, 110000, "GBP", None, None),
    ("wantapply", "65,000 - 80,000€", 65000, 80000, "EUR", None, None),
    ("wantapply", "от $14,150 до $20,800 at month", 14150, 20800, "USD", None, "month"),
    ("wantapply", "$5,000 - $6000", 5000, 6000, "USD", None, None),
    ("wantapply", "$48–90k / year", 48000, 90000, "USD", None, "year"),
    ("wantapply", "$48 000 -90 000", 48000, 90000, "USD", None, None),
    ("wantapply", "$81 000 – $150 000 / year", 81000, 150000, "USD", None, "year"),
    ("wantapply", "6000 — 8000 USD", 6000, 8000, "USD", None, None),
    ("wantapply", "$4000 - 5000 gross", 4000, 5000, "USD", True, None),
    ("wantapply", "$107,500- $165,000", 107500, 165000, "USD", None, None),

    # ── telegram-каналы: самый грязный вход — эмодзи, анкеты, нулевая ширина ──
    ("tg", "💰 Ожидания по зарплате: договорная / от $15/час", 15, None, "USD", None, "hour"),
    ("tg", "Вилка: от 1600$/мес", 1600, None, "USD", None, "month"),
    ("tg", "от 2000р/час или $25/hour", 2000, None, "RUB", None, "hour"),
    ("tg", "— Зарплату от 2500€+ (и выше, зависит от твоего уровня) 💸", 2500, None, "EUR", None, None),
    ("tg", "• Конкурентную зарплату (2000- 3000 € net)", 2000, 3000, "EUR", False, None),
    ("tg", "- ЗП: ~$100,000 – $140,000 в год (гросс).", 100000, 140000, "USD", True, "year"),
    ("tg", "Rate: $35/h", 35, None, "USD", None, "hour"),
    ("tg", "Вилка по ЗП: от 5000$", 5000, None, "USD", None, None),
    ("tg", "✔️ Salary: 4500 EUR net", 4500, None, "EUR", False, None),
    ("tg", "• От €10 000 нетто.", 10000, None, "EUR", False, None),
    ("tg", "💰 €65,000–€75,000 gross annually", 65000, 75000, "EUR", True, "year"),
    ("tg", "Зарплата: 1400€ в месяц", 1400, None, "EUR", None, "month"),
    ("tg", "💰 До $6,000 gross", None, 6000, "USD", True, None),
    ("tg", "💰 3 000–3 500 USD", 3000, 3500, "USD", None, None),
    ("tg", "Salary: €2,000–2,500/month", 2000, 2500, "EUR", None, "month"),
    ("tg", "💰 Expected salary: from $3200", 3200, None, "USD", None, None),
    ("tg", "Зарплата: 81,000 — 102,000 EUR / год", 81000, 102000, "EUR", None, "year"),
    ("tg", "📌 Ожидания: от 1500 $", 1500, None, "USD", None, None),
    ("tg", "💰 Зарплата: до $6000 gross", None, 6000, "USD", True, None),
    ("tg", "от 500 000 ₽/‍мес до налогов", 500000, None, "RUB", True, "month"),
    ("tg", "от 300 000 ₽/‍мес на руки", 300000, None, "RUB", False, "month"),
    ("tg", "from 5 300 €/‍month net", 5300, None, "EUR", False, "month"),
    ("tg", "Salary Range: $1,000 USD/month Gross", 1000, None, "USD", True, "month"),
    ("tg", "💰 Salary: 4 000$ – 8 000$ gross per month via Deel", 4000, 8000, "USD", True, "month"),
    ("tg", "Salary: $70k - $90k", 70000, 90000, "USD", None, None),
    ("tg", "Salary: $131k - $178k", 131000, 178000, "USD", None, None),
    ("tg", "Вилка: от 180 000 до 230 000 ₽", 180000, 230000, "RUB", None, None),
    ("tg", "💰 Rate: $25–30 gross/hour", 25, 30, "USD", True, "hour"),
    ("tg", "💰2500$ - 3200$", 2500, 3200, "USD", None, None),
    ("tg", "💰ЗП: от 2.000 € (net)", 2000, None, "EUR", False, None),
    ("tg", "💰 Заробітна плата: 800 - 1200 EUR", 800, 1200, "EUR", None, None),
    ("tg", "💰2300€ - 2800€", 2300, 2800, "EUR", None, None),
    ("tg", "Фиксированная ставка: 700$ + KPI после ИС", 700, None, "USD", None, None),
    ("tg", "Оклад: 4000-5000$ (оплата в USDT", 4000, 5000, "USD", None, None),
    ("tg", "ставка 700-1000$, залежить від досвіду", 700, 1000, "USD", None, None),
    ("tg", "Salary 3500 USD gross", 3500, None, "USD", True, None),
    ("tg", "Salary 4500-5500 USD gross", 4500, 5500, "USD", True, None),
    ("tg", "• ЗП: 800$+KPI", 800, None, "USD", None, None),
    ("tg", "💰 from $3,500 gross", 3500, None, "USD", True, None),
    ("tg", "Зарплата: от 3000$", 3000, None, "USD", None, None),
    ("tg", "Оклад: до $4000", None, 4000, "USD", None, None),
    ("tg", "💰 Salary: Up to USD 3,000/month (depending on experience)", None, 3000, "USD", None, "month"),
    ("tg", "💵 $25/h gross", 25, None, "USD", True, "hour"),
    ("tg", "$6400 - 7500 gross", 6400, 7500, "USD", True, None),
    ("tg", "💰 Compensation: $1300–2000 (depending on experience and interview results)", 1300, 2000, "USD", None, None),
    ("tg", "💰 Зарплата: 1500–1800$", 1500, 1800, "USD", None, None),
    ("tg", "ставка: 1000–2000$", 1000, 2000, "USD", None, None),
    ("tg", "salary: from 1500$ gross", 1500, None, "USD", True, None),
    ("tg", "💰 Salary: 300 000₽ – 400 000₽ на руки", 300000, 400000, "RUB", False, None),
    ("tg", "Вилка до 3500$", None, 3500, "USD", None, None),
    ("tg", "Зп-вилка: 2500 EUR", 2500, None, "EUR", None, None),
    ("tg", "🔸Фиксированный оклад до 2500$", None, 2500, "USD", None, None),
    ("tg", "🔹 CRM Specialist (€2000)", 2000, None, "EUR", None, None),
    ("tg", "💰 Зарплатная вилка: 3 500–5 000 EUR.", 3500, 5000, "EUR", None, None),
    ("tg", "💰 4000$ net | 🛡 Є бронювання", 4000, None, "USD", False, None),
    ("tg", "💰 Salary: $2,000–3,000 + % (готовы обсуждать с сильными кандидатами)", 2000, 3000, "USD", None, None),
    ("tg", "💰 3 000–4 000 €", 3000, 4000, "EUR", None, None),
    ("tg", "💰 Ожидания по зп: от 1800$", 1800, None, "USD", None, None),
    ("tg", "Ожидания: от €3000 net, готов обсуждать весь пакет.", 3000, None, "EUR", False, None),
    ("tg", "💰 3 500 EUR NET", 3500, None, "EUR", False, None),
    ("tg", "💰 Salary: €5,000–€7,000 NET + performance-based bonus", 5000, 7000, "EUR", False, None),
    ("tg", "💶 Зарплата: €4,000 gross", 4000, None, "EUR", True, None),
    ("tg", "Salary: $2,500–$3,500 per month.", 2500, 3500, "USD", None, "month"),
    ("tg", "— Compensation: 5,000–7,000 EUR gross", 5000, 7000, "EUR", True, None),
    ("tg", "💰 Зарплата €2 000–3 000 NET", 2000, 3000, "EUR", False, None),
    ("tg", "До 3000$", None, 3000, "USD", None, None),
    ("tg", "Зарплата: 1500 € в месяц", 1500, None, "EUR", None, "month"),
    ("tg", "Salary: €1,300 gross per month", 1300, None, "EUR", True, "month"),
    ("tg", "💰 Salary: USD 3,000–3,500 per month", 3000, 3500, "USD", None, "month"),
    ("tg", "✔️ Salary: € 3,500", 3500, None, "EUR", None, None),
    ("tg", "💰 1800–2200 €", 1800, 2200, "EUR", None, None),
    ("tg", "- ЗП от 1500 eur", 1500, None, "EUR", None, None),
    ("tg", "Зарплата: 1000-1500$ net", 1000, 1500, "USD", False, None),
    ("tg", "💶 Salary: от €3,500", 3500, None, "EUR", None, None),
    ("tg", "💰 Заработную плату 1600–2000 EUR;", 1600, 2000, "EUR", None, None),
    ("tg", "SALARY :  2000$-3000$", 2000, 3000, "USD", None, None),
    ("tg", "📍 Лимассол, Кипр | Офис | €3 000–3 500 net", 3000, 3500, "EUR", False, None),
    ("tg", "💰 Зарплата: $3000/мес.", 3000, None, "USD", None, "month"),
    ("tg", "💰 Зарплата: от $5000/мес.", 5000, None, "USD", None, "month"),
    # Неразрывные пробелы ( ) — не украшение: именно на них спотыкались
    # шаблоны, ждавшие обычный пробел. В корпусе строки лежат ровно как в канале.
    ("tg", "от 1 500 €", 1500, None, "EUR", None, None),
    ("tg", "ЗП: от 70 000 до 104 000 ₽", 70000, 104000, "RUB", None, None),
    ("tg", "▫️Compensation: 5000$ to 7000$", 5000, 7000, "USD", None, None),
    ("tg", "💰 Ожидания: от 2000$", 2000, None, "USD", None, None),
    ("tg", "Оплата: 160 000 – 215 000 ₽", 160000, 215000, "RUB", None, None),
    ("tg", "Salary: $102k - $112k estimated", 102000, 112000, "USD", None, None),
    ("tg", "Salary: $50 000 - $70 000", 50000, 70000, "USD", None, None),
    ("tg", "💰 ЗП: от 5000$ до 7000$", 5000, 7000, "USD", None, None),
    ("tg", "Зарплатная вилка: 3000 EUR - 5000 EUR Gross", 3000, 5000, "EUR", True, None),
    ("tg", "ЗП: 2500$", 2500, None, "USD", None, None),
    ("tg",
     "Warsaw (only), B2B-contract (only), English: from B2, salary range: up to $7500 gross.",
     None, 7500, "USD", True, None),
    ("tg", "Salary: 20$/h gross", 20, None, "USD", True, "hour"),
    ("tg", "* Заработную плату от 1 500 €.", 1500, None, "EUR", None, None),
    ("tg", "💰 Зарплата: 1500–2000 €", 1500, 2000, "EUR", None, None),
    ("tg", "💰 Salary: up to 4000 EUR NET", None, 4000, "EUR", False, None),
    ("tg", "💰 Зарплата: 1 700–2 000 €", 1700, 2000, "EUR", None, None),
    ("tg", "💰 Зарплата: 1 500 €", 1500, None, "EUR", None, None),
    ("tg", "💰 Salary: Up to €7,000 Gross/month", None, 7000, "EUR", True, "month"),
    ("tg", "Salary : 2000-2500€", 2000, 2500, "EUR", None, None),
    ("tg", "• зарплата - 1500-2000 EUR", 1500, 2000, "EUR", None, None),
    ("tg", "💰 Salary: €700–1200/month (depending on experience).", 700, 1200, "EUR", None, "month"),
    ("tg", "$7000- 7500 gross", 7000, 7500, "USD", True, None),
    ("tg", "Зарплата: от 5,500 USD / месяц", 5500, None, "USD", None, "month"),
    ("tg", "Зарплата: до 240,000 RUB / месяц", None, 240000, "RUB", None, "month"),
    ("tg", "💰 Ставка: от  $1700/мес", 1700, None, "USD", None, "month"),
    ("tg", "Ставка: до 24 EUR/час.", None, 24, "EUR", None, "hour"),
    ("tg", "Salary:$200k - $250k", 200000, 250000, "USD", None, None),
    ("tg", "Salary:$86k - $97k estimated", 86000, 97000, "USD", None, None),
    ("tg", "7 500 —‍ 9 000 $/‍month net", 7500, 9000, "USD", False, "month"),
    ("tg", "8 000 —‍ 10 000 $/‍month gross", 8000, 10000, "USD", True, "month"),
    ("tg", "200 000 —‍ 350 000 ₽/‍мес на руки", 200000, 350000, "RUB", False, "month"),
    ("tg", "от 3 500 $/‍мес на руки", 3500, None, "USD", False, "month"),
    ("tg", "от 5 000 до 15 000 $", 5000, 15000, "USD", None, None),
    ("tg", "💰 €2500-€3,500 NET monthly salary", 2500, 3500, "EUR", False, "month"),
    ("tg",
     "Зарплатная вилка: 15–30 $/час (нетто) на этапе приёма системы; далее — фикс за согласованные этапы",
     15, 30, "USD", False, "hour"),
    ("tg", "Вилка: до 8 000 $", None, 8000, "USD", None, None),
    ("tg", "ЗП: от 1700 USD", 1700, None, "USD", None, None),
    ("tg", "ЗП: 7500 - 10 800 USD", 7500, 10800, "USD", None, None),
    ("tg", "ЗП: от $2500, готов обсуждать при интересном проекте", 2500, None, "USD", None, None),
    ("tg", "Зарплата: 240,000 — 270,000 RUB / месяц", 240000, 270000, "RUB", None, "month"),
    ("tg", "Зарплата: 400,000 RUB / месяц", 400000, None, "RUB", None, "month"),
    ("tg", "ЗП — от ~$1,5k до ~$2,5k в зависимости от грейд/занятость/скиллов", 1500, 2500, "USD", None, None),
    ("tg", "- $30 USD/hour", 30, None, "USD", None, "hour"),
    ("tg", "Зарплата: от $1800 gross/мес.", 1800, None, "USD", True, "month"),
    ("tg", "• Salary: 2200-2400$", 2200, 2400, "USD", None, None),
    ("tg", "48300 - 51700 RUR", 48300, 51700, "RUR", None, None),
    ("tg", "150000 - 180000 RUR", 150000, 180000, "RUR", None, None),
    ("tg", "Требуется «Аналитик 1С» (от 200 000 до 220 000 ₽)", 200000, 220000, "RUB", None, None),
    ("tg", "Salary: $4,000–8,000", 4000, 8000, "USD", None, None),
    ("tg", "💰 ЗП: от 1500$ / месяц", 1500, None, "USD", None, "month"),
    ("tg", "Salary: $10000 depending on experience", 10000, None, "USD", None, None),
    ("tg", "Зарплата: от 70 000 RUB до 90 000 RUB (на руки)", 70000, 90000, "RUB", False, None),
    ("tg", "Ожидания по зарплате: от 250 000 руб. / от $3500", 250000, None, "RUB", None, None),
    ("tg", "💸 встречаются вилки $1000–8000+", 1000, 8000, "USD", None, None),
    ("tg", "Ожидания по доходу: от 4000$ до 5000$ 💰", 4000, 5000, "USD", None, None),
    ("tg", "💶 Доход — от 2 000 €", 2000, None, "EUR", None, None),
    ("tg", "💰 Доход €2,000–3,000 net (в зависимости от опыта).", 2000, 3000, "EUR", False, None),
    ("tg", "— Base salary: 800-1300$", 800, 1300, "USD", None, None),
    ("tg", "ЗП: от 3 200 $ в месяц, на руки", 3200, None, "USD", False, "month"),
    ("tg", "— Доход: от 2 000 до 3800€;", 2000, 3800, "EUR", None, None),
    ("tg", "💰 Зарплата: $1 500 (ставка)", 1500, None, "USD", None, None),
    ("tg", "• Конкурентная зарплата €2055–2805 gross + возможности роста", 2055, 2805, "EUR", True, None),
    ("tg", "• Fixed salary starting from $2,000 + KPI bonuses", 2000, None, "USD", None, None),
    ("tg", "$6700 - 7800 before taxes", 6700, 7800, "USD", True, None),
    ("tg", "💰 Middle - $1000 + %", 1000, None, "USD", None, None),
    ("tg", "— оклад от $2,500 и обсуждение долгосрочной мотивации после подтверждения результатов.",
     2500, None, "USD", None, None),
    ("tg", "💰 Salary expectations: from 1500 USD / USDT", 1500, None, "USD", None, None),
    ("tg", "💰 Salary expectations: 3500 EUR net", 3500, None, "EUR", False, None),
    # Та же болезнь, что у «UiP … B2B»: «20$/h» — вторая ставка, а не период
    # первой вилки. Ожидаемый период — None: у 1000–2000$ он не назван.
    ("tg", "Salary expectations: 1000-2000$(according to part or full time). 20$/h hourly rate.",
     1000, 2000, "USD", None, None),
    ("tg", "💸60,000 - 75,000 € в год", 60000, 75000, "EUR", None, "year"),
    ("tg", "💸от 2 500 $ за месяц", 2500, None, "USD", None, "month"),
    ("tg", "💸3400 - 3600 EUR", 3400, 3600, "EUR", None, None),
    ("tg", "💸 €50 000 - €70 000 в год", 50000, 70000, "EUR", None, "year"),
    ("tg", "ЗП: $90,000 - $120,000 в год / $7500 - $10800 месяц", 90000, 120000, "USD", None, "year"),
    ("tg", "Вилка: от 2500$ / мес", 2500, None, "USD", None, "month"),
    ("tg", "💰 Зарплата $1500–1800.", 1500, 1800, "USD", None, None),
    ("tg", "200 000 - 250 000 ₽", 200000, 250000, "RUB", None, None),
    ("tg", "220 000 – 250 000 ₽", 220000, 250000, "RUB", None, None),
    ("tg", "ЗП: от 1 500 до 2 500 $", 1500, 2500, "USD", None, None),
    ("tg", "Зарплатная вилка: 1900-3100 USD на руки", 1900, 3100, "USD", False, None),
    ("tg", "ЗП: 1500-1800$", 1500, 1800, "USD", None, None),
    ("tg", "€3.5K — €5.5K. Remote work.", 3500, 5500, "EUR", None, None),
    ("tg", "$1,5K. Удалённая работа.", 1500, None, "USD", None, None),
    ("tg", "$500. Удалённая работа.", 500, None, "USD", None, None),
    ("tg", "Вилка: 6 500 – 9 000 USD", 6500, 9000, "USD", None, None),
    ("tg", "€2.500 / месяц", 2500, None, "EUR", None, "month"),
    ("tg", "💸£60 000 - 85 000 в год (в зависимости от опыта)", 60000, 85000, "GBP", None, "year"),
    ("tg", "Зарплата: 1,700 — 2,300 USD / месяц", 1700, 2300, "USD", None, "month"),
    ("tg", "от 1 200 до 1 500$", 1200, 1500, "USD", None, None),
    ("tg", "Удалённо, полная занятость, гибкий график. Зарплата $30–40/час для удалённых сотрудников.",
     30, 40, "USD", None, "hour"),

    # ── glassdoor: живых строк в базе нет (Cloudflare), взяты из карточки, на
    #    которой разбор проверяется в test_sources_web ─────────────────────────
    ("glassdoor", "EUR 90K - EUR 130K (Employer provided)", 90000, 130000, "EUR", None, None),
    ("glassdoor", "EUR 90K - EUR 130K", 90000, 130000, "EUR", None, None),

    # ── строки инцидентов 05.08.2026: с них и начался разговор о готовом
    #    решении. Каждая — отдельный баг, найденный глазами в отчёте ───────────
    ("инцидент", "от 3 млн руб", 3000000, None, "RUB", None, None),
    ("инцидент", "CA$ 120 000", 120000, None, "CAD", None, None),
    ("инцидент", "80 000 - 120 000 тңг", 80000, 120000, "KZT", None, None),
    ("инцидент", "1 500 000 тенге", 1500000, None, "KZT", None, None),
    ("инцидент", "от 1,5 млн ₽ в год", 1500000, None, "RUB", None, "year"),
    ("инцидент", "₸ 500 000", 500000, None, "KZT", None, None),
    ("инцидент", "A$ 120 000 - A$ 150 000", 120000, 150000, "AUD", None, None),

    # ── дневные и недельные ставки: живые строки EURES/dreamoffer и телеграма ─
    #    Все до 06.08.2026 приезжали БЕЗ периода. Дневная ставка без периода —
    #    не «чуть менее точно», а прямо наоборот: 1000 EUR читались как месячная
    #    вилка, то есть худшее предложение выдачи вместо одного из лучших.
    ("dreamoffer", "100 RON per day", 100, None, "RON", None, "day"),
    ("dreamoffer", "103 RON per day", 103, None, "RON", None, "day"),
    ("dreamoffer", "1000 RON per day", 1000, None, "RON", None, "day"),
    ("dreamoffer", "2500 RON per day", 2500, None, "RON", None, "day"),
    ("dreamoffer", "120-140 PLN per day", 120, 140, "PLN", None, "day"),
    ("dreamoffer", "1000-1500 PLN per day", 1000, 1500, "PLN", None, "day"),
    ("dreamoffer", "180 RON per week", 180, None, "RON", None, "week"),
    ("dreamoffer", "133 RON per week", 133, None, "RON", None, "week"),
    ("dreamoffer", "45000-55000 RON per week", 45000, 55000, "RON", None, "week"),
    ("dreamoffer", "18000 EUR per week", 18000, None, "EUR", None, "week"),
    # $75 000 в неделю — почти наверняка тоже сломанный пересчёт EURES, но
    # «почти» здесь не считается: порог стоит там, где кончается физика.
    ("dreamoffer", "67900 EUR per week", 67900, None, "EUR", None, "week"),
    ("linkedin", "£400 per day", 400, None, "GBP", None, "day"),
    ("wantapply", "1200PLN/day on B2B", 1200, None, "PLN", None, "day"),
    # Целые предложения из телеграма: недельная ставка названа в самом конце, а
    # в начале стоит «phase2» — цифра внутри слова. Она разбиралась в «2 USD».
    ("tg", "phase2: Once we reach to tech video interview process, we will switch "
           "to salary mode. $150/week.", 150, None, "USD", None, "week"),
    ("tg", "phase3: After a month, once you are fully joined our agency and manage "
           "all processes smoothly, 400$/week.", 400, None, "USD", None, "week"),

    # ── неоднозначные знаки валют ─────────────────────────────────────────────
    #    Живых строк с «kr», «R» и «Rp» в базе нет ни одной — все скандинавские и
    #    южноафриканские вакансии приходят с кодом ISO. Строки ниже собраны по
    #    образцу того, как эти суммы пишут сами площадки; проверяют они правило,
    #    а не выдачу: знак, который носят несколько валют, разрешается контекстом
    #    строки, а без контекста валюта остаётся ПУСТОЙ. Выдуманная валюта хуже
    #    отсутствующей — она печатается тем же уверенным тоном, что настоящая.
    ("образец", "Salary: 45 000 kr per month, Stockholm, Sweden", 45000, None, "SEK", None, "month"),
    ("образец", "Location: Oslo, Norway  Salary: 785000 kr", 785000, None, "NOK", None, None),
    ("образец", "København, Danmark — 55.000 kr", 55000, None, "DKK", None, None),
    # Контекста нет: «kr» может быть шведской, норвежской, датской или исландской.
    # Сумма настоящая, валюта — нет.
    ("образец", "45 000 kr в месяц", 45000, None, None, None, "month"),
    ("образец", "R 45 000 – R 60 000 per month, Johannesburg, South Africa",
     45000, 60000, "ZAR", None, "month"),
    ("образец", "R900 000 per annum, Cape Town, South Africa", 900000, None, "ZAR", None, "year"),
    # Голая R без южноафриканского контекста рандом в ранды не превращает: сумма
    # есть, валюты нет.
    ("образец", "R 900 000 per annum", 900000, None, None, None, "year"),
    # А это вообще не деньги — живая строка из описания вакансии Booz Allen.
    # До 06.08.2026 разбор доставал отсюда «вилку» на 242 440.
    ("dreamoffer", "Job Number: R0242440DevOps Platform Engineer", None, None, None, None, None),
    ("образец", "Rp 15.000.000 - Rp 25.000.000 per bulan", 15000000, 25000000, "IDR", None, None),
    ("образец", "¥8,000,000 - ¥12,000,000 per year, Tokyo, Japan",
     8000000, 12000000, "JPY", None, "year"),
    ("образец", "¥300,000 - ¥500,000 per month (Shanghai, China)",
     300000, 500000, "CNY", None, "month"),
    # ¥ носят и иена, и юань, и разница между ними — двадцать раз. До 06.08.2026
    # здесь молча выбиралась иена, потому что японских вилок в выдаче больше.
    ("образец", "¥5,000,000 per year", 5000000, None, None, None, "year"),

    # ── опечатки площадок: числа настоящие, а вилка — нет ─────────────────────
    #    Разбор читает такие строки без единой ошибки. Врёт источник.
    ("geekjob", "150000250K ₽", None, None, "RUB", None, None),
    ("dreamoffer", "28000-35000 EUR per day", None, None, "EUR", None, "day"),
    ("dreamoffer", "26000 EUR per day", None, None, "EUR", None, "day"),
    ("dreamoffer", "5255056 RON per day", None, None, "RON", None, "day"),
    # Нижняя граница правдоподобна ($9,6 тыс. в день), верхняя нет — отдаётся то,
    # чему можно верить, а не всё или ничего.
    ("dreamoffer", "44131-507030 RON per day", 44131, None, "RON", None, "day"),
    ("dreamoffer", "110000 EUR per week", None, None, "EUR", None, "week"),
    ("dreamoffer", "130000-170000 USD per week", None, None, "USD", None, "week"),
    ("dreamoffer", "32465178 CHF per week", None, None, "CHF", None, "week"),

    # ── справочная вилка вместо предложения ───────────────────────────────────
    #    «Ср. рын. зп» пишет один и тот же телеграм-канал во всех своих постах:
    #    работодатель денег не назвал, а числа взяты из чужого обзора рынка.
    ("tg", "**Senior Java-разработчик в Росгосстрах** 💰По итогам собеседования "
           "(ср. рын. зп 300 000 ₽ – 370 000 ₽)", None, None, None, None, None),
    ("tg", "ср. рын. зп 330 000 ₽ – 400 000 ₽", None, None, None, None, None),
    ("tg", "ср. рын. зп 360 000 ₽ – 430 000 ₽", None, None, None, None, None),
    # Справка рядом с НАСТОЯЩЕЙ вилкой: выбрасывается только справка. Иначе
    # лечение оказалось бы хуже болезни — терялись бы обе.
    ("образец", "от 350 000 ₽ (ср. рын. зп 300 000 – 370 000 ₽)", 350000, None, "RUB", None, None),

    # ── прочее: строки, которые парсер обязан НЕ принять за вилку ─────────────
    ("hh", "з/п не указана", None, None, None, None, None),
    ("habr", "зарплата не указана", None, None, None, None, None),
    ("hh", "Опубликовано в 2026", None, None, None, None, None),
    ("hh", "от 3 до 5 лет опыта", None, None, None, None, None),
    ("rabota",
     'Вакансия Backend-разработчик в Казани с зарплатой 150 000 руб, работа в компании ООО "ТОП-АЙТИ"',
     150000, None, "RUB", None, None),
    ("hnhiring", "In-office 4 days | $175,000 - $300,000", 175000, 300000, "USD", None, None),

    # ── множитель у ВЕРХНЕЙ границы, разряды у нижней (10.08.2026) ────────────
    #    Разворот вилки с одним множителем откусывал от «500 000» хвост «000»
    #    и умножал его: получалось «5 000 – 1 000 000». Занижение в сто раз на
    #    самой обычной форме записи, и корпус его не ловил — здесь не было ни
    #    одной вилки, где нижняя граница записана с разрядами.
    ("образец", "от 500 000 до 1 млн руб", 500000, 1000000, "RUB", None, None),
    ("образец", "зарплата 150 000 - 250 тыс руб", 150000, 250000, "RUB", None, None),
    ("образец", "от 800 000 до 1,2 млн ₽", 800000, 1200000, "RUB", None, None),

    # ── число графика вплотную к вилке (10.08.2026) ───────────────────────────
    #    «5/2,» перед деньгами склеивалось в одно число 2 250 000: класс числа
    #    принимал запятую с пробелом за разделитель разрядов.
    ("образец", "график 5/2, 250-350 тыс руб", 250000, 350000, "RUB", None, None),

    # ── срок опыта рядом с настоящей вилкой (10.08.2026) ──────────────────────
    #    Запрет «от N лет» стоял только на вилке, а односторонняя форма и
    #    последняя ветка разбора его не знали: «Опыт от 3 лет, зарплата
    #    300 000 ₽» отдавалось как «от 3 ₽» — худшая строка отчёта.
    ("tg", "Опыт от 3 лет, зарплата 300 000 ₽ на руки", 300000, None, "RUB", False, None),

    # ── .NET — это стек, а не «на руки» (10.08.2026) ──────────────────────────
    #    Точка даёт границу слова, и `\bnet\b` ловил её у 12 вакансий из 42
    #    с .NET в заголовке: выдуманная скидка в 13 % там, где её не было.
    ("tg", ".NET Backend, зарплата 300 000 ₽, стек .NET 8", 300000, None, "RUB", None, None),

    # ── период после постороннего числа (10.08.2026) ──────────────────────────
    #    Область поиска периода обрывало ЛЮБОЕ число за вилкой, а график и срок
    #    опыта стоят в тех же строках. Период терялся у 419 вакансий с вилкой
    #    из 3627, и payband выбрасывал их из медианы целиком.
    ("образец", "200 000 - 300 000 руб. на руки, офис 5/2, в месяц",
     200000, 300000, "RUB", False, "month"),
    ("образец", "2 500 – 3 500 EUR, опыт 5+ лет, в месяц", 2500, 3500, "EUR", None, "month"),
)
