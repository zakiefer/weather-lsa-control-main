# Next.js dev image
FROM node:20-alpine
WORKDIR /app
COPY package.json pnpm-lock.yaml* .npmrc* ./
RUN corepack enable && corepack prepare pnpm@9.10.0 --activate && pnpm fetch || true
COPY . .
RUN pnpm install
EXPOSE 3000
CMD ["pnpm", "dev"]
