# Sincronización diaria con LinkedIn

El README se regenera a diario desde GitHub Actions. GitHub y Gravatar son públicos/API-friendly, pero LinkedIn suele responder `HTTP 999` o authwall cuando el runner intenta leer el perfil público sin sesión.

Por eso el generador soporta estas fuentes, en este orden:

1. `LINKEDIN_PROFILE_JSON`: snapshot estructurado opcional para pruebas o emergencia.
2. `LINKEDIN_COOKIE`: cookie de sesión guardada como GitHub Actions secret para leer `https://www.linkedin.com/in/cehp94/` durante el workflow diario.
3. Fallback público directo/proxy: se intenta, pero LinkedIn normalmente lo bloquea.

## Opción recomendada: `LINKEDIN_COOKIE`

No pegues la cookie en commits ni en chats. Guárdala como secret del repo:

```powershell
gh secret set LINKEDIN_COOKIE --repo h0w4r/h0w4r
```

Cuando el comando pida el valor, pega solo el valor de cookie que salga de tu navegador para `linkedin.com`. Debe incluir, como mínimo, una sesión válida; normalmente el header completo de cookie contiene claves como `li_at` y `JSESSIONID`.

Después ejecuta manualmente el workflow:

```powershell
gh workflow run update-profile.yml --repo h0w4r/h0w4r
```

Y valida el último run:

```powershell
gh run list --repo h0w4r/h0w4r --workflow update-profile.yml --limit 3
```

El workflow ejecuta primero:

```bash
python scripts/build_profile.py --linkedin-diagnostics --require-linkedin-when-configured
```

Eso no imprime la cookie. Solo muestra un JSON seguro con:

- `available`
- `source`
- `secret_configured`
- `has_headline`
- `has_summary`
- conteo de secciones extraídas

Si `LINKEDIN_COOKIE` o `LINKEDIN_PROFILE_JSON` existe pero no permite extraer datos profesionales, el workflow falla para que el problema sea visible en Actions en vez de publicar un README incompleto en silencio.

Con `LINKEDIN_COOKIE`, el generador intenta primero la API autenticada Voyager:

```text
https://www.linkedin.com/voyager/api/identity/profiles/cehp94/profileView
```

Para eso deriva el header `Csrf-Token` desde `JSESSIONID`. Si tu cookie no incluye `JSESSIONID` y `li_at`, o si LinkedIn considera expirada la sesión, el diagnóstico fallará antes de regenerar el README.

También puedes diagnosticarlo localmente:

```powershell
$env:LINKEDIN_COOKIE = '<pegar cookie solo en tu terminal local>'
python scripts/build_profile.py --linkedin-diagnostics --require-linkedin-when-configured
Remove-Item Env:\LINKEDIN_COOKIE
```

## Opción fallback: `LINKEDIN_PROFILE_JSON`

Si LinkedIn cambia el HTML o la cookie no permite extraer secciones limpias, se puede usar un JSON estructurado como secret:

```powershell
gh secret set LINKEDIN_PROFILE_JSON --repo h0w4r/h0w4r < .github/linkedin.profile.example.json
```

El generador entiende estas claves:

- `headline`
- `summary` o `about`
- `experience`
- `projects`
- `courses`
- `publications`
- `certifications`
- `education`

Cada sección puede ser lista de strings o lista de objetos con campos como `title`, `company`, `organization`, `period`, `description`.

## Comportamiento público

Si LinkedIn no entrega datos, el README no muestra una sección rota ni mensajes feos. Solo queda un comentario HTML interno indicando que falta `LINKEDIN_COOKIE` o que LinkedIn bloqueó el acceso. Con datos válidos, aparece la sección:

```text
🔗 Señales profesionales de LinkedIn / LinkedIn professional signals
```

Así el perfil público se ve profesional incluso cuando LinkedIn se pone en modo portero de discoteca.
