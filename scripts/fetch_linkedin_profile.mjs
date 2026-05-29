#!/usr/bin/env node
/**
 * Extrae un snapshot profesional de LinkedIn con Playwright usando una cookie
 * de sesión pasada por LINKEDIN_COOKIE. No imprime la cookie ni HTML privado.
 */
import { chromium } from 'playwright';

const profileUrl = process.env.LINKEDIN_PROFILE_URL || 'https://www.linkedin.com/in/cehp94/';
const cookieHeader = process.env.LINKEDIN_COOKIE || '';

function parseCookieHeader(header) {
  const normalizedHeader = String(header || '').replace(/^cookie:\s*/i, '').trim();
  return header
    ? normalizedHeader
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
    .filter(Boolean)
    : [];
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
  console.error('LinkedIn browser snapshot: LINKEDIN_COOKIE no configurado.');
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
  const parsedCookies = parseCookieHeader(cookieHeader);
  const cookieNames = new Set(parsedCookies.map((cookie) => cookie.name.toLowerCase()));
  console.error(
    `LinkedIn cookie diagnostics: count=${parsedCookies.length} has_li_at=${cookieNames.has('li_at')} has_jsessionid=${cookieNames.has('jsessionid')}`,
  );
  await context.addCookies(parsedCookies);
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
    const reason = `No se pudo abrir LinkedIn autenticado: ${navigationErrors.slice(0, 3).join(' | ')}`;
    console.error(`LinkedIn browser snapshot: unavailable. ${reason}`);
    console.log(
      JSON.stringify({
        url: profileUrl,
        reason,
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

  const safeInnerText = async () =>
    page
      .locator('body')
      .innerText({ timeout: 5000 })
      .then((text) => text || '')
      .catch(() => '');

  const detailPaths = ['education', 'certifications', 'projects', 'courses'];
  const detailChunks = [];
  for (const detailPath of detailPaths) {
    const detailUrl = profileUrl.replace(/\/?$/, `/details/${detailPath}/`);
    try {
      await page.goto(detailUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(3500);
      const text = await safeInnerText();
      if (text && !/join linkedin|sign in|inicia sesión|authwall/i.test(text)) {
        detailChunks.push(`\n\n[linkedin:${detailPath}]\n${text.slice(0, 40000)}`);
      }
    } catch {
      // Se ignora el fallo puntual; el diagnóstico agregado se imprime abajo.
    }
  }
  snapshot.detailsRawText = detailChunks.join('\n').slice(0, 120000);

  const activityUrl = profileUrl.replace(/\/?$/, '/recent-activity/all/');
  try {
    await page.goto(activityUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(5000);
    const text = await safeInnerText();
    snapshot.activityRawText = /join linkedin|sign in|inicia sesión|authwall/i.test(text) ? '' : text.slice(0, 60000);
  } catch {
    snapshot.activityRawText = '';
  }

  snapshot.title = compact(snapshot.title, 220);
  snapshot.name = compact(snapshot.name, 160);
  snapshot.headline = compact(snapshot.headline, 220);
  snapshot.summary = compact(snapshot.summary, 600);
  snapshot.metaDescription = compact(snapshot.metaDescription, 600);
  const lineCount = String(snapshot.rawText || '').split('\n').filter(Boolean).length;
  const detailLineCount = String(snapshot.detailsRawText || '').split('\n').filter(Boolean).length;
  const activityLineCount = String(snapshot.activityRawText || '').split('\n').filter(Boolean).length;
  console.error(
    `LinkedIn browser snapshot: available. profile_lines=${lineCount} detail_lines=${detailLineCount} activity_lines=${activityLineCount} url=${snapshot.url}`,
  );
  console.log(JSON.stringify(snapshot));
} finally {
  await browser.close();
}
