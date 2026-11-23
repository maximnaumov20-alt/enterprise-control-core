FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
# Keep Alpine security fixes current instead of inheriting stale packages
# from the upstream image snapshot.
RUN apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/30-configure-trusted-proxies.sh /docker-entrypoint.d/30-configure-trusted-proxies.sh
RUN chmod 755 /docker-entrypoint.d/30-configure-trusted-proxies.sh \
    && touch /etc/nginx/trusted-proxies.conf
EXPOSE 80
