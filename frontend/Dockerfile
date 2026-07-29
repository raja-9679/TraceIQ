# Build stage
FROM node:18-alpine as build

WORKDIR /app

COPY package*.json ./
RUN npm install

ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html

# Where nginx proxies /api/ to, resolved at CONTAINER START (not build time):
# the base image's entrypoint envsubsts /etc/nginx/templates/*.template into
# conf.d/. Override with `-e BACKEND_URL=http://my-backend:8000` to point one
# prebuilt frontend image at a backend anywhere.
ENV BACKEND_URL=http://backend:8000
COPY nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
