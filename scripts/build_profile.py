#!/usr/bin/env python3
"""Genera el README público del perfil GitHub de h0w4r.

El script está diseñado para ejecutarse tanto localmente como desde GitHub
Actions. No usa dependencias externas: consulta GitHub y Gravatar con urllib,
arma un Markdown determinístico y valida que no queden rastros del perfil viejo.
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".github" / "profile.config.json"
README_PATH = ROOT / "README.md"
GITHUB_API = "https://api.github.com"
GRAVATAR_API = "https://api.gravatar.com/v3"
USER_AGENT = "h0w4r-profile-builder/1.0 (+https://github.com/h0w4r/h0w4r)"

# Cadenas que no deben volver a aparecer en el README público.
BANNED_STRINGS = [
    "deepe4ba5a8d356",
    "Kernel de Sistema Operativo",
    "Proyecto en planificación",
    "cehprad@gmail.com",
]

# Cadenas mínimas que aseguran que el perfil nuevo tiene las piezas esperadas.
REQUIRED_STRINGS = [
    "Chris Kirsch",
    "Perfil profesional",
    "Ecosistema vivo",
    "Live ecosystem",
    "MCP-IBMiDocs",
    "Plugin-Codex-para-RDi",
    "https://gravatar.com/ckirsch94",
    "Repos pineados recomendados",
]

LINKEDIN_SECTION_ALIASES = {
    "experience": ("Experiencia", "Experience"),
    "projects": ("Proyectos", "Projects"),
    "courses": ("Cursos", "Courses"),
    "publications": ("Publicaciones", "Publications"),
    "certifications": ("Licencias y certificaciones", "Licenses & certifications", "Certifications"),
    "education": ("Educación", "Education"),
}

LINKEDIN_ABOUT_ALIASES = ("Acerca de", "About")

LINKEDIN_AUTHWALL_MARKERS = (
    "join linkedin",
    "agree & join linkedin",
    "sign up | linkedin",
    "authwall",
    "login | linkedin",
    "inicia sesión",
    "únete a linkedin",
)

LINKEDIN_AUTH_URL_MARKERS = (
    "/uas/login",
    "/login",
    "/checkpoint",
    "/authwall",
    "session_redirect",
)

LINKEDIN_UI_NOISE_EXACT = {
    "0 notificaciones",
    "notificaciones",
    "notifications",
    "comentarios",
    "imágenes",
    "images",
    "posts",
    "actividad",
    "activity",
    "inicio",
    "home",
    "feed",
    "empleos",
    "jobs",
    "mensajes",
    "messaging",
    "mi red",
    "my network",
    "ver perfil",
    "view profile",
    "mostrar todo",
    "show all",
    "mostrar más",
    "show more",
    "guardar",
    "save",
}

LINKEDIN_UI_NOISE_PATTERNS = (
    r"^\d+\s+notificaciones?$",
    r"^\d+\s+notifications?$",
    r"^activar para ver una imagen más grande$",
    r"^christian enrique huicho prado$",
    r"^ingeniero de software\s*\|\s*procesos de medios de pago y banca\.?$",
    r"^linkedin member$",
    r"^premium$",
    r"^hashtag$",
    r"^reacciones?$",
    r"^comments?$",
    r"^shares?$",
)

LINKEDIN_SECTION_CHUNK_SIZES = {
    "experience": 4,
    "projects": 4,
    "courses": 3,
    "publications": 3,
    "certifications": 4,
    "education": 2,
}

LINKEDIN_PUBLICATION_SIGNALS = re.compile(
    r"(?:\b(?:19|20)\d{2}\b|doi|isbn|issn|publica(?:do|ción|tion)|published|publisher|revista|journal|conference|congreso|https?://)",
    re.IGNORECASE,
)

LINKEDIN_README_NOISE_PATTERNS = (
    "0 notificaciones",
    "0 notifications",
    "Fuente sincronizada",
    "actualización automática diaria",
    "LinkedIn professional signals",
    "**Headline:**",
    "Última actualización automática",
    "Join LinkedIn",
    "Agree & Join LinkedIn",
    "Sign in to view",
    "Inicia sesión en LinkedIn",
    "Registrarse | LinkedIn",
    "session_redirect",
    "página sin contenido autenticado",
    "authwall",
    "\n- Comentarios\n",
    "\n- Imágenes\n",
    "Experiencia reciente / Recent experience",
    "Jornada completa",
)

PUBLICATION_TOPIC_KEYWORDS = {
    "IBM i/AS400": (
        "ibm i",
        "as/400",
        "as400",
        "rpgle",
        "clle",
        "db2 for i",
        "rdi",
        "eclipse",
    ),
    "tooling IA, MCP y Codex": (
        "mcp",
        "codex",
        "ia",
        "ai",
        "llm",
        "agente",
        "agentes",
        "assistant",
        "copilot",
    ),
    "open source/documentación": (
        "open source",
        "github",
        "documentación",
        "documentation",
        "docs",
        "comunidad",
        "community",
    ),
    "backends y APIs": (
        "java",
        "spring",
        "api",
        "rest",
        "backend",
        "servicio",
        "service",
    ),
    "seguridad/datos": (
        "seguridad",
        "security",
        "cve",
        "vulnerabilidad",
        "vulnerability",
        "ocr",
        "sensible",
        "datos",
        "data",
        "azure",
    ),
}


class FetchError(RuntimeError):
    """Error recuperable al consultar una API externa."""


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Carga la configuración editable del perfil."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def get_token() -> str | None:
    """Obtiene un token de GitHub si existe en el entorno."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def linkedin_secret_configured() -> bool:
    """Indica si existe alguna fuente privada configurada para LinkedIn."""
    json_file = os.environ.get("LINKEDIN_PROFILE_JSON_FILE")
    return bool(
        os.environ.get("LINKEDIN_COOKIE")
        or os.environ.get("LINKEDIN_PROFILE_JSON")
        or (json_file and Path(json_file).exists())
    )


def env_truthy(name: str) -> bool:
    """Lee banderas booleanas desde variables de entorno de forma tolerante."""
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "y", "on", "si", "sí"}


def request_json(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    retries: int = 2,
) -> Any:
    """Hace una petición JSON con reintentos acotados y mensajes legibles."""
    data = None
    headers = {
        "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/json",
        "User-Agent": USER_AGENT,
    }
    if "api.github.com" in url:
        headers["X-GitHub-Api-Version"] = "2026-03-10"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise FetchError(f"{method} {url} falló con HTTP {exc.code}: {body[:300]}") from exc
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise FetchError(f"{method} {url} falló: {exc}") from exc
    raise FetchError(f"{method} {url} falló tras {retries + 1} intentos")


def request_text(url: str, *, headers: dict[str, str] | None = None, retries: int = 1) -> str:
    """Hace una petición de texto/HTML con reintentos acotados.

    Se usa para LinkedIn porque, cuando hay sesión, la respuesta esperada es HTML
    y no JSON. Los headers sensibles se reciben desde secretos del workflow y no
    se escriben en logs ni en el README.
    """
    request_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
        "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "User-Agent": USER_AGENT,
    }
    if headers:
        request_headers.update(headers)

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504, 999} and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise FetchError(f"GET {url} falló con HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise FetchError(f"GET {url} falló: {exc}") from exc
    raise FetchError(f"GET {url} falló tras {retries + 1} intentos")


def github_get(path: str, token: str | None) -> Any:
    """Consulta un endpoint REST de GitHub."""
    return request_json(f"{GITHUB_API}{path}", token=token)


def github_get_paginated(path: str, token: str | None, max_pages: int = 3) -> list[dict[str, Any]]:
    """Lee páginas simples de GitHub usando parámetros page/per_page."""
    separator = "&" if "?" in path else "?"
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        page_path = f"{path}{separator}per_page=100&page={page}"
        chunk = github_get(page_path, token)
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 100:
            break
    return rows


def fetch_contributions(username: str, token: str | None) -> dict[str, Any]:
    """Obtiene contribuciones del año actual mediante GraphQL.

    GitHub solo expone esta señal de forma completa vía GraphQL autenticado;
    si no hay token, el README sigue generándose con una nota transparente.
    """
    if not token:
        return {"available": False, "reason": "sin token GitHub"}

    now = datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(now.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar { totalContributions }
          totalCommitContributions
          totalIssueContributions
          totalPullRequestContributions
          totalPullRequestReviewContributions
          restrictedContributionsCount
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {
            "login": username,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        },
    }
    try:
        response = request_json(f"{GITHUB_API}/graphql", token=token, method="POST", payload=payload)
        collection = response["data"]["user"]["contributionsCollection"]
        return {
            "available": True,
            "year": now.year,
            "total": collection["contributionCalendar"]["totalContributions"],
            "commits": collection["totalCommitContributions"],
            "issues": collection["totalIssueContributions"],
            "prs": collection["totalPullRequestContributions"],
            "reviews": collection["totalPullRequestReviewContributions"],
            "restricted": collection["restrictedContributionsCount"],
        }
    except (KeyError, TypeError, FetchError) as exc:
        return {"available": False, "reason": str(exc)}


def fetch_gravatar(slug: str) -> dict[str, Any]:
    """Lee el perfil público de Gravatar usando el slug configurado."""
    try:
        return request_json(f"{GRAVATAR_API}/profiles/{urllib.parse.quote(slug)}")
    except FetchError as exc:
        return {"profile_url": f"https://gravatar.com/{slug}", "error": str(exc)}


def normalize_linkedin_list(value: Any, *, limit: int) -> list[str]:
    """Normaliza listas de LinkedIn desde JSON manual/exportado o extracción HTML."""
    if not value:
        return []
    raw_items = value if isinstance(value, list) else [value]
    items: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            title = item.get("title") or item.get("name") or item.get("role") or item.get("course")
            organization = item.get("company") or item.get("organization") or item.get("issuer") or item.get("school")
            period = item.get("period") or item.get("date") or item.get("dates")
            description = item.get("description") or item.get("summary")
            parts = [part for part in [title, organization, period, description] if part]
            text = " · ".join(str(part) for part in parts)
        else:
            text = str(item)
        text = compact_text(text)
        if text and not is_linkedin_noise(text) and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def normalize_linkedin_text_list(value: Any, *, limit: int = 8) -> list[str]:
    """Normaliza textos largos como publicaciones, actividad o insights editoriales."""
    if not value:
        return []
    raw_items = value if isinstance(value, list) else [value]
    items: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            text = item.get("text") or item.get("content") or item.get("summary") or item.get("title") or ""
        else:
            text = str(item)
        text = compact_text(text, max_len=360)
        if text and not is_linkedin_noise(text) and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def load_json_file(path_value: str | None) -> dict[str, Any] | None:
    """Carga JSON desde archivo temporal si existe."""
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def compact_text(value: Any, *, max_len: int = 220) -> str:
    """Compacta texto público para que el README no se convierta en pergamino medieval."""
    text = html.unescape("" if value is None else str(value))
    text = re.sub(r"\s+", " ", text).strip(" -·|•\t\r\n")
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def is_linkedin_noise(value: str) -> bool:
    """Detecta texto de navegación/UI de LinkedIn que no debe llegar al README."""
    text = compact_text(value, max_len=260)
    if not text:
        return True
    lower = text.lower().strip(" -·|•\t\r\n")
    if lower in LINKEDIN_UI_NOISE_EXACT:
        return True
    return any(re.search(pattern, lower, flags=re.IGNORECASE) for pattern in LINKEDIN_UI_NOISE_PATTERNS)


def clean_linkedin_lines(lines: list[str]) -> list[str]:
    """Deduplica y elimina ruido común de LinkedIn conservando orden."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = compact_text(line, max_len=260)
        key = text.lower()
        if not text or key in seen or is_linkedin_noise(text):
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def sanitize_linkedin_headline(value: Any) -> str:
    """Evita que contadores/notificaciones del DOM se rendericen como headline."""
    headline = compact_text(value, max_len=180)
    if is_linkedin_noise(headline):
        return ""
    if len(headline) < 8 or not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", headline):
        return ""
    if re.search(r"(linkedin|feed|inicio|home|notification|notificación)", headline, flags=re.IGNORECASE):
        return ""
    return headline


def group_linkedin_section_lines(key: str, lines: list[str], *, limit: int) -> list[str]:
    """Agrupa líneas consecutivas de LinkedIn en items profesionales legibles."""
    cleaned = clean_linkedin_lines(lines)
    if key == "publications" and not any(LINKEDIN_PUBLICATION_SIGNALS.search(line) for line in cleaned):
        # La sección de publicaciones suele mezclar actividad/feed; si no hay una
        # señal bibliográfica o temporal clara, es mejor no mostrar basura.
        return []

    chunk_size = LINKEDIN_SECTION_CHUNK_SIZES.get(key, 3)
    grouped: list[str] = []
    for index in range(0, len(cleaned), chunk_size):
        chunk = cleaned[index : index + chunk_size]
        if not chunk:
            continue
        if key in {"experience", "projects", "certifications"} and len(chunk) == 1 and len(chunk[0]) < 12:
            continue
        item = compact_text(" · ".join(chunk), max_len=280)
        if item and item not in grouped:
            grouped.append(item)
        if len(grouped) >= limit:
            break
    return grouped


def visible_text_from_html(raw_html: str) -> list[str]:
    """Extrae líneas visibles de HTML de LinkedIn con filtros de ruido comunes."""
    text = re.sub(r"(?is)<(script|style|noscript|svg|template).*?</\1>", " ", raw_html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|li|h[1-6]|div|section|span)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = compact_text(raw_line, max_len=260)
        key = line.lower()
        if len(line) < 3 or is_linkedin_noise(line) or key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return lines


def extract_meta_value(raw_html: str, *names: str) -> str:
    """Lee meta tags básicos de una página HTML."""
    for name in names:
        patterns = [
            rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, raw_html, flags=re.IGNORECASE)
            if match:
                return compact_text(match.group(1), max_len=260)
    return ""


def extract_json_ld_person(raw_html: str) -> dict[str, Any]:
    """Intenta leer datos Person en JSON-LD cuando LinkedIn los expone."""
    for match in re.finditer(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw_html):
        try:
            payload = json.loads(html.unescape(match.group(1)).strip())
        except json.JSONDecodeError:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if item_type == "Person" or (isinstance(item_type, list) and "Person" in item_type):
                return item
    return {}


def extract_linkedin_sections(lines: list[str], *, limit: int) -> dict[str, list[str]]:
    """Extrae secciones profesionales de texto visible usando encabezados ES/EN."""
    alias_to_key = {
        alias.lower(): key
        for key, aliases in LINKEDIN_SECTION_ALIASES.items()
        for alias in aliases
    }
    section_indexes: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        key = alias_to_key.get(line.strip().lower())
        if key:
            section_indexes.append((index, key))

    sections: dict[str, list[str]] = {}
    for pos, (start_index, key) in enumerate(section_indexes):
        end_index = section_indexes[pos + 1][0] if pos + 1 < len(section_indexes) else min(len(lines), start_index + 28)
        candidates: list[str] = []
        max_candidate_lines = max(limit * LINKEDIN_SECTION_CHUNK_SIZES.get(key, 3), limit)
        for line in lines[start_index + 1 : end_index]:
            lower_line = line.lower()
            if lower_line in alias_to_key or is_linkedin_noise(line):
                continue
            if 3 <= len(line) <= 260 and line not in candidates:
                candidates.append(line)
            if len(candidates) >= max_candidate_lines:
                break
        grouped = group_linkedin_section_lines(key, candidates, limit=limit)
        if grouped:
            sections[key] = grouped
    return sections


def extract_linkedin_about(lines: list[str]) -> str:
    """Extrae un resumen desde la sección Acerca de/About de LinkedIn."""
    aliases = {alias.lower() for alias in LINKEDIN_ABOUT_ALIASES}
    stop_aliases = {
        alias.lower()
        for aliases_for_section in LINKEDIN_SECTION_ALIASES.values()
        for alias in aliases_for_section
    }
    stop_aliases.update({"activity", "actividad", "contacto", "contact", "featured", "destacado"})
    for index, line in enumerate(lines):
        if line.strip().lower() not in aliases:
            continue
        chunks: list[str] = []
        for candidate in lines[index + 1 : index + 8]:
            lower_candidate = candidate.strip().lower()
            if lower_candidate in aliases or lower_candidate in stop_aliases:
                break
            if is_linkedin_noise(candidate):
                continue
            if len(candidate) >= 20:
                chunks.append(candidate)
            if chunks:
                break
        if chunks:
            return compact_text(" ".join(chunks), max_len=360)
    return ""


def sanitize_linkedin_summary(value: Any) -> str:
    """Acepta solo resúmenes personales/profesionales, no footer ni páginas rotas."""
    summary = compact_text(value, max_len=360)
    if not summary or is_linkedin_noise(summary):
        return ""
    lower = summary.lower()
    bad_markers = (
        "privacidad y condiciones",
        "esta página no existe",
        "comprueba la url",
        "controles de invitados",
        "linkedin corporation",
    )
    if any(marker in lower for marker in bad_markers):
        return ""
    if len(summary) < 35:
        return ""
    return summary


DETAIL_SECTION_MARKER = re.compile(r"^\[linkedin:(?P<key>[a-z_]+)\]$", re.IGNORECASE)

DETAIL_STOP_PREFIXES = (
    "idioma del perfil",
    "otros perfiles vistos",
    "acerca de",
    "accesibilidad",
    "talent solutions",
    "pautas comunitarias",
    "empleo",
    "marketing solutions",
    "privacidad y condiciones",
    "opciones de publicidad",
    "publicidad",
    "sales solutions",
    "móvil",
    "pequeñas empresas",
    "centro de seguridad",
    "linkedin corporation",
    "¿tienes preguntas?",
    "visita nuestro centro",
    "gestiona tu cuenta",
    "accede a tu configuración",
    "transparencia de las recomendaciones",
    "seleccionar idioma",
)

DETAIL_SKIP_EXACT = {
    "christian enrique huicho prado",
    "ingeniero de software | procesos de medios de pago y banca.",
    "0 notificaciones",
    "ir al contenido principal",
    "inicio",
    "mi red",
    "empleos",
    "mensajes",
    "notificaciones",
    "yo",
    "para negocios",
    "vuelve a probar premium",
    "ver",
    "mostrar credencial",
    "no hay nada que ver por ahora",
    "cuando añadas nuevos cursos, se mostrarán aquí.",
    "añadir cursos",
}


def detail_body(lines: list[str], headings: set[str]) -> list[str]:
    """Recorta una página de detalle LinkedIn y elimina navegación/footer."""
    body: list[str] = []
    started = False
    for line in lines:
        text = compact_text(line, max_len=260)
        lower = text.lower()
        if not text or is_linkedin_noise(text):
            continue
        if lower in headings:
            started = True
            continue
        if not started:
            continue
        if lower in DETAIL_SKIP_EXACT or lower.isdigit():
            continue
        if any(lower.startswith(prefix) for prefix in DETAIL_STOP_PREFIXES):
            break
        body.append(text)
    return clean_linkedin_lines(body)


def split_tagged_linkedin_details(details_text: str) -> dict[str, list[str]]:
    """Separa el texto `[linkedin:<section>]` generado por Playwright por página real."""
    chunks: dict[str, list[str]] = {}
    current_key = ""
    for raw_line in str(details_text or "").splitlines():
        line = compact_text(raw_line, max_len=260)
        marker = DETAIL_SECTION_MARKER.match(line)
        if marker:
            current_key = marker.group("key").lower()
            chunks.setdefault(current_key, [])
            continue
        if current_key and line:
            chunks[current_key].append(line)
    return chunks


def is_cert_metadata(line: str) -> bool:
    """Identifica líneas auxiliares de una certificación LinkedIn."""
    lower = line.lower()
    return lower.startswith(("expedición:", "id de la credencial:", "aptitudes:", "vencimiento:"))


def format_cert_detail(line: str) -> str:
    """Reduce metadatos largos de certificaciones a señales útiles."""
    if line.lower().startswith("id de la credencial:"):
        return ""
    return compact_text(line.replace(" · ", " · "), max_len=120)


def parse_linkedin_certifications(lines: list[str], *, limit: int) -> list[str]:
    """Parsea certificaciones sin convertir el issuer en experiencia laboral."""
    body = detail_body(lines, {"licencias y certificaciones", "licenses & certifications"})
    items: list[str] = []
    index = 0
    while index < len(body) and len(items) < limit:
        title = body[index]
        if is_cert_metadata(title):
            index += 1
            continue
        issuer = ""
        index += 1
        if index < len(body) and not is_cert_metadata(body[index]):
            issuer = body[index]
            index += 1

        details: list[str] = []
        while index < len(body) and (is_cert_metadata(body[index]) or body[index].lower() == "mostrar credencial"):
            detail = format_cert_detail(body[index])
            if detail:
                details.append(detail)
            index += 1

        # La compañía emisora puede ser una señal académica, pero no la destacamos
        # si parece marca laboral directa; así evitamos que el README parezca CV de experiencia.
        if issuer and re.search(r"\bNTT\s*DATA\b", issuer, flags=re.IGNORECASE):
            issuer = ""
        parts = [part for part in [title, issuer, *details] if part]
        item = compact_text(" · ".join(parts), max_len=280)
        if item and item not in items:
            items.append(item)
    return items


def looks_like_school_name(line: str) -> bool:
    """Heurística pequeña para separar instituciones académicas en detalles LinkedIn."""
    lower = line.lower()
    school_markers = (
        "universidad",
        "university",
        "cibertec",
        "icpna",
        "academy",
        "institute",
        "school",
        "college",
    )
    return any(marker in lower for marker in school_markers)


def parse_linkedin_education(lines: list[str], *, limit: int) -> list[str]:
    """Parsea educación desde la página detail/education sin footer ni sugerencias."""
    body = detail_body(lines, {"educación", "education"})
    items: list[str] = []
    current: list[str] = []
    for line in body:
        if looks_like_school_name(line) and current:
            item = compact_text(" · ".join(current), max_len=240)
            if item and item not in items:
                items.append(item)
            current = [line]
        else:
            current.append(line)
        if len(items) >= limit:
            break
    if current and len(items) < limit:
        item = compact_text(" · ".join(current), max_len=240)
        if item and item not in items:
            items.append(item)
    return items[:limit]


def extract_tagged_detail_sections(details_text: str, *, limit: int) -> dict[str, list[str]]:
    """Extrae secciones de páginas `/details/*` con parsers específicos y menos ruido."""
    chunks = split_tagged_linkedin_details(details_text)
    sections: dict[str, list[str]] = {}

    certifications = parse_linkedin_certifications(chunks.get("certifications", []), limit=limit)
    if certifications:
        sections["certifications"] = certifications

    education = parse_linkedin_education(chunks.get("education", []), limit=limit)
    if education:
        sections["education"] = education

    courses_body = detail_body(chunks.get("courses", []), {"cursos", "courses"})
    if courses_body and not any("no hay nada que ver" in item.lower() for item in courses_body):
        grouped = group_linkedin_section_lines("courses", courses_body, limit=limit)
        if grouped:
            sections["courses"] = grouped

    projects_body = detail_body(chunks.get("projects", []), {"proyectos", "projects"})
    if projects_body:
        grouped = group_linkedin_section_lines("projects", projects_body, limit=limit)
        if grouped:
            sections["projects"] = grouped

    return sections


def linkedin_slug(url: str) -> str:
    """Extrae el slug `/in/<slug>/` usado por LinkedIn."""
    parsed = urllib.parse.urlparse(url)
    match = re.search(r"/in/([^/?#]+)/?", parsed.path)
    return urllib.parse.unquote(match.group(1)) if match else parsed.path.strip("/")


def linkedin_csrf_token(cookie: str) -> str:
    """Obtiene el token CSRF desde la cookie `JSESSIONID` de LinkedIn."""
    match = re.search(r'(?:^|;\s*)JSESSIONID="?([^";]+)"?', cookie)
    return html.unescape(match.group(1)) if match else ""


def walk_dicts(value: Any) -> list[dict[str, Any]]:
    """Recorre una estructura JSON y devuelve todos los diccionarios internos."""
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        rows.append(value)
        for child in value.values():
            rows.extend(walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(walk_dicts(child))
    return rows


def linkedin_plain(value: Any) -> str:
    """Convierte textos RichText/Voyager en texto plano compacto."""
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return compact_text(value["text"])
        if isinstance(value.get("localized"), dict):
            return compact_text(" ".join(str(item) for item in value["localized"].values()))
        if isinstance(value.get("com.linkedin.common.TextViewModel"), dict):
            return linkedin_plain(value["com.linkedin.common.TextViewModel"])
    return ""


def first_text(row: dict[str, Any], *keys: str) -> str:
    """Devuelve el primer campo textual útil de un diccionario."""
    for key in keys:
        if key in row:
            text = linkedin_plain(row.get(key))
            if text:
                return text
    return ""


def linkedin_date(value: Any) -> str:
    """Formatea fechas parciales de LinkedIn cuando existen."""
    if not isinstance(value, dict):
        return ""
    year = value.get("year")
    month = value.get("month")
    if year and month:
        return f"{int(year):04d}-{int(month):02d}"
    if year:
        return str(year)
    return ""


def linkedin_period(row: dict[str, Any]) -> str:
    """Extrae un rango temporal flexible desde estructuras Voyager."""
    for key in ("dateRange", "timePeriod"):
        period = row.get(key)
        if isinstance(period, dict):
            start = linkedin_date(period.get("start") or period.get("startDate"))
            end = linkedin_date(period.get("end") or period.get("endDate")) or "Actualidad"
            if start:
                return f"{start} - {end}"
    return first_text(row, "dateRange", "timePeriod", "period", "dates")


def format_linkedin_entry(row: dict[str, Any], *, title_keys: tuple[str, ...], org_keys: tuple[str, ...]) -> str:
    """Construye una línea profesional compacta desde un item Voyager."""
    title = first_text(row, *title_keys)
    organization = first_text(row, *org_keys)
    period = linkedin_period(row)
    description = first_text(row, "description", "summary", "subtitle")
    parts = [part for part in (title, organization, period, description) if part]
    return compact_text(" · ".join(parts), max_len=260)


def find_view_elements(payload: dict[str, Any], view_names: tuple[str, ...]) -> list[dict[str, Any]]:
    """Encuentra listas `elements` bajo vistas conocidas de LinkedIn Voyager."""
    elements: list[dict[str, Any]] = []
    for row in walk_dicts(payload):
        for view_name in view_names:
            view = row.get(view_name)
            if isinstance(view, dict) and isinstance(view.get("elements"), list):
                elements.extend(item for item in view["elements"] if isinstance(item, dict))
            elif isinstance(view, list):
                elements.extend(item for item in view if isinstance(item, dict))
    return elements


def collect_voyager_section(
    payload: dict[str, Any],
    *,
    view_names: tuple[str, ...],
    urn_markers: tuple[str, ...],
    title_keys: tuple[str, ...],
    org_keys: tuple[str, ...],
    limit: int,
) -> list[str]:
    """Recolecta una sección desde vistas o entidades normalizadas de Voyager."""
    candidates = find_view_elements(payload, view_names)
    if not candidates:
        for row in walk_dicts(payload):
            urn = str(row.get("entityUrn") or row.get("*entity") or "").lower()
            if any(marker in urn for marker in urn_markers):
                candidates.append(row)

    items: list[str] = []
    for row in candidates:
        text = format_linkedin_entry(row, title_keys=title_keys, org_keys=org_keys)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def parse_voyager_profile(payload: dict[str, Any], *, source: str, url: str, limit: int) -> dict[str, Any]:
    """Normaliza la respuesta Voyager/profileView al contrato del README."""
    profile_rows = [
        row
        for row in walk_dicts(payload)
        if any(key in row for key in ("headline", "summary", "firstName", "lastName", "publicIdentifier"))
    ]
    profile = profile_rows[0] if profile_rows else {}
    headline = sanitize_linkedin_headline(first_text(profile, "headline", "occupation"))
    summary = first_text(profile, "summary", "description")

    sections = {
        "experience": collect_voyager_section(
            payload,
            view_names=("positionView", "positions", "experienceView"),
            urn_markers=("position", "experience"),
            title_keys=("title", "name"),
            org_keys=("companyName", "company", "organizationName"),
            limit=limit,
        ),
        "projects": collect_voyager_section(
            payload,
            view_names=("projectView", "projects"),
            urn_markers=("project",),
            title_keys=("title", "name"),
            org_keys=("occupation", "companyName", "organizationName"),
            limit=limit,
        ),
        "courses": collect_voyager_section(
            payload,
            view_names=("courseView", "courses"),
            urn_markers=("course",),
            title_keys=("name", "title", "courseName"),
            org_keys=("number", "provider", "organizationName"),
            limit=limit,
        ),
        "publications": collect_voyager_section(
            payload,
            view_names=("publicationView", "publications"),
            urn_markers=("publication",),
            title_keys=("name", "title"),
            org_keys=("publisher", "organizationName"),
            limit=limit,
        ),
        "certifications": collect_voyager_section(
            payload,
            view_names=("certificationView", "certifications"),
            urn_markers=("certification", "license"),
            title_keys=("name", "title"),
            org_keys=("authority", "companyName", "organizationName"),
            limit=limit,
        ),
        "education": collect_voyager_section(
            payload,
            view_names=("educationView", "educations"),
            urn_markers=("education",),
            title_keys=("schoolName", "degreeName", "title", "name"),
            org_keys=("degreeName", "fieldOfStudy", "organizationName"),
            limit=limit,
        ),
    }
    # El README público no muestra historial laboral; LinkedIn se usa aquí para
    # perfil profesional, formación y señales editoriales/académicas.
    sections.pop("experience", None)
    sections = {key: clean_linkedin_lines(values) for key, values in sections.items() if values}
    sections = {key: values for key, values in sections.items() if values}
    return {
        "available": bool(headline or summary or sections),
        "source": source,
        "url": url,
        "headline": headline,
        "summary": summary,
        "sections": sections,
        "reason": "" if (headline or summary or sections) else "Voyager respondió sin datos profesionales mapeables",
    }


def fetch_linkedin_voyager(url: str, cookie: str, *, limit: int) -> dict[str, Any]:
    """Lee LinkedIn usando la API Voyager autenticada con la cookie del usuario."""
    slug = linkedin_slug(url)
    csrf = linkedin_csrf_token(cookie)
    headers = {
        "Accept": "application/vnd.linkedin.normalized+json+2.1",
        "Cookie": cookie,
        "Csrf-Token": csrf,
        "X-Restli-Protocol-Version": "2.0.0",
        "X-Li-Lang": "es_ES",
        "X-Li-Track": '{"clientVersion":"1.0.0","osName":"web","timezoneOffset":-5,"deviceFormFactor":"DESKTOP"}',
        "Referer": url,
    }
    endpoint = f"https://www.linkedin.com/voyager/api/identity/profiles/{urllib.parse.quote(slug)}/profileView"
    raw = request_text(endpoint, headers=headers, retries=1)
    payload = json.loads(raw)
    return parse_voyager_profile(payload, source="LINKEDIN_COOKIE/voyager", url=url, limit=limit)


def normalize_linkedin_payload(payload: dict[str, Any], *, source: str, url: str, limit: int) -> dict[str, Any]:
    """Normaliza una fuente estructurada de LinkedIn al contrato interno del README."""
    payload_url = str(payload.get("url") or "")
    if any(marker in payload_url.lower() for marker in LINKEDIN_AUTH_URL_MARKERS):
        return {"available": False, "source": source, "url": url, "reason": "snapshot apunta a login/checkpoint de LinkedIn"}

    sections: dict[str, list[str]] = {}
    for key in LINKEDIN_SECTION_ALIASES:
        if key == "experience":
            # No se expone experiencia laboral en el README de GitHub.
            continue
        values = normalize_linkedin_list(payload.get(key), limit=limit)
        if key == "publications" and not any(LINKEDIN_PUBLICATION_SIGNALS.search(value) for value in values):
            values = []
        sections[key] = values
    raw_text = str(payload.get("rawText") or payload.get("raw_text") or "")
    details_text = str(payload.get("detailsRawText") or payload.get("details_raw_text") or "")
    activity_text = payload.get("activityRawText") or payload.get("activity_raw_text") or ""
    if raw_text:
        lines = [compact_text(line, max_len=260) for line in str(raw_text).splitlines()]
        lines = [line for line in lines if line]
        extracted_sections = extract_linkedin_sections(lines, limit=limit)
        for key, values in extracted_sections.items():
            if key == "experience":
                continue
            sections.setdefault(key, [])
            for value in values:
                if value not in sections[key] and len(sections[key]) < limit:
                    sections[key].append(value)
    if details_text:
        for key, values in extract_tagged_detail_sections(details_text, limit=limit).items():
            # Las páginas `/details/*` son más confiables que el texto global porque
            # vienen separadas por sección; reemplazan cualquier extracción genérica.
            sections[key] = values
    sections = {key: values for key, values in sections.items() if values}
    summary = sanitize_linkedin_summary(
        payload.get("summary")
        or payload.get("about")
        or payload.get("description")
        or payload.get("metaDescription"),
    )
    if not summary and raw_text:
        summary = sanitize_linkedin_summary(
            extract_linkedin_about([compact_text(line, max_len=260) for line in str(raw_text).splitlines() if line.strip()])
        )
    headline = sanitize_linkedin_headline(payload.get("headline") or payload.get("title"))
    posts = normalize_linkedin_text_list(payload.get("posts") or payload.get("activity"), limit=8)
    if activity_text:
        for line in clean_linkedin_lines(
            [compact_text(line, max_len=360) for line in str(activity_text).splitlines() if line.strip()]
        ):
            if looks_like_publication_signal(line) and line not in posts:
                posts.append(line)
            if len(posts) >= 8:
                break
    publication_insights = normalize_linkedin_text_list(
        payload.get("publication_insights") or payload.get("publicationInsights"),
        limit=6,
    )
    available = bool(summary or headline or sections or publication_insights or posts)
    return {
        "available": available,
        "source": source,
        "url": payload.get("url") or url,
        "headline": headline,
        "summary": summary,
        "sections": sections,
        "publication_insights": publication_insights,
        "posts": posts,
        "reason": "" if available else "sin datos profesionales estructurados",
    }


def parse_linkedin_html(raw_html: str, *, source: str, url: str, limit: int) -> dict[str, Any]:
    """Convierte HTML de LinkedIn en un snapshot pequeño y publicable."""
    lower = raw_html.lower()
    if any(marker in lower for marker in LINKEDIN_AUTHWALL_MARKERS):
        return {"available": False, "source": source, "url": url, "reason": "authwall o bloqueo de LinkedIn"}

    person = extract_json_ld_person(raw_html)
    lines = visible_text_from_html(raw_html)
    sections = extract_linkedin_sections(lines, limit=limit)
    sections.pop("experience", None)
    summary = (
        compact_text(person.get("description"), max_len=360)
        or extract_meta_value(raw_html, "description", "og:description")
    )
    headline = sanitize_linkedin_headline(person.get("jobTitle")) or sanitize_linkedin_headline(
        extract_meta_value(raw_html, "og:title", "title")
    )
    return {
        "available": bool(summary or headline or sections),
        "source": source,
        "url": url,
        "headline": headline,
        "summary": summary,
        "sections": sections,
        "reason": "" if (summary or headline or sections) else "sin datos profesionales extraíbles",
    }


def fetch_linkedin(config: dict[str, Any]) -> dict[str, Any]:
    """Lee LinkedIn con fallback seguro.

    Orden de fuentes:
    1. `LINKEDIN_PROFILE_JSON_FILE`: snapshot vivo extraído por Playwright en el workflow.
    2. `LINKEDIN_COOKIE`: fallback legacy para leer la página real.
    3. `LINKEDIN_PROFILE_JSON`: snapshot estructurado de emergencia si LinkedIn bloquea al runner.
    4. Acceso público directo/proxy: normalmente LinkedIn responde authwall/999, pero se intenta.

    Si `LINKEDIN_SNAPSHOT_ONLY=1`, el generador no hace fallback a cookie/API pública:
    falla explícitamente cuando el snapshot vivo no trae datos. Esto evita que el
    workflow self-hosted publique datos viejos cuando la sesión local se rompió.
    """
    profile = config["profile"]
    linkedin_config = config.get("linkedin", {})
    url = linkedin_config.get("url") or profile["links"]["linkedin"]
    limit = int(linkedin_config.get("sectionItemLimit", 4))
    snapshot_only = env_truthy("LINKEDIN_SNAPSHOT_ONLY")

    attempts: list[str] = []

    json_file = os.environ.get("LINKEDIN_PROFILE_JSON_FILE")
    file_payload = load_json_file(json_file)
    if file_payload:
        normalized = normalize_linkedin_payload(file_payload, source="LINKEDIN_PROFILE_JSON_FILE", url=url, limit=limit)
        if normalized["available"]:
            return normalized
        attempts.append(f"browser_snapshot: {file_payload.get('reason') or normalized.get('reason')}")
    elif snapshot_only and json_file:
        attempts.append(f"browser_snapshot: no se pudo leer {json_file}")

    if snapshot_only:
        return {
            "available": False,
            "source": "snapshot_unavailable",
            "url": url,
            "headline": "",
            "summary": "",
            "sections": {},
            "reason": "; ".join(attempts[:3]) or "snapshot requerido sin datos",
        }

    cookie = os.environ.get("LINKEDIN_COOKIE")
    if cookie:
        try:
            parsed = fetch_linkedin_voyager(url, cookie, limit=limit)
            if parsed.get("available"):
                return parsed
            attempts.append(str(parsed.get("reason") or "voyager sin datos extraíbles"))
        except (FetchError, json.JSONDecodeError, TypeError, TimeoutError, OSError, urllib.error.URLError) as exc:
            attempts.append(f"voyager_api: {exc}")
        try:
            raw_html = request_text(url, headers={"Cookie": cookie}, retries=1)
            parsed = parse_linkedin_html(raw_html, source="LINKEDIN_COOKIE", url=url, limit=limit)
            if parsed.get("available"):
                return parsed
            attempts.append(str(parsed.get("reason") or "cookie sin datos extraíbles"))
        except (FetchError, TimeoutError, OSError, urllib.error.URLError) as exc:
            attempts.append(str(exc))
    else:
        attempts.append("LINKEDIN_COOKIE no configurado")

    secret_json = os.environ.get("LINKEDIN_PROFILE_JSON")
    if secret_json:
        try:
            payload = json.loads(secret_json)
            normalized = normalize_linkedin_payload(payload, source="LINKEDIN_PROFILE_JSON", url=url, limit=limit)
            if normalized["available"]:
                return normalized
            attempts.append(str(normalized.get("reason") or "LINKEDIN_PROFILE_JSON sin datos útiles"))
        except json.JSONDecodeError:
            attempts.append("LINKEDIN_PROFILE_JSON no es JSON válido")

    reader_profile_url = f"https://r.jina.ai/{url}"
    reader_activity_url = f"https://r.jina.ai/{url.rstrip('/')}/recent-activity/all/"
    for source, candidate_url in (
        ("linkedin_public", url),
        ("jina_public_profile", reader_profile_url),
        ("jina_public_activity", reader_activity_url),
    ):
        try:
            raw_html = request_text(candidate_url, retries=0)
            parsed = parse_linkedin_html(raw_html, source=source, url=url, limit=limit)
            if parsed.get("available"):
                return parsed
            attempts.append(f"{source}: {parsed.get('reason')}")
        except FetchError as exc:
            attempts.append(f"{source}: {exc}")

    return {
        "available": False,
        "source": "unavailable",
        "url": url,
        "headline": "",
        "summary": "",
        "sections": {},
        "reason": "; ".join(attempts[:3]),
    }


def linkedin_diagnostics(config: dict[str, Any]) -> dict[str, Any]:
    """Devuelve un diagnóstico seguro, sin imprimir cookies ni HTML privado."""
    linkedin = fetch_linkedin(config)
    sections = linkedin.get("sections", {}) or {}
    publication_items = publication_items_from_linkedin(linkedin)
    return {
        "available": bool(linkedin.get("available")),
        "source": linkedin.get("source"),
        "url": linkedin.get("url"),
        "secret_configured": linkedin_secret_configured(),
        "has_headline": bool(linkedin.get("headline")),
        "has_summary": bool(linkedin.get("summary")),
        "section_counts": {key: len(values) for key, values in sections.items()},
        "publication_signal_count": len(publication_items),
        "publication_insight_count": len(analyze_publication_items(publication_items)),
        "reason": "" if linkedin.get("available") else linkedin.get("reason", "sin detalle"),
    }


def print_linkedin_diagnostics(config: dict[str, Any], *, require_when_configured: bool) -> int:
    """Imprime diagnóstico de LinkedIn y falla solo si un secreto configurado no funciona."""
    diagnostics = linkedin_diagnostics(config)
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    if require_when_configured and diagnostics["secret_configured"] and not diagnostics["available"]:
        print(
            "ERROR: hay secreto de LinkedIn configurado, pero no se extrajeron datos profesionales.",
            file=sys.stderr,
        )
        return 1
    return 0


def repo_map(repos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Indexa repos por nombre para mezclar metadatos vivos con overrides."""
    return {repo["name"]: repo for repo in repos}


def fetch_languages(username: str, repos: list[dict[str, Any]], token: str | None, limit: int) -> dict[str, dict[str, int]]:
    """Obtiene lenguajes por repositorio con tolerancia a fallos puntuales."""
    languages: dict[str, dict[str, int]] = {}
    for repo in repos[:limit]:
        if repo.get("archived"):
            continue
        name = repo["name"]
        try:
            languages[name] = github_get(f"/repos/{username}/{urllib.parse.quote(name)}/languages", token) or {}
        except FetchError:
            languages[name] = {}
    return languages


def top_languages(repos: list[dict[str, Any]], languages: dict[str, dict[str, int]]) -> list[tuple[str, int]]:
    """Calcula un ranking híbrido de lenguajes para que HTML de docs no opaque todo."""
    primary_counter: Counter[str] = Counter()
    byte_counter: defaultdict[str, int] = defaultdict(int)
    for repo in repos:
        primary = repo.get("language")
        if primary:
            primary_counter[primary] += 1
        for lang, amount in languages.get(repo["name"], {}).items():
            byte_counter[lang] += int(amount)

    # Peso: aparición como lenguaje principal + señal de bytes. HTML se conserva,
    # pero con menos protagonismo cuando representa corpus/documentación.
    scored: dict[str, int] = {}
    for lang in set(primary_counter) | set(byte_counter):
        score = primary_counter[lang] * 1000 + min(byte_counter[lang] // 1000, 999)
        if lang == "HTML":
            score = score // 3
        scored[lang] = score
    return sorted(scored.items(), key=lambda item: (-item[1], item[0]))[:8]


def format_date(value: str | None) -> str:
    """Convierte fechas ISO de GitHub a YYYY-MM-DD."""
    if not value:
        return "n/d"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return value[:10]


def md_escape(value: Any) -> str:
    """Escapa valores para tablas Markdown."""
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def badge(label: str, message: str, color: str = "0f172a") -> str:
    """Crea badges estáticos de shields.io."""
    safe_label = urllib.parse.quote(label.replace("-", "--"))
    safe_message = urllib.parse.quote(message.replace("-", "--"))
    return f"![{label}: {message}](https://img.shields.io/badge/{safe_label}-{safe_message}-{color}?style=for-the-badge)"


def with_query_params(url: str, **params: str) -> str:
    """Agrega o reemplaza parámetros de query sin romper URLs que ya tenían query string."""
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def local_timestamp(profile: dict[str, Any]) -> str:
    """Devuelve la fecha de actualización en la zona horaria pública del perfil.

    Se usa fecha, no minuto exacto, para que `--check` sea estable durante el día
    y el workflow no genere commits ruidosos por diferencias de reloj.
    """
    timezone_name = profile.get("timezone", "UTC")
    timezone_label = profile.get("timezoneLabel", timezone_name)
    try:
        now = datetime.now(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        # Fallback explícito: el README sigue generándose aunque el runner no conozca la zona.
        now = datetime.now(timezone.utc)
        timezone_label = "UTC"

    offset = now.strftime("%z")
    offset_text = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return f"{now:%Y-%m-%d} {timezone_label} ({offset_text})"


def repo_url(username: str, repo_name: str) -> str:
    """Construye la URL pública del repositorio."""
    return f"https://github.com/{username}/{repo_name}"


def summarize_event(event: dict[str, Any]) -> str:
    """Convierte eventos públicos de GitHub en frases compactas."""
    event_type = event.get("type", "Evento")
    repo = event.get("repo", {}).get("name", "h0w4r/repo").split("/", 1)[-1]
    payload = event.get("payload", {})

    if event_type == "PushEvent":
        count = len(payload.get("commits", []))
        ref = (payload.get("ref") or "").replace("refs/heads/", "")
        branch = f" · rama `{ref}`" if ref else ""
        if count > 0:
            return f"Push en `{repo}` · {count} commit{'s' if count != 1 else ''}{branch}"
        return f"Push/actualización en `{repo}`{branch}"
    if event_type == "CreateEvent":
        ref_type = payload.get("ref_type", "recurso")
        ref = payload.get("ref") or repo
        return f"Creó {ref_type} `{ref}` en `{repo}`"
    if event_type == "PullRequestEvent":
        action = payload.get("action", "actualizó")
        number = payload.get("number")
        suffix = f" #{number}" if number else ""
        return f"Pull request {action}{suffix} en `{repo}`"
    if event_type == "IssuesEvent":
        action = payload.get("action", "actualizó")
        number = payload.get("issue", {}).get("number")
        suffix = f" #{number}" if number else ""
        return f"Issue {action}{suffix} en `{repo}`"
    if event_type == "ReleaseEvent":
        action = payload.get("action", "publicó")
        tag = payload.get("release", {}).get("tag_name", "release")
        return f"Release {action} `{tag}` en `{repo}`"
    if event_type == "WatchEvent":
        return f"Star/Watch en `{repo}`"
    return f"{event_type.replace('Event', '')} en `{repo}`"


def repo_freshness_key(repo: dict[str, Any]) -> str:
    """Clave de orden para priorizar repos por actividad real de commits/pushes recientes."""
    return repo.get("pushed_at") or repo.get("updated_at") or ""


def render_featured_table(config: dict[str, Any], repos_by_name: dict[str, dict[str, Any]]) -> str:
    """Renderiza los proyectos destacados priorizando los que tienen commits/pushes recientes."""
    username = config["profile"]["username"]
    featured_items = sorted(
        enumerate(config["featuredRepositories"]),
        key=lambda pair: (repo_freshness_key(repos_by_name.get(pair[1]["name"], {})), -pair[0]),
        reverse=True,
    )
    rows = [
        "| Proyecto / Project | Foco / Focus | Impacto / Impact | Actividad / Activity |",
        "|---|---|---|---|",
    ]
    for _, item in featured_items:
        name = item["name"]
        repo = repos_by_name.get(name, {})
        lang = repo.get("language") or "multi-stack"
        stars = repo.get("stargazers_count", 0)
        pushed = format_date(repo.get("pushed_at"))
        signal = f"{lang} · ⭐ {stars} · último push {pushed}"
        focus = f"{item['taglineEs']}<br/><sub>{item['taglineEn']}</sub>"
        impact = f"{item['impactEs']}<br/><sub>{item['impactEn']}</sub>"
        rows.append(
            f"| [`{md_escape(name)}`]({repo_url(username, name)}) | {md_escape(focus)} | {md_escape(impact)} | {md_escape(signal)} |"
        )
    return "\n".join(rows)


def render_recent_repos(config: dict[str, Any], repos: list[dict[str, Any]]) -> str:
    """Muestra repos activos ordenados por último push."""
    username = config["profile"]["username"]
    limit = int(config.get("activity", {}).get("recentRepoLimit", 7))
    active = [repo for repo in repos if not repo.get("archived") and repo["name"] != username]
    active.sort(key=repo_freshness_key, reverse=True)
    rows = ["| Repo | Stack | Último commit/push | Descripción |", "|---|---|---:|---|"]
    for repo in active[:limit]:
        description = repo.get("description") or "Proyecto público en evolución."
        rows.append(
            f"| [`{md_escape(repo['name'])}`]({repo.get('html_url') or repo_url(username, repo['name'])}) | "
            f"{md_escape(repo.get('language') or 'multi-stack')} | {format_date(repo.get('pushed_at'))} | {md_escape(description)} |"
        )
    return "\n".join(rows)


def render_events(events: list[dict[str, Any]], limit: int) -> str:
    """Renderiza actividad pública reciente o un fallback honesto."""
    if not events:
        return "- Actividad pública no disponible en este momento; el generador seguirá intentando en la próxima ejecución."
    grouped: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, str], int] = {}
    for event in events[: limit * 4]:
        date = format_date(event.get("created_at"))
        summary = summarize_event(event)
        key = (date, summary)
        if key in index_by_key:
            grouped[index_by_key[key]]["count"] += 1
            continue
        index_by_key[key] = len(grouped)
        grouped.append({"date": date, "summary": summary, "count": 1})
        if len(grouped) >= limit:
            break

    lines = []
    for item in grouped[:limit]:
        suffix = f" · {item['count']} eventos" if item["count"] > 1 else ""
        lines.append(f"- {item['date']}: {item['summary']}{suffix}")
    return "\n".join(lines)


def render_contributions(contrib: dict[str, Any]) -> str:
    """Renderiza métricas de contribución con fallback si GraphQL no está disponible."""
    if not contrib.get("available"):
        return f"Contribuciones privadas/públicas: no disponibles en esta ejecución (`{md_escape(contrib.get('reason', 'sin detalle'))}`)."
    return f"{contrib['total']} contribuciones registradas por GitHub en {contrib['year']}."


def publication_items_from_linkedin(linkedin: dict[str, Any]) -> list[str]:
    """Obtiene textos de publicaciones/actividad profesional sin mezclar experiencia laboral."""
    sections = linkedin.get("sections", {}) or {}
    items: list[str] = []
    for source in (
        linkedin.get("publication_insights"),
        linkedin.get("publicationInsights"),
        linkedin.get("posts"),
        linkedin.get("activity"),
        sections.get("publications"),
    ):
        for item in normalize_linkedin_text_list(source, limit=10):
            if item not in items:
                items.append(item)
    return items[:10]


def analyze_publication_items(items: list[str]) -> list[str]:
    """Sintetiza temas recurrentes a partir de publicaciones o insights ya extraídos."""
    cleaned = clean_linkedin_lines(items)
    if not cleaned:
        return []

    topic_hits: Counter[str] = Counter()
    corpus = " ".join(cleaned).lower()
    for topic, keywords in PUBLICATION_TOPIC_KEYWORDS.items():
        topic_hits[topic] = sum(corpus.count(keyword.lower()) for keyword in keywords)

    top_topics = [topic for topic, count in topic_hits.most_common(4) if count > 0]
    insights: list[str] = []
    if top_topics:
        topic_text = top_topics[0] if len(top_topics) == 1 else ", ".join(top_topics[:-1]) + f" y {top_topics[-1]}"
        insights.append(
            f"Mis publicaciones giran principalmente alrededor de {topic_text}."
        )

    # Conserva las señales editoriales explícitas cuando el snapshot trae frases ya resumidas.
    for item in cleaned:
        if len(item) < 45:
            continue
        if item not in insights:
            insights.append(item)
        if len(insights) >= 4:
            break
    return insights[:4]


def looks_like_publication_signal(text: str) -> bool:
    """Filtra líneas de actividad que parecen contenido técnico y no navegación."""
    if len(text) < 55 or is_linkedin_noise(text):
        return False
    lower = text.lower()
    if any(marker in lower for marker in ("reposted", "liked by", "comentó", "reaccionó", "notificaciones")):
        return False
    return any(keyword.lower() in lower for keywords in PUBLICATION_TOPIC_KEYWORDS.values() for keyword in keywords)


def render_publication_analysis(linkedin: dict[str, Any]) -> str:
    """Renderiza aprendizajes de publicaciones solo cuando hay material real para analizar."""
    insights = analyze_publication_items(publication_items_from_linkedin(linkedin))
    if not insights:
        return ""
    lines = [
        "### Ideas que suelo compartir / What I usually share",
        "",
        "Además del código, uso mis publicaciones para ordenar aprendizajes, compartir avances y dejar señales técnicas útiles para otros devs.",
        "",
        "<sub>Beyond code, I use my posts to organize learnings, share progress and leave useful technical signals for other developers.</sub>",
        "",
    ]
    lines.extend(f"- {md_escape(insight)}" for insight in insights)
    return "\n".join(lines)


def render_linkedin_snapshot(linkedin: dict[str, Any]) -> str:
    """Renderiza perfil profesional y enriquece con LinkedIn cuando hay datos reales."""
    labels = {
        "projects": "Proyectos profesionales / Professional projects",
        "courses": "Formación continua / Continuous learning",
        "certifications": "Certificaciones / Certifications",
        "education": "Formación académica / Education",
    }
    lines = [
        "## 🧑‍💻 Perfil profesional / Professional profile",
        "",
        "Trabajo en la intersección entre sistemas empresariales, backends modernos y tooling con IA. "
        "Mi foco es convertir problemas técnicos complejos en herramientas que otros desarrolladores puedan usar, revisar y mantener.",
        "",
        "<sub>I work at the intersection of enterprise systems, modern backends and AI-powered tooling. "
        "My focus is turning complex technical problems into tools other developers can use, review and maintain.</sub>",
    ]
    if not linkedin.get("available"):
        return "\n".join(lines)

    if linkedin.get("headline"):
        lines.extend(["", f"**Foco actual / Current focus:** {md_escape(linkedin['headline'])}"])
    if linkedin.get("summary"):
        lines.extend(["", f"**Perfil / Profile:** {md_escape(linkedin['summary'])}"])

    sections = linkedin.get("sections", {})
    for key in ("education", "courses", "certifications", "projects"):
        values = sections.get(key) or []
        if not values:
            continue
        lines.extend(["", f"### {labels[key]}"])
        lines.extend(f"- {md_escape(value)}" for value in values)

    publication_analysis = render_publication_analysis(linkedin)
    if publication_analysis:
        lines.extend(["", publication_analysis])
    return "\n".join(lines)


def render_readme(config: dict[str, Any], data: dict[str, Any]) -> str:
    """Construye el README completo en Markdown."""
    profile = config["profile"]
    username = profile["username"]
    gravatar = data["gravatar"]
    github_user = data["github_user"]
    repos = data["repos"]
    languages = data["languages"]
    repos_by_name = repo_map(repos)
    generated_date = local_timestamp(profile)

    avatar_url = gravatar.get("avatar_url") or github_user.get("avatar_url") or "https://avatars.githubusercontent.com/u/33362684?v=4"
    avatar_url = with_query_params(avatar_url, s="260")
    public_repos = github_user.get("public_repos", len(repos))
    followers = int(github_user.get("followers", 0) or 0)
    followers_min = int(config.get("activity", {}).get("followersMinDisplay", 100))
    lang_list = top_languages(repos, languages)
    lang_text = ", ".join(lang for lang, _ in lang_list) if lang_list else "Java, Python, PowerShell, C"

    stack_badges = "\n".join(badge("stack", item, "1f2937") for item in config["stackBadges"])
    signal_badges_items = [
        badge("repos", str(public_repos), "2563eb"),
        badge("location", profile["location"], "059669"),
        badge("updated", generated_date, "334155"),
    ]
    # La audiencia pública no necesita ver una métrica social pequeña; se muestra solo cuando ya aporta señal.
    if followers >= followers_min:
        signal_badges_items.insert(1, badge("followers", str(followers), "7c3aed"))
    signal_badges = "\n".join(signal_badges_items)

    focus_lines = []
    for area in config["focusAreas"]:
        # Evita duplicar el título cuando español e inglés son iguales.
        focus_title = area["labelEs"] if area["labelEs"] == area["labelEn"] else f"{area['labelEs']} / {area['labelEn']}"
        focus_lines.append(
            f"- **{focus_title}** — {area['detailsEs']}  \n"
            f"  <sub>{area['detailsEn']}</sub>"
        )

    links = profile["links"]
    contact_links = " · ".join(
        [
            f"[LinkedIn]({links['linkedin']})",
            f"[ORCID]({links['orcid']})",
            f"[GitHub]({links['github']})",
        ]
    )

    featured = render_featured_table(config, repos_by_name)
    recent_repos = render_recent_repos(config, repos)
    events = render_events(data["events"], int(config.get("activity", {}).get("recentEventLimit", 6)))
    contributions = render_contributions(data["contributions"])
    linkedin = render_linkedin_snapshot(data["linkedin"])
    pinned = "\n".join(f"- `{repo}`" for repo in config["pinnedRecommendation"])
    ascii_card = """
<pre>
┌─ h0w4r.dev ────────────────────────────────────────────────┐
│ legacy systems -> modern tooling -> verifiable automation  │
│ IBM i/AS400 · Spring Boot · MCP/Codex · security-minded    │
└────────────────────────────────────────────────────────────┘
</pre>
""".strip()

    markdown = f"""
<!-- Perfil generado por scripts/build_profile.py. -->

<a href="{links['gravatar']}"><img align="right" width="130" src="{avatar_url}" alt="Chris Kirsch" /></a>

### 👋 Chris Kirsch · h0w4r

**{profile['headlineEs']}**  
<sub>**{profile['headlineEn']}**</sub>

{signal_badges}

Soy Chris, software engineer en Lima. Construyo herramientas open source alrededor de IBM i/AS400, backends Java/Spring Boot y automatización con IA para desarrollo real.<br/>
<sub>I am Chris, a software engineer based in Lima. I build open-source tools around IBM i/AS400, Java/Spring Boot backends and AI-assisted automation for real development workflows.</sub>

Si llegaste por IBM i/AS400, tooling para IA, backends o seguridad defensiva: este es mi laboratorio público. Me gusta construir piezas que no solo funcionen en una demo, sino que se puedan instalar, auditar, documentar y mantener sin invocar espíritus del mainframe a medianoche.<br/>
<sub>If you are here for IBM i/AS400, AI developer tooling, backends or defensive security: this is my public lab. I like building things that do not just work in a demo, but can be installed, audited, documented and maintained without summoning mainframe spirits at midnight.</sub>

<br clear="right" />

{ascii_card}

---

## 🧭 Qué estoy construyendo / What I build

Me interesan los proyectos donde el valor está en conectar mundos que normalmente viven separados: sistemas empresariales clásicos, herramientas modernas para desarrolladores, automatización confiable y validación técnica con evidencia.
<br/><sub>I care about projects that connect worlds that usually live apart: classic enterprise systems, modern developer tooling, reliable automation and evidence-driven technical validation.</sub>

{chr(10).join(focus_lines)}

{linkedin}

## 🧰 Stack vivo / Live stack

Estas tecnologías aparecen en mis repos, mis experimentos y mi trabajo diario; no son stickers pegados al README porque sí.
<br/><sub>These technologies show up across my repos, experiments and day-to-day work; they are not random stickers pasted into the README for decoration.</sub>

{stack_badges}

**Lenguajes detectados en repos públicos / Languages detected from public repos:** {md_escape(lang_text)}.

---

## 🧪 Cómo construyo / How I build

- **Evidencia antes que promesas:** prefiero pruebas, logs, validaciones y documentación ejecutable antes que frases bonitas que se caen al primer deploy.
- **Automatización útil:** si una tarea se repite, la convierto en script, workflow o herramienta; si no aporta, no la inflo con ceremonia.
- **Contexto real del sistema:** me interesa entender fuentes, datos, jobs, APIs y límites operativos antes de tocar una arquitectura.
- **Open source con intención:** publico herramientas que resuelven fricción concreta y que otros devs puedan leer, ejecutar y adaptar.
- **Seguridad práctica:** pienso en permisos, secretos, datos sensibles y superficies de ataque desde el diseño, no como checklist de último minuto.

<sub>Evidence over promises, useful automation, real system context, intentional open source and practical security from the design stage.</sub>

---

## 🚀 Ecosistema vivo / Live ecosystem

Lo que más me representa ahora mismo: herramientas open source, integración con IBM i/AS400, automatización para desarrolladores y proyectos donde la documentación importa tanto como el código.
<br/><sub>The work that represents me best right now: open-source tools, IBM i/AS400 integration, developer automation and projects where documentation matters as much as code.</sub>

{featured}

---

## 📡 Actividad reciente / Recent activity

**Resumen del año / Year snapshot:** {contributions}

### 🔥 Repositorios activos / Active repositories

Mis repos más activos aparecen primero para que puedas ver rápido qué estoy empujando ahora.<br/>
<sub>My most active repositories appear first so you can quickly see what I am pushing forward right now.</sub>

{recent_repos}

### 🛰️ Eventos públicos recientes / Recent public events

{events}

---

## 🤝 Cómo puedo aportar / How I can help

Si estás explorando colaboración, contratación o simplemente revisando mi trabajo, estas son las zonas donde puedo aportar más rápido:

- **Modernización IBM i/AS400:** entender sistemas existentes, documentar flujos reales, conectar RDi/Eclipse con herramientas modernas y reducir dependencia de conocimiento tribal.
- **Tooling para desarrolladores:** MCPs, plugins, scripts y automatizaciones que eliminan fricción repetitiva en lugar de producir otra pantalla bonita que nadie mantiene.
- **Backends y APIs:** Java/Spring Boot, integración REST, servicios internos y piezas que priorizan mantenibilidad, observabilidad y validación reproducible.
- **Seguridad y datos:** análisis de vulnerabilidades, detección de información sensible, priorización CVE y automatización defensiva con foco práctico.
- **Forma de trabajar:** commits pequeños, documentación clara, pruebas que demuestran algo y respeto por el contexto real del sistema.

<sub>If you are exploring collaboration, hiring or just reviewing my work, I can contribute fastest in IBM i/AS400 modernization, developer tooling, Java/Spring backends, defensive security automation and evidence-driven engineering.</sub>

## 📬 Contacto / Contact

{contact_links}

<details>
<summary>Repos pineados recomendados / Recommended pinned repositories</summary>

{pinned}

</details>

---

<sub>Perfil actualizado al {generated_date} · Lima, Perú.</sub>
""".strip() + "\n"
    return textwrap.dedent(markdown)


def collect_data(config: dict[str, Any]) -> dict[str, Any]:
    """Recolecta todas las fuentes vivas necesarias para generar el perfil."""
    token = get_token()
    username = config["profile"]["username"]
    activity_config = config.get("activity", {})

    github_user = github_get(f"/users/{username}", token)
    repos = github_get_paginated(f"/users/{username}/repos?sort=updated&type=owner", token)
    repos.sort(key=lambda repo: repo.get("pushed_at") or repo.get("updated_at") or "", reverse=True)
    languages = fetch_languages(username, repos, token, int(activity_config.get("languageRepoLimit", 20)))

    try:
        events = github_get(f"/users/{username}/events/public?per_page=30", token) or []
        # Evita que los commits automáticos del propio perfil ensucien la actividad
        # pública y generen cambios circulares en el README.
        profile_repo = f"{username}/{username}"
        events = [event for event in events if event.get("repo", {}).get("name") != profile_repo]
    except FetchError:
        events = []

    return {
        "github_user": github_user,
        "repos": repos,
        "languages": languages,
        "events": events,
        "contributions": fetch_contributions(username, token),
        "gravatar": fetch_gravatar(config["profile"]["gravatarSlug"]),
        "linkedin": fetch_linkedin(config),
    }


def validate_content(content: str) -> list[str]:
    """Valida requisitos de contenido y evita regresiones obvias."""
    errors: list[str] = []
    for banned in BANNED_STRINGS:
        if banned in content:
            errors.append(f"Contenido prohibido detectado: {banned}")
    for noisy in LINKEDIN_README_NOISE_PATTERNS:
        if noisy in content:
            errors.append(f"Ruido de LinkedIn detectado en README: {noisy.strip()}")
    for required in REQUIRED_STRINGS:
        if required not in content:
            errors.append(f"Contenido requerido ausente: {required}")
    if "@" in content and "github-actions[bot]" not in content:
        errors.append("El README parece contener un correo plano; se evita para reducir scraping.")
    return errors


def write_readme(content: str) -> None:
    """Escribe README.md en UTF-8 con salto final."""
    README_PATH.write_text(content, encoding="utf-8", newline="\n")


def check_readme(content: str) -> int:
    """Comprueba que README.md está sincronizado con la salida generada."""
    current = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else ""
    errors = validate_content(content)
    current_errors = validate_content(current)
    errors.extend(f"README actual: {error}" for error in current_errors)
    if current != content:
        diff = difflib.unified_diff(
            current.splitlines(),
            content.splitlines(),
            fromfile="README.md actual",
            tofile="README.md generado",
            lineterm="",
        )
        print("README.md no está sincronizado con scripts/build_profile.py:", file=sys.stderr)
        print("\n".join(list(diff)[:220]), file=sys.stderr)
        errors.append("README.md desactualizado")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("README.md está sincronizado y validado.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI."""
    parser = argparse.ArgumentParser(description="Genera el README vivo del perfil h0w4r.")
    parser.add_argument("--write", action="store_true", help="Escribe README.md con el contenido generado.")
    parser.add_argument("--check", action="store_true", help="Valida que README.md esté sincronizado.")
    parser.add_argument("--linkedin-diagnostics", action="store_true", help="Diagnostica la fuente LinkedIn sin mostrar secretos.")
    parser.add_argument(
        "--require-linkedin-when-configured",
        action="store_true",
        help="Falla si LINKEDIN_COOKIE/LINKEDIN_PROFILE_JSON existe pero no entrega datos útiles.",
    )
    args = parser.parse_args(argv)

    config = load_config()

    if args.linkedin_diagnostics:
        return print_linkedin_diagnostics(config, require_when_configured=args.require_linkedin_when_configured)

    if not args.write and not args.check:
        parser.error("usa --write, --check o --linkedin-diagnostics")

    content = render_readme(config, collect_data(config))
    validation_errors = validate_content(content)
    if validation_errors:
        for error in validation_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.write:
        write_readme(content)
        print(f"README.md generado en {README_PATH}")
    if args.check:
        return check_readme(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
