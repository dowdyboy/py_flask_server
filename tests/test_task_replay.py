"""死信重放：队列方法 + 运维 CLI。"""

import importlib.util
import os

from flask_server.module.task_queue import MemoryTaskQueue


def _load_cli():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        'task_replay_cli_under_test', os.path.join(root, 'scripts', 'task_replay.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _one_dead(q):
    q.enqueue('doomed.task', args=[7], kwargs={'why': 'test'})
    reserved = q.reserve(timeout=0.01)
    q.dead(reserved, 'boom')
    return q


def test_memory_replay_dead_requeues_and_removes():
    q = _one_dead(MemoryTaskQueue())
    assert len(q.dead_letters()) == 1

    task_id = q.replay_dead(0)
    assert q.dead_letters() == []

    reserved = q.reserve(timeout=0.05)
    assert reserved is not None
    assert reserved.envelope.id == task_id
    assert reserved.envelope.name == 'doomed.task'
    assert reserved.envelope.args == [7]
    assert reserved.envelope.kwargs == {'why': 'test'}


def test_memory_replay_dead_out_of_range_raises():
    import pytest

    from flask_server.module.task_queue import TaskNotFound

    q = MemoryTaskQueue()
    with pytest.raises(TaskNotFound):
        q.replay_dead(0)


def test_cli_list_shows_dead_letter(capsys, monkeypatch):
    cli = _load_cli()
    q = _one_dead(MemoryTaskQueue())
    monkeypatch.setattr(cli, '_build_queue', lambda stream: q)

    assert cli.main(['--list']) == 0
    out = capsys.readouterr().out
    assert 'doomed.task' in out
    assert 'boom' in out


def test_cli_replay_index(capsys, monkeypatch):
    cli = _load_cli()
    q = _one_dead(MemoryTaskQueue())
    monkeypatch.setattr(cli, '_build_queue', lambda stream: q)

    assert cli.main(['--replay', '0']) == 0
    assert '已重放入队' in capsys.readouterr().out
    assert q.dead_letters() == []
    assert q.reserve(timeout=0.05).envelope.name == 'doomed.task'


def test_cli_dry_run_does_not_mutate(capsys, monkeypatch):
    cli = _load_cli()
    q = _one_dead(MemoryTaskQueue())
    monkeypatch.setattr(cli, '_build_queue', lambda stream: q)

    assert cli.main(['--replay', '0', '--dry-run']) == 0
    assert '将重放' in capsys.readouterr().out
    assert len(q.dead_letters()) == 1


def test_cli_replay_all(capsys, monkeypatch):
    cli = _load_cli()
    q = MemoryTaskQueue()
    for i in range(3):
        q.enqueue('bulk.fail', args=[i])
        q.dead(q.reserve(timeout=0.01), 'x')
    monkeypatch.setattr(cli, '_build_queue', lambda stream: q)

    assert cli.main(['--replay-all']) == 0
    assert q.dead_letters() == []
    got = [q.reserve(timeout=0.05) for _ in range(3)]
    assert all(r is not None for r in got)
    assert sorted(r.envelope.args[0] for r in got) == [0, 1, 2]
