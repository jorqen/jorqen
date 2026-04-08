const SUPPORTED_LANGS = ["en", "ru"];
const LANGUAGE_STORAGE_KEY = "jorqen.language";
const THEME_STORAGE_KEY = "jorqen.theme";
const SUPPORTED_THEMES = ["light", "dark"];
const THEMED_ICON_MAP = {
  "briefcase": {
    light: "assets/icons/light/briefcase.svg",
    dark: "assets/icons/dark/briefcase.svg",
  },
  "download": {
    light: "assets/icons/light/download.svg",
    dark: "assets/icons/dark/download.svg",
  },
  "education": {
    light: "assets/icons/light/education.svg",
    dark: "assets/icons/dark/education.svg",
  },
  "external-link": {
    light: "assets/icons/light/external-link.svg",
    dark: "assets/icons/dark/external-link.svg",
  },
  "layers": {
    light: "assets/icons/light/layers.svg",
    dark: "assets/icons/dark/layers.svg",
  },
  "location": {
    light: "assets/icons/light/location.svg",
    dark: "assets/icons/dark/location.svg",
  },
  "star": {
    light: "assets/icons/light/star.svg",
    dark: "assets/icons/dark/star.svg",
  },
  "sun": {
    light: "assets/icons/light/sun.svg",
    dark: "assets/icons/dark/sun.svg",
  },
  "moon": {
    light: "assets/icons/light/moon.svg",
    dark: "assets/icons/dark/moon.svg",
  },
  "github": {
    light: "assets/icons/light/github.svg",
    dark: "assets/icons/dark/github.svg",
  },
  "telegram": {
    light: "assets/icons/light/telegram.svg",
    dark: "assets/icons/dark/telegram.svg",
  },
};
const STATIC_THEME_ICON_BINDINGS = [
  {
    selector: '[data-theme-switch="light"] img',
    icon: "sun",
  },
  {
    selector: '[data-theme-switch="dark"] img',
    icon: "moon",
  },
  {
    selector: "#resume .panel-icon img",
    icon: "download",
  },
  {
    selector: "#experience .panel-icon img",
    icon: "briefcase",
  },
  {
    selector: "#education .panel-icon img",
    icon: "education",
  },
  {
    selector: "#strengths .panel-icon img",
    icon: "star",
  },
  {
    selector: "#skills .panel-icon img",
    icon: "layers",
  },
  {
    selector: "#preferences .panel-icon img",
    icon: "star",
  },
  {
    selector: "#photos .panel-icon img",
    icon: "star",
  },
];
const photoLightboxState = {
  items: [],
  index: 0,
  opener: null,
};

const CONTENT = {
  en: {
    meta: {
      title: "Matvey Sizov | Backend Developer / Software Engineer",
      description:
        "Matvey Sizov — Backend Developer / Software Engineer. Go, distributed systems, low-latency backends, and production reliability across product and platform teams.",
    },
    brand: "Matvey Sizov",
    nav: {
      resume: "Resume",
      experience: "Experience",
      education: "Education",
      strengths: "Profile",
      skills: "Stack",
    },
    langSwitcherLabel: "Language switcher",
    theme: {
      toDark: "Dark theme",
      toLight: "Light theme",
      switcherLabel: "Theme switcher",
    },
    hero: {
      kicker: "Backend Developer / Software Engineer",
      name: "Matvey Sizov",
      role: "4+ years in backend engineering | Go, distributed systems, product + platform backends",
      summary:
        "I started programming with Java in my early teens, entered backend professionally through Java, and later switched my main focus to Go because I enjoy clear, explicit, low-latency systems. Today I work across both infrastructure and product backends, and I am strongest when a team needs someone to turn ambiguous requirements into a production system with sane trade-offs, observability, and reliable delivery.",
      photo: {
        src: "assets/photos/matvey-studio.jpg",
        alt: "Matvey Sizov studio portrait",
        caption: "Studio portrait",
        position: "center 18%",
        filter: "brightness(0.94) contrast(1.04) saturate(0.9)",
      },
      links: {
        linkedin: "LinkedIn",
        github: "GitHub",
        telegram: "Telegram",
      },
      facts: [
        {
          icon: "location",
          label: "Location",
          value: "Moscow, Russia",
        },
        {
          icon: "briefcase",
          label: "Experience",
          value: "4+ years commercial",
        },
        {
          icon: "layers",
          label: "Primary stack",
          value: "Go, distributed systems",
        },
        {
          icon: "layers",
          label: "Recent mix",
          value: "Product + platform",
        },
        {
          icon: "star",
          label: "Availability",
          value: "Remote / Relocation",
        },
      ],
    },
    resume: {
      title: "Download resume",
      subtitle: "Russian and English versions stay aligned in meaning, scope, and current contact information.",
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
        pdf: "Matvey_Sizov_EN.pdf",
        docx: "Matvey_Sizov_EN.docx",
        txt: "Matvey_Sizov_EN.txt",
      },
      note: "All downloadable versions keep the same current contacts as the site, including the website link.",
    },
    experience: {
      title: "Experience",
      subtitle: "Commercial roles ordered by work start date, newest first.",
      companySiteLabel: "Company site",
      items: [
        {
          company: "Atom",
          companyIcon: "assets/icons/atom.svg",
          companyUrl: "https://atom.auto",
          role: "Senior Software Engineer, Communications & Telemetry",
          period: "February 2025 - Present",
          location: "Remote from Russia",
          intro:
            "Atom is building a Russian electric vehicle platform. I work in the communications and telemetry area on backend services that connect the car, cloud, and external clients, with emphasis on latency, security, and operational reliability.",
          bullets: [
            "Worked with leadership, peer teams, and architects to turn ambiguous and partly unrealistic requirements into a releasable backend architecture for a zero-to-one broker initiative.",
            "Designed and developed an mTLS-secured MQTT broker with Redis-backed state recovery for persistent vehicle-to-cloud communication and restart-safe session handling.",
            "Challenged an initial microservice decomposition, proved it could not meet the target latency, and pushed a broker-centered design that sustained ~70K requests per second at p99 below 50 ms.",
            "Built production observability and delivery tooling from scratch after the team lost dedicated DevOps support: metrics, logs, traces, dashboards, alerts, CI/CD, and deployment automation.",
            "Selected key libraries and system components, including Redis for durable in-memory state and access-control mechanisms for trusted client connectivity.",
          ],
          stack: [
            "Go",
            "MQTT",
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
          company: "SberTech",
          companyIcon: "assets/icons/light/sbertech.png",
          companyIconDark: "assets/icons/dark/sbertech.png",
          companyUrl: "https://sbertech.ru",
          role: "Software Engineer, Service Mesh & Platform Infrastructure",
          period: "January 2024 - February 2025",
          location: "Moscow, Russia",
          intro:
            "SberTech develops Platform V, a large enterprise platform. I worked on a heavily customized Istio fork and adjacent infrastructure components used for service-mesh control, policy enforcement, and platform integration.",
          bullets: [
            "Restored broken automated testing for a heavily customized Istio fork, bringing unit tests back into the daily engineering workflow and raising unit coverage to 80%.",
            "Designed and built a Go integration-testing framework that provisioned isolated Kubernetes environments, ran tests in parallel, and generated Allure reports for CI.",
            "Expanded automation to cover ~95% of critical functionality with integration tests and fixed several high-severity defects while stabilizing release pipelines.",
            "Mentored ~10 interns on the framework and automation workflow, helping them convert manual QA scenarios into scalable automated tests.",
            "Independently designed and implemented a custom Kubernetes resource for managing control-plane and data-plane relationships in Istio; the solution was later presented internally as a target platform approach.",
          ],
          stack: [
            "Go",
            "Kubernetes",
            "Istio",
            "gRPC",
            "REST",
            "PostgreSQL",
            "CI/CD",
            "Allure",
          ],
        },
        {
          company: "Lukyanov Tech",
          companyIcon: "assets/icons/lukyanov.png",
          companyUrl: "https://lukyanov.tech",
          role: "Part-Time Mentor / Mock Interviewer",
          period: "January 2024 - Present",
          location: "Remote",
          intro: "A part-time mentorship project focused on backend growth, interviews, and candidate preparation.",
          bullets: [
            "Conduct mock interviews and mentor candidates on backend fundamentals, system design, technical communication, and answer structure.",
            "Help improve mentoring workflows, practical preparation, and feedback quality for students targeting software engineering roles.",
          ],
          stack: ["Mentoring", "Mock interviews", "System design", "Technical communication"],
        },
        {
          company: "Magnus Tech",
          companyIcon: "assets/icons/magnus.svg",
          companyUrl: "https://magnustech.com",
          role: "Backend Engineer",
          period: "March 2023 - January 2024",
          location: "Remote",
          intro:
            "Magnus Tech is a custom software development company. I worked on a retail pricing platform for Bristol that brought together store data, employee workflows, ML pricing outputs, and photo evidence into one operating system.",
          bullets: [
            "Built Go backend services from scratch for the pricing platform and integrated store data, product information, ML recommendations, and employee actions into one backend flow.",
            "Designed admin APIs and tooling for price review, manual overrides, cross-store comparison, and day-to-day operational monitoring.",
            "Integrated notification flows across web, email, SMS, and mobile channels and maintained API contracts for frontend and mobile teams.",
            "Wrote tests, supported observability, and helped deliver the product from active development to production release.",
          ],
          stack: ["Go", "PostgreSQL", "Redis", "Kafka", "MinIO", "REST", "gRPC", "Prometheus", "CI/CD"],
        },
        {
          company: "Exnode",
          companyIcon: "assets/icons/exnode.png",
          companyUrl: "https://exnode.ru",
          role: "Backend Engineer",
          period: "September 2022 - March 2023",
          location: "Moscow, Russia",
          intro: "Exnode built crypto exchange, B2B payment, and P2P trading products.",
          bullets: [
            "Split a large monolithic backend into smaller services and replaced part of the internal REST communication with gRPC to reduce latency and simplify service boundaries.",
            "Optimized payment and reporting queries using EXPLAIN ANALYZE, cutting several heavy PostgreSQL requests from 10-30 seconds to near real time.",
            "Built core product features around online payments, pricing, email notifications, Telegram alerting, and magic-link authentication.",
            "Resolved a critical currency-conversion incident by rolling back affected transactions and then hardening validation, observability, and release discipline.",
          ],
          stack: ["Go", "PostgreSQL", "Redis", "RabbitMQ", "REST", "gRPC", "Grafana", "Telegram Bot API"],
        },
        {
          company: "Kaluga Power Sale Company",
          companyIcon: "assets/icons/kaluga.png",
          companyUrl: "https://kskkaluga.ru",
          role: "Backend Engineer",
          period: "October 2021 - March 2022",
          location: "Remote",
          intro: "Early backend role in utility payments, customer accounts, and administrative systems.",
          bullets: [
            "Built backend architecture from scratch for a customer portal and admin system integrated with 1C, payments, dashboards, and mobile/web clients.",
            "Worked as the sole backend engineer on the project, delivering account, billing, and operational views for customers and property-level management.",
          ],
          stack: ["PostgreSQL", "1C integrations", "Payments", "Dashboards"],
        },
        {
          company: "Center for Regional Management of Lipetsk Oblast (CUR)",
          companyIcon: "",
          companyUrl: "",
          role: "Java Developer",
          period: "June 2021 - November 2021",
          location: "Lipetsk, Russia",
          intro: "My first commercial backend role in Java, focused on public-sector digital and monitoring systems.",
          bullets: [
            "Contributed to backend services for regional digital products and monitoring systems in a legacy-heavy environment.",
            "Worked on business logic and service development for government workflows while building my first professional engineering habits.",
          ],
          stack: ["Java", "SQL", "Backend"],
        },
      ],
    },
    education: {
      title: "Education",
      subtitle: "Formal education and current degree track.",
      items: [
        {
          institution: "Voronezh State Technical University",
          degree: "B.S. in Intelligent Automated Systems, part-time",
          period: "Expected 2030",
        },
        {
          institution: "Voronezh State Technical University",
          degree: "Secondary Vocational Diploma in Information Technology and Programming",
          period: "2021 - 2025",
        },
      ],
    },
    strengths: {
      title: "Profile",
      subtitle: "The patterns that best describe my engineering style and career direction.",
      cards: [
        {
          title: "From Java to Go",
          body: "I started with Java as a teenager, got my first commercial backend role in Java, and later moved to Go because I prefer explicit engineering, simpler syntax, and low-latency services.",
        },
        {
          title: "Product + infrastructure",
          body: "My recent work is infrastructure-heavy, but I also built product backends for retail pricing, crypto payments, utility billing, and internal admin systems.",
        },
        {
          title: "Architecture through trade-offs",
          body: "I care about choosing the right tool and the right system shape, whether that means Redis vs. PostgreSQL, gRPC vs. REST, or simplifying an architecture to hit latency targets.",
        },
        {
          title: "Leadership trajectory",
          body: "I already mentor candidates and interns, and over the next few years I want to grow into a technical leadership role without losing hands-on depth or system-design thinking.",
        },
      ],
    },
    skills: {
      title: "Core stack",
      subtitle: "Technologies I actively use or have used in production backend work.",
      groups: [
        {
          title: "Languages",
          items: ["Go", "Java", "Python", "SQL"],
        },
        {
          title: "APIs & Messaging",
          items: ["gRPC", "REST", "MQTT", "Kafka", "RabbitMQ", "NATS"],
        },
        {
          title: "Data & Storage",
          items: ["PostgreSQL", "Redis", "MinIO"],
        },
        {
          title: "Platform",
          items: ["Docker", "Kubernetes", "Helm", "Istio", "Linux", "Git", "CI/CD"],
        },
        {
          title: "Observability & Quality",
          items: ["Prometheus", "Grafana", "Loki", "Sentry", "Allure", "Testing frameworks"],
        },
      ],
    },
    preferences: {
      title: "What recruiters should know",
      items: [
        "I see myself as a backend engineer first, strongest in Go, distributed systems, and complex backend logic.",
        "I am comfortable in both product and platform teams and can switch between latency-sensitive infrastructure and business-facing backend features.",
        "I am best matched with international teams solving high-load, low-latency, or technically ambiguous backend problems.",
        "Over the next 2-5 years I want to grow into tech lead / engineering leadership while staying deeply technical.",
        "I use English in interview and work contexts when needed and continue improving spoken fluency for international teams.",
        "Outside work I mentor candidates, cycle, swim, go to the gym, and travel; Japan is one of the places I most want to revisit.",
      ],
    },
    gallery: {
      title: "A few more photos",
      subtitle:
        "I keep the public photo section intentionally small: this site is primarily about my work, but I also want it to feel human and real.",
      items: [
        {
          src: "assets/photos/matvey-stairs.jpg",
          alt: "Matvey Sizov outdoors on stairs in Japan",
          caption: "Japan street portrait",
          position: "center 18%",
          filter: "brightness(0.8) contrast(1.03) saturate(0.88)",
        },
        {
          src: "assets/photos/matvey-japan.jpg",
          alt: "Matvey Sizov in Japan holding a training sword",
          caption: "Japan, 2026",
          filter: "brightness(1.06) contrast(1.03) saturate(0.92)",
        },
        {
          src: "assets/photos/matvey-cafe.jpg",
          alt: "Matvey Sizov sitting in a cafe",
          caption: "A more casual everyday photo",
          filter: "brightness(0.96) contrast(1.02) saturate(0.92)",
        },
        {
          src: "assets/photos/matvey-mountains.jpg",
          alt: "Matvey Sizov in the mountains",
          caption: "Mountain travel photo",
          position: "center 26%",
          filter: "brightness(0.88) contrast(1.03) saturate(0.92)",
        },
        {
          src: "assets/photos/matvey-lake.jpg",
          alt: "Matvey Sizov outdoors near a lake",
          caption: "Another travel photo outdoors",
          filter: "brightness(0.9) contrast(1.02) saturate(0.9)",
        },
        {
          src: "assets/photos/matvey-travel.jpg",
          alt: "Matvey Sizov outdoors in a rocky landscape",
          caption: "A wider travel shot",
          filter: "brightness(1.01) contrast(1.03) saturate(0.9)",
        },
      ],
    },
    lightbox: {
      openPhoto: "Open photo",
      close: "Close photo viewer",
      previous: "Previous photo",
      next: "Next photo",
    },
    footer: "© {year} Matvey Sizov. Personal website for recruiters and hiring managers.",
  },

  ru: {
    meta: {
      title: "Матвей Сизов | Backend Developer / Software Engineer",
      description:
        "Матвей Сизов — Backend Developer / Software Engineer. Go, распределенные системы, low-latency backend и надежность production в продуктовых и платформенных командах.",
    },
    brand: "Матвей Сизов",
    nav: {
      resume: "Резюме",
      experience: "Опыт",
      education: "Образование",
      strengths: "Профиль",
      skills: "Стек",
    },
    langSwitcherLabel: "Переключение языка",
    theme: {
      toDark: "Тёмная тема",
      toLight: "Светлая тема",
      switcherLabel: "Переключение темы",
    },
    hero: {
      kicker: "Backend Developer / Software Engineer",
      name: "Матвей Сизов",
      role: "4+ года в backend engineering | Go, распределенные системы, продуктовый и платформенный backend",
      summary:
        "Я начал программировать на Java еще в раннем подростковом возрасте, вошел в профессию backend-разработчика через Java, а затем перевел основной фокус на Go, потому что мне ближе явные, понятные и low-latency системы. Сейчас я работаю и с инфраструктурным, и с продуктовым backend, а сильнее всего проявляюсь там, где команде нужен человек, который превращает размытые требования в production-систему с разумными trade-off, observability и надежной поставкой.",
      photo: {
        src: "assets/photos/matvey-studio.jpg",
        alt: "Студийный портрет Матвея Сизова",
        caption: "Студийный портрет",
        position: "center 18%",
        filter: "brightness(0.94) contrast(1.04) saturate(0.9)",
      },
      links: {
        linkedin: "LinkedIn",
        github: "GitHub",
        telegram: "Telegram",
      },
      facts: [
        {
          icon: "location",
          label: "Локация",
          value: "Москва, Россия",
        },
        {
          icon: "briefcase",
          label: "Опыт",
          value: "4+ года коммерчески",
        },
        {
          icon: "layers",
          label: "Основной стек",
          value: "Go, распределенные системы",
        },
        {
          icon: "layers",
          label: "Последний опыт",
          value: "Продукт + платформа",
        },
        {
          icon: "star",
          label: "Формат",
          value: "Удаленно / релокация",
        },
      ],
    },
    resume: {
      title: "Скачать резюме",
      subtitle: "Русская и английская версии синхронизированы по смыслу, объему и актуальным контактам.",
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
        pdf: "Matvey_Sizov_RU.pdf",
        docx: "Matvey_Sizov_RU.docx",
        txt: "Matvey_Sizov_RU.txt",
      },
      note: "Во всех скачиваемых версиях сохранены те же актуальные контакты, что и на сайте, включая ссылку на сайт.",
    },
    experience: {
      title: "Опыт",
      subtitle: "Коммерческие роли, отсортированные по дате начала работы: от более новых к более ранним.",
      companySiteLabel: "Сайт компании",
      items: [
        {
          company: "Atom",
          companyIcon: "assets/icons/atom.svg",
          companyUrl: "https://atom.auto",
          role: "Senior Software Engineer, Communications & Telemetry",
          period: "фев 2025 - настоящее время",
          location: "Удаленно из России",
          intro:
            "Atom строит российскую платформу электромобиля. Я работаю в направлении коммуникаций и телеметрии над backend-сервисами, которые соединяют автомобиль, облако и внешних клиентов, с фокусом на latency, безопасность и операционную надежность.",
          bullets: [
            "Работал с руководством, смежными командами и архитекторами, чтобы превратить размытые и частично нереалистичные требования в релизопригодную backend-архитектуру для zero-to-one брокера.",
            "Спроектировал и разработал MQTT-брокер с mTLS-защитой и Redis-backed восстановлением состояния для постоянной vehicle-to-cloud коммуникации и безопасного восстановления сессий после рестарта.",
            "Оспорил первоначальную microservice-декомпозицию, показал, что она не укладывается в целевую latency, и продвинул broker-centered дизайн, который выдержал ~70K запросов в секунду при p99 ниже 50 мс.",
            "С нуля собрал production-observability и delivery tooling после потери выделенной DevOps-поддержки: метрики, логи, трейсы, дашборды, алерты, CI/CD и автоматизацию деплоя.",
            "Выбрал ключевые библиотеки и системные компоненты, включая Redis для durable in-memory state и механизмы контроля доступа для доверенных клиентов.",
          ],
          stack: [
            "Go",
            "MQTT",
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
          company: "SberTech",
          companyIcon: "assets/icons/light/sbertech.png",
          companyIconDark: "assets/icons/dark/sbertech.png",
          companyUrl: "https://sbertech.ru",
          role: "Software Engineer, Service Mesh & Platform Infrastructure",
          period: "янв 2024 - фев 2025",
          location: "Москва, Россия",
          intro:
            "Сбертех развивает Platform V, крупную enterprise-платформу. Я работал с сильно модифицированным форком Istio и смежными инфраструктурными компонентами для service mesh, политик и платформенной интеграции.",
          bullets: [
            "Восстановил сломанное автотестирование в сильно модифицированном форке Istio, вернув юнит-тесты в ежедневный инженерный процесс и подняв покрытие до 80%.",
            "Спроектировал и реализовал Go-фреймворк интеграционного тестирования, который поднимал изолированные Kubernetes-окружения, запускал тесты параллельно и генерировал Allure-отчеты для CI.",
            "Расширил автоматизацию примерно до 95% критического функционала через интеграционные тесты и параллельно исправил несколько high-severity дефектов, стабилизируя релизные пайплайны.",
            "Наставлял около 10 стажеров по фреймворку и процессу автоматизации, помогая переводить ручные QA-сценарии в масштабируемые автотесты.",
            "Самостоятельно спроектировал и реализовал custom Kubernetes resource для управления связями control plane и data plane в Istio; позже решение представили внутри компании как целевой платформенный подход.",
          ],
          stack: [
            "Go",
            "Kubernetes",
            "Istio",
            "gRPC",
            "REST",
            "PostgreSQL",
            "CI/CD",
            "Allure",
          ],
        },
        {
          company: "Lukyanov Tech",
          companyIcon: "assets/icons/lukyanov.png",
          companyUrl: "https://lukyanov.tech",
          role: "Part-Time Mentor / Mock Interviewer",
          period: "янв 2024 - настоящее время",
          location: "Удаленно",
          intro: "Part-time проект в области менторства, подготовки к интервью и роста backend-кандидатов.",
          bullets: [
            "Провожу mock-собеседования и менторю кандидатов по backend-базе, system design, технической коммуникации и структуре ответов.",
            "Помогаю улучшать сам процесс менторства, практическую подготовку и качество обратной связи для студентов, идущих в software engineering роли.",
          ],
          stack: ["Менторство", "Mock-собеседования", "System design", "Техническая коммуникация"],
        },
        {
          company: "Magnus Tech",
          companyIcon: "assets/icons/magnus.svg",
          companyUrl: "https://magnustech.com",
          role: "Backend Engineer",
          period: "мар 2023 - янв 2024",
          location: "Удаленно",
          intro:
            "Magnus Tech — компания заказной разработки. Я работал над платформой контроля цен для сети «Бристоль», которая объединяла данные магазинов, действия сотрудников, ML-рекомендации по ценам и фотоподтверждения в единую операционную систему.",
          bullets: [
            "С нуля разрабатывал Go backend-сервисы для pricing-платформы и интегрировал данные магазинов, продуктовую информацию, ML-рекомендации и действия сотрудников в единый backend-поток.",
            "Спроектировал admin API и внутренние инструменты для просмотра цен, ручных корректировок, сравнения между магазинами и ежедневного операционного контроля.",
            "Интегрировал notification flows для web, email, SMS и mobile-каналов и поддерживал API-контракты для frontend и mobile команд.",
            "Писал тесты, поддерживал observability и участвовал в доведении продукта от активной разработки до production-релиза.",
          ],
          stack: ["Go", "PostgreSQL", "Redis", "Kafka", "MinIO", "REST", "gRPC", "Prometheus", "CI/CD"],
        },
        {
          company: "Exnode",
          companyIcon: "assets/icons/exnode.png",
          companyUrl: "https://exnode.ru",
          role: "Backend Engineer",
          period: "сен 2022 - мар 2023",
          location: "Москва, Россия",
          intro: "Exnode развивал продукты криптобиржи, B2B-платежей и P2P-обмена.",
          bullets: [
            "Разделил большой монолитный backend на более мелкие сервисы и заменил часть внутреннего REST-взаимодействия на gRPC, чтобы снизить latency и упростить границы сервисов.",
            "Оптимизировал платежные и отчетные запросы через EXPLAIN ANALYZE, сократив несколько тяжелых PostgreSQL-запросов с 10-30 секунд до почти real-time уровня.",
            "Реализовал ключевые продуктовые возможности вокруг онлайн-платежей, pricing, email-уведомлений, Telegram-alerting и magic-link аутентификации.",
            "Разобрал и локализовал критический инцидент с конвертацией валют, откатил затронутые транзакции и затем усилил валидацию, observability и release discipline.",
          ],
          stack: ["Go", "PostgreSQL", "Redis", "RabbitMQ", "REST", "gRPC", "Grafana", "Telegram Bot API"],
        },
        {
          company: "Калужская сбытовая компания",
          companyIcon: "assets/icons/kaluga.png",
          companyUrl: "https://kskkaluga.ru",
          role: "Backend Engineer",
          period: "окт 2021 - мар 2022",
          location: "Удаленно",
          intro: "Ранняя backend-роль в системах коммунальных платежей, клиентских кабинетов и административных сервисов.",
          bullets: [
            "С нуля построил backend-архитектуру для клиентского портала и административной системы с интеграциями в 1С, платежи, дашборды и mobile/web-клиенты.",
            "Работал единственным backend-инженером на проекте, реализуя аккаунты, биллинг и операционные представления для клиентов и управления объектами.",
          ],
          stack: ["PostgreSQL", "1C integrations", "Платежи", "Дашборды"],
        },
        {
          company: "Центр Управления Регионом Липецкой области (ЦУР)",
          companyIcon: "",
          companyUrl: "",
          role: "Java Developer",
          period: "июн 2021 - ноя 2021",
          location: "Липецк, Россия",
          intro: "Моя первая коммерческая backend-роль на Java, связанная с государственными цифровыми и мониторинговыми системами.",
          bullets: [
            "Участвовал в разработке backend-сервисов для региональных цифровых продуктов и систем мониторинга в legacy-heavy окружении.",
            "Работал над бизнес-логикой и сервисной разработкой для государственных процессов, параллельно формируя первые профессиональные инженерные привычки.",
          ],
          stack: ["Java", "SQL", "Backend"],
        },
      ],
    },
    education: {
      title: "Образование",
      subtitle: "Формальное образование и текущая степень.",
      items: [
        {
          institution: "Воронежский государственный технический университет",
          degree: "Бакалавриат, интеллектуальные автоматизированные системы (заочно)",
          period: "Ожидаемое окончание: 2030",
        },
        {
          institution: "Воронежский государственный технический университет",
          degree: "СПО, информационные технологии и программирование",
          period: "2021 - 2025",
        },
      ],
    },
    strengths: {
      title: "Профиль",
      subtitle: "Паттерны, которые лучше всего описывают мой инженерный стиль и карьерное направление.",
      cards: [
        {
          title: "От Java к Go",
          body: "Я начал с Java еще подростком, получил первую коммерческую backend-роль на Java, а затем перешел в Go, потому что мне ближе явная инженерия, более простой синтаксис и low-latency сервисы.",
        },
        {
          title: "Продукт + инфраструктура",
          body: "Последние роли у меня инфраструктурные, но я также строил продуктовые backend-системы для retail pricing, crypto payments, коммунального биллинга и внутренних admin-панелей.",
        },
        {
          title: "Архитектура через trade-off",
          body: "Мне важно выбирать правильный инструмент и правильную форму системы: Redis или PostgreSQL, gRPC или REST, более сложная схема или упрощение архитектуры ради latency и operability.",
        },
        {
          title: "Траектория в leadership",
          body: "Я уже менторю кандидатов и стажеров, а в ближайшие годы хочу вырасти в техническое лидерство, не теряя hands-on глубину и системное мышление.",
        },
      ],
    },
    skills: {
      title: "Ключевой стек",
      subtitle: "Технологии, которые я активно использую или уже использовал в production backend-задачах.",
      groups: [
        {
          title: "Языки",
          items: ["Go", "Java", "Python", "SQL"],
        },
        {
          title: "API и messaging",
          items: ["gRPC", "REST", "MQTT", "Kafka", "RabbitMQ", "NATS"],
        },
        {
          title: "Данные и storage",
          items: ["PostgreSQL", "Redis", "MinIO"],
        },
        {
          title: "Платформа",
          items: ["Docker", "Kubernetes", "Helm", "Istio", "Linux", "Git", "CI/CD"],
        },
        {
          title: "Observability и quality",
          items: ["Prometheus", "Grafana", "Loki", "Sentry", "Allure", "Testing frameworks"],
        },
      ],
    },
    preferences: {
      title: "Что важно знать рекрутеру",
      items: [
        "Я в первую очередь вижу себя backend-инженером и сильнее всего чувствую себя в Go, распределенных системах и сложной backend-логике.",
        "Мне комфортно и в продуктовых, и в платформенных командах: я умею переключаться между latency-sensitive инфраструктурой и backend-функциональностью, которая ближе к бизнесу.",
        "Лучше всего подхожу международным командам, которые решают high-load, low-latency или просто технически неоднозначные backend-задачи.",
        "В ближайшие 2-5 лет хочу вырасти в tech lead / engineering leadership роль, оставаясь при этом глубоко техническим инженером.",
        "Я использую английский в рабочих и интервью-контекстах, когда это нужно, и продолжаю улучшать разговорную беглость для международных команд.",
        "Вне работы я менторю кандидатов, катаюсь на велосипеде, плаваю, хожу в зал и люблю путешествовать; Япония — одно из мест, куда мне особенно хочется вернуться.",
      ],
    },
    gallery: {
      title: "Еще несколько фото",
      subtitle:
        "Этот публичный фото-раздел я специально держу небольшим: сайт в первую очередь про мою работу, но мне хочется, чтобы он оставался живым и человеческим.",
      items: [
        {
          src: "assets/photos/matvey-stairs.jpg",
          alt: "Матвей Сизов на улице в Японии на лестнице",
          caption: "Уличный портрет в Японии",
          position: "center 18%",
          filter: "brightness(0.8) contrast(1.03) saturate(0.88)",
        },
        {
          src: "assets/photos/matvey-japan.jpg",
          alt: "Матвей Сизов в Японии с тренировочным мечом",
          caption: "Япония, 2026",
          filter: "brightness(1.06) contrast(1.03) saturate(0.92)",
        },
        {
          src: "assets/photos/matvey-cafe.jpg",
          alt: "Матвей Сизов сидит в кафе",
          caption: "Более повседневое фото",
          filter: "brightness(0.96) contrast(1.02) saturate(0.92)",
        },
        {
          src: "assets/photos/matvey-mountains.jpg",
          alt: "Матвей Сизов в горах",
          caption: "Фото из поездки в горах",
          position: "center 26%",
          filter: "brightness(0.88) contrast(1.03) saturate(0.92)",
        },
        {
          src: "assets/photos/matvey-lake.jpg",
          alt: "Матвей Сизов на улице у озера",
          caption: "Еще одно travel-фото на природе",
          filter: "brightness(0.9) contrast(1.02) saturate(0.9)",
        },
        {
          src: "assets/photos/matvey-travel.jpg",
          alt: "Матвей Сизов на фоне скального пейзажа",
          caption: "Более широкий travel-кадр",
          filter: "brightness(1.01) contrast(1.03) saturate(0.9)",
        },
      ],
    },
    lightbox: {
      openPhoto: "Открыть фото",
      close: "Закрыть просмотр фото",
      previous: "Предыдущее фото",
      next: "Следующее фото",
    },
    footer: "© {year} Матвей Сизов. Сайт-визитка для рекрутеров и hiring managers.",
  },
};

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value || "";
  }
}

function getPhotoItems(data) {
  return [data.hero.photo, ...(data.gallery?.items || [])];
}

function setPhotoTriggerAttributes(element, index, label) {
  if (!element) {
    return;
  }

  element.dataset.photoIndex = String(index);
  element.tabIndex = 0;
  element.setAttribute("role", "button");
  element.setAttribute("aria-label", label || "");
}

function getThemedIcon(iconPath, theme) {
  const mapping = THEMED_ICON_MAP[iconPath];
  if (!mapping) {
    return iconPath;
  }

  return mapping[theme] || iconPath;
}

function setImageSource(selector, source) {
  const image = document.querySelector(selector);
  if (!image) {
    return;
  }

  image.src = source;
}

function syncStaticThemeIcons(theme) {
  STATIC_THEME_ICON_BINDINGS.forEach((binding) => {
    setImageSource(binding.selector, getThemedIcon(binding.icon, theme));
  });
}

function setLinkLabel(id, text) {
  const link = document.getElementById(id);
  if (!link) {
    return;
  }

  const span = link.querySelector("span");
  if (span) {
    span.textContent = text || "";
  } else {
    link.textContent = text || "";
  }
  link.setAttribute("aria-label", text || "");
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

function renderFacts(items, theme) {
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
      icon.src = getThemedIcon(item.icon, theme);
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

function renderHeroPhoto(photo, index, triggerLabel) {
  const card = document.querySelector(".hero-photo-card");
  const image = document.getElementById("hero-photo");
  const caption = document.getElementById("hero-photo-caption");

  if (!image) {
    return;
  }

  image.src = photo?.src || "";
  image.alt = photo?.alt || "";
  image.style.objectPosition = photo?.position || "";
  image.style.setProperty("--photo-filter", photo?.filter || "");

  if (caption) {
    caption.textContent = photo?.caption || "";
  }

  setPhotoTriggerAttributes(card, index, triggerLabel);
}

function renderExperience(content, theme) {
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

    const companyIconPath = theme === "dark" && item.companyIconDark ? item.companyIconDark : item.companyIcon;
    if (companyIconPath) {
      const companyIcon = document.createElement("img");
      companyIcon.className = "company-icon";
      companyIcon.alt = `${item.company} icon`;
      companyIcon.src = companyIconPath;
      companyMain.append(companyIcon);
    }
    companyMain.append(companyLink);
    companyRow.append(companyMain);

    if (item.companyUrl) {
      const companySiteLink = document.createElement("a");
      companySiteLink.className = "company-site-link";
      companySiteLink.href = item.companyUrl;
      companySiteLink.target = "_blank";
      companySiteLink.rel = "noopener noreferrer";

      const extIcon = document.createElement("img");
      extIcon.src = getThemedIcon("external-link", theme);
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

function renderGallery(items, startIndex, triggerLabel) {
  const container = document.getElementById("gallery-grid");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  items.forEach((item, offset) => {
    const card = document.createElement("article");
    card.className = "gallery-card";
    setPhotoTriggerAttributes(card, startIndex + offset, `${triggerLabel}: ${item.caption || item.alt || ""}`);

    const image = document.createElement("img");
    image.className = "gallery-photo";
    image.src = item.src;
    image.alt = item.alt || "";
    image.loading = "lazy";
    image.style.objectPosition = item.position || "";
    image.style.setProperty("--photo-filter", item.filter || "");

    card.append(image);
    container.append(card);
  });
}

function updatePhotoLightboxLabels(labels) {
  const closeButton = document.getElementById("lightbox-close");
  const closeBackdrop = document.querySelector("[data-lightbox-close]");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");

  if (closeButton) {
    closeButton.setAttribute("aria-label", labels.close);
  }
  if (closeBackdrop) {
    closeBackdrop.setAttribute("aria-label", labels.close);
  }
  if (prevButton) {
    prevButton.setAttribute("aria-label", labels.previous);
  }
  if (nextButton) {
    nextButton.setAttribute("aria-label", labels.next);
  }
}

function renderPhotoLightbox() {
  const item = photoLightboxState.items[photoLightboxState.index];
  const image = document.getElementById("lightbox-image");
  const caption = document.getElementById("lightbox-caption");
  const counter = document.getElementById("lightbox-counter");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");

  if (!item || !image) {
    return;
  }

  image.src = item.src;
  image.alt = item.alt || "";
  image.style.setProperty("--photo-filter", item.filter || "");
  if (caption) {
    caption.textContent = item.caption || "";
  }
  if (counter) {
    counter.textContent = `${photoLightboxState.index + 1} / ${photoLightboxState.items.length}`;
  }
  if (prevButton) {
    prevButton.disabled = photoLightboxState.index === 0;
  }
  if (nextButton) {
    nextButton.disabled = photoLightboxState.index >= photoLightboxState.items.length - 1;
  }
}

function syncPhotoLightboxItems(items) {
  photoLightboxState.items = items;
  if (photoLightboxState.index >= items.length) {
    photoLightboxState.index = Math.max(0, items.length - 1);
  }

  const lightbox = document.getElementById("photo-lightbox");
  if (lightbox && !lightbox.hidden) {
    renderPhotoLightbox();
  }
}

function openPhotoLightbox(index, opener) {
  const lightbox = document.getElementById("photo-lightbox");
  const dialog = document.getElementById("lightbox-dialog");
  if (!lightbox || !dialog || !photoLightboxState.items.length) {
    return;
  }

  photoLightboxState.index = Math.max(0, Math.min(index, photoLightboxState.items.length - 1));
  photoLightboxState.opener = opener || null;
  lightbox.hidden = false;
  document.body.classList.add("lightbox-open");
  renderPhotoLightbox();
  window.requestAnimationFrame(() => dialog.focus());
}

function closePhotoLightbox() {
  const lightbox = document.getElementById("photo-lightbox");
  if (!lightbox || lightbox.hidden) {
    return;
  }

  lightbox.hidden = true;
  document.body.classList.remove("lightbox-open");
  if (photoLightboxState.opener instanceof HTMLElement) {
    photoLightboxState.opener.focus();
  }
}

function showAdjacentPhoto(direction) {
  const nextIndex = photoLightboxState.index + direction;
  if (nextIndex < 0 || nextIndex >= photoLightboxState.items.length) {
    return;
  }

  photoLightboxState.index = nextIndex;
  renderPhotoLightbox();
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

  if (switcher) {
    switcher.setAttribute("aria-label", data.theme.switcherLabel);
    document.querySelectorAll("[data-theme-switch]").forEach((button) => {
      const buttonTheme = button.getAttribute("data-theme-switch");
      const label = buttonTheme === "dark" ? data.theme.toDark : data.theme.toLight;
      button.setAttribute("aria-label", label);
    });
  }

  syncStaticThemeIcons(theme);
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
  const photoItems = getPhotoItems(data);

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
  setImageSource("#hero-linkedin img", "assets/icons/linkedin.svg");
  setImageSource("#hero-github img", getThemedIcon("github", theme));
  setImageSource("#hero-telegram img", getThemedIcon("telegram", theme));

  renderFacts(data.hero.facts, theme);
  renderHeroPhoto(data.hero.photo, 0, `${data.lightbox.openPhoto}: ${data.hero.photo.caption || data.hero.photo.alt || ""}`);

  setText("resume-title", data.resume.title);
  setText("resume-subtitle", data.resume.subtitle);
  setText("resume-note", data.resume.note);
  syncResumeLinks(data.resume);

  setText("experience-title", data.experience.title);
  setText("experience-subtitle", data.experience.subtitle);
  renderExperience(data.experience, theme);

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

  setText("photos-title", data.gallery.title);
  setText("photos-subtitle", data.gallery.subtitle);
  renderGallery(data.gallery.items, 1, data.lightbox.openPhoto);
  updatePhotoLightboxLabels(data.lightbox);
  syncPhotoLightboxItems(photoItems);

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
      renderLanguage(currentLang);
      updateHeaderOffset();
    });
  });
}

function setupPhotoLightbox() {
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }

    const trigger = event.target.closest("[data-photo-index]");
    if (trigger && !trigger.closest("#photo-lightbox")) {
      openPhotoLightbox(Number(trigger.dataset.photoIndex || "0"), trigger);
      return;
    }

    if (event.target.closest("[data-lightbox-close]") || event.target.closest("#lightbox-close")) {
      closePhotoLightbox();
      return;
    }

    if (event.target.closest("#lightbox-prev")) {
      showAdjacentPhoto(-1);
      return;
    }

    if (event.target.closest("#lightbox-next")) {
      showAdjacentPhoto(1);
    }
  });

  document.addEventListener("keydown", (event) => {
    const lightbox = document.getElementById("photo-lightbox");

    if (event.target instanceof Element) {
      const trigger = event.target.closest("[data-photo-index]");
      if (trigger && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        openPhotoLightbox(Number(trigger.dataset.photoIndex || "0"), trigger);
        return;
      }
    }

    if (!lightbox || lightbox.hidden) {
      return;
    }

    if (event.key === "Escape") {
      closePhotoLightbox();
      return;
    }

    if (event.key === "ArrowLeft") {
      showAdjacentPhoto(-1);
      return;
    }

    if (event.key === "ArrowRight") {
      showAdjacentPhoto(1);
    }
  });
}

function init() {
  const lang = detectLanguage();
  const theme = detectTheme();

  setTheme(theme);
  updateHeaderOffset();
  setupLanguageSwitcher();
  setupThemeSwitcher();
  setupPhotoLightbox();
  window.addEventListener("resize", updateHeaderOffset);
  window.addEventListener("load", updateHeaderOffset);
  applyLanguage(lang);
}

init();
