FROM node:24.22.2-bookworm-slim AS frontend
WORKDIR /build/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
COPY contracts/generated/ /build/contracts/generated/
ENV VITE_DATA_MODE=demo
RUN npm run build
FROM nginx:stable-alpine
COPY --from=frontend /build/apps/web/dist/ /usr/share/nginx/html/
