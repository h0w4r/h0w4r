# Sincronización diaria con LinkedIn

Este perfil se actualiza desde tres fuentes: GitHub, Gravatar y LinkedIn. GitHub y Gravatar son estables para API; LinkedIn no lo es tanto: en runners públicos suele responder con `429`, `999`, redirects o authwall aunque la cookie sea válida. El resultado práctico: el runner público de GitHub sirve como fallback/manual, pero no como fuente diaria confiable para LinkedIn vivo.

## Decisión técnica

La ruta principal queda así:

1. **Workflow diario self-hosted**: `.github/workflows/update-profile-self-hosted.yml`.
2. **Runner Windows propio** con etiqueta `linkedin-sync`.
3. **Snapshot vivo de LinkedIn** con `scripts/fetch_linkedin_profile.mjs`.
4. **Generación del README** con `scripts/build_profile.py`, sin usar `LINKEDIN_PROFILE_JSON` como respaldo silencioso.

El workflow antiguo `.github/workflows/update-profile.yml` queda como ejecución manual/fallback. Ya no es el cron principal, para evitar que un runner público bloquee LinkedIn y termine publicando contenido menos fresco.

## Por qué no usar una API intermedia como ruta principal

Se revisaron tres familias de opciones:

| Opción | Resultado | Motivo |
| --- | --- | --- |
| API oficial de LinkedIn | No viable para este objetivo | La lectura de posts personales requiere `r_member_social`, que LinkedIn documenta como permiso restringido/cerrado para usuarios aprobados. El perfil completo también está detrás de permisos/partner access. |
| APIs intermedias tipo Proxycurl/Apify/Unipile | Técnicamente posibles, pero no ideales como core | En la práctica actúan como wrappers/scrapers, tienen coste, límites, variación de calidad y no garantizan la misma profundidad de datos del perfil autenticado propio. |
| Runner propio con sesión autorizada | Ruta elegida | Usa tu propia sesión, desde tu red/host, con control total del pipeline y sin depender del fingerprint de los runners públicos. |

Referencias útiles:

- [LinkedIn - Getting Access to LinkedIn APIs](https://learn.microsoft.com/linkedin/shared/authentication/getting-access)
- [LinkedIn - Posts API](https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-05#find-posts-by-authors)
- [LinkedIn - Marketing API FAQ](https://learn.microsoft.com/linkedin/marketing/lms-faq?view=li-lms-2026-05#permissions)
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

Esto desbloquea el workflow diario sin pegar tokens en archivos ni en commits. Para dejarlo resistente a reinicios, después conviene instalarlo como servicio con `svc.cmd` desde PowerShell administrador.

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

Eso evita que otro runner self-hosted agarre este job por accidente. Nada de magia negra; solo una etiqueta con buen gusto.

Recomendación: instalarlo como servicio para que el sync diario corra aunque no haya consola abierta:

```powershell
.\svc.cmd install
.\svc.cmd start
```

## Secret requerido

Mantener el secret existente:

```powershell
gh secret set LINKEDIN_COOKIE --repo h0w4r/h0w4r
```

Debe incluir al menos `li_at` y `JSESSIONID`. Si la sesión expira, el workflow fallará explícitamente en el diagnóstico de LinkedIn vivo, en lugar de publicar un README con datos viejos como si nada hubiera pasado.

## Prueba local antes del primer run

En la máquina donde vivirá el runner:

```powershell
# Opción A: variable temporal
$env:LINKEDIN_COOKIE = '<pegar header Cookie de LinkedIn>'
.\scripts\sync_linkedin_self_hosted.ps1
Remove-Item Env:\LINKEDIN_COOKIE

# Opción B: archivo local ignorado por git
Set-Content .linkedin-cookie.txt '<pegar header Cookie de LinkedIn>'
.\scripts\sync_linkedin_self_hosted.ps1
Remove-Item .linkedin-cookie.txt
```

Para regenerar `README.md` localmente durante una prueba:

```powershell
.\scripts\sync_linkedin_self_hosted.ps1 -WriteReadme
```

## Operación diaria

El workflow self-hosted corre todos los días a las **06:22 de Lima** (`11:22 UTC`). Pasos principales:

1. Valida que `LINKEDIN_COOKIE` exista.
2. Instala solo el cliente Playwright y usa Chrome local del runner.
3. Extrae `.linkedin-profile.json` con sesión autenticada.
4. Ejecuta diagnóstico sin fallback.
5. Genera y valida `README.md`.
6. Hace commit solo si cambió el README.

## Fallback manual

Si el runner local está apagado o LinkedIn cambia el DOM, se puede ejecutar manualmente:

```powershell
gh workflow run update-profile.yml --repo h0w4r/h0w4r
```

Ese workflow puede usar `LINKEDIN_PROFILE_JSON` como snapshot curado, pero no debe considerarse la fuente diaria principal.
