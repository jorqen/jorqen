#!/bin/bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
u="$1"
r=$(curl -sL -m 30 --compressed -A "$UA" -H "Accept: text/html,application/xhtml+xml" -H "Accept-Language: tr,en;q=0.9" -w "\n__C__%{http_code} %{url_effective}" "$u" 2>/dev/null)
echo "### $u"
printf '%s' "$r" | tail -1
printf '%s' "$r" | grep -oiE 'https?://[a-z0-9.-]+\.[a-z]{2,}' | sed -E 's#https?://##' | sort -u | grep -viE 'google|facebook|gstatic|cloudflare|jquery|bootstrap|fontawesome|youtube|twitter|linkedin|instagram|w3\.org|schema\.org|adobe|hotjar|segment|sentry|cdn|apple|microsoft|doubleclick|criteo|akamai|tiktok|jsdelivr|unpkg|typekit|newrelic|clarity\.ms|licdn|cookiebot|onetrust|vimeo|whatsapp|amazonaws|azureedge|bing|yandex|mixpanel|amplitude|intercom|zendesk|hubspot|gtm|analytics' | head -20
