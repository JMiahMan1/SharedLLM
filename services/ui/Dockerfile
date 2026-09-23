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

# Inject version info into a JSON file for the UI to consume.
# Key name must be `git_sha` — the gateway's OTA endpoint and the mobile
# updater both key off that. build.js overwrites this file after the Vite build
# with the same schema; this copy is the fallback if packaging is skipped.
# Version comes from package.json (single source of truth, matches Android versionName).
RUN node -e "const p=require('./package.json');const fs=require('fs');fs.writeFileSync('public/version.json',JSON.stringify({version:p.version,git_sha:process.env.GIT_SHA||'unknown',build_timestamp:process.env.BUILD_DATE||'unknown'},null,2)+'\n')"

# A bundle with no identifiable SHA cannot be offered as an OTA update (the app
# could never tell whether it already has it), so refuse to build one.
RUN if [ "${GIT_SHA}" = "unknown" ] || [ -z "${GIT_SHA}" ]; then \
        echo "ERROR: GIT_SHA build arg is required (docker build --build-arg GIT_SHA=\$(git rev-parse --short HEAD))"; \
        exit 1; \
    fi

# Set memory limit for Node to prevent bundler crashes
ENV NODE_OPTIONS="--max-old-space-size=4096"

RUN npm run build

# Production stage
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8008
CMD ["nginx", "-g", "daemon off;"]
