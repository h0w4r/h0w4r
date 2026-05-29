# Reglas del repo para el perfil GitHub de h0w4r

## Tono y audiencia

- El README debe sonar como si Chris Kirsch / h0w4r se estuviera presentando directamente a visitantes, recruiters, maintainers y otros devs.
- Escribir en primera persona, con voz profesional, humana, técnica y cercana.
- Evitar texto que parezca reporte interno, changelog, explicación del generador o respuesta del asistente al dueño del perfil.
- No usar frases visibles como: "Fuente sincronizada", "actualización automática diaria", "perfil vivo para reclutadores", "LinkedIn professional signals", "Headline" o similares.
- Mantener el perfil bilingüe ES/EN, pero priorizar naturalidad y marca personal sobre literalidad mecánica.
- Puede tener personalidad ligera y humor técnico sutil; no debe sonar seco, corporativo o robótico.


## Prueba de voz obligatoria

Antes de aceptar cualquier texto visible del README, aplicar esta pregunta: "¿Chris escribiría esto así a alguien que acaba de entrar a su perfil?". Si la respuesta es no, el texto debe reescribirse o moverse a documentación interna. El README no conversa con Chris ni explica la automatización; presenta a Chris ante el mundo.

## Contenido permitido en el README

- Presentación profesional general.
- Perfil profesional proveniente de LinkedIn, sin exponer historial laboral como línea de tiempo.
- Formación académica, cursos, certificaciones y proyectos profesionales relevantes.
- Información extraída o sintetizada desde publicaciones/actividad profesional cuando exista material real o un snapshot curado explícito.
- Ecosistema open source, repos activos, stack detectado, contribuciones, actividad pública de GitHub y enlaces públicos.

## Contenido que NO debe mostrarse

- Experiencia laboral detallada, empleadores, modalidad contractual, fechas de empleo o historial de cargos.
- Métricas sociales pequeñas como followers si no superan el umbral configurado.
- Ruido de LinkedIn: notificaciones, comentarios, imágenes, authwall, prompts de login, navegación o contenido de UI.
- Emails planos o datos que faciliten scraping.
- Texto visible que explique cómo se sincroniza el README; la automatización puede existir, pero no debe sentirse en la presentación pública.

## Regla de implementación

- Si se modifica `scripts/build_profile.py`, ejecutar como mínimo:
  - `python -m py_compile scripts/build_profile.py`
  - `python scripts/build_profile.py --write`
  - `python scripts/build_profile.py --check`
  - `git diff --check`
- Antes de commitear, revisar que `README.md` no contenga frases meta, experiencia laboral o ruido de LinkedIn.
