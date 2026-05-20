### Stage 1 — build React app
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

### Stage 2 — serve with nginx
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html

RUN printf 'server {\n\
    listen 80;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    location /api/ {\n\
        proxy_pass         http://backend:8000;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Connection      "";\n\
        proxy_set_header   Host            $host;\n\
        proxy_buffering    off;\n\
        proxy_cache        off;\n\
        proxy_read_timeout 300s;\n\
    }\n\
\n\
    location / { try_files $uri $uri/ /index.html; }\n\
    gzip on;\n\
    gzip_types text/plain text/css application/json application/javascript;\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]