from flask_server.util.data_encrypt_util import DataEncryptUtil


def test_sha1():
    h = DataEncryptUtil.sha1('hello')
    assert len(h) == 40
    assert h == 'aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d'


def test_sha256():
    h = DataEncryptUtil.sha256('hello')
    assert len(h) == 64
    assert h == '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'


def test_pbkdf2_hmac_format():
    """新格式: salt$iterations$hash"""
    out = DataEncryptUtil.pbkdf2_hmac('password')
    parts = out.split('$')
    assert len(parts) == 3
    salt, iterations, hash_hex = parts
    assert len(salt) == 32   # 16 bytes hex
    assert iterations == '100000'
    assert len(hash_hex) == 64   # 32 bytes hex


def test_pbkdf2_hmac_custom_iterations():
    """自定义迭代次数应编码进存储格式"""
    out = DataEncryptUtil.pbkdf2_hmac('password', iterations=50000)
    parts = out.split('$')
    assert parts[1] == '50000'


def test_pbkdf2_hmac_with_salt():
    out = DataEncryptUtil.pbkdf2_hmac('password', salt='00112233445566778899aabbccddeeff')
    assert out.startswith('00112233445566778899aabbccddeeff$')


def test_verify_pbkdf2_match():
    stored = DataEncryptUtil.pbkdf2_hmac('mypassword')
    assert DataEncryptUtil.verify_pbkdf2('mypassword', stored) is True


def test_verify_pbkdf2_mismatch():
    stored = DataEncryptUtil.pbkdf2_hmac('mypassword')
    assert DataEncryptUtil.verify_pbkdf2('wrongpassword', stored) is False


def test_verify_pbkdf2_custom_iterations():
    """自定义迭代次数生成的密码，校验时能正确识别"""
    stored = DataEncryptUtil.pbkdf2_hmac('mypassword', iterations=50000)
    assert DataEncryptUtil.verify_pbkdf2('mypassword', stored) is True
    assert DataEncryptUtil.verify_pbkdf2('wrong', stored) is False


def test_verify_pbkdf2_legacy_format():
    """兼容旧格式 salt$hash（无 iterations）"""
    import hashlib
    salt = '00112233445566778899aabbccddeeff'
    derived = hashlib.pbkdf2_hmac('sha256', b'test', bytes.fromhex(salt), 100000)
    legacy_stored = f'{salt}${derived.hex()}'
    assert DataEncryptUtil.verify_pbkdf2('test', legacy_stored) is True


def test_verify_pbkdf2_invalid_format():
    assert DataEncryptUtil.verify_pbkdf2('x', 'invalid') is False
    assert DataEncryptUtil.verify_pbkdf2('x', None) is False
    assert DataEncryptUtil.verify_pbkdf2('x', 'a$b$c$d') is False
