from flask_server.util.random_generator import RandomGenerator


def test_random_integer():
    n = RandomGenerator.random_integer(1, 10)
    assert 1 <= n <= 10


def test_random_string():
    s = RandomGenerator.random_string(20)
    assert len(s) == 20
    assert all(c.isalpha() for c in s)


def test_random_float():
    f = RandomGenerator.random_float(0.0, 1.0)
    assert 0.0 <= f <= 1.0


def test_secrets_token_length():
    s = RandomGenerator.secrets_token(32)
    assert len(s) == 32


def test_secrets_token_alphabet():
    import string
    s = RandomGenerator.secrets_token(100)
    allowed = set(string.ascii_letters + string.digits)
    assert all(c in allowed for c in s)


def test_secrets_token_uniqueness():
    tokens = {RandomGenerator.secrets_token(16) for _ in range(100)}
    assert len(tokens) == 100
