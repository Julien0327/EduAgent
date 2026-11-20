# EduMate 后端镜像（Phase 6 / S2-C）
# 构建（仓库根执行）：docker build -f deploy/Dockerfile -t edumate:latest .
#
# 说明：
# - Python 3.13 与本机/CI 验证版本一致；
# - 官方 acps-sdk 不在 PyPI，基础镜像不安装（ACP_ENABLED 默认关闭，不影响业务）；
# - 非 root 运行（uid/gid 10001）；data/db、data/outputs 由 compose 挂载，
#   宿主机需保证该 uid 可写（见 DEPLOYMENT.md）；
# - BGE 模型缓存不入镜像，由 compose 以只读卷挂载 data/models。
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# onnxruntime 需要 libgomp；同时创建非 root 用户
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN grep -v '^acps-sdk' requirements.txt > /tmp/requirements-core.txt \
    && pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r /tmp/requirements-core.txt \
    && rm -f /tmp/requirements-core.txt

COPY backend ./backend
COPY frontend ./frontend
COPY data ./data

RUN mkdir -p /app/data/db /app/data/outputs /app/data/models \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

# python -m 使 cwd（/app）进入 sys.path，backend.* 顶层包可直接导入
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
