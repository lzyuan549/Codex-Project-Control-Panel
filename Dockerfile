ARG BASE_IMAGE=m.daocloud.io/docker.io/library/node:22-bookworm-slim
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    WORKSPACE_ROOT=/www/wwwroot \
    HOST=0.0.0.0 \
    PORT=8000 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

ARG APT_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian
ARG APT_SECURITY_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG NPM_REGISTRY=https://registry.npmmirror.com

RUN sed -i \
      -e "s|http://deb.debian.org/debian|${APT_MIRROR}|g" \
      -e "s|http://deb.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
      -e "s|http://security.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

RUN npm config set registry "${NPM_REGISTRY}" \
    && npm install -g @openai/codex

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY static /app/static

RUN mkdir -p /data /www/wwwroot
VOLUME ["/data"]
EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
