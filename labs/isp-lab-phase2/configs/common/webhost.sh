#!/bin/sh
# netlab web host: serve a page naming this host and its eth1 address on :80.
# network-multitool ships nginx but its entrypoint does not start it (cmd=bash).
mkdir -p /srv/www /run/nginx
echo "<h1>$(hostname)</h1><p>$(ip -4 -o addr show eth1 | tr -s ' ' | cut -d' ' -f4)</p>" > /srv/www/index.html
if command -v nginx >/dev/null; then
  printf 'events{}\nhttp{server{listen 80;root /srv/www;}}\n' > /srv/nginx.conf
  nginx -c /srv/nginx.conf && echo "web: nginx"
elif busybox --list 2>/dev/null | grep -qx httpd; then
  httpd -p 80 -h /srv/www && echo "web: busybox-httpd"
else
  echo "web: NO SERVER AVAILABLE" >&2; exit 1
fi
