# Sincronización diaria con LinkedIn

Este perfil se actualiza desde tres fuentes: GitHub, Gravatar y LinkedIn. GitHub y Gravatar tienen APIs estables; LinkedIn, en cambio, suele bloquear runners públicos con `429`, `999`, redirects o authwall aunque una cookie sea válida. Por eso la fuente diaria real para LinkedIn queda anclada a un runner propio con sesión local persistente.

## Decisión técnica vigente

La ruta principal queda así:

1. **Workflow diario self-hosted**: `.github/workflows/update-profile-self-hosted.yml`.
2. **Runner Windows propio** con etiqueta `linkedin-sync`.
3. **Sesión local persistente** creada con `scripts/bootstrap_linkedin_session.ps1` en `%LOCALAPPDATA%\h0w4r-linkedin-sync\browser-profile`.
4. **Snapshot vivo de LinkedIn** con `scripts/fetch_linkedin_profile.mjs`.
5. **Generación del README** con `scripts/build_profile.py`, usando `LINKEDIN_SNAPSHOT_ONLY=1` para no publicar fallback viejo si LinkedIn falla.

El workflow antiguo `.github/workflows/update-profile.yml` queda como ejecución manual/fallback. Ya no es el cron principal, para evitar que un runner público bloquee LinkedIn y termine publicando contenido menos fresco.

## Investigación: API oficial y APIs intermedias

| Opción | Estado | Decisión |
| --- | --- | --- |
| API oficial de LinkedIn | No viable para este objetivo completo | La API abierta permite OIDC básico (`profile`, `email`) y escritura social (`w_member_social`), pero leer posts personales requiere `r_member_social`, documentado como restringido/cerrado para usuarios aprobados. El perfil completo también depende de permisos cerrados/partner access. |
| OutX | Viable como capa intermedia, pero no como core ahora | Ofrece endpoints para perfiles y posts, pero requiere API key y extensión de Chrome activa en una sesión real dentro de las últimas 48h. Eso igual nos deja dependiendo de una sesión de navegador, solo que metiendo un tercero en medio. |
| Unipile | Viable comercialmente, no ideal para este README | Expone perfil, posts, followers, acciones y conexión de cuentas, pero está orientado a apps multiusuario/CRM/ATS/outreach. Para un README personal diario sería más infraestructura de la necesaria. |
| Apify / actores de scraping | Viable como job externo, variable por actor | Puede ejecutar scrapers vía API, pero la calidad depende del actor elegido, coste, límites y mantenimiento ante cambios de LinkedIn. Útil como plan B puntual, flojo como fuente principal elegante. |
| Sesión local persistente en runner propio | Ruta elegida | No depende de un tercero, evita el fingerprint del runner público, usa la red/host real de Chris y permite fallar explícitamente si la sesión expira. Menos glamour SaaS, más control. Como debe ser. |

Referencias revisadas:

- [LinkedIn - Getting Access to LinkedIn APIs](https://learn.microsoft.com/linkedin/shared/authentication/getting-access)
- [LinkedIn - Posts API / permisos](https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-05#permissions)
- [LinkedIn - Marketing API FAQ / permisos cerrados](https://learn.microsoft.com/linkedin/marketing/lms-faq?view=li-lms-2026-05#permissions)
- [OutX API - LinkedIn Data](https://www.outx.ai/docs/api-reference/introduction)
- [Unipile Social Media API](https://www.unipile.com/social-media-api/)
- [Apify API](https://docs.apify.com/api)
- [GitHub - usar runners self-hosted en workflows](https://docs.github.com/en/actions/how-tos/managing-self-hosted-runners/using-self-hosted-runners-in-a-workflow)
- [GitHub - labels para runners self-hosted](https://docs.github.com/en/actions/how-tos/managing-self-hosted-runners/using-labels-with-self-hosted-runners)

## Instalación rápida desde este repo

Desde una consola autenticada con `gh`, se puede preparar el runner local con:

```powershell
.\scripts\install_github_runner.ps1 -StartNow
```

El instalador:

1. Descarga la última release oficial de `actions/runner` para Windows x64.
2. Registra el runner contra `h0w4r/h0w4r` usando un token efímero obtenido con `gh api`.
3. Asigna la etiqueta `linkedin-sync`.
4. Lo deja bajo `.local/actions-runner/`, ignorado por git.
5. Opcionalmente lo arranca en segundo plano con `-StartNow`.

Para dejarlo resistente a reinicios, conviene instalarlo como servicio con `svc.cmd` desde PowerShell administrador.

## Crear o renovar la sesión local de LinkedIn

En la misma máquina y con el mismo usuario que ejecutará el runner:

```powershell
.\scripts\bootstrap_linkedin_session.ps1
```

Qué hace:

1. Instala el cliente Playwright sin descargar navegador.
2. Abre Chrome con un perfil dedicado en `%LOCALAPPDATA%\h0w4r-linkedin-sync\browser-profile`.
3. Te deja iniciar sesión manualmente en LinkedIn.
4. Extrae `.linkedin-profile.json`.
5. Ejecuta el diagnóstico con `LINKEDIN_SNAPSHOT_ONLY=1`.

Si el runner se instala como servicio con otro usuario, repite este bootstrap bajo ese mismo usuario. Si no, Windows guardará la sesión en un barrio distinto y el workflow entrará a LinkedIn como turista perdido en Miraflores.

## Configuración del runner self-hosted

En GitHub:

1. Abrir `h0w4r/h0w4r` → **Settings** → **Actions** → **Runners**.
2. Crear un runner nuevo para **Windows x64**.
3. Durante `config.cmd`, agregar la etiqueta:

```powershell
linkedin-sync
```

El workflow usa:

```yaml
runs-on: [self-hosted, windows, linkedin-sync]
```

Recomendación para servicio:

```powershell
.\svc.cmd install
.\svc.cmd start
```

## Secrets

Para el workflow self-hosted diario **ya no se requiere `LINKEDIN_COOKIE`**. La sesión vive en el perfil local persistente.

`LINKEDIN_COOKIE` puede conservarse solo como fallback legacy para pruebas manuales o para `.github/workflows/update-profile.yml`, pero no es la ruta principal.

## Auditoría rápida

Para saber si el host quedó listo sin disparar un run a ciegas:

```powershell
.\scripts\test_linkedin_sync_ready.ps1
```

Cuando la sesión local ya exista, puedes validar LinkedIn vivo:

```powershell
.\scripts\test_linkedin_sync_ready.ps1 -LiveProbe
```

Y si todo está verde, disparar el workflow manual desde el mismo doctor:

```powershell
.\scripts\test_linkedin_sync_ready.ps1 -LiveProbe -DispatchWorkflow
```
## Prueba local antes del primer run

Ruta recomendada:

```powershell
.\scripts\bootstrap_linkedin_session.ps1
.\scripts\sync_linkedin_self_hosted.ps1 -WriteReadme
```

Fallback legacy con cookie, solo si hiciera falta:

```powershell
$env:LINKEDIN_COOKIE = '<pegar header Cookie de LinkedIn>'
.\scripts\sync_linkedin_self_hosted.ps1 -ForceCookie
Remove-Item Env:\LINKEDIN_COOKIE
```

## Operación diaria

El workflow self-hosted corre todos los días a las **06:22 de Lima** (`11:22 UTC`). Pasos principales:

1. Valida que exista el perfil local de LinkedIn.
2. Instala solo el cliente Playwright y usa Chrome local del runner.
3. Extrae `.linkedin-profile.json` desde la sesión persistente.
4. Ejecuta diagnóstico sin fallback (`LINKEDIN_SNAPSHOT_ONLY=1`).
5. Genera y valida `README.md`.
6. Hace commit solo si cambió el README.

## Fallback manual

Si el runner local está apagado o LinkedIn cambia el DOM, se puede ejecutar manualmente:

```powershell
gh workflow run update-profile.yml --repo h0w4r/h0w4r
```

Ese workflow puede usar `LINKEDIN_PROFILE_JSON` como snapshot curado, pero no debe considerarse la fuente diaria principal.
