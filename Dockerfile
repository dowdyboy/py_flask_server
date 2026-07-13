FROM python:3.12-slim

WORKDIR /app

# 安装依赖（利用 Docker 缓存层）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 创建非 root 用户并切换
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 5000

# 默认开发入口
# 生产环境请改用: CMD ["python", "wsgi.py"]（waitress WSGI 服务器）
CMD ["python", "server.py"]
