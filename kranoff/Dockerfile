FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:24-alpine
RUN apk add --no-cache tzdata font-dejavu ca-certificates
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY --from=build /app/dist ./dist
COPY server ./server
RUN mkdir -p /data/uploads
ENV DATA_DIR=/data
ENV TZ=Asia/Almaty
EXPOSE 3000
CMD ["node", "server/index.ts"]
