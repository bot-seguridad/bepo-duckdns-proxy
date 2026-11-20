# Órbita Media

Sitio de descargas de video y audio, con interfaz adaptable a celulares y backend Flask + yt-dlp + FFmpeg. No utiliza servicios privados de Snaptube ni credenciales de plataformas.

## Deploy en Render

- Type: Web Service
- Runtime: Docker
- Branch: orbita-media-web
- Dockerfile: ./Dockerfile
- Health: /api/health
- Plan: Free para pruebas (puede suspenderse por inactividad)

Admite enlaces públicos de YouTube, TikTok, Instagram, Facebook, X, Vimeo, Dailymotion, SoundCloud, Reddit, Pinterest, Twitch y otras plataformas compatibles. Algunos proveedores pueden bloquear solicitudes por región, iniciar sesión o limitar datacenters. No elude DRM ni accede a contenido privado.

Límite de 220 MiB por archivo y máximo dos descargas simultáneas. Caducidad de archivos a los 20 minutos, con limpieza al recibir solicitudes. No hay almacenamiento persistente.
