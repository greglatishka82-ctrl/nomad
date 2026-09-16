FROM node:22-alpine AS build
WORKDIR /app
COPY admin/frontend/package*.json ./
RUN npm ci
COPY admin/frontend/ .
RUN npm run build
FROM nginx:1.27-alpine
COPY deploy/admin-frontend.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
