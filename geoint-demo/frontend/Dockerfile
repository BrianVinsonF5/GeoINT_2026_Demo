FROM node:20-alpine AS build
WORKDIR /app

COPY package.json ./
RUN npm install

COPY src ./src
COPY nginx.conf ./nginx.conf
RUN npm run build

FROM nginx:alpine
WORKDIR /usr/share/nginx/html

COPY --from=build /app/dist/ /usr/share/nginx/html/
COPY --from=build /app/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
