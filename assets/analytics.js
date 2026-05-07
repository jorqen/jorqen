(function () {
  "use strict";

  if (window.__JORQEN_ANALYTICS_SCRIPT_LOADED__) {
    return;
  }
  window.__JORQEN_ANALYTICS_SCRIPT_LOADED__ = true;

  let siteUrl = normalizeSiteUrl(readMetaContent("jorqen:site-url") || window.location.origin);
  let counterIdRaw = readMetaContent("jorqen:yandex-metrika-id");
  let counterId = Number(counterIdRaw);
  let hasCounterId = Number.isFinite(counterId) && counterId > 0;
  let siteHostname = hostnameFromUrl(siteUrl);
  let isProductionHost = window.location.hostname === siteHostname;
  let shouldSendMetrika = hasCounterId && isProductionHost;
  const SCROLL_THRESHOLDS = [
    { goal: "scroll_50", percent: 50, trigger: 50 },
    { goal: "scroll_75", percent: 75, trigger: 75 },
    { goal: "scroll_100", percent: 100, trigger: 95 },
  ];
  const CONTACT_GOALS_BY_HOSTNAME = {
    "github.com": "github_click",
    "linkedin.com": "linkedin_click",
    "t.me": "telegram_click",
    "telegram.me": "telegram_click",
  };
  const CONTACT_TYPE_BY_GOAL = {
    email_click: "email",
    github_click: "github",
    linkedin_click: "linkedin",
    telegram_click: "telegram",
  };

  const onceGoalKeys = new Set();
  const viewedPhotos = new Set();

  let photoObserver = null;
  let linkTrackingReady = false;
  let scrollTrackingReady = false;

  function readMetaContent(name) {
    return String(document.querySelector(`meta[name="${name}"]`)?.getAttribute("content") || "").trim();
  }

  function normalizeSiteUrl(value) {
    try {
      const url = new URL(String(value || ""), window.location.origin);
      url.hash = "";
      url.search = "";
      return url.href.replace(/\/$/, "");
    } catch (_error) {
      return window.location.origin.replace(/\/$/, "");
    }
  }

  function hostnameFromUrl(value) {
    try {
      return new URL(value).hostname;
    } catch (_error) {
      return "";
    }
  }

  function absoluteSiteUrl(path) {
    try {
      return new URL(path || "/", `${siteUrl}/`).href;
    } catch (_error) {
      return path || siteUrl;
    }
  }

  function isDebugMode() {
    return !shouldSendMetrika && window.console && typeof window.console.debug === "function";
  }

  function configure(options = {}) {
    siteUrl = normalizeSiteUrl(options.siteUrl || siteUrl);
    counterIdRaw = String(options.yandexMetrikaId || counterIdRaw).trim();
    counterId = Number(counterIdRaw);
    hasCounterId = Number.isFinite(counterId) && counterId > 0;
    siteHostname = hostnameFromUrl(siteUrl);
    isProductionHost = window.location.hostname === siteHostname;
    shouldSendMetrika = hasCounterId && isProductionHost;
    initMetrika();
  }

  function sanitizeParams(params) {
    if (!params || typeof params !== "object") {
      return {};
    }

    return Object.fromEntries(
      Object.entries(params)
        .filter(([_key, value]) => value !== undefined && value !== null && value !== "")
        .map(([key, value]) => {
          if (typeof value === "number" || typeof value === "boolean") {
            return [key, value];
          }
          return [key, String(value).slice(0, 500)];
        }),
    );
  }

  function initMetrika() {
    if (!shouldSendMetrika || window.__JORQEN_METRIKA_COUNTER_ID__ === counterId) {
      return;
    }

    window.__JORQEN_METRIKA_COUNTER_ID__ = counterId;
    (function (m, e, t, r, i, k, a) {
      m[i] = m[i] || function () {
        (m[i].a = m[i].a || []).push(arguments);
      };
      m[i].l = 1 * new Date();
      k = e.createElement(t);
      a = e.getElementsByTagName(t)[0];
      k.async = 1;
      k.src = r;
      a.parentNode.insertBefore(k, a);
    })(window, document, "script", "https://mc.yandex.ru/metrika/tag.js", "ym");

    try {
      window.ym(counterId, "init", {
        ssr: true,
        webvisor: true,
        clickmap: true,
        accurateTrackBounce: true,
        trackLinks: true,
      });
    } catch (error) {
      if (window.console && typeof window.console.warn === "function") {
        window.console.warn("Yandex Metrika initialization failed.", error);
      }
    }
  }

  function trackGoal(goalName, params = {}) {
    const safeGoalName = String(goalName || "").trim();
    const safeParams = sanitizeParams(params);

    if (!safeGoalName) {
      return false;
    }

    if (!shouldSendMetrika) {
      if (isDebugMode()) {
        window.console.debug("[analytics]", safeGoalName, safeParams);
      }
      return false;
    }

    try {
      if (typeof window.ym === "function") {
        window.ym(counterId, "reachGoal", safeGoalName, safeParams);
        return true;
      }
    } catch (error) {
      if (window.console && typeof window.console.warn === "function") {
        window.console.warn(`Could not send analytics goal: ${safeGoalName}`, error);
      }
    }

    return false;
  }

  function trackGoalOnce(goalName, params = {}, onceKey = "") {
    const key = onceKey || `${goalName}:${JSON.stringify(sanitizeParams(params))}`;
    if (onceGoalKeys.has(key)) {
      return false;
    }
    onceGoalKeys.add(key);
    return trackGoal(goalName, params);
  }

  function textForElement(element) {
    return String(
      element.dataset.analyticsText ||
        element.dataset.analyticsLabel ||
        element.getAttribute("aria-label") ||
        element.textContent ||
        "",
    )
      .replace(/\s+/g, " ")
      .trim();
  }

  function labelForElement(element) {
    return String(element.dataset.analyticsLabel || textForElement(element) || "").trim();
  }

  function sectionForElement(element) {
    return (
      element.dataset.analyticsSection ||
      element.closest("[data-section]")?.getAttribute("data-section") ||
      ""
    );
  }

  function linkUrl(link) {
    const href = link.getAttribute("href") || "";
    if (!href || href.startsWith("mailto:") || href.startsWith("tel:")) {
      return href;
    }

    try {
      return new URL(href, window.location.href).href;
    } catch (_error) {
      return href;
    }
  }

  function urlHostname(url) {
    try {
      return new URL(url, window.location.href).hostname.replace(/^www\./, "");
    } catch (_error) {
      return "";
    }
  }

  function fileNameFromUrl(url) {
    try {
      const path = new URL(url, window.location.href).pathname;
      return decodeURIComponent(path.split("/").filter(Boolean).pop() || "");
    } catch (_error) {
      return "";
    }
  }

  function linkBaseParams(link) {
    const url = linkUrl(link);
    return {
      url,
      label: labelForElement(link),
      text: textForElement(link),
      section: sectionForElement(link),
    };
  }

  function contactGoalForLink(link) {
    const explicitGoal = link.dataset.analyticsGoal;
    if (CONTACT_TYPE_BY_GOAL[explicitGoal]) {
      return explicitGoal;
    }

    const href = link.getAttribute("href") || "";
    if (href.startsWith("mailto:")) {
      return "email_click";
    }

    return CONTACT_GOALS_BY_HOSTNAME[urlHostname(linkUrl(link))] || "";
  }

  function specificGoalForLink(link) {
    const explicitGoal = link.dataset.analyticsGoal;
    if (explicitGoal) {
      return explicitGoal;
    }

    if (link.hasAttribute("download") && /\/resume\//.test(linkUrl(link))) {
      return "resume_download_click";
    }

    return contactGoalForLink(link);
  }

  function paramsForGoal(goalName, link) {
    const params = linkBaseParams(link);
    const url = params.url;

    if (goalName === "resume_download_click") {
      return {
        ...params,
        file: fileNameFromUrl(url),
      };
    }

    if (goalName === "contact_click") {
      const contactType = CONTACT_TYPE_BY_GOAL[contactGoalForLink(link)] || "";
      return {
        ...params,
        contact_type: contactType,
      };
    }

    return params;
  }

  function trackLink(link) {
    const goals = new Set();
    const specificGoal = specificGoalForLink(link);

    if (specificGoal) {
      goals.add(specificGoal);
    }
    if (link.dataset.analyticsContact === "true" || CONTACT_TYPE_BY_GOAL[contactGoalForLink(link)]) {
      goals.add("contact_click");
    }

    goals.forEach((goalName) => {
      trackGoal(goalName, paramsForGoal(goalName, link));
    });
  }

  function setupLinkTracking() {
    if (linkTrackingReady) {
      return;
    }
    linkTrackingReady = true;

    document.addEventListener(
      "click",
      (event) => {
        if (!(event.target instanceof Element)) {
          return;
        }

        const link = event.target.closest("a[href]");
        if (link) {
          trackLink(link);
        }
      },
      { capture: true },
    );
  }

  function scrollPercent() {
    const documentElement = document.documentElement;
    const scrollable = documentElement.scrollHeight - window.innerHeight;
    if (scrollable <= 0) {
      return 100;
    }

    return Math.min(100, Math.max(0, Math.round((window.scrollY / scrollable) * 100)));
  }

  function checkScrollDepth() {
    const currentPercent = scrollPercent();
    SCROLL_THRESHOLDS.forEach((item) => {
      if (currentPercent >= item.trigger) {
        trackGoalOnce(item.goal, { percent: item.percent }, `scroll:${item.percent}`);
      }
    });
  }

  function setupScrollTracking() {
    if (scrollTrackingReady) {
      checkScrollDepth();
      return;
    }
    scrollTrackingReady = true;

    window.addEventListener("scroll", checkScrollDepth, { passive: true });
    window.addEventListener("resize", checkScrollDepth);
    window.addEventListener("load", checkScrollDepth);
    window.requestAnimationFrame(checkScrollDepth);
  }

  function setupPhotoTracking() {
    if (photoObserver) {
      photoObserver.disconnect();
    }

    photoObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const photoId = entry.target.getAttribute("data-photo-id");
          if (!photoId || viewedPhotos.has(photoId)) {
            return;
          }

          if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
            viewedPhotos.add(photoId);
            trackGoalOnce(
              "photo_view",
              { photo_id: photoId, section: sectionForElement(entry.target) },
              `photo_view:${photoId}`,
            );
          }
        });
      },
      { threshold: [0, 0.5, 0.75, 1] },
    );

    document.querySelectorAll("[data-photo-id]").forEach((photo) => photoObserver.observe(photo));
  }

  function refreshTrackedElements() {
    setupScrollTracking();
    setupPhotoTracking();
    window.requestAnimationFrame(checkScrollDepth);
  }

  function trackPhotoClick(element) {
    if (!element) {
      return;
    }

    trackGoal("photo_click", {
      photo_id: element.getAttribute("data-photo-id") || "",
      section: sectionForElement(element),
      label: element.getAttribute("aria-label") || textForElement(element),
    });
  }

  function initPageTracking() {
    setupLinkTracking();
    refreshTrackedElements();
  }

  window.JorqenAnalytics = {
    get SITE_URL() {
      return siteUrl;
    },
    get COUNTER_ID() {
      return counterIdRaw;
    },
    absoluteSiteUrl,
    configure,
    initPageTracking,
    refreshTrackedElements,
    trackGoal,
    trackGoalOnce,
    trackPhotoClick,
  };

  initMetrika();
  initPageTracking();
})();
