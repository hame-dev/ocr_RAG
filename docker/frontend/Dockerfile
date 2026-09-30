# Node 24 is the current LTS line (Krypton). Next 15.5 supports it.
#
# Two targets:
#   dev   `next dev` against the bind-mounted source (docker-compose.yml).
#   prod  a real `next build`, served by `next start`. NEXT_PUBLIC_API_BASE is
#         inlined into the client bundle at build time, so it is a build arg:
#           docker build --target prod \
#             --build-arg NEXT_PUBLIC_API_BASE=https://api.example.com \
#             -f docker/frontend/Dockerfile frontend
FROM node:24-alpine AS deps
WORKDIR /app
# `npm ci` installs exactly what package-lock.json pins and fails loudly if the
# lockfile is out of sync, which `npm install` silently papers over by rewriting
# it. Falls back to `npm install` only when no lockfile is present.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

FROM deps AS build
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN npm run build && npm prune --omit=dev

FROM node:24-alpine AS prod
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app/package.json ./
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/.next ./.next
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["npm", "start"]
