# MATVEY SIZOV

Moscow, Russia | Open to remote work and relocation  
Website: https://jorqen.github.io/jorqen/ | GitHub: https://github.com/jorqen | LinkedIn: https://www.linkedin.com/in/jorqen | Telegram: https://t.me/jorqen

4+ years building Go backend systems and distributed services | from EV vehicle-to-cloud communication at ~70K req/s to service-mesh automation, retail pricing, and crypto payments.

## EXPERIENCE

### Atom | Senior Software Engineer, Communications & Telemetry (Russia, Feb 2025 - Present)

[Atom](https://atom.auto) is building an electric vehicle platform in Russia. I work on backend services that connect the car, cloud, and external clients, with emphasis on latency, security, and operational reliability.

- Worked with leadership, peer teams, and architects to turn ambiguous and partly unrealistic requirements into a releasable backend architecture for a zero-to-one broker initiative.
- Designed and developed an mTLS-secured MQTT broker with Redis-backed state recovery for persistent vehicle-to-cloud communication and restart-safe session handling.
- Challenged an initial microservice decomposition, proved it could not meet the target latency, and pushed a broker-centered design that sustained ~70K requests per second at p99 below 50 ms.
- Built production observability and delivery tooling from scratch after the team lost dedicated DevOps support: metrics, logs, traces, dashboards, alerts, CI/CD, and deployment automation.
- Selected key libraries and system components, including Redis for durable in-memory state and access-control mechanisms for trusted client connectivity.

Stack: Go, MQTT, Redis, PostgreSQL, Kafka, gRPC, mTLS, Prometheus, Grafana, Loki, Sentry, Docker, Kubernetes, CI/CD

### SberTech | Software Engineer, Service Mesh & Platform Infrastructure (Russia, Jan 2024 - Feb 2025)

[SberTech](https://sbertech.ru) develops Platform V, a large enterprise platform. I worked on a heavily customized Istio fork and adjacent infrastructure components used for service-mesh control, policy enforcement, and platform integration.

- Restored broken automated testing for a heavily customized Istio fork, bringing unit tests back into the daily engineering workflow and raising unit coverage to 80%.
- Designed and built a Go integration-testing framework that provisioned isolated Kubernetes environments, ran tests in parallel, and generated Allure reports for CI.
- Expanded automation to cover ~95% of critical functionality with integration tests and fixed several high-severity defects while stabilizing release pipelines.
- Mentored ~10 interns on the framework and automation workflow, helping them convert manual QA scenarios into scalable automated tests.
- Independently designed and implemented a custom Kubernetes resource for managing control-plane and data-plane relationships in Istio; the solution was later presented internally as a target platform approach.

Stack: Go, Kubernetes, Istio, gRPC, REST, PostgreSQL, Allure, CI/CD

### Lukyanov Tech | Part-Time Mentor / Mock Interviewer (Remote, Jan 2024 - Present)

A part-time mentorship project focused on backend growth, interviews, and candidate preparation.

- Conduct mock interviews and mentor candidates on backend fundamentals, system design, technical communication, and answer structure.
- Help improve mentoring workflows, practical preparation, and feedback quality for students targeting software engineering roles.

Stack: Mentoring, Mock interviews, System design, Technical communication

### Magnus Tech | Backend Engineer (Russia, Mar 2023 - Jan 2024)

Magnus Tech is a custom software development company. I worked on a retail pricing platform for Bristol that brought together store data, employee workflows, ML pricing outputs, and photo evidence into one operating system.

- Built Go backend services from scratch for the pricing platform and integrated store data, product information, ML recommendations, and employee actions into one backend flow.
- Designed admin APIs and tooling for price review, manual overrides, cross-store comparison, and day-to-day operational monitoring.
- Integrated notification flows across web, email, SMS, and mobile channels and maintained API contracts for frontend and mobile teams.
- Wrote tests, supported observability, and helped deliver the product from active development to production release.

Stack: Go, PostgreSQL, Redis, Kafka, MinIO, REST, gRPC, Prometheus, CI/CD

### Exnode | Backend Engineer (Russia, Sep 2022 - Mar 2023)

[Exnode](https://exnode.ru) built crypto exchange, B2B payment, and P2P trading products.

- Split a large monolithic backend into smaller services and replaced part of the internal REST communication with gRPC to reduce latency and simplify service boundaries.
- Optimized payment and reporting queries using EXPLAIN ANALYZE, cutting several heavy PostgreSQL requests from 10-30 seconds to near real time.
- Built core product features around online payments, pricing, email notifications, Telegram alerting, and magic-link authentication.
- Resolved a critical currency-conversion incident by rolling back affected transactions and then hardening validation, observability, and release discipline.

Stack: Go, PostgreSQL, Redis, RabbitMQ, REST, gRPC, Grafana, Telegram Bot API

## EARLIER EXPERIENCE

### Kaluga Power Sale Company | Backend Engineer (Russia, Oct 2021 - Mar 2022)

- Built backend architecture from scratch for a customer portal and admin system integrated with 1C, payments, dashboards, and mobile/web clients.
- Worked as the sole backend engineer on the project, delivering account, billing, and operational views for customers and property-level management.

### Center for Regional Management of Lipetsk Oblast (CUR) | Java Developer (Russia, Jun 2021 - Nov 2021)

- Contributed to backend services for regional digital products and monitoring systems in a legacy-heavy environment.
- Worked on business logic and service development for government workflows while building my first professional engineering habits.

## SKILLS

- Languages: Go, Java, Python, SQL
- APIs & Messaging: gRPC, REST, MQTT, Kafka, RabbitMQ, NATS
- Data & Storage: PostgreSQL, Redis, MinIO
- Platform: Docker, Kubernetes, Helm, Istio, Linux, Git, CI/CD
- Observability & Quality: Prometheus, Grafana, Loki, Sentry, Allure, testing frameworks
- Focus Areas: distributed systems, low-latency backend, reliability, testing infrastructure, mentoring

## EDUCATION

### Voronezh State Technical University
B.S. in Intelligent Automated Systems (part-time) | Expected 2030

### Voronezh State Technical University
Secondary Vocational Diploma in Information Technology and Programming (with honors) | 2021 - 2025

## LANGUAGES

- Russian: Native
- English: B2 (working proficiency; actively improving spoken communication)
