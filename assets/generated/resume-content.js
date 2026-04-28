window.JORQEN_RESUME_DATA = {
  "contacts": {
    "email": {
      "value": "oxoxevi351@gmail.com",
      "icon": "assets/icons/contact.svg",
      "href": "mailto:oxoxevi351@gmail.com"
    },
    "linkedin": {
      "value": "https://www.linkedin.com/in/jorqen",
      "icon": "assets/icons/linkedin.svg",
      "href": "https://www.linkedin.com/in/jorqen"
    },
    "github": {
      "value": "https://github.com/jorqen",
      "icon": "assets/icons/light/github.svg",
      "iconDark": "assets/icons/dark/github.svg",
      "href": "https://github.com/jorqen"
    },
    "telegram": {
      "value": "https://t.me/jorqen",
      "icon": "assets/icons/telegram.svg",
      "href": "https://t.me/jorqen"
    }
  },
  "content": {
    "en": {
      "meta": {
        "title": "Matvey Sizov | Backend Developer / Software Engineer",
        "description": "Matvey Sizov - Backend Developer / Software Engineer. Go, distributed systems, low-latency backend development, and production reliability in product and platform teams."
      },
      "brand": "Matvey Sizov",
      "nav": {
        "resume": "Resume",
        "experience": "Experience",
        "education": "Education",
        "strengths": "Profile",
        "skills": "Stack"
      },
      "langSwitcherLabel": "Language switcher",
      "theme": {
        "toDark": "Dark theme",
        "toLight": "Light theme",
        "switcherLabel": "Theme switcher"
      },
      "hero": {
        "kicker": "Backend Developer / Software Engineer",
        "name": "Matvey Sizov",
        "role": "4+ years building production Go backend systems | distributed systems, high-load and low-latency infrastructure, service mesh, and fintech/crypto products.",
        "summary": "Backend engineer specializing in Go, distributed systems, and low-latency infrastructure. I have 4+ years of commercial experience building production services across vehicle telemetry and car-to-cloud connectivity, service-mesh platform infrastructure, retail pricing operations, crypto payments and exchange flows, utility billing, and public digital services. My strongest work is turning ambiguous requirements into reliable systems: clarifying trade-offs, choosing architecture, implementing core services, adding observability and security, and carrying delivery through CI/CD into production operation.",
        "photo": {
          "src": "assets/photos/matvey-formal.png",
          "position": "center 22%",
          "filter": "brightness(0.99) contrast(1.02) saturate(0.96)",
          "caption": "Formal portrait"
        },
        "facts": [
          {
            "icon": "assets/icons/light/location.svg",
            "iconDark": "assets/icons/dark/location.svg",
            "label": "Location",
            "value": "Moscow, Russia"
          },
          {
            "icon": "assets/icons/light/briefcase.svg",
            "iconDark": "assets/icons/dark/briefcase.svg",
            "label": "Experience",
            "value": "Go - 4+ years\nJava - 6 months"
          },
          {
            "icon": "assets/icons/light/layers.svg",
            "iconDark": "assets/icons/dark/layers.svg",
            "label": "Primary stack",
            "value": "Go, distributed systems"
          },
          {
            "icon": "assets/icons/light/star.svg",
            "iconDark": "assets/icons/dark/star.svg",
            "label": "Format",
            "value": "Remote / relocation"
          }
        ]
      },
      "resume": {
        "fileBaseName": "Matvey_Sizov",
        "title": "Download resume",
        "labels": {
          "pdf": "For sharing and printing",
          "docx": "Editable source",
          "txt": "Text version"
        },
        "files": {
          "pdf": "resume/en/Matvey_Sizov.pdf",
          "docx": "resume/en/Matvey_Sizov.docx",
          "txt": "resume/en/Matvey_Sizov.txt"
        },
        "downloadNames": {
          "pdf": "Matvey_Sizov.pdf",
          "docx": "Matvey_Sizov.docx",
          "txt": "Matvey_Sizov.txt"
        }
      },
      "experience": {
        "title": "Experience",
        "companySiteLabel": "Company site",
        "items": [
          {
            "company": "ATOM",
            "role": "Senior Software Engineer",
            "period": "Feb 2025 - Present",
            "duration": "1 yr 2 mos",
            "location": "Remote",
            "intro": "ATOM develops an electric-vehicle platform. In the communications and telemetry area, I build backend services for secure vehicle-to-cloud connectivity, external client integration, and reliable low-latency operation.",
            "bullets": [
              "Aligned leadership, peer teams, and architects around an MVP architecture for ambiguous requirements, simplifying an initially overcomplicated service split into a broker-centered design that could be released and operated.",
              "Designed and co-developed an mTLS-secured MQTT broker on Mochi MQTT with Redis-backed session and state recovery for persistent vehicle-to-cloud communication and safe restarts.",
              "Validated latency limits of the initial microservice decomposition and drove the central-broker approach that sustained ~70K requests per second with p99 latency below 50 ms.",
              "Built production observability and delivery tooling from scratch: metrics, logs, traces, dashboards, alerts, latency/throughput and business-health monitoring, CI/CD, and deployment automation.",
              "Strengthened trusted connectivity by enforcing certificate-based client authentication and topic-level authorization."
            ],
            "stack": [
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
              "Helm",
              "Kubernetes",
              "CI/CD"
            ],
            "companyIcon": "assets/icons/atom.svg",
            "companyUrl": "https://atom.auto"
          },
          {
            "company": "Lukyanov Tech",
            "role": "Part-Time Mentor / Mock Interviewer",
            "period": "May 2024 - Present",
            "duration": "1 yr 11 mos",
            "location": "Remote",
            "intro": "Part-time mentorship project for backend candidates preparing for engineering interviews. I run practical preparation tracks and mock interviews focused on backend fundamentals, system design, technical communication, and structured feedback.",
            "bullets": [
              "Conduct mock interviews and mentor candidates on backend fundamentals, system design, technical communication, and answer structure.",
              "Provide structured feedback and improve preparation materials and processes so candidates can turn weak interview signals into concrete practice tasks."
            ],
            "stack": [
              "Mentoring",
              "Mock interviews",
              "System design",
              "Technical communication"
            ],
            "companyIcon": "assets/icons/lukyanov.png",
            "companyUrl": "https://lukyanov.tech"
          },
          {
            "company": "Sber Tech",
            "role": "Software Engineer",
            "period": "Jan 2024 - Feb 2025",
            "duration": "1 yr 1 mo",
            "location": "Moscow, Russia",
            "intro": "Sber Tech develops Platform V, a large enterprise platform. In the Service Mesh & Platform Infrastructure team, I worked on a heavily customized Istio fork and adjacent Kubernetes components for workload control, policy enforcement, and platform integration.",
            "bullets": [
              "Restored the automated test suite in a heavily customized Istio fork, returning unit tests to daily development and raising coverage to 80%.",
              "Designed and built a Go integration-testing framework that provisioned isolated Kubernetes environments, ran tests in parallel, and generated Allure reports for CI.",
              "Repaired CI pipelines and expanded integration-test automation to ~95% of critical functionality, moving defect detection earlier and helping fix several critical issues.",
              "Mentored ~10 School 21 interns on the framework and automation process, helping convert manual QA scenarios into scalable automated tests.",
              "Designed and implemented a custom Kubernetes resource for managing control-plane/data-plane relationships in Istio; the solution was later presented internally as a target platform approach."
            ],
            "stack": [
              "Go",
              "Kubernetes",
              "Istio",
              "gRPC",
              "REST",
              "PostgreSQL",
              "CI/CD",
              "Allure"
            ],
            "companyIcon": "assets/icons/light/sbertech.svg",
            "companyIconDark": "assets/icons/dark/sbertech.svg",
            "companyUrl": "https://sbertech.ru"
          },
          {
            "company": "Magnus Tech",
            "role": "Software Engineer",
            "period": "Mar 2023 - Jan 2024",
            "duration": "10 mos",
            "location": "Remote",
            "intro": "Magnus Tech is a custom software development company. I worked on a pricing-control platform for the Bristol retail chain that combined store data, employee actions, ML pricing recommendations, and photo confirmations into one operational workflow. Operations teams used it to prepare price changes, review evidence from stores, and coordinate corrective actions across a large retail network.",
            "bullets": [
              "Designed and developed Go backend services from scratch for the pricing platform, integrating store data, product information, ML recommendations, and employee actions into a unified backend flow.",
              "Built admin-panel APIs and internal tools for viewing prices, manual overrides, cross-store comparison, and day-to-day operational control.",
              "Integrated multi-channel notification flows across web, email, SMS, and mobile clients and maintained API contracts for frontend and mobile teams.",
              "Covered critical flows with tests and observability and helped bring the product from active development to production release."
            ],
            "stack": [
              "Go",
              "PostgreSQL",
              "Redis",
              "Kafka",
              "MinIO",
              "REST",
              "gRPC",
              "Prometheus",
              "CI/CD"
            ],
            "companyIcon": "assets/icons/magnus.svg",
            "companyUrl": "https://magnustech.com"
          },
          {
            "company": "Exnode",
            "role": "Backend Engineer",
            "period": "Jun 2022 - Mar 2023",
            "duration": "9 mos",
            "location": "Moscow, Russia",
            "intro": "Exnode develops crypto exchange, B2B payment, and P2P trading products. I worked on backend transaction flows where payments, exchange operations, and reporting had to remain consistent, fast, and accurate under real business load.",
            "bullets": [
              "Split a large monolithic backend into smaller services and moved part of internal communication from REST to gRPC, reducing latency and clarifying service boundaries.",
              "Optimized payment and reporting queries using EXPLAIN ANALYZE, cutting several heavy PostgreSQL queries from 10-30 seconds to near real time.",
              "Implemented core product capabilities for P2P exchange and online payments, including pricing logic, service integrations, email notifications, Telegram alerts, and magic-link authentication in a lean two-engineer backend setup.",
              "Investigated and contained a critical currency-conversion incident, rolled back affected transactions, and then strengthened validation, observability, and release discipline to reduce recurrence risk."
            ],
            "stack": [
              "Go",
              "PostgreSQL",
              "Redis",
              "RabbitMQ",
              "REST",
              "gRPC",
              "Grafana",
              "Telegram Bot API"
            ],
            "companyIcon": "assets/icons/light/exnode.svg",
            "companyIconDark": "assets/icons/dark/exnode.svg",
            "companyUrl": "https://exnode.ru"
          },
          {
            "company": "Kaluga Power Supply Company",
            "role": "Backend Engineer (Go)",
            "period": "Dec 2021 - Jun 2022",
            "duration": "6 mos",
            "location": "Remote",
            "intro": "Customer portal and internal administrative system for personal accounts, utility billing, payments, and property-management operations.",
            "bullets": [
              "Designed and launched the core Go backend architecture for a customer portal and administrative system with integrations to 1C, payments, dashboards, and web/mobile clients.",
              "Rewrote a key PHP service in Go, migrating critical 1C integration and payment-processing scenarios without losing business functionality.",
              "Acted as the sole backend engineer on the project, delivering account, billing, and operational views for customers and property management."
            ],
            "stack": [
              "Go",
              "PHP",
              "PostgreSQL",
              "1C integrations",
              "Payments",
              "Dashboards"
            ],
            "companyIcon": "assets/icons/kaluga.svg",
            "companyUrl": "https://kskkaluga.ru"
          },
          {
            "company": "Center for Regional Management of Lipetsk Oblast (CUR)",
            "role": "Java Developer",
            "period": "Aug 2021 - Dec 2021",
            "duration": "4 mos",
            "location": "Lipetsk, Russia",
            "intro": "Regional digital services and monitoring tools that supported government workflows and citizen-facing operational reporting in a legacy-heavy environment.",
            "bullets": [
              "Contributed to Java backend services for regional digital products and monitoring systems, implementing business logic in an environment with many legacy integrations.",
              "Maintained SQL-backed service functionality for public-sector workflows and operational reporting."
            ],
            "stack": [
              "Java",
              "SQL",
              "Backend"
            ]
          }
        ]
      },
      "education": {
        "title": "Education",
        "subtitle": "Formal education and current degree track.",
        "institutionSiteLabel": "University site",
        "items": [
          {
            "institution": "Voronezh State Technical University",
            "degree": "B.S. in Intelligent Automated Systems (part-time)",
            "period": "2025 - Expected graduation: 2030",
            "institutionIcon": "assets/icons/light/vstu.svg",
            "institutionIconDark": "assets/icons/dark/vstu.svg",
            "institutionUrl": "https://cchgeu.ru/"
          },
          {
            "institution": "Voronezh State Technical University",
            "degree": "Vocational diploma in Information Technology and Programming",
            "period": "2021 - 2025",
            "institutionIcon": "assets/icons/light/vstu.svg",
            "institutionIconDark": "assets/icons/dark/vstu.svg",
            "institutionUrl": "https://cchgeu.ru/"
          }
        ]
      },
      "strengths": {
        "title": "Profile",
        "subtitle": "Patterns that best describe my engineering style and career direction.",
        "cards": [
          {
            "title": "Architecture through trade-offs",
            "body": "I make architectural decisions through concrete trade-offs: latency, reliability, operational complexity, team capacity, and future change cost. I am comfortable choosing between Redis and PostgreSQL, gRPC and REST, or a simpler broker-centered design and a broader microservice split."
          },
          {
            "title": "Product + infrastructure",
            "body": "I have built both platform infrastructure and product backends: service mesh components, testing infrastructure, vehicle telemetry, retail pricing, crypto payments, utility billing, and administrative systems."
          },
          {
            "title": "Mentoring and technical leadership",
            "body": "I mentor candidates and interns, explain system design and backend fundamentals, and want my leadership path to stay grounded in hands-on engineering."
          },
          {
            "title": "Go-centered backend engineering",
            "body": "I have 4+ years of commercial Go backend development and 6 months of Java experience. I started in Java, moved to Go, and now focus on explicit system design, simple service boundaries, and low-latency backend services."
          }
        ]
      },
      "skills": {
        "title": "Key stack",
        "groups": [
          {
            "title": "Languages",
            "items": [
              "Go",
              "Java",
              "Python",
              "SQL"
            ]
          },
          {
            "title": "APIs & Messaging",
            "items": [
              "gRPC",
              "REST",
              "MQTT",
              "Kafka",
              "RabbitMQ",
              "NATS"
            ]
          },
          {
            "title": "Data & Storage",
            "items": [
              "PostgreSQL",
              "Redis",
              "MinIO"
            ]
          },
          {
            "title": "Platform",
            "items": [
              "Docker",
              "Kubernetes",
              "Helm",
              "Istio",
              "Linux",
              "Git",
              "CI/CD"
            ]
          },
          {
            "title": "Observability & Quality",
            "items": [
              "Prometheus",
              "Grafana",
              "Loki",
              "Sentry",
              "Allure",
              "Unit/integration testing"
            ]
          },
          {
            "title": "Core areas",
            "items": [
              "Distributed systems",
              "Backend architecture",
              "High-load and low-latency services",
              "Service mesh",
              "Testing infrastructure",
              "Observability",
              "Service reliability",
              "Security",
              "Mentoring"
            ]
          },
          {
            "title": "Spoken languages",
            "items": [
              "Russian (Native)",
              "English (B2 / Working proficiency)"
            ]
          }
        ]
      },
      "preferences": {
        "title": "What recruiters should know",
        "items": [
          "I primarily see myself as a backend engineer and feel strongest in Go, distributed systems, and complex backend logic.",
          "I am comfortable in both product and platform teams: I can switch between latency-sensitive infrastructure and business-facing backend functionality.",
          "I am the best fit for teams solving high-load, low-latency, or technically ambiguous backend problems.",
          "In the next 2-5 years, I want to grow into a tech lead and broader technical leadership role while remaining a deeply technical engineer.",
          "Russian is my native language; English is B2 / working proficiency, and I use it in work when needed.",
          "Outside work, I keep a steady routine around mentoring, cycling, swimming, gym training, and travel; Japan is one of the places I especially want to revisit."
        ]
      },
      "gallery": {
        "title": "A few more photos",
        "subtitle": "I intentionally keep this public photo section small: this site is primarily about my work, but I want it to stay human and real.",
        "items": [
          {
            "src": "assets/photos/matvey-stairs.jpg",
            "position": "center 18%",
            "filter": "brightness(0.8) contrast(1.03) saturate(0.88)",
            "caption": "Stairs from the anime \"Your Name\""
          },
          {
            "src": "assets/photos/matvey-japan.jpg",
            "filter": "brightness(1.06) contrast(1.03) saturate(0.92)",
            "caption": "Kendo training"
          },
          {
            "src": "assets/photos/matvey-cafe.jpg",
            "filter": "brightness(0.96) contrast(1.02) saturate(0.92)",
            "caption": "Photo from a cafe"
          },
          {
            "src": "assets/photos/matvey-mountains.jpg",
            "position": "center 26%",
            "filter": "brightness(0.88) contrast(1.03) saturate(0.92)",
            "caption": "A 1,500m mountain climb"
          },
          {
            "src": "assets/photos/matvey-lake.jpg",
            "filter": "brightness(0.9) contrast(1.02) saturate(0.9)",
            "caption": "Japanese garden"
          },
          {
            "src": "assets/photos/matvey-travel.jpg",
            "filter": "brightness(1.01) contrast(1.03) saturate(0.9)",
            "caption": "Cappadocia atmosphere (Turkey)"
          }
        ]
      },
      "lightbox": {
        "openPhoto": "Open photo",
        "close": "Close photo viewer",
        "previous": "Previous photo",
        "next": "Next photo"
      },
      "footer": "© {year} Matvey Sizov. Personal website for recruiters and hiring managers."
    },
    "ru": {
      "meta": {
        "title": "Матвей Сизов | Backend Developer / Software Engineer",
        "description": "Матвей Сизов - Backend Developer / Software Engineer. Go, распределенные системы, backend с низкой задержкой и надежность production в продуктовых и платформенных командах."
      },
      "brand": "Матвей Сизов",
      "nav": {
        "resume": "Резюме",
        "experience": "Опыт",
        "education": "Образование",
        "strengths": "Профиль",
        "skills": "Стек"
      },
      "langSwitcherLabel": "Переключение языка",
      "theme": {
        "toDark": "Тёмная тема",
        "toLight": "Светлая тема",
        "switcherLabel": "Переключение темы"
      },
      "hero": {
        "kicker": "Backend Developer / Software Engineer",
        "name": "Матвей Сизов",
        "role": "4+ года строю production backend-системы на Go | распределенные системы, высоконагруженная и низколатентная инфраструктура, service mesh и fintech/crypto-продукты.",
        "summary": "Backend-инженер, специализирующийся на Go, распределенных системах и низколатентной инфраструктуре. У меня 4+ года коммерческого опыта в разработке production-сервисов для телеметрии автомобиля и связи car-to-cloud, service mesh-инфраструктуры, операционного ценообразования в ритейле, криптоплатежей и обменных сценариев, коммунального биллинга и государственных цифровых сервисов. Моя сильная сторона - превращать неоднозначные требования в надежные системы: прояснять компромиссы, выбирать архитектуру, реализовывать ключевые сервисы, добавлять наблюдаемость и безопасность и доводить поставку через CI/CD до production-эксплуатации.",
        "photo": {
          "src": "assets/photos/matvey-formal.png",
          "position": "center 22%",
          "filter": "brightness(0.99) contrast(1.02) saturate(0.96)",
          "caption": "Деловой портрет"
        },
        "facts": [
          {
            "icon": "assets/icons/light/location.svg",
            "iconDark": "assets/icons/dark/location.svg",
            "label": "Локация",
            "value": "Москва, Россия"
          },
          {
            "icon": "assets/icons/light/briefcase.svg",
            "iconDark": "assets/icons/dark/briefcase.svg",
            "label": "Опыт",
            "value": "Go - 4+ года\nJava - 6 месяцев"
          },
          {
            "icon": "assets/icons/light/layers.svg",
            "iconDark": "assets/icons/dark/layers.svg",
            "label": "Основной стек",
            "value": "Go, распределенные системы"
          },
          {
            "icon": "assets/icons/light/star.svg",
            "iconDark": "assets/icons/dark/star.svg",
            "label": "Формат",
            "value": "Удаленно / релокация"
          }
        ]
      },
      "resume": {
        "fileBaseName": "Matvey_Sizov",
        "title": "Скачать резюме",
        "labels": {
          "pdf": "Для отправки и печати",
          "docx": "Редактируемый источник",
          "txt": "Текстовая версия"
        },
        "files": {
          "pdf": "resume/ru/Matvey_Sizov.pdf",
          "docx": "resume/ru/Matvey_Sizov.docx",
          "txt": "resume/ru/Matvey_Sizov.txt"
        },
        "downloadNames": {
          "pdf": "Matvey_Sizov.pdf",
          "docx": "Matvey_Sizov.docx",
          "txt": "Matvey_Sizov.txt"
        }
      },
      "experience": {
        "title": "Опыт",
        "companySiteLabel": "Сайт компании",
        "items": [
          {
            "company": "АТОМ",
            "role": "Senior Software Engineer",
            "period": "Фев 2025 - настоящее время",
            "duration": "1 г. 2 мес.",
            "location": "Удаленно",
            "intro": "АТОМ развивает платформу электромобиля. В направлении коммуникаций и телеметрии я строю backend-сервисы для защищенной связи автомобиля с облаком, интеграции внешних клиентов и надежной работы с низкой задержкой.",
            "bullets": [
              "Согласовал с руководством, смежными командами и архитекторами MVP-архитектуру для неоднозначных требований, упростив изначально переусложненную декомпозицию сервисов до брокер-центричного дизайна, который можно было выпустить и эксплуатировать.",
              "Спроектировал и совместно разработал MQTT-брокер на Mochi MQTT с mTLS-защитой и восстановлением сессий и состояния через Redis для постоянной коммуникации автомобиля с облаком и безопасных перезапусков.",
              "Проверил ограничения по задержке в первоначальной микросервисной декомпозиции и продвинул подход с центральным брокером, который выдержал ~70K запросов в секунду при p99 ниже 50 мс.",
              "С нуля построил production-инструменты наблюдаемости и поставки: метрики, логи, трейсы, дашборды, алерты, мониторинг задержек/пропускной способности и бизнес-состояния, CI/CD и автоматизацию деплоя.",
              "Усилил доверенный контур связи, внедрив аутентификацию клиентов по сертификатам и авторизацию на уровне топиков."
            ],
            "stack": [
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
              "Helm",
              "Kubernetes",
              "CI/CD"
            ],
            "companyIcon": "assets/icons/atom.svg",
            "companyUrl": "https://atom.auto"
          },
          {
            "company": "Lukyanov Tech",
            "role": "Part-Time Mentor / Mock Interviewer",
            "period": "Май 2024 - настоящее время",
            "duration": "1 г. 11 мес.",
            "location": "Удаленно",
            "intro": "Проект неполной занятости по менторству backend-кандидатов, которые готовятся к инженерным интервью. Я веду практические треки подготовки и пробные собеседования с фокусом на backend-базу, system design, техническую коммуникацию и структурированную обратную связь.",
            "bullets": [
              "Провожу пробные собеседования и менторю кандидатов по backend-базе, проектированию систем, технической коммуникации и структуре ответов.",
              "Даю структурированную обратную связь и улучшаю материалы и процессы подготовки, чтобы кандидаты могли превращать слабые сигналы на интервью в конкретные практические задачи."
            ],
            "stack": [
              "Mentoring",
              "Mock interviews",
              "System design",
              "Technical communication"
            ],
            "companyIcon": "assets/icons/lukyanov.png",
            "companyUrl": "https://lukyanov.tech"
          },
          {
            "company": "Sber Tech",
            "role": "Software Engineer",
            "period": "Янв 2024 - Фев 2025",
            "duration": "1 г. 1 мес.",
            "location": "Москва, Россия",
            "intro": "Sber Tech развивает Platform V, крупную корпоративную платформу. В команде Service Mesh & Platform Infrastructure я работал с сильно кастомизированным форком Istio и смежными Kubernetes-компонентами для управления рабочими нагрузками, контроля политик и платформенной интеграции.",
            "bullets": [
              "Восстановил набор автотестов в сильно кастомизированном форке Istio, вернув юнит-тесты в ежедневную разработку и подняв покрытие до 80%.",
              "Спроектировал и реализовал Go-фреймворк интеграционного тестирования, который поднимал изолированные Kubernetes-окружения, запускал тесты параллельно и генерировал Allure-отчеты для CI.",
              "Починил CI-пайплайны и расширил автоматизацию интеграционными тестами до ~95% критического функционала, сдвинув поиск дефектов на более ранний этап и помогая исправить несколько критичных проблем.",
              "Наставлял ~10 стажеров из Школы 21 по фреймворку и процессу автоматизации, помогая переводить ручные QA-сценарии в масштабируемые автотесты.",
              "Спроектировал и реализовал пользовательский ресурс Kubernetes для управления связями control plane/data plane в Istio; позже решение представили внутри компании как целевой платформенный подход."
            ],
            "stack": [
              "Go",
              "Kubernetes",
              "Istio",
              "gRPC",
              "REST",
              "PostgreSQL",
              "CI/CD",
              "Allure"
            ],
            "companyIcon": "assets/icons/light/sbertech.svg",
            "companyIconDark": "assets/icons/dark/sbertech.svg",
            "companyUrl": "https://sbertech.ru"
          },
          {
            "company": "Magnus Tech",
            "role": "Software Engineer",
            "period": "Мар 2023 - Янв 2024",
            "duration": "10 мес.",
            "location": "Удаленно",
            "intro": "Magnus Tech - компания заказной разработки ПО. Я работал над платформой контроля цен для сети магазинов «Бристоль», которая объединяла данные магазинов, действия сотрудников, ML-рекомендации по ценам и фотоподтверждения в единый операционный процесс. Операционные команды использовали продукт для подготовки изменений цен, проверки подтверждений из магазинов и координации корректирующих действий по большой розничной сети.",
            "bullets": [
              "Спроектировал и разрабатывал с нуля Go backend-сервисы для платформы ценообразования, интегрируя данные магазинов, продуктовую информацию, ML-рекомендации и действия сотрудников в единый backend-поток.",
              "Реализовал API панели администрирования и внутренние инструменты для просмотра цен, ручных корректировок, сравнения между магазинами и ежедневного операционного контроля.",
              "Интегрировал мультиканальные уведомления для веб, email, SMS и мобильных клиентов и поддерживал API-контракты для frontend- и mobile-команд.",
              "Покрывал критичные сценарии тестами и наблюдаемостью и участвовал в доведении продукта от активной разработки до production-релиза."
            ],
            "stack": [
              "Go",
              "PostgreSQL",
              "Redis",
              "Kafka",
              "MinIO",
              "REST",
              "gRPC",
              "Prometheus",
              "CI/CD"
            ],
            "companyIcon": "assets/icons/magnus.svg",
            "companyUrl": "https://magnustech.com"
          },
          {
            "company": "Exnode",
            "role": "Backend Engineer",
            "period": "Июн 2022 - Мар 2023",
            "duration": "9 мес.",
            "location": "Москва, Россия",
            "intro": "Exnode развивает продукты криптобиржи, B2B-платежей и P2P-обмена. Я работал над backend-частями транзакционных сценариев, где платежи, обменные операции и отчетность должны были оставаться консистентными, быстрыми и точными под реальной бизнес-нагрузкой.",
            "bullets": [
              "Разделил большой монолитный backend на более мелкие сервисы и перевел часть внутреннего взаимодействия с REST на gRPC, снижая задержку и проясняя границы сервисов.",
              "Оптимизировал платежные и отчетные запросы через EXPLAIN ANALYZE, сократив несколько тяжелых PostgreSQL-запросов с 10-30 секунд до почти реального времени.",
              "Реализовал ключевые продуктовые возможности для P2P-обмена и онлайн-платежей, включая логику курсов, интеграции между сервисами, email-уведомления, Telegram-оповещения и magic-link аутентификацию в компактной backend-команде из двух инженеров.",
              "Разобрал и локализовал критический инцидент с конвертацией валют, откатил затронутые транзакции и затем усилил валидацию, наблюдаемость и дисциплину релизов, чтобы снизить риск повторения."
            ],
            "stack": [
              "Go",
              "PostgreSQL",
              "Redis",
              "RabbitMQ",
              "REST",
              "gRPC",
              "Grafana",
              "Telegram Bot API"
            ],
            "companyIcon": "assets/icons/light/exnode.svg",
            "companyIconDark": "assets/icons/dark/exnode.svg",
            "companyUrl": "https://exnode.ru"
          },
          {
            "company": "Калужская сбытовая компания",
            "role": "Backend Engineer (Go)",
            "period": "Дек 2021 - Июн 2022",
            "duration": "6 мес.",
            "location": "Удаленно",
            "intro": "Клиентский портал и внутренняя административная система для лицевых счетов, коммунального биллинга, платежей и операционных сценариев управления объектами.",
            "bullets": [
              "Спроектировал и запустил основную Go backend-архитектуру клиентского портала и административной системы с интеграциями в 1С, платежи, дашборды и веб/мобильные клиенты.",
              "Переписал ключевой PHP-сервис на Go, перенеся критичные сценарии интеграции с 1С и обработки платежей без потери бизнес-функциональности.",
              "Работал единственным backend-инженером на проекте, реализуя аккаунты, биллинг и операционные представления для клиентов и управления объектами."
            ],
            "stack": [
              "Go",
              "PHP",
              "PostgreSQL",
              "1C integrations",
              "Payments",
              "Dashboards"
            ],
            "companyIcon": "assets/icons/kaluga.svg",
            "companyUrl": "https://kskkaluga.ru"
          },
          {
            "company": "Центр Управления Регионом Липецкой области (ЦУР)",
            "role": "Java Developer",
            "period": "Авг 2021 - Дек 2021",
            "duration": "4 мес.",
            "location": "Липецк, Россия",
            "intro": "Региональные цифровые сервисы и инструменты мониторинга, которые поддерживали государственные процессы и операционную отчетность для сценариев, связанных с гражданами, в окружении с большим количеством легаси.",
            "bullets": [
              "Участвовал в разработке Java backend-сервисов для региональных цифровых продуктов и систем мониторинга, реализуя бизнес-логику в окружении с большим количеством легаси-интеграций.",
              "Поддерживал сервисную функциональность на основе SQL для государственных процессов и операционной отчетности."
            ],
            "stack": [
              "Java",
              "SQL",
              "Backend"
            ]
          }
        ]
      },
      "education": {
        "title": "Образование",
        "subtitle": "Формальное образование и текущая степень.",
        "institutionSiteLabel": "Сайт университета",
        "items": [
          {
            "institution": "Воронежский государственный технический университет",
            "degree": "Бакалавриат, интеллектуальные автоматизированные системы (заочно)",
            "period": "2025 - Ожидаемое окончание: 2030",
            "institutionIcon": "assets/icons/light/vstu.svg",
            "institutionIconDark": "assets/icons/dark/vstu.svg",
            "institutionUrl": "https://cchgeu.ru/"
          },
          {
            "institution": "Воронежский государственный технический университет",
            "degree": "СПО, информационные технологии и программирование",
            "period": "2021 - 2025",
            "institutionIcon": "assets/icons/light/vstu.svg",
            "institutionIconDark": "assets/icons/dark/vstu.svg",
            "institutionUrl": "https://cchgeu.ru/"
          }
        ]
      },
      "strengths": {
        "title": "Профиль",
        "subtitle": "Паттерны, которые лучше всего описывают мой инженерный стиль и карьерное направление.",
        "cards": [
          {
            "title": "Архитектура через компромиссы",
            "body": "Я принимаю архитектурные решения через конкретные компромиссы: задержка, надежность, операционная сложность, возможности команды и стоимость будущих изменений. Мне комфортно выбирать между Redis и PostgreSQL, gRPC и REST, более простым брокер-центричным дизайном и более широкой микросервисной декомпозицией."
          },
          {
            "title": "Продукт + инфраструктура",
            "body": "Я строил и платформенную инфраструктуру, и продуктовые backend-системы: service mesh-компоненты, тестовую инфраструктуру, телеметрию автомобиля, ценообразование в ритейле, криптоплатежи, коммунальный биллинг и административные системы."
          },
          {
            "title": "Менторство и техническое лидерство",
            "body": "Я менторю кандидатов и стажеров, объясняю system design и backend-базу и хочу, чтобы мой путь в лидерство оставался связанным с практической инженерией."
          },
          {
            "title": "Backend-инженерия с фокусом на Go",
            "body": "У меня 4+ года коммерческой backend-разработки на Go и 6 месяцев опыта на Java. Я начал с Java, перешел на Go и сейчас фокусируюсь на явном проектировании систем, простых границах сервисов и низколатентных backend-сервисах."
          }
        ]
      },
      "skills": {
        "title": "Ключевой стек",
        "groups": [
          {
            "title": "Языки",
            "items": [
              "Go",
              "Java",
              "Python",
              "SQL"
            ]
          },
          {
            "title": "API и обмен сообщениями",
            "items": [
              "gRPC",
              "REST",
              "MQTT",
              "Kafka",
              "RabbitMQ",
              "NATS"
            ]
          },
          {
            "title": "Данные и хранилища",
            "items": [
              "PostgreSQL",
              "Redis",
              "MinIO"
            ]
          },
          {
            "title": "Платформа",
            "items": [
              "Docker",
              "Kubernetes",
              "Helm",
              "Istio",
              "Linux",
              "Git",
              "CI/CD"
            ]
          },
          {
            "title": "Наблюдаемость и качество",
            "items": [
              "Prometheus",
              "Grafana",
              "Loki",
              "Sentry",
              "Allure",
              "Unit/integration testing"
            ]
          },
          {
            "title": "Ключевые направления",
            "items": [
              "Распределенные системы",
              "Backend-архитектура",
              "Высоконагруженные и низколатентные сервисы",
              "Service mesh",
              "Тестовая инфраструктура",
              "Наблюдаемость",
              "Надежность сервисов",
              "Безопасность",
              "Менторство"
            ]
          },
          {
            "title": "Разговорные языки",
            "items": [
              "Русский (родной)",
              "Английский (B2 / рабочий уровень)"
            ]
          }
        ]
      },
      "preferences": {
        "title": "Что важно знать рекрутеру",
        "items": [
          "Я в первую очередь вижу себя backend-инженером и сильнее всего чувствую себя в Go, распределенных системах и сложной backend-логике.",
          "Мне комфортно и в продуктовых, и в платформенных командах: я умею переключаться между инфраструктурой, чувствительной к задержкам, и backend-функциональностью, которая ближе к бизнесу.",
          "Лучше всего подхожу командам, которые решают высоконагруженные, низколатентные или технически неоднозначные backend-задачи.",
          "В ближайшие 2-5 лет хочу вырасти в роль техлида и технического лидера, оставаясь при этом глубоко техническим инженером.",
          "Русский - мой родной язык; английский - B2 / рабочий уровень, и я использую его в работе, когда это нужно.",
          "Вне работы я поддерживаю стабильную рутину вокруг менторства, велосипеда, плавания, тренировок в зале и путешествий; Япония - одно из мест, куда мне особенно хочется вернуться."
        ]
      },
      "gallery": {
        "title": "Еще несколько фото",
        "subtitle": "Этот публичный фото-раздел я специально держу небольшим: сайт в первую очередь про мою работу, но мне хочется, чтобы он оставался живым и человеческим.",
        "items": [
          {
            "src": "assets/photos/matvey-stairs.jpg",
            "position": "center 18%",
            "filter": "brightness(0.8) contrast(1.03) saturate(0.88)",
            "caption": "Лестница из аниме «Твоё имя»"
          },
          {
            "src": "assets/photos/matvey-japan.jpg",
            "filter": "brightness(1.06) contrast(1.03) saturate(0.92)",
            "caption": "Тренировка по Кэндо"
          },
          {
            "src": "assets/photos/matvey-cafe.jpg",
            "filter": "brightness(0.96) contrast(1.02) saturate(0.92)",
            "caption": "Фото из кафе"
          },
          {
            "src": "assets/photos/matvey-mountains.jpg",
            "position": "center 26%",
            "filter": "brightness(0.88) contrast(1.03) saturate(0.92)",
            "caption": "Подъём на 1 500м в гору"
          },
          {
            "src": "assets/photos/matvey-lake.jpg",
            "filter": "brightness(0.9) contrast(1.02) saturate(0.9)",
            "caption": "Японский сад"
          },
          {
            "src": "assets/photos/matvey-travel.jpg",
            "filter": "brightness(1.01) contrast(1.03) saturate(0.9)",
            "caption": "Атмосфера Каппадокии (Турция)"
          }
        ]
      },
      "lightbox": {
        "openPhoto": "Открыть фото",
        "close": "Закрыть просмотр фото",
        "previous": "Предыдущее фото",
        "next": "Следующее фото"
      },
      "footer": "© {year} Матвей Сизов. Сайт-визитка для рекрутеров и менеджеров по найму."
    }
  }
};
