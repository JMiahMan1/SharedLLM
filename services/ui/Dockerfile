# Build stage
FROM node:22-bookworm AS build

# Install build essentials for native dependencies
RUN apt-get update && apt-get install -y build-essential python3 && rm -rf /var/lib/apt/lists/*

ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

WORKDIR /app
COPY package*.json ./

# Use legacy-peer-deps to handle complex version mixes in latest releases
RUN npm install --legacy-peer-deps

COPY . .

# Inject version info into a JSON file for the UI to consume
RUN echo "{\"version\":\"${BUILD_DATE}\",\"gitSha\":\"${GIT_SHA}\"}" > public/version.json

# Set memory limit for Node to prevent bundler crashes
ENV NODE_OPTIONS="--max-old-space-size=4096"

RUN npm run build

# Production stage
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8008
CMD ["nginx", "-g", "daemon off;"]
