const SUPPORTED_LANGS = ["en", "ru"];
const LANGUAGE_STORAGE_KEY = "jorqen.language";
const THEME_STORAGE_KEY = "jorqen.theme";
const SUPPORTED_THEMES = ["light", "dark"];

const CONTENT = {
  en: {
    meta: {
      title: "Matvey Sizov | Senior Backend / Software Engineer",
      description:
        "Matvey Sizov — Senior Backend / Software Engineer. Go, distributed systems, high-load services, and production reliability.",
    },
    brand: "Matvey Sizov",
    nav: {
      resume: "Resume",
      experience: "Experience",
      education: "Education",
      strengths: "Strengths",
      skills: "Stack",
    },
    langSwitcherLabel: "Language switcher",
    theme: {
      toDark: "Dark theme",
      toLight: "Light theme",
      switcherLabel: "Theme switcher",
    },
    hero: {
      kicker: "Senior Backend / Software Engineer",
      name: "Matvey Sizov",
      role: "4+ years in backend and distributed systems | from EV telematics to service-mesh infrastructure",
      summary:
        "I design and ship production backend systems with clear ownership: architecture, delivery, observability, and reliability. My strongest area is high-impact work under ambiguity, where teams need both technical depth and execution speed.",
      links: {
        linkedin: "LinkedIn",
        github: "GitHub",
        telegram: "Telegram @jorqen",
      },
      facts: [
        {
          icon: "assets/icons/location.svg",
          label: "Location",
          value: "Moscow, Russia",
        },
        {
          icon: "assets/icons/briefcase.svg",
          label: "Experience",
          value: "4+ years commercial",
        },
        {
          icon: "assets/icons/star.svg",
          label: "Current focus",
          value: "High-load Go services",
        },
        {
          icon: "assets/icons/layers.svg",
          label: "Readiness",
          value: "Remote / Relocation",
        },
      ],
    },
    resume: {
      title: "Download resume",
      subtitle: "The resume language always follows the current website language.",
      note: "Formats: PDF, DOCX, TXT.",
      labels: {
        pdf: "Print and share",
        docx: "Editable source",
        txt: "Plain text",
      },
      files: {
        pdf: "resume/en/resume.pdf",
        docx: "resume/en/resume.docx",
        txt: "resume/en/resume.txt",
      },
      downloadNames: {
        pdf: "Matvey_Sizov_Resume_EN.pdf",
        docx: "Matvey_Sizov_Resume_EN.docx",
        txt: "Matvey_Sizov_Resume_EN.txt",
      },
    },
    experience: {
      title: "Experience",
      subtitle:
        "Backend and distributed systems roles with production ownership, delivery impact, and reliability focus.",
      companySiteLabel: "Company site",
      items: [
        {
          company: "ATOM",
          companyIcon: "assets/companies/atom.svg",
          companyUrl: "https://atom.auto",
          role: "Senior Software Engineer",
          period: "February 2025 - Present",
          location: "Moscow, Russia",
          intro:
            "Atom develops an electric vehicle platform. I worked in the communications and telemetry area on backend services responsible for vehicle-to-cloud connectivity and operational reliability.",
          bullets: [
            "Designed and co-developed an mTLS-secured MQTT broker for persistent vehicle-to-cloud communication, simplifying an initially overengineered concept into a cleaner broker-centered architecture with Redis-backed state recovery.",
            "Helped clarify ambiguous and partially unrealistic requirements with engineering and product leadership, turning the project into a releasable first version and influencing the final architecture.",
            "Built production observability from scratch after the team lost dedicated DevOps support: metrics, dashboards, alerts, logs, traces, latency and throughput monitoring, and business health signals.",
            "Took ownership of CI/CD and deployment setup, including manifests/charts and delivery automation for a high-load backend service.",
            "Improved security and reliability by enforcing certificate-based client authentication and topic-level access control for trusted connectivity.",
            "Supported a service handling 70K requests per second with p99 latency below ~50 ms.",
          ],
          stack: [
            "Go",
            "MQTT",
            "Mochi MQTT",
            "Redis",
            "PostgreSQL",
            "Kafka",
            "gRPC",
            "mTLS",
            "Prometheus",
            "Grafana",
            "Loki",
            "Sentry",
            "Docker",
            "Kubernetes",
            "CI/CD",
          ],
        },
        {
          company: "SBERTECH",
          companyIcon: "assets/companies/sbertech.svg",
          companyUrl: "https://sbertech.ru",
          role: "Software Engineer, Service Mesh & Platform Infrastructure",
          period: "January 2024 - February 2025",
          location: "Moscow, Russia",
          intro:
            "SberTech develops Platform V, a large enterprise platform. I worked on a heavily customized Istio fork and adjacent infrastructure components used for workload control, policies, and platform integration.",
          bullets: [
            "Restored broken automated testing for a heavily customized Istio fork, bringing unit tests back into regular engineering workflow and increasing unit-test coverage to 80%.",
            "Designed and built a Go-based integration testing framework that provisioned isolated Kubernetes resources and enabled scalable automated testing with CI integration and Allure reporting.",
            "Repaired CI pipelines and shifted defect detection earlier in the development cycle, improving release safety for infrastructure changes.",
            "Mentored interns from School 21 on the testing framework and automation workflow, helping them convert QA scenarios into scalable automated tests.",
            "Independently designed and implemented a custom Kubernetes resource for managing control-plane and data-plane relationships in Istio.",
          ],
          stack: [
            "Go",
            "Kubernetes",
            "Istio",
            "controllers",
            "REST API",
            "gRPC",
            "PostgreSQL",
            "CI/CD",
            "Allure",
            "Bitbucket",
          ],
        },
        {
          company: "MAGNUS TECH",
          companyIcon: "assets/companies/magnus.svg",
          companyUrl: "https://magnustech.com",
          role: "Backend Engineer",
          period: "March 2023 - January 2024",
          location: "Russia",
          intro:
            "Magnus Tech is a custom software development company. I worked on a retail pricing platform for Bristol that aggregated in-store pricing data, photos, and ML outputs into a unified operational system.",
          bullets: [
            "Built backend services and internal tooling for a pricing platform used to digitize in-store price-control workflows.",
            "Helped structure data coming from store employees, product systems, and ML analysis into a central backend for monitoring, validation, and operational decision-making.",
            "Contributed to product delivery from active development through release over an approximately one-year engagement.",
          ],
          stack: ["Go", "MinIO", "Redis", "PostgreSQL", "Kafka", "REST", "gRPC", "Prometheus", "CI/CD"],
        },
        {
          company: "BLACKWALL",
          companyIcon: "assets/companies/blackwall.svg",
          companyUrl: "",
          role: "Backend Engineer",
          period: "September 2022 - March 2023",
          location: "Russia",
          intro: "BlackWall developed crypto exchange and P2P trading products.",
          bullets: [
            "Implemented core backend flows for P2P crypto exchange, including exchange-rate logic and integration between trading-related product components.",
            "Worked closely with another backend engineer to deliver end-to-end exchange functionality in a fast-moving product environment.",
          ],
          stack: ["Go", "Redis", "PostgreSQL", "RabbitMQ", "REST", "gRPC", "Prometheus", "CI/CD"],
        },
        {
          company: "LUKYANOV TECH",
          companyIcon: "assets/companies/lukyanov.svg",
          companyUrl: "https://lukyanov.tech",
          role: "Part-Time Mentor / Mock Interviewer",
          period: "January 2024 - Present",
          location: "Remote",
          intro: "Part-time mentoring and mock interview practice alongside core engineering roles.",
          bullets: [
            "Conduct mock interviews, mentor students, and provide structured feedback on backend interview performance, technical communication, and problem-solving.",
          ],
          stack: ["Mentoring", "Mock interviews", "Technical communication"],
        },
        {
          company: "Kaluga Power Sale Company",
          companyIcon: "assets/companies/kaluga.svg",
          companyUrl: "https://kskkaluga.ru",
          role: "Backend Engineer",
          period: "October 2021 - March 2022",
          location: "Russia",
          intro: "Early backend role in utility payments and account management systems.",
          bullets: [
            "Built backend functionality for a customer account and admin system related to utility payments and account management.",
          ],
          stack: ["Backend", "Databases", "Admin systems", "Payments"],
        },
        {
          company: "Center for Regional Management of Lipetsk Oblast",
          companyIcon: "assets/companies/lipetsk.svg",
          companyUrl: "",
          role: "Java Developer",
          period: "June 2021 - November 2021",
          location: "Russia",
          intro: "Contributed to public-sector digital and monitoring systems.",
          bullets: [
            "Contributed to public-sector digital products, including service and operational tracking systems.",
          ],
          stack: ["Java", "Backend"],
        },
      ],
    },
    education: {
      title: "Education",
      subtitle: "Formal education and academic track.",
      items: [
        {
          institution: "Voronezh State Technical University",
          degree: "B.S. in Intelligent Automated Systems, part-time",
          period: "Expected 2030",
        },
        {
          institution: "Voronezh State Technical University",
          degree: "Secondary Vocational Diploma in Information Technology and Programming (with honors)",
          period: "2021 - 2025",
        },
      ],
    },
    strengths: {
      title: "What I bring",
      subtitle: "Senior-level behaviors I consistently demonstrate in delivery.",
      cards: [
        {
          title: "Clarity under ambiguity",
          body: "I can convert vague requirements into executable architecture and aligned implementation plans.",
        },
        {
          title: "Production ownership",
          body: "I treat reliability, observability, deployment, and incident response as first-class engineering concerns.",
        },
        {
          title: "High-load systems",
          body: "Comfortable with distributed, message-driven backends and low-latency service paths.",
        },
        {
          title: "Team leverage",
          body: "I mentor engineers and build tooling that multiplies team velocity and quality.",
        },
      ],
    },
    skills: {
      title: "Core stack",
      subtitle: "Technologies I actively use in production backend work.",
      groups: [
        {
          title: "Languages",
          items: ["Go", "Java", "Python", "SQL"],
        },
        {
          title: "Data and Messaging",
          items: ["PostgreSQL", "Redis", "Kafka", "RabbitMQ", "NATS", "MQTT"],
        },
        {
          title: "Platform",
          items: ["Docker", "Kubernetes", "Helm", "Istio", "Linux", "Git", "CI/CD"],
        },
        {
          title: "Observability",
          items: ["Prometheus", "Grafana", "Loki", "Sentry", "Tracing", "Alerting"],
        },
      ],
    },
    preferences: {
      title: "Current focus",
      items: [
        "Target role: Senior Backend / Software Engineer.",
        "Open to remote, hybrid, and relocation opportunities.",
        "Priority: products where backend quality directly impacts business outcomes.",
        "Actively improving spoken English for international teams and interviews.",
      ],
    },
    footer: "© {year} Matvey Sizov. Personal website for recruiters and hiring managers.",
  },

  ru: {
    meta: {
      title: "Матвей Сизов | Senior Backend / Software Engineer",
      description:
        "Матвей Сизов — Senior Backend / Software Engineer. Go, распределенные системы, high-load сервисы и надежность production.",
    },
    brand: "Матвей Сизов",
    nav: {
      resume: "Резюме",
      experience: "Опыт",
      education: "Образование",
      strengths: "Сильные стороны",
      skills: "Стек",
    },
    langSwitcherLabel: "Переключение языка",
    theme: {
      toDark: "Тёмная тема",
      toLight: "Светлая тема",
      switcherLabel: "Переключение темы",
    },
    hero: {
      kicker: "Senior Backend / Software Engineer",
      name: "Матвей Сизов",
      role: "4+ года в backend и distributed systems | от EV-телеметрии до service-mesh инфраструктуры",
      summary:
        "Проектирую и довожу до production backend-системы с полным ownership: архитектура, поставка, observability и надежность. Сильнее всего проявляюсь в задачах с высокой неопределенностью, где нужен и технический уровень, и скорость реализации.",
      links: {
        linkedin: "LinkedIn",
        github: "GitHub",
        telegram: "Telegram @jorqen",
      },
      facts: [
        {
          icon: "assets/icons/location.svg",
          label: "Локация",
          value: "Москва, Россия",
        },
        {
          icon: "assets/icons/briefcase.svg",
          label: "Опыт",
          value: "4+ года коммерчески",
        },
        {
          icon: "assets/icons/star.svg",
          label: "Фокус",
          value: "High-load Go сервисы",
        },
        {
          icon: "assets/icons/layers.svg",
          label: "Формат",
          value: "Удаленно / релокация",
        },
      ],
    },
    resume: {
      title: "Скачать резюме",
      subtitle: "Язык файла всегда совпадает с текущим языком сайта.",
      note: "Форматы: PDF, DOCX, TXT.",
      labels: {
        pdf: "Для отправки и печати",
        docx: "Редактируемый источник",
        txt: "Текстовая версия",
      },
      files: {
        pdf: "resume/ru/resume.pdf",
        docx: "resume/ru/resume.docx",
        txt: "resume/ru/resume.txt",
      },
      downloadNames: {
        pdf: "Matvey_Sizov_Resume_RU.pdf",
        docx: "Matvey_Sizov_Resume_RU.docx",
        txt: "Matvey_Sizov_Resume_RU.txt",
      },
    },
    experience: {
      title: "Опыт",
      subtitle:
        "Роли, где я отвечал за production-результат, а не только за реализацию кода. В каждой карточке есть ссылка на компанию.",
      companySiteLabel: "Сайт компании",
      items: [
        {
          company: "Атом",
          companyIcon: "assets/companies/atom.svg",
          companyUrl: "https://atom.auto",
          role: "Senior Software Engineer (Communication & Telemetry)",
          period: "фев 2025 - настоящее время",
          location: "Москва, Россия",
          intro:
            "«Атом» развивает экосистему электромобиля. Я отвечаю за безопасные и постоянно доступные сервисы связи автомобиля с облаком.",
          bullets: [
            "Спроектировал и соразработал mTLS-защищенный MQTT-брокер для постоянной car-to-cloud коммуникации.",
            "Помог переработать нечеткие требования вместе с инженерным и продуктовым руководством, чтобы довести решение до релиза.",
            "Обеспечил профиль сервиса около 70K req/s при p99 latency ниже 50 мс.",
            "Собрал observability с нуля: метрики, логи, трассировки, дашборды и алерты.",
            "Взял ownership за CI/CD и деплой после ухода DevOps-поддержки.",
          ],
          stack: [
            "Go",
            "MQTT",
            "Redis",
            "PostgreSQL",
            "gRPC",
            "mTLS",
            "Prometheus",
            "Grafana",
            "Kubernetes",
          ],
        },
        {
          company: "Сбертех",
          companyIcon: "assets/companies/sbertech.svg",
          companyUrl: "https://sbertech.ru",
          role: "Software Engineer, Service Mesh & Platform Infrastructure",
          period: "янв 2024 - фев 2025",
          location: "Москва, Россия",
          intro:
            "Работал с сильно модифицированным форком Istio в Platform V.",
          bullets: [
            "Восстановил сломанное автотестирование и повысил покрытие юнит-тестами до 80%.",
            "Разработал Go-фреймворк интеграционных тестов для изолированных Kubernetes-окружений.",
            "Сместил поиск дефектов влево за счет стабилизации CI и тестового контура.",
            "Наставлял стажеров и масштабировал автоматизацию через переиспользуемые паттерны фреймворка.",
          ],
          stack: ["Go", "Kubernetes", "Istio", "gRPC", "REST", "PostgreSQL", "Allure", "CI/CD"],
        },
        {
          company: "Magnus Tech",
          companyIcon: "assets/companies/magnus.svg",
          companyUrl: "https://magnustech.com",
          role: "Backend Engineer",
          period: "мар 2023 - янв 2024",
          location: "Россия",
          intro:
            "Разрабатывал backend платформы контроля цен для сети «Бристоль».",
          bullets: [
            "Реализовал backend-потоки сбора и нормализации данных по ценникам.",
            "Объединил данные сотрудников, продуктовых систем и ML-аналитики в единый операционный контур.",
            "Довел набор функциональности от разработки до production-запуска.",
          ],
          stack: ["Go", "PostgreSQL", "Redis", "Kafka", "MinIO", "gRPC", "Prometheus"],
        },
        {
          company: "Exnode",
          companyIcon: "assets/companies/exnode.svg",
          companyUrl: "https://exnode.ru",
          role: "Backend Engineer",
          period: "сен 2022 - мар 2023",
          location: "Россия",
          intro: "Разрабатывал backend для криптобиржи и P2P-обмена.",
          bullets: [
            "Реализовал ключевые потоки P2P-обмена и логику управления курсами.",
            "Работал в небольшой команде и поставлял end-to-end функциональность обмена.",
            "После критического инцидента с курсами внедрил более строгие проверки и безопасный rollout-подход.",
          ],
          stack: ["Go", "PostgreSQL", "Redis", "RabbitMQ", "REST", "gRPC"],
        },
        {
          company: "Калужская сбытовая компания",
          companyIcon: "assets/companies/kaluga.svg",
          companyUrl: "https://kskkaluga.ru",
          role: "Backend Engineer",
          period: "окт 2021 - мар 2022",
          location: "Россия",
          intro: "Разработал backend личного кабинета и админ-системы для биллинга.",
          bullets: [
            "Реализовал функции управления аккаунтом и оплатой.",
            "Построил админ-контур для операций с клиентами и статусами начислений.",
          ],
          stack: ["Backend", "Базы данных", "Админ-системы"],
        },
        {
          company: "Центр управления регионом Липецкой области",
          companyIcon: "assets/companies/lipetsk.svg",
          companyUrl: "",
          role: "Java Developer",
          period: "июн 2021 - ноя 2021",
          location: "Россия",
          intro: "Участвовал в разработке цифровых сервисов государственного уровня.",
          bullets: [
            "Разрабатывал модули для мониторинга сервисов и социально-ориентированных систем.",
            "Получил первый production-опыт, совмещая работу и учебу.",
          ],
          stack: ["Java", "Backend"],
        },
        {
          company: "Lukyanov Tech",
          companyIcon: "assets/companies/lukyanov.svg",
          companyUrl: "https://lukyanov.tech",
          role: "Part-Time Mentor / Mock Interviewer",
          period: "янв 2024 - настоящее время",
          location: "Удаленно",
          intro: "Параллельно с основной работой провожу менторство backend-кандидатов.",
          bullets: [
            "Провожу структурированные mock-собеседования.",
            "Помогаю улучшать техническую коммуникацию и аргументацию решений.",
          ],
          stack: ["Менторство", "Интервью-коучинг"],
        },
      ],
    },
    education: {
      title: "Образование",
      subtitle: "Формальное обучение и академический трек.",
      items: [
        {
          institution: "Воронежский государственный технический университет",
          degree: "Бакалавриат, интеллектуальные автоматизированные системы (заочно)",
          period: "Ожидаемое окончание: 2030",
        },
        {
          institution: "Воронежский государственный технический университет",
          degree: "СПО, информационные технологии и программирование (с отличием)",
          period: "2021 - 2025",
        },
      ],
    },
    strengths: {
      title: "Что я даю команде",
      subtitle: "Поведение senior-уровня, которое стабильно демонстрирую в delivery.",
      cards: [
        {
          title: "Ясность в условиях неопределенности",
          body: "Перевожу размытые требования в исполнимую архитектуру и синхронизированный план реализации.",
        },
        {
          title: "Ownership в production",
          body: "Считаю надежность, observability, деплой и поддержку инцидентов частью инженерной ответственности.",
        },
        {
          title: "High-load системы",
          body: "Уверенно работаю с распределенными и message-driven backend-сервисами с низкими задержками.",
        },
        {
          title: "Усиление команды",
          body: "Наставляю инженеров и создаю инструменты, которые повышают скорость и качество всей команды.",
        },
      ],
    },
    skills: {
      title: "Ключевой стек",
      subtitle: "Технологии, которые активно использую в production backend-задачах.",
      groups: [
        {
          title: "Языки",
          items: ["Go", "Java", "Python", "SQL"],
        },
        {
          title: "Данные и очереди",
          items: ["PostgreSQL", "Redis", "Kafka", "RabbitMQ", "NATS", "MQTT"],
        },
        {
          title: "Платформа",
          items: ["Docker", "Kubernetes", "Helm", "Istio", "Linux", "Git", "CI/CD"],
        },
        {
          title: "Observability",
          items: ["Prometheus", "Grafana", "Loki", "Sentry", "Tracing", "Alerting"],
        },
      ],
    },
    preferences: {
      title: "Текущий фокус",
      items: [
        "Целевая роль: Senior Backend / Software Engineer.",
        "Открыт к удаленному, гибридному формату и релокации.",
        "Приоритет: продукты, где качество backend напрямую влияет на бизнес-результат.",
        "Активно улучшаю разговорный английский для международных интервью и команд.",
      ],
    },
    footer: "© {year} Матвей Сизов. Сайт-визитка для рекрутеров и hiring managers.",
  },
};

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value;
  }
}

function getFaviconUrl(siteUrl) {
  if (!siteUrl) {
    return "";
  }

  try {
    const parsed = new URL(siteUrl);
    return `${parsed.origin}/favicon.ico`;
  } catch (_error) {
    return "";
  }
}

function setIconWithFallback(imgElement, remoteIconUrl, fallbackIconUrl) {
  if (!imgElement) {
    return;
  }

  if (!remoteIconUrl) {
    imgElement.src = fallbackIconUrl;
    return;
  }

  imgElement.onerror = () => {
    imgElement.onerror = null;
    imgElement.src = fallbackIconUrl;
  };

  imgElement.src = remoteIconUrl;
}

function setLinkLabel(id, text) {
  const link = document.getElementById(id);
  if (!link) {
    return;
  }

  const span = link.querySelector("span");
  if (span) {
    span.textContent = text;
  } else {
    link.textContent = text;
  }
  link.setAttribute("aria-label", text);
}

function detectLanguage() {
  const url = new URL(window.location.href);
  const queryLang = url.searchParams.get("lang");
  if (SUPPORTED_LANGS.includes(queryLang)) {
    return queryLang;
  }

  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (SUPPORTED_LANGS.includes(stored)) {
      return stored;
    }
  } catch (_error) {
    // Ignore storage access restrictions.
  }

  const browserLanguages = [];
  if (Array.isArray(window.navigator.languages)) {
    browserLanguages.push(...window.navigator.languages);
  }
  if (typeof window.navigator.language === "string") {
    browserLanguages.push(window.navigator.language);
  }

  const hasRussian = browserLanguages.some((item) => item.toLowerCase().startsWith("ru"));
  return hasRussian ? "ru" : "en";
}

function detectTheme() {
  try {
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (SUPPORTED_THEMES.includes(storedTheme)) {
      return storedTheme;
    }
  } catch (_error) {
    // Ignore storage access restrictions.
  }
  return "light";
}

function setLanguageButtonsState(lang) {
  document.querySelectorAll("[data-lang-switch]").forEach((button) => {
    const isActive = button.getAttribute("data-lang-switch") === lang;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function setThemeButtonsState(theme) {
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    const isActive = button.getAttribute("data-theme-switch") === theme;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function updateLanguageQuery(lang) {
  const url = new URL(window.location.href);
  url.searchParams.set("lang", lang);
  window.history.replaceState({}, "", url);
}

function setTheme(theme) {
  const safeTheme = SUPPORTED_THEMES.includes(theme) ? theme : "light";
  document.documentElement.setAttribute("data-theme", safeTheme);

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, safeTheme);
  } catch (_error) {
    // Ignore storage access restrictions.
  }
}

function renderFacts(items) {
  const container = document.getElementById("hero-facts");
  if (!container) {
    return;
  }

  container.innerHTML = "";

  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "fact-item";

    const heading = document.createElement("div");
    heading.className = "fact-heading";

    if (item.icon) {
      const icon = document.createElement("img");
      icon.src = item.icon;
      icon.alt = "";
      icon.setAttribute("aria-hidden", "true");
      icon.className = "fact-icon";
      heading.append(icon);
    }

    const label = document.createElement("p");
    label.className = "fact-label";
    label.textContent = item.label;
    heading.append(label);

    const value = document.createElement("p");
    value.className = "fact-value";
    value.textContent = item.value;

    card.append(heading, value);
    container.append(card);
  });
}

function renderExperience(content) {
  const container = document.getElementById("experience-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";

  content.items.forEach((item) => {
    const article = document.createElement("article");
    article.className = "timeline-item";

    const companyRow = document.createElement("div");
    companyRow.className = "timeline-company-row";

    const companyMain = document.createElement("div");
    companyMain.className = "timeline-company-main";

    const companyIcon = document.createElement("img");
    companyIcon.className = "company-icon";
    companyIcon.alt = `${item.company} icon`;
    setIconWithFallback(companyIcon, getFaviconUrl(item.companyUrl), item.companyIcon);

    let companyLink = null;
    if (item.companyUrl) {
      companyLink = document.createElement("a");
      companyLink.className = "company-link";
      companyLink.href = item.companyUrl;
      companyLink.target = "_blank";
      companyLink.rel = "noopener noreferrer";
      companyLink.textContent = item.company;
    } else {
      companyLink = document.createElement("span");
      companyLink.className = "company-link";
      companyLink.textContent = item.company;
    }

    companyMain.append(companyIcon, companyLink);
    companyRow.append(companyMain);

    if (item.companyUrl) {
      const companySiteLink = document.createElement("a");
      companySiteLink.className = "company-site-link";
      companySiteLink.href = item.companyUrl;
      companySiteLink.target = "_blank";
      companySiteLink.rel = "noopener noreferrer";

      const extIcon = document.createElement("img");
      extIcon.src = "assets/icons/external-link.svg";
      extIcon.alt = "";
      extIcon.setAttribute("aria-hidden", "true");

      const extLabel = document.createElement("span");
      extLabel.textContent = content.companySiteLabel;

      companySiteLink.append(extIcon, extLabel);
      companyRow.append(companySiteLink);
    }

    const role = document.createElement("h3");
    role.className = "timeline-role";
    role.textContent = item.role;

    const meta = document.createElement("p");
    meta.className = "timeline-meta";
    meta.textContent = `${item.period} · ${item.location}`;

    const intro = document.createElement("p");
    intro.className = "timeline-intro";
    intro.textContent = item.intro;

    const list = document.createElement("ul");
    list.className = "timeline-list";
    item.bullets.forEach((bullet) => {
      const li = document.createElement("li");
      li.textContent = bullet;
      list.append(li);
    });

    const stack = document.createElement("div");
    stack.className = "stack-list";
    item.stack.forEach((tech) => {
      const chip = document.createElement("span");
      chip.className = "stack-chip";
      chip.textContent = tech;
      stack.append(chip);
    });

    article.append(companyRow, role, meta, intro, list, stack);
    container.append(article);
  });
}

function renderEducation(content) {
  const container = document.getElementById("education-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  content.items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "education-item";

    const institution = document.createElement("h3");
    institution.textContent = item.institution;

    const degree = document.createElement("p");
    degree.className = "education-degree";
    degree.textContent = item.degree;

    const meta = document.createElement("p");
    meta.className = "education-meta";
    meta.textContent = item.period;

    card.append(institution, degree, meta);
    container.append(card);
  });
}

function renderStrengths(cards) {
  const container = document.getElementById("strengths-grid");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  cards.forEach((card) => {
    const node = document.createElement("article");
    node.className = "strength-card";

    const title = document.createElement("h3");
    title.textContent = card.title;

    const text = document.createElement("p");
    text.textContent = card.body;

    node.append(title, text);
    container.append(node);
  });
}

function renderSkills(groups) {
  const container = document.getElementById("skills-grid");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  groups.forEach((group) => {
    const card = document.createElement("article");
    card.className = "skill-card";

    const title = document.createElement("h3");
    title.textContent = group.title;

    const list = document.createElement("div");
    list.className = "skill-items";

    group.items.forEach((item) => {
      const chip = document.createElement("span");
      chip.className = "skill-item";
      chip.textContent = item;
      list.append(chip);
    });

    card.append(title, list);
    container.append(card);
  });
}

function renderPreferences(items) {
  const container = document.getElementById("preferences-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    container.append(li);
  });
}

function syncResumeLinks(resume) {
  const configs = [
    { key: "pdf", id: "resume-pdf", labelId: "resume-pdf-label" },
    { key: "docx", id: "resume-docx", labelId: "resume-docx-label" },
    { key: "txt", id: "resume-txt", labelId: "resume-txt-label" },
  ];

  configs.forEach((item) => {
    const link = document.getElementById(item.id);
    const label = document.getElementById(item.labelId);
    if (!link || !label) {
      return;
    }

    link.href = resume.files[item.key];
    link.setAttribute("download", resume.downloadNames[item.key]);
    label.textContent = resume.labels[item.key];
  });
}

function updateThemeSwitcher(lang, theme) {
  const data = CONTENT[lang] || CONTENT.en;
  const switcher = document.getElementById("theme-switch");

  if (!switcher) {
    return;
  }

  switcher.setAttribute("aria-label", data.theme.switcherLabel);
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    const buttonTheme = button.getAttribute("data-theme-switch");
    const label = buttonTheme === "dark" ? data.theme.toDark : data.theme.toLight;
    button.setAttribute("aria-label", label);
  });

  setThemeButtonsState(theme);
}

function updateHeaderOffset() {
  const header = document.querySelector(".site-header");
  if (!header) {
    return;
  }

  const styles = window.getComputedStyle(header);
  const stickyTop = Number.parseFloat(styles.top) || 0;
  const marginTop = Number.parseFloat(styles.marginTop) || 0;
  const offset = Math.ceil(header.offsetHeight + stickyTop + marginTop + 8);
  document.documentElement.style.setProperty("--header-offset", `${offset}px`);
}

function renderLanguage(lang) {
  const data = CONTENT[lang] || CONTENT.en;
  const theme = document.documentElement.getAttribute("data-theme") || "light";

  document.documentElement.lang = lang;
  document.title = data.meta.title;

  const description = document.querySelector('meta[name="description"]');
  if (description) {
    description.setAttribute("content", data.meta.description);
  }

  setText("brand-link", data.brand);
  setText("nav-resume", data.nav.resume);
  setText("nav-experience", data.nav.experience);
  setText("nav-education", data.nav.education);
  setText("nav-strengths", data.nav.strengths);
  setText("nav-skills", data.nav.skills);

  const langSwitch = document.getElementById("lang-switch");
  if (langSwitch) {
    langSwitch.setAttribute("aria-label", data.langSwitcherLabel);
  }

  setText("hero-kicker", data.hero.kicker);
  setText("hero-name", data.hero.name);
  setText("hero-role", data.hero.role);
  setText("hero-summary", data.hero.summary);

  setLinkLabel("hero-linkedin", data.hero.links.linkedin);
  setLinkLabel("hero-github", data.hero.links.github);
  setLinkLabel("hero-telegram", data.hero.links.telegram);
  setIconWithFallback(document.querySelector("#hero-linkedin img"), "https://www.linkedin.com/favicon.ico", "assets/icons/linkedin.svg");
  setIconWithFallback(document.querySelector("#hero-github img"), "https://github.com/favicon.ico", "assets/icons/github.svg");
  setIconWithFallback(document.querySelector("#hero-telegram img"), "https://t.me/favicon.ico", "assets/icons/telegram.svg");

  renderFacts(data.hero.facts);

  setText("resume-title", data.resume.title);
  setText("resume-subtitle", data.resume.subtitle);
  setText("resume-note", data.resume.note);
  syncResumeLinks(data.resume);

  setText("experience-title", data.experience.title);
  setText("experience-subtitle", data.experience.subtitle);
  renderExperience(data.experience);

  setText("education-title", data.education.title);
  setText("education-subtitle", data.education.subtitle);
  renderEducation(data.education);

  setText("strengths-title", data.strengths.title);
  setText("strengths-subtitle", data.strengths.subtitle);
  renderStrengths(data.strengths.cards);

  setText("skills-title", data.skills.title);
  setText("skills-subtitle", data.skills.subtitle);
  renderSkills(data.skills.groups);

  setText("preferences-title", data.preferences.title);
  renderPreferences(data.preferences.items);

  setText("footer-text", data.footer.replace("{year}", String(new Date().getFullYear())));

  setLanguageButtonsState(lang);
  updateThemeSwitcher(lang, theme);
  updateHeaderOffset();
}

function applyLanguage(lang) {
  const safeLang = SUPPORTED_LANGS.includes(lang) ? lang : "en";

  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, safeLang);
  } catch (_error) {
    // Ignore storage access restrictions.
  }

  updateLanguageQuery(safeLang);
  renderLanguage(safeLang);
}

function setupLanguageSwitcher() {
  document.querySelectorAll("[data-lang-switch]").forEach((button) => {
    button.addEventListener("click", () => {
      const lang = button.getAttribute("data-lang-switch") || "en";
      applyLanguage(lang);
    });
  });
}

function setupThemeSwitcher() {
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextTheme = button.getAttribute("data-theme-switch") || "light";
      setTheme(nextTheme);

      const currentLang = document.documentElement.lang || detectLanguage();
      updateThemeSwitcher(currentLang, nextTheme);
      updateHeaderOffset();
    });
  });
}

function init() {
  const lang = detectLanguage();
  const theme = detectTheme();

  setTheme(theme);
  updateHeaderOffset();
  setupLanguageSwitcher();
  setupThemeSwitcher();
  window.addEventListener("resize", updateHeaderOffset);
  window.addEventListener("load", updateHeaderOffset);
  applyLanguage(lang);
}

init();
