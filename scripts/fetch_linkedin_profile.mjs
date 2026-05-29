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
  await page.goto(profileUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(6000);

  const snapshot = await page.evaluate(() => {
    const pick = (selector) => document.querySelector(selector)?.textContent?.trim() || '';
    const meta = (selector) => document.querySelector(selector)?.getAttribute('content') || '';
    const bodyText = document.body?.innerText || '';
    const headings = Array.from(document.querySelectorAll('h1,h2,h3')).map((node) => node.textContent?.trim()).filter(Boolean);
    return {
      url: location.href,
      title: document.title || '',
      headline: pick('h1') || headings[0] || '',
      summary: meta('meta[name="description"]') || meta('meta[property="og:description"]') || '',
      metaDescription: meta('meta[name="description"]') || meta('meta[property="og:description"]') || '',
      headings,
      rawText: bodyText.slice(0, 120000),
    };
  });

  snapshot.title = compact(snapshot.title, 220);
  snapshot.headline = compact(snapshot.headline, 220);
  snapshot.summary = compact(snapshot.summary, 600);
  snapshot.metaDescription = compact(snapshot.metaDescription, 600);
  console.log(JSON.stringify(snapshot));
} finally {
  await browser.close();
}
