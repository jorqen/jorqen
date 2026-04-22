window.JORQEN_RESUME_DATA = {
  "contacts": {
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
        "role": "4+ years building production Go backends and distributed systems | high-load and low-latency infrastructure, service mesh, and fintech/crypto products.",
        "summary": "I am a backend and distributed systems engineer with 4+ years of commercial experience in Go. I work across both product and infrastructure domains and am strongest where ambiguous requirements need to become a concrete production system with sound trade-offs, observability, security, testing, and reliable delivery. Across recent roles I have built backend systems for secure vehicle telemetry and car-to-cloud connectivity, service-mesh platform infrastructure and testing frameworks, retail pricing operations, crypto payments and exchange flows, utility billing, and public digital services.",
        "photo": {
          "src": "assets/photos/matvey-studio.jpg",
          "position": "center 18%",
          "filter": "brightness(0.94) contrast(1.04) saturate(0.9)",
          "caption": "Studio portrait"
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
            "intro": "ATOM develops an electric vehicle platform. I work in communications and telemetry on backend services responsible for secure vehicle-to-cloud connectivity, external client integration, and operational reliability under low-latency requirements.",
            "bullets": [
              "Worked with leadership, peer teams, and architects to turn ambiguous and partially unrealistic requirements into a releasable first version, simplifying an overengineered concept into a cleaner broker-centered architecture.",
              "Designed and co-developed an mTLS-secured MQTT broker on Mochi MQTT with Redis-backed state recovery for persistent vehicle-to-cloud communication and safe session restoration after restarts.",
              "Challenged the initial microservice decomposition, showed it could not meet target latency, and advanced a central-broker design that sustained ~70K requests per second at p99 below 50 ms.",
              "Built production observability and delivery tooling from scratch after losing dedicated DevOps support: metrics, logs, traces, dashboards, alerts, latency/throughput monitoring, business health signals, CI/CD, and deployment automation.",
              "Improved security and reliability by enforcing certificate-based client authentication and topic-level access control for trusted connectivity."
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
            "company": "Sber Tech",
            "role": "Software Engineer",
            "period": "Jan 2024 - Feb 2025",
            "duration": "1 yr 1 mo",
            "location": "Moscow, Russia",
            "intro": "Sber Tech develops Platform V, a large enterprise platform. I worked on a heavily customized Istio fork and adjacent infrastructure components used for workload control, policy enforcement, and platform integration in the Service Mesh & Platform Infrastructure team.",
            "bullets": [
              "Restored broken automated testing in a heavily modified Istio fork, returning unit tests to the daily engineering process and raising coverage to 80%.",
              "Designed and built a Go integration-testing framework that provisioned isolated Kubernetes environments, ran tests in parallel, and generated Allure reports for CI.",
              "Repaired CI pipelines and shifted defect detection earlier in the development cycle by expanding integration-test automation to about 95% of critical functionality and fixing several critical defects.",
              "Mentored around 10 interns - students from School 21, on the framework and automation process, helping convert manual QA scenarios into scalable automated tests.",
              "Independently designed and implemented a custom Kubernetes resource for managing control-plane and data-plane relationships in Istio; the solution was later presented internally as a target platform approach."
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
            "company": "Lukyanov Tech",
            "role": "Part-Time Mentor / Mock Interviewer",
            "period": "Jan 2024 - Present",
            "duration": "2 yrs 3 mos",
            "location": "Remote",
            "intro": "A part-time project focused on mentorship, interview preparation, and growth of backend candidates. In practice, it is a hands-on educational service built around individual preparation tracks and mock interviews for engineers who want stronger fundamentals, clearer system-design thinking, and better performance in commercial hiring loops.",
            "bullets": [
              "Conduct mock interviews and mentor candidates on backend fundamentals, system design, technical communication, and answer structure.",
              "Help improve the mentoring process, hands-on preparation, and feedback quality for students targeting engineering roles."
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
            "company": "Magnus Tech",
            "role": "Software Engineer",
            "period": "Mar 2023 - Jan 2024",
            "duration": "10 mos",
            "location": "Remote",
            "intro": "Magnus Tech is a custom development company. I worked on a pricing-control platform for the Bristol shop retail chain that combines store data, employee actions, ML pricing recommendations, and photo confirmation into one operational system. The product was used by operational teams to form price, review store evidence, and coordinate corrective actions across a large retail network.",
            "bullets": [
              "Developed Go backend services from scratch for the pricing platform and integrated store data, product information, ML recommendations, and employee actions into a unified backend flow.",
              "Designed admin-panel APIs and internal tools for viewing prices, manual overrides, cross-store comparison, and day-to-day operational control.",
              "Integrated notification flows across web, email, SMS, and mobile channels and maintained API contracts for frontend and mobile teams.",
              "Wrote tests, maintained observability, and helped bring the product from active development to production release."
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
            "period": "Sep 2022 - Mar 2023",
            "duration": "6 mos",
            "location": "Moscow, Russia",
            "intro": "Exnode develops crypto exchange, B2B payment, and P2P trading products. I worked on backend parts of transactional flows where payments, exchange operations, and reporting had to stay consistent, fast and exact under real business load.",
            "bullets": [
              "Split a large monolithic backend into smaller services and replaced part of internal REST communication with gRPC to reduce latency and simplify service boundaries.",
              "Optimized payment and reporting queries using EXPLAIN ANALYZE, cutting several heavy PostgreSQL queries from 10-30 seconds to near real time.",
              "Implemented core product capabilities for P2P exchange and online payments, including pricing logic, service integrations, email notifications, Telegram alerts, and magic-link authentication in a lean two-engineer backend setup.",
              "Investigated and localized a critical currency-conversion incident, rolled back affected transactions, and then strengthened validation, observability, and release discipline."
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
            "role": "GoLang Developer",
            "period": "Oct 2021 - Mar 2022",
            "duration": "5 mos",
            "location": "Remote",
            "intro": "A customer portal and internal administrative system for personal accounts, billing, payments, and property-management operations.",
            "bullets": [
              "Developed and launched the core backend architecture for a customer portal and administrative system with integrations to 1C, payments, dashboards, and web/mobile clients.",
              "Rewrote a key service from PHP to Go, migrating critical 1C integration and payment-processing scenarios without losing business functionality.",
              "Worked as the sole backend engineer on the project, delivering account, billing, and operational views for customers and property management."
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
            "period": "Jun 2021 - Nov 2021",
            "duration": "5 mos",
            "location": "Lipetsk, Russia",
            "intro": "The projects were regional digital services and monitoring tools that supported government workflows and citizen-facing operational reporting.",
            "bullets": [
              "Contributed to backend services for regional digital products and monitoring systems in an environment with a large amount of legacy systems.",
              "Worked on business logic and service development for government processes."
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
            "degree": "Secondary vocational education in Information Technology and Programming, graduated with honors",
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
            "body": "For me, choosing the right tool and the right system shape matters: Redis or PostgreSQL, gRPC or REST, a more complex setup or a simplified architecture to minimize latency and improve adaptability."
          },
          {
            "title": "Product + infrastructure",
            "body": "My recent roles are infrastructure-focused, but I also built product backend systems for retail pricing, crypto payments, utility billing, and internal administrative panels."
          },
          {
            "title": "Leadership trajectory",
            "body": "I already mentor candidates and interns, and in the coming years I want to grow into technical leadership without losing hands-on depth and systems thinking."
          },
          {
            "title": "From Java to Go",
            "body": "I have 4 years of commercial backend development in Go and 6 months in Java. I started with Java as a teenager, got my first commercial backend role in Java, and then moved to Go because explicit engineering, simpler syntax, and low-latency services fit me better."
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
              "Testing frameworks"
            ]
          },
          {
            "title": "Core areas",
            "items": [
              "Distributed systems",
              "Backend architecture",
              "High-load services",
              "Testing infrastructure",
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
          "Outside work, I mentor candidates, cycle, swim, go to the gym, and enjoy traveling; Japan is one of the places I especially want to revisit."
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
        "role": "4+ года строю production backend-системы и распределенные системы на Go | high-load and low-latency инфраструктура, service mesh и fintech/crypto-продукты.",
        "summary": "Я backend-инженер и инженер распределенных систем с 4+ годами коммерческого опыта на Go. Работаю и в продуктовых, и в инфраструктурных доменах и сильнее всего проявляюсь там, где размытые требования нужно превратить в конкретную production-систему с понятными компромиссами, наблюдаемостью, безопасностью, тестированием и надежной поставкой. В последних ролях я строил backend-системы для защищенной телеметрии и связи автомобиля с облаком, service mesh-инфраструктуры и тестовых фреймворков, операционного ценообразования в ритейле, криптоплатежей и обменных сценариев, коммунального биллинга и государственных цифровых сервисов.",
        "photo": {
          "src": "assets/photos/matvey-studio.jpg",
          "position": "center 18%",
          "filter": "brightness(0.94) contrast(1.04) saturate(0.9)",
          "caption": "Студийный портрет"
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
            "intro": "АТОМ развивает платформу электромобиля. Я работаю в направлении коммуникаций и телеметрии над backend-сервисами, которые отвечают за защищенную связь автомобиля с облаком, интеграцию внешних клиентов и операционную надежность при жестких требованиях к задержке.",
            "bullets": [
              "Работал с руководством, смежными командами и архитекторами, чтобы превратить размытые и частично нереалистичные требования в релизопригодную первую версию, упростив переусложненную концепцию до более чистой архитектуры вокруг брокера.",
              "Спроектировал и совместно разработал MQTT-брокер на Mochi MQTT с mTLS-защитой и восстановлением состояния через Redis для постоянной коммуникации автомобиля с облаком и безопасного восстановления сессий после перезапуска.",
              "Оспорил первоначальную декомпозицию на микросервисы, показал, что она не укладывается в целевую задержку, и продвинул дизайн с центральным брокером, который выдержал ~70K запросов в секунду при p99 ниже 50 мс.",
              "С нуля собрал production-инструментарий наблюдаемости и поставки после потери выделенной DevOps-поддержки: метрики, логи, трейсы, дашборды, алерты, мониторинг задержек и пропускной способности, бизнесовые сигналы состояния сервиса, CI/CD и автоматизацию деплоя.",
              "Усилил безопасность и надежность, внедрив аутентификацию клиентов по сертификатам и контроль доступа на уровне топиков для доверенного контура связи."
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
            "company": "Sber Tech",
            "role": "Software Engineer",
            "period": "Янв 2024 - Фев 2025",
            "duration": "1 г. 1 мес.",
            "location": "Москва, Россия",
            "intro": "Sber Tech развивает Platform V, крупную корпоративную платформу. Я работал с сильно кастомизированным форком Istio и смежными инфраструктурными компонентами для управления рабочими нагрузками, контроля политик и платформенной интеграции в команде Service Mesh & Platform Infrastructure.",
            "bullets": [
              "Восстановил сломанное автотестирование в сильно модифицированном форке Istio, вернув юнит-тесты в ежедневный инженерный процесс и подняв покрытие до 80%.",
              "Спроектировал и реализовал Go-фреймворк интеграционного тестирования, который поднимал изолированные Kubernetes-окружения, запускал тесты параллельно и генерировал Allure-отчеты для CI.",
              "Починил CI-пайплайны и сдвинул поиск дефектов на более ранний этап разработки, расширив автоматизацию интеграционными тестами примерно до 95% критического функционала и исправив несколько критичных дефектов.",
              "Наставлял около 10 стажеров - студентов Школы 21, по фреймворку и процессу автоматизации, помогая переводить ручные QA-сценарии в масштабируемые автотесты.",
              "Самостоятельно спроектировал и реализовал пользовательский ресурс Kubernetes для управления связями control plane и data plane в Istio; позже решение представили внутри компании как целевой платформенный подход."
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
            "company": "Lukyanov Tech",
            "role": "Part-Time Mentor / Mock Interviewer",
            "period": "Янв 2024 - настоящее время",
            "duration": "2 г. 3 мес.",
            "location": "Удаленно",
            "intro": "Проект неполной занятости в области менторства, подготовки к интервью и роста backend-кандидатов. На практике это прикладной образовательный сервис, построенный вокруг индивидуальных треков подготовки и пробных интервью для инженеров, которым нужны более сильная база, более ясное системное мышление и лучший результат в коммерческих процессах найма.",
            "bullets": [
              "Провожу пробные собеседования и менторю кандидатов по backend-базе, проектированию систем, технической коммуникации и структуре ответов.",
              "Помогаю улучшать процесс менторства, практическую подготовку и качество обратной связи для студентов, идущих в инженерные роли."
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
            "company": "Magnus Tech",
            "role": "Software Engineer",
            "period": "Мар 2023 - Янв 2024",
            "duration": "10 мес.",
            "location": "Удаленно",
            "intro": "Magnus Tech - компания заказной разработки. Я работал над платформой контроля цен для сети магазинов «Бристоль», которая объединяет данные магазинов, действия сотрудников, ML-рекомендации по ценам и фотоподтверждения в единую операционную систему. Продукт использовался операционными командами для формирования цен, проверки подтверждений из магазинов и координации корректирующих действий по большой розничной сети.",
            "bullets": [
              "С нуля разрабатывал Go backend-сервисы для платформы ценообразования и интегрировал данные магазинов, продуктовую информацию, ML-рекомендации и действия сотрудников в единый backend-поток.",
              "Спроектировал API панели администрирования и внутренние инструменты для просмотра цен, ручных корректировок, сравнения между магазинами и ежедневного операционного контроля.",
              "Интегрировал потоки уведомлений для веб, email, SMS и мобильных каналов и поддерживал API-контракты для frontend- и mobile-команд.",
              "Писал тесты, поддерживал наблюдаемость и участвовал в доведении продукта от активной разработки до production-релиза."
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
            "period": "Сен 2022 - Мар 2023",
            "duration": "6 мес.",
            "location": "Москва, Россия",
            "intro": "Exnode развивает продукты криптобиржи, B2B-платежей и P2P-обмена. Я работал над backend-частями транзакционных сценариев, где платежи, обменные операции, и отчетность должны были оставаться консистентными, быстрыми и точными под реальной бизнес-нагрузкой.",
            "bullets": [
              "Разделил большой монолитный backend на более мелкие сервисы и заменил часть внутреннего REST-взаимодействия на gRPC, чтобы снизить задержку и упростить границы сервисов.",
              "Оптимизировал платежные и отчетные запросы через EXPLAIN ANALYZE, сократив несколько тяжелых PostgreSQL-запросов с 10-30 секунд до почти реального времени.",
              "Реализовал ключевые продуктовые возможности для P2P-обмена и онлайн-платежей, включая логику курсов, интеграции между сервисами, email-уведомления, Telegram-оповещения и magic-link аутентификацию в компактной backend-команде из двух инженеров.",
              "Разобрал и локализовал критический инцидент с конвертацией валют, откатил затронутые транзакции и затем усилил валидацию, наблюдаемость и дисциплину релизов."
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
            "role": "GoLang Developer",
            "period": "Окт 2021 - Мар 2022",
            "duration": "5 мес.",
            "location": "Удаленно",
            "intro": "Клиентский портал и внутренняя административная система для лицевых счетов, биллинга, платежей и операционных сценариев управления объектами.",
            "bullets": [
              "Разработал и запустил основную backend-архитектуру клиентского портала и административной системы с интеграциями в 1С, платежи, дашборды и веб/мобильные клиенты.",
              "Переписал на Go ключевой сервис, ранее реализованный на PHP: перенес критичные сценарии интеграции с 1С и обработки платежей без потери бизнес-функциональности.",
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
            "period": "Июн 2021 - Ноя 2021",
            "duration": "5 мес.",
            "location": "Липецк, Россия",
            "intro": "Проекты представляли собой региональные цифровые сервисы и инструменты мониторинга, которые поддерживали государственные процессы и операционную отчетность для сценариев, связанных с гражданами.",
            "bullets": [
              "Участвовал в разработке backend-сервисов для региональных цифровых продуктов и систем мониторинга в окружении с большим количеством легаси-систем.",
              "Работал над бизнес-логикой и сервисной разработкой для государственных процессов."
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
            "degree": "СПО, информационные технологии и программирование, диплом с отличием",
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
            "body": "Мне важно выбирать правильный инструмент и правильную форму системы: Redis или PostgreSQL, gRPC или REST, более сложная схема или упрощение архитектуры ради минимизации задержки и увеличения мобильности."
          },
          {
            "title": "Продукт + инфраструктура",
            "body": "Последние роли у меня инфраструктурные, но я также строил продуктовые backend-системы для ценообразования в ритейле, криптоплатежей, коммунального биллинга и внутренних административных панелей."
          },
          {
            "title": "Траектория в лидерство",
            "body": "Я уже менторю кандидатов и стажеров, а в ближайшие годы хочу вырасти в техническое лидерство, не теряя прикладной глубины и системного мышления."
          },
          {
            "title": "От Java к Go",
            "body": "У меня 4 года коммерческой backend-разработки на Go и 6 месяцев на Java. Я начал с Java еще подростком, получил первую коммерческую backend-роль на Java, а затем перешел в Go, потому что мне ближе явная инженерия, более простой синтаксис и сервисы с низкой задержкой."
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
              "Testing frameworks"
            ]
          },
          {
            "title": "Ключевые направления",
            "items": [
              "Distributed systems",
              "Backend architecture",
              "High-load services",
              "Testing infrastructure",
              "Service reliability",
              "Security",
              "Mentoring"
            ]
          },
          {
            "title": "Разговорные языки",
            "items": [
              "Russian (Native)",
              "English (B2 / Working proficiency)"
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
          "Вне работы я менторю кандидатов, катаюсь на велосипеде, плаваю, хожу в зал и люблю путешествовать; Япония - одно из мест, куда мне особенно хочется вернуться."
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
