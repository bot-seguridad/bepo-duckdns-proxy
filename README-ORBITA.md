# Órbita Media · adaptación de Snaptube para Render

Analizamos la APK Snaptube 7.69.0 para identificar el enfoque de extracción: `ExtractorWrapper`, análisis del reproductor, interceptación de recursos WebView (`shouldInterceptRequest`) y conversión de audio.

El servidor usa una adaptación independiente de esa arquitectura con **Chromium/Playwright** para detectar peticiones multimedia, lectura directa de metadatos y HTML/JSON, streaming directo del origen y **FFmpeg exclusivamente para convertir o unir pistas**. Incluye análisis directo del reproductor YouTube e Innertube donde el propio sitio permite extraer URLs sin cifrado.

**No contiene yt-dlp ni emplea APIs externas de descarga.** Las bibliotecas originales del APK dependen de Android ARM y no pueden ejecutarse de manera idéntica en Render (Linux x86); el port no asegura soporte de todas las plataformas, tokens, DRM, firmas o archivos privados.

Límites: 220 MB/archivo, dos trabajos en paralelo, 6 solicitudes por IP en 10 minutos; enlaces temporales de 20 minutos. Render free puede demorar en despertar.
