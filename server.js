'use strict';

const http = require('node:http');
const { URL } = require('node:url');

// Un unico backend Pterodactyl, con dos dominios que tu aplicacion ya reconoce.
const ORIGIN_HOST = process.env.ORIGIN_HOST || '64.20.54.50';
const ORIGIN_PORT = Number(process.env.ORIGIN_PORT || 25640);
const HOSTS = new Set([
  'bepo67.duckdns.org',
  'bepo-alya-code.duckdns.org'
]);
const PORT = Number(process.env.PORT || 10000);

if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535 ||
    !Number.isInteger(ORIGIN_PORT) || ORIGIN_PORT < 1 || ORIGIN_PORT > 65535) {
  console.error('Los puertos deben ser numeros entre 1 y 65535');
  process.exit(1);
}

const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade'
]);

const server = http.createServer((req, res) => {
  const host = String(req.headers.host || '').split(':')[0].toLowerCase().replace(/\.$/, '');
  if (req.url === '/_proxy_health' && host === 'localhost') {
    res.writeHead(200, {'Content-Type': 'text/plain; charset=utf-8'});
    res.end('OK');
    return;
  }
  if (!HOSTS.has(host)) {
    res.writeHead(404, {'Content-Type': 'text/plain; charset=utf-8'});
    res.end('Dominio no configurado en el proxy');
    return;
  }

  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (!HOP_BY_HOP.has(key) && key !== 'host' && key !== 'x-forwarded-host' &&
        key !== 'x-forwarded-proto' && key !== 'x-forwarded-for') headers[key] = value;
  }
  headers.host = host;
  headers['x-forwarded-host'] = host;
  headers['x-forwarded-proto'] = 'https';
  // No confiar en IPs proporcionadas por clientes; este valor NO es una IP verificada.

  const upstream = http.request({
    hostname: ORIGIN_HOST,
    port: ORIGIN_PORT,
    method: req.method,
    path: req.url,
    headers,
    timeout: 20000
  }, (originResponse) => {
    const responseHeaders = {};
    for (const [key, value] of Object.entries(originResponse.headers)) {
      if (!HOP_BY_HOP.has(key) && key !== 'strict-transport-security') responseHeaders[key] = value;
    }
    // Cambia redirecciones absolutas http://<dominio>/ a https://<dominio>/.
    if (typeof responseHeaders.location === 'string') {
      const location = responseHeaders.location;
      if (location.startsWith(`http://${host}/`)) {
        responseHeaders.location = `https://${host}/` + location.slice(`http://${host}/`.length);
      }
    }
    res.writeHead(originResponse.statusCode || 502, responseHeaders);
    originResponse.pipe(res);
  });

  upstream.on('timeout', () => upstream.destroy(new Error('Tiempo de espera agotado')));
  upstream.on('error', (err) => {
    console.error('Fallo comunicacion con Pterodactyl:', err.message);
    if (!res.headersSent) {
      res.writeHead(502, {'Content-Type': 'text/plain; charset=utf-8'});
      res.end('No se puede contactar al servidor de origen');
    } else {
      res.destroy();
    }
  });
  req.on('aborted', () => upstream.destroy());
  req.pipe(upstream);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`Proxy de DuckDNS escuchando en 0.0.0.0:${PORT}`);
  console.log(`Origen: ${ORIGIN_HOST}:${ORIGIN_PORT}`);
  console.log(`Dominios: ${[...HOSTS].join(', ')}`);
});
