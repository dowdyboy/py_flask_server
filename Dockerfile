# 多阶段构建：builder 只安装依赖到独立前缀，最终层仅携带运行时文件，显著减小镜像体积
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && find /install -name '__pycache__' -type d -exec rm -rf {} + \
    && find /install -name '*.pyc' -delete

FROM python:3.12-slim

WORKDIR /app

# 仅复制运行时依赖（不包含构建工具链）
COPY --from=builder /install /usr/local

# 复制项目文件
COPY . .

# 清理源码目录中的缓存文件
RUN find /app -name '__pycache__' -type d -exec rm -rf {} + \
    && find /app -name '*.pyc' -delete

# 创建非 root 用户并切换
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 5000

# 默认开发入口
# 生产环境请改用: CMD ["python", "wsgi.py"]（waitress WSGI 服务器）
# Linux 生产多进程/WebSocket: CMD ["python", "wsgi_gunicorn.py"]（需安装 gunicorn）
CMD ["python", "server.py"]
