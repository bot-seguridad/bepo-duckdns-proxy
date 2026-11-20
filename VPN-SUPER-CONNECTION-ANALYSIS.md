# VPN Super Unlimited APK: auditoría de conexión para Órbita

Fecha: 20 de noviembre de 2026. Archivo proporcionado: \`vpn.zip\` con \`Vpn-Super-Unlimited-v2.34.1-MOD-OTR-(Getmodsapk.com) (1).apk\`.

## Evidencias comprobadas directamente del APK

Se inspeccionó el ZIP, los ocho archivos \`classes*.dex\`, \`res/raw/china_clash_config.yaml\`, y las tablas dinámicas ELF de las bibliotecas. No se ejecutó la APK modificada ni se contactó a los servidores privados.

### Obtención de servidores (en lugar de perfiles VPN hardcodeados)

Los literales de \`classes.dex\` y \`classes2.dex\` muestran el mecanismo de descubrimiento de servidores:

- \`https://prod-api.mobilejump.mobi/be-data/v7/server_list?platform=android&vip=true&region={region}&app={app}\`
- \`https://prod-export.mobilejump.mobi/v7/sl/{hash}\`
- \`https://raw.githubusercontent.com/SuperUnlimited/data-backup/v7/sl/{hash}\`

La presencia de \`{region}\`, \`{app}\` y \`{hash}\` indica que son **plantillas**, no una conexión estática utilizable. El campo \`vip=true\` aparece en el valor predeterminado; no prueba que un usuario tenga acceso premium.

También se identificaron clases y referencias del flujo, entre ellas \`ServerListFetchingConfig\`, \`ServerListResponse\`, \`ServerDto\`, \`VpnServer\`, \`VpnParams\` y \`ServerListDetails\`. La configuración puede cambiar mediante parámetros remotos; la auditoría estática no demuestra cuál servidor devuelve el servicio a una instancia de la aplicación.

### Protocolos y arranque

Los tipos de protocolo representados en las clases del APK incluyen:

- \`OpenVpnTcp\` y \`OpenVpnUdp\`
- \`V2ray\`
- \`Ikev2\`
- \`Super\` (implementación propietaria; protocolo de transporte exacto no demostrado)

La biblioteca \`libvpn-super-android-v2ray.so\` importa \`libv2ray.so\` y bibliotecas de Android (\`libandroid.so\`, \`liblog.so\`, \`libc.so\`). Sus símbolos JNI incluyen:

- \`Java_jni_V2RayPoint_setConfigureFileContent\`
- \`Java_jni_V2RayPoint_runLoop\`
- \`Java_jni_V2RayPoint_stopLoop\`
- \`Java_jni_LibV2ray_newV2RayPoint\`

Esto demuestra que el motor V2Ray puede recibir **configuración en tiempo de ejecución**, en vez de tener la dirección de un servidor fijada dentro del binario.

\`res/raw/china_clash_config.yaml\` configura el destino \`socks5://127.0.0.1:1081\` y un puerto SOCKS local. Esa dirección es un **puente interno del dispositivo**, no la dirección remota de un nodo VPN.

### Qué NO aparece listo para usar

- No se encontró un perfil \`.ovpn\` o \`v2ray.json\` de conexión remota listo para importar.
- No se ha obtenido una lista de servidores vigente, ni certificados, ni credenciales autorizadas para usar uno.
- No se ha verificado un flujo de autenticación real por ejecución dinámica.
- Los binarios nativos están compilados para Android ARM64 y dependen del sistema Android; no sirven directamente en un contenedor Linux x86 de Render.
- La APK es una versión MOD y contiene rastros de modificación (\`assets/SignatureKiller/origin.apk\`, referencia al actualizador de Getmodsapk). No se debe ejecutar ni incorporar como código confiable en Render.

## Secuencia de conexión reconstruida a nivel de componentes

1. La app recibe la configuración de obtención de servidores.
2. Intenta recuperar la lista de servidores y puede utilizar las rutas de respaldo por hash.
3. Selecciona un servidor y un protocolo compatible.
4. Genera o recupera los parámetros del protocolo para esa conexión.
5. Inicia el motor nativo OpenVPN, V2Ray u otro, y lo vincula con la interfaz VPN Android.
6. El túnel o SOCKS local reenvía tráfico hacia el servidor seleccionado.

Los pasos 1–5 son una reconstrucción de clases, recursos y símbolos; los cuerpos completos de todas las funciones no fueron reconstruidos y la comunicación real no fue capturada.

## Consecuencia para Órbita Media

**La APK no contiene una conexión autónoma que podamos activar en Render.** Conocer los mecanismos de selección de servidores no concede permisos para usar los nodos de MobileJump, y no elimina el bloqueo 429 aplicado por YouTube.

La infraestructura actual del proyecto ya incluye \`vpn_linux.py\`, que puede usar un **proxy Linux autorizado** para las solicitudes de búsqueda, Chromium y descargas mediante \`ORBITA_OUTBOUND_PROXY\`. Sin esa variable sigue saliendo por la IP original de Render.

Para conectar un nodo real en Linux sin Android se necesita uno de estos recursos, obtenido legítimamente:

- Perfil OpenVPN completo y autorización para ese servidor, ejecutado en un entorno compatible con su interfaz de túnel.
- Perfil V2Ray/Xray válido y un proceso Linux que abra un proxy local.
- Dirección SOCKS5 o HTTP(S) de un proxy funcional con sus credenciales.

Si se obtienen estos datos de forma autorizada, se puede conectar el perfil al adaptador de Órbita. La inspección de esta APK **no produjo** tales credenciales o perfiles.

## Nota sobre licencias/seguridad

Los servidores pertenecen a un servicio de terceros. No se debe intentar suplantar la identidad de un cliente, acceder a servidores premium sin derecho o extraer credenciales ajenas. Esta auditoría es informativa y no concede autorización de uso de la infraestructura.
