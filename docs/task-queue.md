# 任务队列

把"必须执行、但不必在请求里执行完"的动作（发邮件、生成报表、同步第三方、图片处理）
放到后台任务里。框架内置一个**可持久化、多进程安全**的队列：

| 后端 | 触发条件 | 适用 |
|---|---|---|
| `RedisTaskQueue` | 配置了 `REDIS_URL` | 生产：多 worker / 多实例、重启不丢、崩溃可恢复 |
| `MemoryTaskQueue` | 未配置 `REDIS_URL` | 开发/测试：单进程，进程退出即丢 |

> 与 `util/async_task_util.py` 的线程池（`AsyncTaskUtil`）的区别：线程池是**进程内尽力而为**，
> 重启即丢、多 worker 各自为战、没有重试与状态查询。任务队列解决的就是这些。

---

## 1. 三步接入

### ① 定义任务（`flask_server/task/` 下建文件即生效）

```python
# flask_server/task/email.py
from flask_server.module.task_queue import task

@task('send_welcome_email', max_retries=5)
def send_welcome_email(to, nickname):
    # handler 运行在 app 上下文内：可直接用 db.session / 现有 service
    ...
```

文件名不以 `_` 开头的模块会被 worker 启动时自动导入（`auto_import_tasks()`）。

### ② 入队（controller / service 里）

```python
from flask_server.module.task_queue import TaskQueueUnavailable, enqueue_task

@blp.route('/register')
class RegisterView(MethodView):
    def post(self):
        user = create_user(...)
        try:
            task_id = enqueue_task('send_welcome_email', user.email, user.nickname)
        except TaskQueueUnavailable:
            # 任务丢失比接口报错更难发现：这里显式降级（同步发 / 提示重试），不要假装成功
            task_id = None
        return GraceResult.ok({'uid': user.uid, 'task_id': task_id})
```

### ③ 启动消费者

```bash
# 独立进程（生产推荐，可独立扩容/重启）
python scripts/worker.py --concurrency 4
# 或 make worker

# 开发时想省一个终端：让 Web 进程内嵌一个 worker
TASK_WORKER_EMBEDDED=true python server.py
```

---

## 2. 语义与保证

| 项 | 说明 |
|---|---|
| 交付语义 | **at-least-once**：任务至少执行一次，崩溃恢复可能重复 → handler 必须幂等 |
| 执行顺序 | 不保证（多个消费者并发取；同一任务的重试会延后） |
| 重试 | 失败后按 `TASK_RETRY_BACKOFF * 2**(已失败次数)` 退避重投；超过 `TASK_MAX_RETRIES` 进死信 |
| 崩溃恢复 | worker 崩溃后消息停留在 pending，超过 `TASK_VISIBILITY_TIMEOUT` 被其它 worker 接管 |
| 死信 | 超过重试上限或任务未注册 → 写入死信列表（`{stream}:dead`，上限 `TASK_DEAD_LETTER_MAX`） |
| 状态查询 | `get_task_status(task_id)` → `queued/running/succeeded/retrying/dead` + `attempts` + `last_error` |
| 入队失败 | **抛 `TaskQueueUnavailable`**，不静默丢弃（与缓存降级策略刻意不同） |

---

## 3. 状态机

```
                 ┌──────────┐
   enqueue ─────▶│  queued  │
                 └────┬─────┘
                      │ worker 取到
                 ┌────▼─────┐
                 │ running  │
                 └──┬────┬──┘
           成功 │      │ 失败且还有重试次数
        ┌───────▼──┐ ┌▼────────┐
        │succeeded │ │retrying │──┐（退避到期后重新入队 → running）
        └──────────┘ └─────────┘◀─┘
                      │ 失败且已达上限 / 任务未注册
                 ┌────▼─────┐
                 │   dead   │（进死信，供人工排查/重放）
                 └──────────┘
```

---

## 4. 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `REDIS_URL` | _无_ | 配置后启用 Redis Streams 后端；未配置用进程内内存队列 |
| `TASK_QUEUE_STREAM` | `tasks:default` | 队列名（不同业务可用不同 stream，互不干扰） |
| `TASK_QUEUE_GROUP` | `default` | consumer group 名 |
| `TASK_WORKER_CONCURRENCY` | `4` | 消费线程数 |
| `TASK_MAX_RETRIES` | `3` | 最大重试次数（0 = 失败直接进死信） |
| `TASK_RETRY_BACKOFF` | `5` | 退避基数（秒） |
| `TASK_VISIBILITY_TIMEOUT` | `300` | 崩溃恢复的接管阈值（秒） |
| `TASK_STATUS_TTL` | `86400` | 状态保留时长（秒） |
| `TASK_DEAD_LETTER_MAX` | `1000` | 死信列表上限 |
| `TASK_STATUS_MAX` | `10000` | 内存后端状态表上限（防内存无界增长） |
| `TASK_RESERVE_TIMEOUT` | `2` | 单次出队阻塞时长（秒） |
| `TASK_WORKER_EMBEDDED` | `false` | 是否在 Web 进程内嵌 worker（仅开发/单进程） |

---

## 5. 运维

```bash
# 队列积压/死信数量（Redis 后端）
redis-cli XLEN tasks:default
redis-cli ZCARD tasks:default:delayed
redis-cli LLEN tasks:default:dead

# 死信列表（最近 20 条；也可在代码里 get_task_queue().dead_letters(limit=20)）
redis-cli LRANGE tasks:default:dead 0 19

# 重放一条死信：取出信封重新入队（示例）
python - <<'PY'
import json
from flask_server.module.task_queue import get_task_queue

q = get_task_queue()
letter = q.dead_letters(limit=1)[0]          # {'id','name','status','last_error',...}
if letter.get('id'):
    # 用同一 task_id 重新入队（相当于重跑；handler 需幂等）
    q.enqueue(letter['name'], delay=0, task_id=letter['id'])
PY
```

**进程退出**：worker 收到 SIGTERM 后不再取新任务，等在途任务完成再退出；
容器里请把 `stop_grace_period` 设得比最长任务长一点。

---

## 6. 注意事项

1. **handler 必须幂等**：at-least-once 语义下，超时/崩溃/确认失败都会导致重复执行。
   建议用业务唯一键去重（如"订单 123 已发过欢迎邮件"），或把副作用写成可重复执行。
2. **参数必须可 JSON 序列化**：队列里传的是 JSON 信封；不要传 ORM 对象，传 id 再查库。
3. **多 worker 必须配 Redis**：内存后端下每个进程只消费自己入队的任务。
4. **不要用任务队列做"强一致"的事**：它不是事务的一部分（没有 outbox 语义），
   需要"业务提交成功才发任务"的场景，请把任务写入放在事务提交之后。
5. **长任务**：超过 `TASK_VISIBILITY_TIMEOUT` 的任务会被判定为卡死并被接管（重复执行）。
   长任务请调大该值，或在 handler 内部续期（暂未内置心跳）。
