# jorqen resume site

Static single-page resume website deployed with GitHub Pages.

## Analytics

The site uses Yandex Metrika. Runtime analytics settings are kept in `resume/resume.yaml`, which is the source of truth for the site:

- `site.url` is the public production URL source of truth.
- `analytics.yandexMetrikaId` is the Yandex Metrika counter ID.
- Current production URL: https://jorqen.link.

GitHub Pages and HTTPS are already configured for the production domain. The old GitHub Pages subdomain is not intentionally supported.

Yandex Metrika automatically collects basic metrics after the counter is installed: page views, visits, unique visitors, traffic sources, devices, geography, time on site, bounce metrics, click maps, scroll maps, and Webvisor. Ecommerce tracking is not used because this site is not an online store.

Custom events are sent with `reachGoal`. Create the important events in the Yandex Metrika UI as JavaScript-event goals if they should appear as conversion goals in reports. If a goal is not created in advance, the event can be sent from the site but may not appear as a full conversion goal. On localhost, events are logged to the browser console as `[analytics] ...` instead of being sent to Yandex Metrika.

Reports are available at https://metrika.yandex.ru.

Recommended goals to create manually:

- `contact_click`
- `telegram_click`
- `email_click`
- `github_click`
- `linkedin_click`
- `resume_download_click`
- `project_link_click`
- `scroll_50`
- `scroll_75`
- `scroll_100`
- `photo_view`
- `photo_click`

Local test:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open `http://127.0.0.1:8000/`, then use the browser console to verify analytics debug logs while clicking contacts, downloading resumes, opening photos, and scrolling.
