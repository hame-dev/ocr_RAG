# Node 24 is the current LTS line (Krypton). Next 15.5 supports it.
FROM node:24-alpine

WORKDIR /app

# `npm ci` installs exactly what package-lock.json pins and fails loudly if the
# lockfile is out of sync, which `npm install` silently papers over by rewriting
# it. Falls back to `npm install` only when no lockfile is present.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev"]
