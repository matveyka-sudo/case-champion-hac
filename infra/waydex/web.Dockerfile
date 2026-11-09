FROM node:24.22.2-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY contracts/generated/ /build/contracts/generated/
ENV VITE_DATA_MODE=demo
RUN npm run build
FROM nginx:stable-alpine
COPY --from=frontend /build/frontend/dist/ /usr/share/nginx/html/
