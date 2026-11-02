# Costos estimados por consulta y límites de entrada

Cifras **estimadas** (precios públicos de lista a la fecha de escritura; verificar antes de
presupuestar). Los parámetros viven en `.env` / `app/config/settings.py`.

## Límites (corte en servidor; el front solo avisa antes)

| Límite | Valor | Variable | Dónde se aplica |
|---|---|---|---|
| Texto por consulta (escrito, dictado o transcrito) | 1000 caracteres | `MAX_CHARS_CONSULTA` | `/consulta`, antes de llamar a cualquier modelo |
| Duración de audio (dictado o archivo) | 60 s, **medida con ffmpeg** | `AUDIO_MAX_SEGUNDOS` | `/transcribir`, antes de llamar a OpenAI |
| Tamaño de audio | 10 MB | `AUDIO_MAX_BYTES` | nginx + `/transcribir` |
| Frecuencia de transcripción | 3/min por IP (ráfaga 2) | `transcribir_zone` en nginx | |
| Frecuencia de consulta | 5/min por IP (ráfaga 3) | `consulta_zone` en nginx | |

La duración se mide en el servidor porque el WebM del micrófono no trae duración en la
cabecera y un cliente puede mentir. El audio rechazado no se archiva.

## Costo por operación (USD, aproximado)

| Operación | Cálculo | Estimado |
|---|---|---|
| Transcripción, por minuto (OpenAI, defecto) | `gpt-4o-mini-transcribe` ≈ 0,003/min (`OPENAI_TRANSCRIBE_USD_POR_MIN`) | 0,003 |
| Transcripción, por minuto (Gemini, `TRANSCRIPTION_PROVIDER=gemini`) | ~32 tokens de audio/s ≈ 1900 tokens/min; ≈ 0,002/min (`GEMINI_TRANSCRIBE_USD_POR_MIN`), depende del modelo Flash configurado y del tier | 0,002 |
| Audio de 60 s (máximo) | 1 min × 0,003 | **0,003** |
| Audio de 15 s (dictado típico) | 0,25 min × 0,003 | 0,00075 |
| Consulta de texto (1000 car. máx.) | 2 llamadas Gemini Flash (intención + nombre), ~1,6k tokens de entrada, + 1 embedding OpenAI (~300 tokens, ≈0,000006) | ≈ 0,001 |
| **Consulta por dictado o audio, máximo** | transcripción 0,003 + consulta 0,001 | **≈ 0,004** |
| Veredicto (por solicitud) | 3 corridas Gemini Pro + reintento + anclaje Flash; depende del tamaño de la evidencia | ≈ 0,05 – 0,10 (a medir en producción) |

Techo por IP con los límites de nginx: 3 transcripciones/min × 0,003 ≈ **0,009 USD/min**.

Cada transcripción registra en el log del `api` su duración y su costo estimado
(`transcripcion <id>: N s, costo estimado US$X`); la respuesta de `/transcribir` incluye
`costo_estimado_usd`. Sumar esos logs da el gasto aproximado del mes.

## Pendiente

- Tope global diario de gasto (contador en Redis) — hoy solo hay límites por IP.
- Medir el costo real del veredicto con consultas reales y reemplazar el rango estimado.
