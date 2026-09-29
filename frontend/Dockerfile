# syntax=docker/dockerfile:1
# Build context: frontend/

FROM node:20-alpine AS build
WORKDIR /app

# Lockfile first so `npm ci` is cached until dependencies change.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime

# Values are substituted into the template at container start by the stock
# nginx entrypoint (envsubst over /etc/nginx/templates/*.template).
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS makes the image's 15-local-resolvers.envsh
# script export NGINX_LOCAL_RESOLVERS from /etc/resolv.conf (Docker's embedded
# DNS at 127.0.0.11 in compose), which the template uses for a `resolver`
# directive so nginx re-resolves core instead of caching its IP for the
# process lifetime.
ENV CORE_UPSTREAM=http://core:8100 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

# Run as the unprivileged `nginx` user: it needs to write the rendered config,
# the cache dirs and the pid file. Port 8080 needs no privileges.
RUN touch /run/nginx.pid \
 && chown -R nginx:nginx /run/nginx.pid /var/cache/nginx /etc/nginx/conf.d
USER nginx

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD ["wget", "-q", "--spider", "http://127.0.0.1:8080/nginx-health"]
