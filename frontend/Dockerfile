# Frontend image for the AI Quant Platform (Vite React SPA served by nginx).
# Build context: ./frontend  (set in docker-compose.yml)
#
# Multi-stage: node builds the static bundle, nginx serves it and proxies
# /api to the backend compose service.

# ---- build stage ----
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- serve stage ----
FROM nginx:alpine

# Static assets produced by `vite build` (output dir: dist/).
COPY --from=build /app/dist /usr/share/nginx/html

# nginx server config with SPA fallback + /api reverse proxy to the backend
# compose service (service name: "backend", port 8000).
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# healthcheck: nginx serves index.html at /.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
