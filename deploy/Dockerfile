# EduAgent 后端镜像（Phase 0/I5：可运行的最小容器形态）
# 构建（在仓库根目录执行）：docker build -f deploy/Dockerfile -t eduagent .
#
# 说明：官方 acps-sdk 不在 PyPI（AtomGit 源/本地 wheel），且 AIP 智能体互联为
# 可选能力（ACP_ENABLED 默认关闭，未安装 SDK 不影响业务启动，见 interop/__init__.py），
# 故基础镜像不安装它；需要 AIP 时在容器内另行安装本地 wheel。
FROM python:3.11-slim

WORKDIR /app

# 先装依赖以利用层缓存（过滤掉非 PyPI 的 acps-sdk 行）
# PIP_INDEX_URL 可用 --build-arg 覆盖：国内网络建议传 https://pypi.tuna.tsinghua.edu.cn/simple
COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN grep -v '^acps-sdk' requirements.txt > requirements-core.txt \
    && pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements-core.txt

# 拷贝代码、前端与种子数据（.dockerignore 已排除 .git/.venv/logs/.env 等）
COPY backend ./backend
COPY frontend ./frontend
COPY data ./data

EXPOSE 8000

# python -m 使 cwd（/app）进入 sys.path，backend.* 顶层包直接可导入（Phase 0/I1）
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
