# Dev image. Source is bind-mounted by docker-compose; node_modules and .next
# stay in container-only volumes so the macOS host copies never shadow them.
# Node 20 LTS — Next 14's supported runtime.
FROM node:20-alpine

# Webpack's watcher does not receive inotify events through a macOS bind mount,
# so without polling hot reload silently stops working.
ENV WATCHPACK_POLLING=true

WORKDIR /app

# Copied before the source so a code edit does not invalidate the npm layer.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3002

# -H 0.0.0.0 so the dev server is reachable from outside the container.
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0"]
