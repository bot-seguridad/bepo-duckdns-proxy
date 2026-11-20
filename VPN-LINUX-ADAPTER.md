# VPN Super Android ARM → adaptación para Linux y Render

## Qué encontramos en `vpn.zip`

La APK contiene **solo código Android ARMv7 y ARM64**, no binarios Linux x86:

| APK | Arquitectura | Dependencias |
|---|---|---|
| `libopenvpn.so` | AArch64 Android | Bionic, `libm`, `libdl`, `libc` |
| `libv2ray.so` | AArch64 Android | `liblog`, `libdl`, Bionic |
| `libtun2socks.so` | AArch64 Android | `/system/bin/linker64`, Bionic |
| `libvpn-super-android-v2ray.so` | AArch64 Android | `libandroid`, `liblog`, `libv2ray` |

**No se ha modificado, descompilado, ejecutado ni incrustado la APK en Render.** Sin el código fuente original no podemos recompilar sus bibliotecas privadas para `linux/amd64`. Se implementó un adaptador nativo Linux de las funciones de transporte compatibles.

El recurso `res/raw/china_clash_config.yaml` apunta a un proxy SOCKS5 local `127.0.0.1:1081`. **No es un servidor VPN público ni incluye credenciales de un servidor remoto**.

## Adaptación implementada

- `vpn_linux.py` configura un proxy Linux saliente cuando existe `ORBITA_OUTBOUND_PROXY`.
- `search_engine.py` usa ese transporte para las búsquedas de YouTube.
- `native_engine.py` usa el mismo transporte para extraer información, descargar MP4/MP3 y lanzar Chromium.
- `/api/health` expone `outbound_proxy: configured|unconfigured|invalid`, sin revelar secretos.
- Sin `ORBITA_OUTBOUND_PROXY`, se mantiene exactamente el modo de conexión original, sin prometer una IP VPN.

Un ejemplo de **formato de configuración**, no un servidor real:

```text
ORBITA_OUTBOUND_PROXY=socks5://usuario:contrasena@proxy-propio.example:1080
```

El proxy externo deberá ser tuyo o uno al que tengas acceso legítimo. No subas credenciales al repositorio: configurarlas como variables secretas de Render. Un proxy configurado no garantiza evitar límites o restricciones de la plataforma.

## Restricciones

- Los binarios Android originales no son ejecutables en el contenedor Linux de Render.
- OpenVPN como interfaz de red suele necesitar `/dev/net/tun` y privilegios `NET_ADMIN`, y Render no ofrece configuración de contenedores privilegiados mediante sus servicios web normales.
- El proxy SOCKS5 funciona a nivel de aplicación, sin un dispositivo TUN.
- FFmpeg al reproducir HLS/DASH a través de SOCKS5 se rechaza explícitamente en vez de hacer una solicitud directa que eluda el proxy.
- **No tenemos una configuración de los servidores remotos de VPN Super extraída de este APK**, de modo que no es posible activar automáticamente su red de salida ni garantizar una IP diferente a la de Render.

## Verificación

```shell
python -m py_compile app.py native_engine.py search_engine.py vpn_linux.py tests_smoke.py
python -m unittest tests_smoke -v
```

Esta compatibilidad es una **implementación independiente para Linux**, no una copia binaria funcional de la VPN propietaria.
