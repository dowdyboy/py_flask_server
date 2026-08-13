# Examples 参考样例

本目录为**教学参考样例**，不在工程运行路径内，`flask_server` 包不会导入这里的任何文件。

## 目录结构

```
examples/
├── component/
│   └── interceptor_example.py      # 鉴权拦截器样例（X-AUTH-TOKEN / X-USER-KEY）
├── controller/
│   ├── user_controller.py          # 用户登录/登出（@app.route 风格）
│   ├── article_controller.py       # 文章增删改查（@app.route 风格）
│   ├── user_crud_controller.py     # 用户 CRUD（flask-smorest Blueprint 风格，推荐）
│   └── file_controller.py          # 文件上传/下载/删除（LocalFileStorage 闭环）
├── service/
│   ├── user_service.py             # 用户业务（@app.route 风格配套）
│   ├── article_service.py          # 文章业务（@app.route 风格配套）
│   └── user_crud_service.py        # 用户 CRUD 业务（flask-smorest 风格配套，推荐）
├── schema/
│   └── user_schema.py              # 用户 CRUD 的 Schema 定义（marshmallow）
├── model/
│   ├── user.py                     # 反射式模型样例（不推荐，import 即连库）
│   ├── user_declared.py            # 声明式模型正例（推荐，配合 Flask-Migrate）
│   └── article.py                  # 反射式模型样例（不推荐）
├── scheduler_demo.py               # APScheduler 定时任务示例
├── socketio_demo.py                # SocketIO 事件示例（需安装 Flask-SocketIO）
└── api.http                        # REST Client 一键测试全部端点
```

## 两种写法对比

| | @app.route 风格 | flask-smorest Blueprint 风格 |
|---|---|---|
| 示例文件 | `user_controller.py` / `article_controller.py` | `user_crud_controller.py` |
| 参数校验 | 手动从 `request.payload` 取值 | `@blp.arguments(Schema)` 自动校验 |
| API 文档 | 无 | 自动生成 Swagger UI（`/docs`） |
| 推荐度 | 简单接口可用 | **推荐**，生产项目首选 |

## 接入工程前的注意事项

这些样例直接复制回 `flask_server/` 并不能立即运行，需自行适配：

1. **配置数据库**：在 `.env` 中设置 `SQLALCHEMY_URI` 或 `SQLITE_DB_PATH`（模型样例在 import 时即反射表结构，未配数据库会启动报错）。
2. **补全缺失模型**：`article_service.py` 引用了 `BuyRecordPO`，原样例未提供其模型定义，需自行创建。
3. **导出注册**：把样例放回 `flask_server/` 对应目录后，需在各自的 `__init__.py` 中添加导入，才会被加载。
4. **未完成代码**：`article_service.py` 的 `increase_access_count` 为空实现，需按需补全。
5. **反射式 PO 的 import 时 DB IO**：`user.py`/`article.py` 在类定义体内执行 `db.Table(..., autoload_with=db.engine)`，import 即连库反射表结构。未配数据库或表不存在会启动报错；推荐使用声明式 `user_declared.py`。
6. **拦截器依赖**：`user_controller.py` 的登出读 `request.info['token']`、`article_controller.py` 的前端
   接口（get/list）读 `request.info['user_key']`——这些值由 `component/interceptor_example.py`
   写入（并配置 `need_auth_path_list` / `need_user_key_path_list`）。未合并拦截器时这些
   接口会因 KeyError 返回 400。

## 用法

样例可作为编写 Controller / Service / Model / 拦截器的参考模板，按自身业务改写后接入工程。推荐参考 `user_crud_controller.py` 的 flask-smorest 风格。
