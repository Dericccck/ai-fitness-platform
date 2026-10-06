ARG NODE_BUILD_IMAGE=node:24-bookworm-slim
ARG NGINX_RUNTIME_IMAGE=nginxinc/nginx-unprivileged:1.28-alpine
FROM ${NODE_BUILD_IMAGE} AS build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig*.json ./
COPY src ./src
RUN npm run build

FROM ${NGINX_RUNTIME_IMAGE}
COPY deployment/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html
EXPOSE 8080
STOPSIGNAL SIGQUIT
