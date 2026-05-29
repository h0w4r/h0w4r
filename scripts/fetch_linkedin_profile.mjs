#!/usr/bin/env node
/**
 * Extrae un snapshot profesional de LinkedIn con Playwright.
 *
 * Modos soportados, en orden práctico de estabilidad:
 * 1. LINKEDIN_USER_DATA_DIR: perfil persistente local del runner self-hosted.
 * 2. LINKEDIN_STORAGE_STATE_FILE: storageState exportado por Playwright.
 * 3. LINKEDIN_COOKIE: header Cookie como fallback legacy.
 *
 * El script nunca imprime cookies ni HTML privado. Los diagnósticos públicos se
 * limitan a contadores, URL final y motivos de fallo sanitizados.
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { chromium } from 'playwright';

const profileUrl = process.env.LINKEDIN_PROFILE_URL || 'https://www.linkedin.com/in/cehp94/';
const cookieHeader = process.env.LINKEDIN_COOKIE || '';
const userDataDir = process.env.LINKEDIN_USER_DATA_DIR || '';
const storageStateFile = process.env.LINKEDIN_STORAGE_STATE_FILE || '';
const interactiveLogin = truthy(process.env.LINKEDIN_INTERACTIVE_LOGIN);
const headless = process.env.LINKEDIN_HEADLESS ? truthy(process.env.LINKEDIN_HEADLESS) : true;
const requestedChannel = process.env.LINKEDIN_BROWSER_CHANNEL || process.env.PLAYWRIGHT_CHROMIUM_CHANNEL || 'msedge';
const applyCookie = process.env.LINKEDIN_APPLY_COOKIE
  ? truthy(process.env.LINKEDIN_APPLY_COOKIE)
  : !userDataDir && !storageStateFile;

const AUTHWALL_PATTERN =
  /join linkedin|join now|agree & join linkedin|sign up \| linkedin|authwall|login \| linkedin|inicia sesi[oó]n|únete a linkedin|sign in|sign in to linkedin|email or phone|forgot password|keep me logged in|new to linkedin/i;
const CHECKPOINT_PATTERN = /checkpoint|challenge|captcha|verification|verificaci[oó]n|security check|control de seguridad/i;
const AUTH_URL_PATTERN = /\/uas\/login|\/login|\/checkpoint|\/authwall|session_redirect|trk=guest_homepage/i;

function truthy(value) {
  return /^(1|true|yes|y|on|si|sí)$/i.test(String(value || '').trim());
}

function parseCookieHeader(header) {
  const normalizedHeader = String(header || '').replace(/^cookie:\s*/i, '').trim();
  return normalizedHeader
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

function authMode() {
  if (userDataDir) return 'user-data-dir';
  if (storageStateFile) return 'storage-state';
  if (cookieHeader) return 'cookie';
  return 'none';
}

function contextBaseOptions() {
  const options = {
    locale: 'es-PE',
    timezoneId: 'America/Lima',
    viewport: { width: 1366, height: 900 },
    userAgent:
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
      '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
  };

  if (storageStateFile) {
    const resolved = path.resolve(storageStateFile);
    if (!fs.existsSync(resolved)) {
      throw new Error(`LINKEDIN_STORAGE_STATE_FILE no existe: ${resolved}`);
    }
    options.storageState = resolved;
  }

  return options;
}

function launchAttempts() {
  const attempts = [];
  if (!['', 'bundled', 'chromium', 'none'].includes(requestedChannel.toLowerCase())) {
    attempts.push({ channel: requestedChannel });
  }
  attempts.push({});
  return attempts;
}

async function createContext() {
  const errors = [];
  const baseOptions = contextBaseOptions();

  for (const attempt of launchAttempts()) {
    const label = attempt.channel ? `channel=${attempt.channel}` : 'bundled-chromium';
    try {
      if (userDataDir) {
        const resolvedUserDataDir = path.resolve(userDataDir);
        fs.mkdirSync(resolvedUserDataDir, { recursive: true });
        const context = await chromium.launchPersistentContext(resolvedUserDataDir, {
          ...baseOptions,
          ...attempt,
          headless,
          args: ['--no-sandbox', '--disable-dev-shm-usage'],
        });
        return { context, browser: null, mode: 'user-data-dir', label };
      }

      const browser = await chromium.launch({
        ...attempt,
        headless,
        args: ['--no-sandbox', '--disable-dev-shm-usage'],
      });
      const context = await browser.newContext(baseOptions);
      return { context, browser, mode: storageStateFile ? 'storage-state' : 'cookie', label };
    } catch (error) {
      errors.push(`${label}: ${safeError(error)}`);
    }
  }

  throw new Error(`No se pudo iniciar Chromium: ${errors.join(' | ')}`);
}

async function bodyText(page) {
  return page
    .locator('body')
    .innerText({ timeout: 5000 })
    .then((text) => text || '')
    .catch(() => '');
}

function authenticatedPage(url, text) {
  return Boolean(text && !AUTH_URL_PATTERN.test(url || '') && !AUTHWALL_PATTERN.test(text) && !CHECKPOINT_PATTERN.test(text));
}

async function pageDiagnostics(page) {
  const text = await bodyText(page);
  const lines = text
    .split(/\r?\n/)
    .map((line) => compact(line, 180))
    .filter(Boolean)
    .filter((line) => !AUTHWALL_PATTERN.test(line))
    .slice(0, 8);

  return {
    finalUrl: compact(page.url(), 240),
    title: compact(await page.title().catch(() => ''), 180),
    authUrlDetected: AUTH_URL_PATTERN.test(page.url()),
    authwallDetected: AUTHWALL_PATTERN.test(text),
    checkpointDetected: CHECKPOINT_PATTERN.test(text),
    bodyLineCount: text.split(/\r?\n/).filter((line) => line.trim()).length,
    firstUsefulLines: lines,
  };
}

async function waitForEnter() {
  if (!process.stdin.isTTY) {
    console.error('LinkedIn interactive bootstrap: stdin no es interactivo; no puedo esperar Enter.');
    return;
  }

  console.error('LinkedIn interactive bootstrap: inicia sesión en la ventana de Chrome y pulsa Enter aquí para continuar.');
  process.stdin.resume();
  await new Promise((resolve) => process.stdin.once('data', resolve));
  process.stdin.pause();
}

async function installRequestFilter(context) {
  await context.route('**/*', async (route) => {
    const resourceType = route.request().resourceType();
    // Se bloquean recursos pesados; dejamos scripts/XHR porque LinkedIn renderiza mucho por JS.
    if (['image', 'media', 'font'].includes(resourceType)) {
      await route.abort();
      return;
    }
    await route.continue();
  });
}

async function tryLoadProfile(page, candidates, navigationErrors) {
  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 45000 }).catch((error) => {
    navigationErrors.push(`feed: ${safeError(error)}`);
  });

  for (const candidate of candidates) {
    try {
      await page.goto(candidate, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(5000);
      const text = await bodyText(page);
      if (authenticatedPage(page.url(), text)) {
        return true;
      }
      const diag = await pageDiagnostics(page);
      navigationErrors.push(`${candidate}: página sin contenido autenticado útil (${diag.title || diag.finalUrl})`);
    } catch (error) {
      navigationErrors.push(`${candidate}: ${safeError(error)}`);
    }
  }
  return false;
}

if (!cookieHeader && !userDataDir && !storageStateFile) {
  console.log(JSON.stringify({ url: profileUrl, reason: 'No hay fuente de sesión LinkedIn configurada', authMode: 'none' }));
  console.error('LinkedIn browser snapshot: sin LINKEDIN_USER_DATA_DIR, LINKEDIN_STORAGE_STATE_FILE ni LINKEDIN_COOKIE.');
  process.exit(0);
}

let contextHandle;
try {
  contextHandle = await createContext();
  const { context, browser, mode, label } = contextHandle;
  const parsedCookies = parseCookieHeader(cookieHeader);
  const cookieNames = new Set(parsedCookies.map((cookie) => cookie.name.toLowerCase()));

  console.error(
    `LinkedIn session diagnostics: mode=${mode} browser=${label} headless=${headless} ` +
    `cookie_count=${parsedCookies.length} has_li_at=${cookieNames.has('li_at')} has_jsessionid=${cookieNames.has('jsessionid')}`,
  );

  if (applyCookie && parsedCookies.length) {
    await context.addCookies(parsedCookies);
  }

  await installRequestFilter(context);
  const page = context.pages()[0] || (await context.newPage());

  const slug = linkedinSlug(profileUrl);
  const candidates = [
    profileUrl,
    profileUrl.replace(/\/$/, ''),
    slug ? `https://www.linkedin.com/in/${slug}/?originalSubdomain=pe` : '',
    slug ? `https://www.linkedin.com/mwlite/in/${slug}` : '',
  ].filter(Boolean);

  const navigationErrors = [];
  let loaded = await tryLoadProfile(page, candidates, navigationErrors);

  if (!loaded && interactiveLogin && !headless) {
    await page.goto(profileUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => undefined);
    await waitForEnter();
    loaded = await tryLoadProfile(page, candidates, navigationErrors);
  }

  if (!loaded) {
    const diag = await pageDiagnostics(page);
    const reason = `No se pudo abrir LinkedIn autenticado: ${navigationErrors.slice(0, 3).join(' | ')}`;
    console.error(`LinkedIn browser snapshot: unavailable. ${reason}`);
    console.log(
      JSON.stringify({
        url: profileUrl,
        reason,
        authMode: authMode(),
        browser: label,
        diagnostics: diag,
      }),
    );
    process.exit(0);
  }

  const snapshot = await page.evaluate(() => {
    const pick = (selector) => document.querySelector(selector)?.textContent?.trim() || '';
    const pickFirst = (selectors) => selectors.map((selector) => pick(selector)).find(Boolean) || '';
    const meta = (selector) => document.querySelector(selector)?.getAttribute('content') || '';
    const bodyText = document.body?.innerText || '';
    const headings = Array.from(document.querySelectorAll('h1,h2,h3'))
      .map((node) => node.textContent?.trim())
      .filter(Boolean);

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

  const detailPaths = ['education', 'certifications', 'projects', 'courses'];
  const detailChunks = [];
  for (const detailPath of detailPaths) {
    const detailUrl = profileUrl.replace(/\/?$/, `/details/${detailPath}/`);
    try {
      await page.goto(detailUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(3500);
      const text = await bodyText(page);
      if (authenticatedPage(page.url(), text)) {
        detailChunks.push(`\n\n[linkedin:${detailPath}]\n${text.slice(0, 40000)}`);
      }
    } catch {
      // El detalle puede fallar sin invalidar todo el snapshot; se conserva lo demás.
    }
  }
  snapshot.detailsRawText = detailChunks.join('\n').slice(0, 120000);

  const activityUrl = profileUrl.replace(/\/?$/, '/recent-activity/all/');
  try {
    await page.goto(activityUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(5000);
    const text = await bodyText(page);
    snapshot.activityRawText = authenticatedPage(page.url(), text) ? text.slice(0, 60000) : '';
  } catch {
    snapshot.activityRawText = '';
  }

  snapshot.authMode = mode;
  snapshot.browser = label;
  snapshot.title = compact(snapshot.title, 220);
  snapshot.name = compact(snapshot.name, 160);
  snapshot.headline = compact(snapshot.headline, 220);
  snapshot.summary = compact(snapshot.summary, 600);
  snapshot.metaDescription = compact(snapshot.metaDescription, 600);

  const lineCount = String(snapshot.rawText || '').split('\n').filter(Boolean).length;
  const detailLineCount = String(snapshot.detailsRawText || '').split('\n').filter(Boolean).length;
  const activityLineCount = String(snapshot.activityRawText || '').split('\n').filter(Boolean).length;
  console.error(
    `LinkedIn browser snapshot: available. profile_lines=${lineCount} detail_lines=${detailLineCount} ` +
    `activity_lines=${activityLineCount} url=${snapshot.url}`,
  );
  console.log(JSON.stringify(snapshot));
} catch (error) {
  const reason = safeError(error);
  console.error(`LinkedIn browser snapshot: unavailable. ${reason}`);
  console.log(JSON.stringify({ url: profileUrl, reason, authMode: authMode() }));
} finally {
  if (contextHandle?.context) {
    await contextHandle.context.close().catch(() => undefined);
  }
  if (contextHandle?.browser) {
    await contextHandle.browser.close().catch(() => undefined);
  }
}
