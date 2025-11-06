#!/usr/bin/env bash
set -e
echo "Building frontend and starting backend"
cd frontend || true
if [ -f package.json ]; then
  npm ci --silent || true
  npm run build --silent || true
  rm -rf ../backend/static/*
  mkdir -p ../backend/static
  cp -r build/* ../backend/static/ || true
fi
cd ../backend
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
