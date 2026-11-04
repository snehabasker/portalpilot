FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
# In AWS the ALB routes /api to the API service; the proxy block serves docker compose.
ARG API_UPSTREAM=api:8000
ENV API_UPSTREAM=${API_UPSTREAM} NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 NGINX_ENVSUBST_FILTER="^(API_UPSTREAM|NGINX_LOCAL_RESOLVERS)$"
COPY templates /etc/nginx/templates
COPY --from=build /app/dist /usr/share/nginx/html
