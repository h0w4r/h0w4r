#!/usr/bin/env python3
"""Genera el README público del perfil GitHub de h0w4r.

El script está diseñado para ejecutarse tanto localmente como desde GitHub
Actions. No usa dependencias externas: consulta GitHub y Gravatar con urllib,
arma un Markdown determinístico y valida que no queden rastros del perfil viejo.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
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
    "Ecosistema vivo",
    "Live ecosystem",
    "MCP-IBMiDocs",
    "Plugin-Codex-para-RDi",
    "https://gravatar.com/ckirsch94",
    "Repos pineados recomendados",
]


class FetchError(RuntimeError):
    """Error recuperable al consultar una API externa."""


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Carga la configuración editable del perfil."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def get_token() -> str | None:
    """Obtiene un token de GitHub si existe en el entorno."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


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


def render_featured_table(config: dict[str, Any], repos_by_name: dict[str, dict[str, Any]]) -> str:
    """Renderiza los proyectos destacados con metadatos vivos de GitHub."""
    username = config["profile"]["username"]
    rows = [
        "| Proyecto / Project | Foco / Focus | Impacto / Impact | Señal viva / Live signal |",
        "|---|---|---|---|",
    ]
    for item in config["featuredRepositories"]:
        name = item["name"]
        repo = repos_by_name.get(name, {})
        lang = repo.get("language") or "multi-stack"
        stars = repo.get("stargazers_count", 0)
        pushed = format_date(repo.get("pushed_at"))
        signal = f"{lang} · ⭐ {stars} · push {pushed}"
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
    active.sort(key=lambda repo: repo.get("pushed_at") or repo.get("updated_at") or "", reverse=True)
    rows = ["| Repo | Stack | Último push | Descripción |", "|---|---|---:|---|"]
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
    lines = []
    for event in events[:limit]:
        lines.append(f"- {format_date(event.get('created_at'))}: {summarize_event(event)}")
    return "\n".join(lines)


def render_contributions(contrib: dict[str, Any]) -> str:
    """Renderiza métricas de contribución con fallback si GraphQL no está disponible."""
    if not contrib.get("available"):
        return f"Contribuciones privadas/públicas: no disponibles en esta ejecución (`{md_escape(contrib.get('reason', 'sin detalle'))}`)."
    return f"{contrib['total']} contribuciones registradas por GitHub en {contrib['year']}."


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
    gravatar_role = gravatar.get("job_title") or "Software Engineer"
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
            f"[Gravatar]({links['gravatar']})",
            f"[ORCID]({links['orcid']})",
            f"[GitHub]({links['github']})",
        ]
    )

    featured = render_featured_table(config, repos_by_name)
    recent_repos = render_recent_repos(config, repos)
    events = render_events(data["events"], int(config.get("activity", {}).get("recentEventLimit", 6)))
    contributions = render_contributions(data["contributions"])
    pinned = "\n".join(f"- `{repo}`" for repo in config["pinnedRecommendation"])

    markdown = f"""
<!-- Perfil generado automáticamente por scripts/build_profile.py. -->
<!-- Fuente viva: APIs públicas de GitHub y perfil visual externo. Edita .github/profile.config.json para cambios manuales. -->

<a href="{links['gravatar']}"><img align="right" width="130" src="{avatar_url}" alt="Chris Kirsch" /></a>

### Chris Kirsch · h0w4r

**{profile['headlineEs']}**  
<sub>**{profile['headlineEn']}**</sub>

{signal_badges}

{profile['bioEs']}<br/>
<sub>{profile['bioEn']}</sub>

Me gusta crear software que sea útil, verificable y fácil de mantener; especialmente cuando conecta mundos que normalmente no conversan bien entre sí: IBM i/AS400, backends modernos y agentes de IA.<br/>
<sub>I like building useful, verifiable and maintainable software, especially when it connects worlds that usually do not talk nicely to each other: IBM i/AS400, modern backends and AI agents.</sub>

<br clear="right" />

---

## Qué estoy construyendo / What I build

{chr(10).join(focus_lines)}

## Stack vivo / Live stack

{stack_badges}

**Lenguajes detectados en repos públicos / Languages detected from public repos:** {md_escape(lang_text)}.

---

## Ecosistema vivo / Live ecosystem

{featured}

---

## Actividad reciente / Recent activity

**Resumen del año / Year snapshot:** {contributions}

### Repositorios activos / Active repositories

{recent_repos}

### Eventos públicos recientes / Recent public events

{events}

---

## Cómo puedo aportar / How I can help

- **Dónde suelo aportar más valor:** modernización IBM i/AS400, tooling para desarrolladores, automatización con IA, backends Java/Spring y validación técnica reproducible.
- **Where I usually add the most value:** IBM i/AS400 modernization, developer tooling, AI-assisted automation, Java/Spring backends and reproducible technical validation.
- **Cómo trabajo:** me gustan los commits pequeños, la documentación clara, las pruebas que demuestran algo y las herramientas que eliminan fricción real.
- **How I work:** I like small commits, clear documentation, tests that actually prove something and tools that remove real friction.

## Contacto / Contact

{contact_links}

<details>
<summary>Repos pineados recomendados / Recommended pinned repositories</summary>

{pinned}

</details>

---

<sub>Última actualización automática: {generated_date}. Rol público: `{md_escape(gravatar_role)}` en `{md_escape(gravatar.get('company') or profile['company'])}`.</sub>
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
    }


def validate_content(content: str) -> list[str]:
    """Valida requisitos de contenido y evita regresiones obvias."""
    errors: list[str] = []
    for banned in BANNED_STRINGS:
        if banned in content:
            errors.append(f"Contenido prohibido detectado: {banned}")
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
    args = parser.parse_args(argv)

    if not args.write and not args.check:
        parser.error("usa --write, --check o ambos")

    config = load_config()
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
