# 베이스 이미지
FROM python:3.11-slim

# 작업 디렉터리 설정
WORKDIR /app

# 시스템 필수 패키지 설치
RUN apt-get update && apt-get install -y curl ca-certificates build-essential && rm -rf /var/lib/apt/lists/*

# Node.js 설치
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs

# 앱 전체 복사
COPY . /app

# ------------------------------
# frontend 빌드
WORKDIR /app/frontend

# package.json 존재 확인 (문제 파악용, 필요 없으면 제거 가능)
RUN ls -la

# npm 패키지 설치
RUN npm ci

# frontend 빌드
RUN npm run build

# ------------------------------
# backend 설치
WORKDIR /app/backend
RUN pip install --no-cache-dir -r requirements.txt

# 환경 설정
ENV PYTHONUNBUFFERED=1

# 포트
EXPOSE 8000

# 실행 명령
CMD ["/bin/bash", "./start.sh"]
