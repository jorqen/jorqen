# Matvey Sizov

Moscow, Russia | Remote / relocation  
Website: https://jorqen.github.io/jorqen/ | LinkedIn: https://www.linkedin.com/in/jorqen | GitHub: https://github.com/jorqen | Telegram: https://t.me/jorqen

More than 4 years in backend development. Golang, Java, distributed systems, product and infrastructure engineering.

I started programming in Java in my early teens, entered backend professionally through Java, and then shifted my primary focus to Go because explicit and understandable systems are a better fit for me. Today I work on both infrastructure and product backends, and I am strongest where a team needs someone who can turn ambiguous requirements into a production system with reasonable trade-offs, observability, and reliable delivery.

---

## EXPERIENCE

### ATOM | Senior Software Engineer, Communications & Telemetry (Remote from Russia, Feb 2025 - Present)

ATOM is building a Russian electric vehicle platform. I work in communications and telemetry on backend services that connect the car, cloud, and external clients, with a focus on latency, security, and operational reliability.

- Worked with leadership, peer teams, and architects to turn ambiguous and partially unrealistic requirements into a releasable backend architecture for a new broker.
- Designed and developed an mTLS-secured MQTT broker with Redis-based state recovery for persistent vehicle-to-cloud communication and safe session restoration after restarts.
- Challenged the initial microservice decomposition, showed it could not meet target latency, and advanced a central-broker design that sustained ~70K requests per second at p99 below 50 ms.
- Built production observability and delivery tooling from scratch after losing dedicated DevOps support: metrics, logs, traces, dashboards, alerts, CI/CD, and deployment automation.
- Selected key libraries and system components, including Redis for resilient in-memory state and access-control mechanisms for trusted clients.

**Stack:** Go, MQTT, Redis, PostgreSQL, Kafka, gRPC, mTLS, Prometheus, Grafana, Loki, Sentry, Docker, Kubernetes, CI/CD

### Sber Tech | Software Engineer, Service Mesh & Platform Infrastructure (Moscow, Russia, Jan 2024 - Feb 2025)

Sber Tech develops Platform V, a large corporate platform. I worked on a heavily modified Istio fork and adjacent infrastructure components for service-mesh control, policy enforcement, and platform integration.

- Restored broken automated testing in a heavily modified Istio fork, returning unit tests to the daily engineering process and raising coverage to 80%.
- Designed and built a Go integration-testing framework that provisioned isolated Kubernetes environments, ran tests in parallel, and generated Allure reports for CI.
- Expanded automation to about 95% of critical functionality through integration tests and, in parallel, fixed several critical defects, stabilizing release pipelines.
- Mentored around 10 interns on the framework and automation process, helping convert manual QA scenarios into scalable automated tests.
- Independently designed and implemented a custom Kubernetes resource for managing control-plane and data-plane relationships in Istio; the solution was later presented internally as a target platform approach.

**Stack:** Go, Kubernetes, Istio, gRPC, REST, PostgreSQL, CI/CD, Allure

### Lukyanov Tech | Part-Time Mentor / Mock Interviewer (Remote, Jan 2024 - Present)

A part-time project focused on mentorship, interview preparation, and growth of backend candidates.

- Conduct mock interviews and mentor candidates on backend fundamentals, system design, technical communication, and answer structure.
- Help improve the mentoring process, hands-on preparation, and feedback quality for students targeting engineering roles.

**Stack:** Mentoring, Mock interviews, System design, Technical communication

### Magnus Tech | Backend Engineer (Remote, Mar 2023 - Jan 2024)

Magnus Tech is a custom development company. I worked on a pricing-control platform for the Bristol retail chain that combined store data, employee actions, ML pricing recommendations, and photo confirmation into one operational system.

- Developed Go backend services from scratch for the pricing platform and integrated store data, product information, ML recommendations, and employee actions into a unified backend flow.
- Designed admin-panel APIs and internal tools for viewing prices, manual overrides, cross-store comparison, and day-to-day operational control.
- Integrated notification flows across web, email, SMS, and mobile channels and maintained API contracts for frontend and mobile teams.
- Wrote tests, maintained observability, and helped bring the product from active development to production release.

**Stack:** Go, PostgreSQL, Redis, Kafka, MinIO, REST, gRPC, Prometheus, CI/CD

### Exnode | Backend Engineer (Moscow, Russia, Sep 2022 - Mar 2023)

Exnode developed crypto exchange, B2B payment, and P2P trading products.

- Split a large monolithic backend into smaller services and replaced part of internal REST communication with gRPC to reduce latency and simplify service boundaries.
- Optimized payment and reporting queries using EXPLAIN ANALYZE, cutting several heavy PostgreSQL queries from 10-30 seconds to near real time.
- Implemented key product capabilities around online payments, pricing, email notifications, Telegram alerts, and magic-link authentication.
- Investigated and localized a critical currency-conversion incident, rolled back affected transactions, and then strengthened validation, observability, and release discipline.

**Stack:** Go, PostgreSQL, Redis, RabbitMQ, REST, gRPC, Grafana, Telegram Bot API

### Kaluga Power Supply Company | Backend Engineer (Remote, Oct 2021 - Mar 2022)

An early backend role in utility payment systems, customer portals, and administrative services.

- Developed and launched the core backend architecture for a customer portal and administrative system with integrations to 1C, payments, dashboards, and web/mobile clients.
- Rewrote a key service from PHP to Go, migrating critical 1C integration and payment-processing scenarios without losing business functionality.
- Worked as the sole backend engineer on the project, delivering account, billing, and operational views for customers and property management.

**Stack:** Go, PHP, PostgreSQL, 1C integrations, Payments, Dashboards

### Center for Regional Management of Lipetsk Oblast (CUR) | Java Developer (Lipetsk, Russia, Jun 2021 - Nov 2021)

My first commercial backend role in Java, focused on public digital and monitoring systems.

- Contributed to backend services for regional digital products and monitoring systems in an environment with a large amount of legacy systems.
- Worked on business logic and service development for government processes.

**Stack:** Java, SQL, Backend

## EDUCATION

### Voronezh State Technical University
B.S. in Intelligent Automated Systems (part-time) | Expected graduation: 2030

### Voronezh State Technical University
Secondary vocational education in Information Technology and Programming | 2021 - 2025

## PROFILE

- Architecture through trade-offs: For me, choosing the right tool and the right system shape matters: Redis or PostgreSQL, gRPC or REST, a more complex setup or a simplified architecture to minimize latency and improve adaptability.
- Product + infrastructure: My recent roles are infrastructure-focused, but I also built product backend systems for retail pricing, crypto payments, utility billing, and internal administrative panels.
- Leadership trajectory: I already mentor candidates and interns, and in the coming years I want to grow into technical leadership without losing hands-on depth and systems thinking.
- From Java to Go: I have 4 years of commercial backend development in Go and 6 months in Java. I started with Java as a teenager, got my first commercial backend role in Java, and then moved to Go because explicit engineering, simpler syntax, and low-latency services fit me better.

## KEY STACK

- Languages: Go, Java, Python, SQL
- APIs & Messaging: gRPC, REST, MQTT, Kafka, RabbitMQ, NATS
- Data & Storage: PostgreSQL, Redis, MinIO
- Platform: Docker, Kubernetes, Helm, Istio, Linux, Git, CI/CD
- Observability & Quality: Prometheus, Grafana, Loki, Sentry, Allure, Testing frameworks

## WHAT RECRUITERS SHOULD KNOW

- I primarily see myself as a backend engineer and feel strongest in Go, distributed systems, and complex backend logic.
- I am comfortable in both product and platform teams: I can switch between latency-sensitive infrastructure and business-facing backend functionality.
- I am the best fit for teams solving high-load, low-latency, or technically ambiguous backend problems.
- In the next 2-5 years, I want to grow into a tech lead and broader technical leadership role while remaining a deeply technical engineer.
- I use English in work and interview contexts when needed and continue improving spoken fluency for international teams.
- Outside work, I mentor candidates, cycle, swim, go to the gym, and enjoy traveling; Japan is one of the places I especially want to revisit.

