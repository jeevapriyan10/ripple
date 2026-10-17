# Multi-stage build for Ripple Frontend

# Stage 1: Build static assets with Node
FROM node:22-alpine AS builder

WORKDIR /app

# Copy dependencies manifest
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm install

# Copy application sources
COPY . .

# Build static bundle
RUN npm run build


# Stage 2: Serve static assets with Nginx
FROM nginx:alpine AS runner

# Copy compiled static assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy Nginx reverse proxy and SPA configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose HTTP port 80
EXPOSE 80

# Start Nginx server
CMD ["nginx", "-g", "daemon off;"]
