# Flask Server 常用命令
# 用法：make <target>（Windows 可用 Git Bash / WSL；纯 Windows 环境请用 scripts/dev.ps1）

.PHONY: install dev prod test lint audit migrate upgrade init scaffold help

install:            ## 安装依赖（运行时 + 开发）
	pip install -r requirements.txt -r requirements-dev.txt

dev:                ## 启动开发服务器（http://127.0.0.1:5000/docs）
	python server.py

prod:               ## 启动生产服务器（waitress）
	python wsgi.py

test:               ## 运行测试（含覆盖率门槛）
	pytest tests/ --cov=flask_server --cov-report=term

lint:               ## 代码风格检查（ruff）
	ruff check flask_server/ tests/ examples/ server.py wsgi.py wsgi_gunicorn.py

audit:              ## 依赖安全审计（pip-audit）
	pip-audit -r requirements.txt

migrate:            ## 生成数据库迁移脚本（需配置 SQLALCHEMY_URI）：make migrate m="create users table"
	FLASK_APP=flask_server.app:app flask db migrate -m "$(m)"

upgrade:            ## 执行数据库迁移
	FLASK_APP=flask_server.app:app flask db upgrade

init:               ## 初始化迁移目录（首次）
	FLASK_APP=flask_server.app:app flask db init

scaffold:           ## 生成新项目脚手架：make scaffold p=my_project author="Your Name"
	python scripts/scaffold.py $(p) --author "$(author)"

help:               ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*## ' Makefile | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
