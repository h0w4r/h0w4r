#!/usr/bin/env node
/**
 * Extrae un snapshot profesional de LinkedIn con Playwright usando una cookie
 * de sesión pasada por LINKEDIN_COOKIE. No imprime la cookie ni HTML privado.
 */
import { chromium } from 'playwright';

const profileUrl = process.env.LINKEDIN_PROFILE_URL || 'https://www.linkedin.com/in/cehp94/';
const cookieHeader = process.env.LINKEDIN_COOKIE || '';

function parseCookieHeader(header) {
  return header
    .split(';')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const separator = part.indexOf('=');
      if (separator === -1) return null;
      const name = part.slice(0, separator).trim();
      const value = part.slice(separator + 1).trim();
      if (!name) return null;
      return {
        name,
        value: value.replace(/^"|"$/g, ''),
        domain: '.linkedin.com',
        path: '/',
        httpOnly: false,
        secure: true,
        sameSite: 'Lax',
      };
    })
    .filter(Boolean);
}

function compact(value, limit = 400) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, limit - 1).trim()}…` : text;
}

function linkedinSlug(url) {
  try {
    return new URL(url).pathname.match(/\/in\/([^/]+)/)?.[1] || '';
  } catch {
    return '';
  }
}

function safeError(error) {
  return compact(error?.message || String(error), 500);
}

if (!cookieHeader) {
  console.log(JSON.stringify({ url: profileUrl, reason: 'LINKEDIN_COOKIE no configurado' }));
  process.exit(0);
}

const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL || 'chrome',
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});
try {
  const context = await browser.newContext({
    locale: 'es-PE',
    timezoneId: 'America/Lima',
    userAgent:
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
      '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
  });
  await context.addCookies(parseCookieHeader(cookieHeader));
  const page = await context.newPage();
  await page.route('**/*', async (route) => {
    const resourceType = route.request().resourceType();
    if (['image', 'media', 'font'].includes(resourceType)) {
      await route.abort();
      return;
    }
    await route.continue();
  });

  const slug = linkedinSlug(profileUrl);
  const candidates = [
    profileUrl,
    profileUrl.replace(/\/$/, ''),
    slug ? `https://www.linkedin.com/in/${slug}/?originalSubdomain=pe` : '',
    slug ? `https://www.linkedin.com/in/${slug}/details/experience/` : '',
    slug ? `https://www.linkedin.com/mwlite/in/${slug}` : '',
  ].filter(Boolean);

  const navigationErrors = [];
  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 45000 }).catch((error) => {
    navigationErrors.push(`feed: ${safeError(error)}`);
  });

  let loaded = false;
  for (const candidate of candidates) {
    try {
      await page.goto(candidate, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(5000);
      const bodyText = await page.locator('body').innerText({ timeout: 5000 }).catch(() => '');
      if (bodyText && !/join linkedin|sign in|inicia sesión|authwall/i.test(bodyText)) {
        loaded = true;
        break;
      }
      navigationErrors.push(`${candidate}: página sin contenido autenticado útil`);
    } catch (error) {
      navigationErrors.push(`${candidate}: ${safeError(error)}`);
    }
  }

  if (!loaded) {
    console.log(
      JSON.stringify({
        url: profileUrl,
        reason: `No se pudo abrir LinkedIn autenticado: ${navigationErrors.slice(0, 3).join(' | ')}`,
      }),
    );
    process.exit(0);
  }

  const snapshot = await page.evaluate(() => {
    const pick = (selector) => document.querySelector(selector)?.textContent?.trim() || '';
    const pickFirst = (selectors) => selectors.map((selector) => pick(selector)).find(Boolean) || '';
    const meta = (selector) => document.querySelector(selector)?.getAttribute('content') || '';
    const bodyText = document.body?.innerText || '';
    const headings = Array.from(document.querySelectorAll('h1,h2,h3')).map((node) => node.textContent?.trim()).filter(Boolean);
    return {
      url: location.href,
      title: document.title || '',
      name: pickFirst(['main h1', 'h1']),
      headline: pickFirst([
        'main .text-body-medium.break-words',
        'main .pv-text-details__left-panel .text-body-medium',
        'section .text-body-medium.break-words',
      ]),
      summary: meta('meta[name="description"]') || meta('meta[property="og:description"]') || '',
      metaDescription: meta('meta[name="description"]') || meta('meta[property="og:description"]') || '',
      headings,
      rawText: bodyText.slice(0, 120000),
    };
  });

  const activityUrl = profileUrl.replace(/\/?$/, '/recent-activity/all/');
  try {
    await page.goto(activityUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(5000);
    snapshot.activityRawText = await page
      .locator('body')
      .innerText({ timeout: 5000 })
      .then((text) => text.slice(0, 60000))
      .catch(() => '');
  } catch {
    snapshot.activityRawText = '';
  }

  snapshot.title = compact(snapshot.title, 220);
  snapshot.name = compact(snapshot.name, 160);
  snapshot.headline = compact(snapshot.headline, 220);
  snapshot.summary = compact(snapshot.summary, 600);
  snapshot.metaDescription = compact(snapshot.metaDescription, 600);
  console.log(JSON.stringify(snapshot));
} finally {
  await browser.close();
}
