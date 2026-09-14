import secrets

from iosc.core.errors import AesError

BLOCK_SIZE = 16

RCON = [
    0x01,
    0x02,
    0x04,
    0x08,
    0x10,
    0x20,
    0x40,
    0x80,
    0x1B,
    0x36,
    0x6C,
    0xD8,
    0xAB,
    0x4D,
]


def _multiply(a: int, b: int) -> int:
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        high = a & 0x80
        a = (a << 1) & 0xFF
        if high:
            a ^= 0x1B
        b >>= 1
    return result


def _reciprocal(a: int) -> int:
    if a == 0:
        return 0
    for candidate in range(1, 256):
        if _multiply(a, candidate) == 1:
            return candidate
    raise AesError("no reciprocal, which is impossible in this field")


def _rotate(value: int, count: int) -> int:
    return ((value << count) | (value >> (8 - count))) & 0xFF


def _build_sbox():
    sbox = []
    for value in range(256):
        b = _reciprocal(value)
        sbox.append(
            b
            ^ _rotate(b, 1)
            ^ _rotate(b, 2)
            ^ _rotate(b, 3)
            ^ _rotate(b, 4)
            ^ 0x63
        )
    inverse = [0] * 256
    for index, value in enumerate(sbox):
        inverse[value] = index
    return sbox, inverse


SBOX, INVERSE_SBOX = _build_sbox()


def _expand_key(key: bytes):
    if len(key) not in (16, 24, 32):
        raise AesError(f"key is {len(key)} bytes, expected 16, 24 or 32")
    words = [list(key[i : i + 4]) for i in range(0, len(key), 4)]
    count = len(words)
    rounds = count + 6
    for i in range(count, 4 * (rounds + 1)):
        word = list(words[i - 1])
        if i % count == 0:
            word = word[1:] + word[:1]
            word = [SBOX[b] for b in word]
            word[0] ^= RCON[i // count - 1]
        elif count > 6 and i % count == 4:
            word = [SBOX[b] for b in word]
        words.append([a ^ b for a, b in zip(words[i - count], word)])
    return [sum(words[4 * r : 4 * r + 4], []) for r in range(rounds + 1)], rounds


def _add_round_key(state, round_key):
    return [a ^ b for a, b in zip(state, round_key)]


def _shift_rows(state):
    return [state[(i + 4 * (i % 4)) % 16] for i in range(16)]


def _unshift_rows(state):
    output = [0] * 16
    for i in range(16):
        output[(i + 4 * (i % 4)) % 16] = state[i]
    return output


def _mix_column(column, matrix):
    return [
        _multiply(column[0], matrix[0])
        ^ _multiply(column[1], matrix[1])
        ^ _multiply(column[2], matrix[2])
        ^ _multiply(column[3], matrix[3]),
        _multiply(column[0], matrix[3])
        ^ _multiply(column[1], matrix[0])
        ^ _multiply(column[2], matrix[1])
        ^ _multiply(column[3], matrix[2]),
        _multiply(column[0], matrix[2])
        ^ _multiply(column[1], matrix[3])
        ^ _multiply(column[2], matrix[0])
        ^ _multiply(column[3], matrix[1]),
        _multiply(column[0], matrix[1])
        ^ _multiply(column[1], matrix[2])
        ^ _multiply(column[2], matrix[3])
        ^ _multiply(column[3], matrix[0]),
    ]


def _mix_columns(state, matrix):
    output = []
    for i in range(0, 16, 4):
        output.extend(_mix_column(state[i : i + 4], matrix))
    return output


FORWARD_MATRIX = [2, 3, 1, 1]
REVERSE_MATRIX = [14, 11, 13, 9]


class Aes:
    def __init__(self, key: bytes) -> None:
        self.round_keys, self.rounds = _expand_key(key)

    def encrypt_block(self, block: bytes) -> bytes:
        if len(block) != BLOCK_SIZE:
            raise AesError("a block is 16 bytes")
        state = _add_round_key(list(block), self.round_keys[0])
        for index in range(1, self.rounds):
            state = [SBOX[b] for b in state]
            state = _shift_rows(state)
            state = _mix_columns(state, FORWARD_MATRIX)
            state = _add_round_key(state, self.round_keys[index])
        state = [SBOX[b] for b in state]
        state = _shift_rows(state)
        return bytes(_add_round_key(state, self.round_keys[self.rounds]))

    def decrypt_block(self, block: bytes) -> bytes:
        if len(block) != BLOCK_SIZE:
            raise AesError("a block is 16 bytes")
        state = _add_round_key(list(block), self.round_keys[self.rounds])
        for index in range(self.rounds - 1, 0, -1):
            state = _unshift_rows(state)
            state = [INVERSE_SBOX[b] for b in state]
            state = _add_round_key(state, self.round_keys[index])
            state = _mix_columns(state, REVERSE_MATRIX)
        state = _unshift_rows(state)
        state = [INVERSE_SBOX[b] for b in state]
        return bytes(_add_round_key(state, self.round_keys[0]))


def pad(data: bytes) -> bytes:
    count = BLOCK_SIZE - len(data) % BLOCK_SIZE
    return data + bytes([count]) * count


def unpad(data: bytes) -> bytes:
    if not data or len(data) % BLOCK_SIZE:
        raise AesError("padded data must be a whole number of blocks")
    count = data[-1]
    if not 1 <= count <= BLOCK_SIZE or data[-count:] != bytes([count]) * count:
        raise AesError("padding is malformed")
    return data[:-count]


def encrypt_cbc(
    key: bytes, iv: bytes, data: bytes, padding: bool = True
) -> bytes:
    if len(iv) != BLOCK_SIZE:
        raise AesError("the iv is 16 bytes")
    if padding:
        data = pad(data)
    elif len(data) % BLOCK_SIZE:
        raise AesError("unpadded input must be a whole number of blocks")
    cipher = Aes(key)
    previous = iv
    output = bytearray()
    for offset in range(0, len(data), BLOCK_SIZE):
        block = bytes(
            a ^ b for a, b in zip(data[offset : offset + BLOCK_SIZE], previous)
        )
        previous = cipher.encrypt_block(block)
        output += previous
    return bytes(output)


def decrypt_cbc(
    key: bytes, iv: bytes, data: bytes, padding: bool = True
) -> bytes:
    if len(iv) != BLOCK_SIZE:
        raise AesError("the iv is 16 bytes")
    if not data or len(data) % BLOCK_SIZE:
        raise AesError("ciphertext must be a whole number of blocks")
    cipher = Aes(key)
    previous = iv
    output = bytearray()
    for offset in range(0, len(data), BLOCK_SIZE):
        block = data[offset : offset + BLOCK_SIZE]
        output += bytes(
            a ^ b for a, b in zip(cipher.decrypt_block(block), previous)
        )
        previous = block
    return unpad(bytes(output)) if padding else bytes(output)


def random_iv() -> bytes:
    return secrets.token_bytes(BLOCK_SIZE)


TAG_SIZE = 16
_REDUCTION = 0xE1 << 120


def _gf_multiply(x: int, y: int) -> int:
    result = 0
    value = y
    for bit in range(128):
        if x >> (127 - bit) & 1:
            result ^= value
        if value & 1:
            value = (value >> 1) ^ _REDUCTION
        else:
            value >>= 1
    return result


def _ghash(subkey: int, data: bytes) -> int:
    accumulator = 0
    for offset in range(0, len(data), BLOCK_SIZE):
        block = data[offset : offset + BLOCK_SIZE].ljust(BLOCK_SIZE, b"\x00")
        accumulator = _gf_multiply(
            accumulator ^ int.from_bytes(block, "big"), subkey
        )
    return accumulator


def _counter_block(counter: int) -> bytes:
    return counter.to_bytes(BLOCK_SIZE, "big")


def _gcm_setup(key: bytes, nonce: bytes):
    cipher = Aes(key)
    subkey = int.from_bytes(cipher.encrypt_block(b"\x00" * BLOCK_SIZE), "big")
    if len(nonce) == 12:
        start = int.from_bytes(nonce + b"\x00\x00\x00\x01", "big")
    else:
        padded = nonce + b"\x00" * (-len(nonce) % BLOCK_SIZE)
        start = _ghash(
            subkey,
            padded + b"\x00" * 8 + (len(nonce) * 8).to_bytes(8, "big"),
        )
    return cipher, subkey, start


def _gcm_keystream(cipher, start: int, length: int) -> bytes:
    output = bytearray()
    counter = start
    while len(output) < length:
        counter = (counter & ~0xFFFFFFFF) | ((counter + 1) & 0xFFFFFFFF)
        output += cipher.encrypt_block(_counter_block(counter))
    return bytes(output[:length])


def _gcm_tag(
    cipher, subkey: int, start: int, aad: bytes, ciphertext: bytes
) -> bytes:
    padded_aad = aad + b"\x00" * (-len(aad) % BLOCK_SIZE)
    padded_text = ciphertext + b"\x00" * (-len(ciphertext) % BLOCK_SIZE)
    lengths = (len(aad) * 8).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(
        8, "big"
    )
    digest = _ghash(subkey, padded_aad + padded_text + lengths)
    mask = int.from_bytes(cipher.encrypt_block(_counter_block(start)), "big")
    return (digest ^ mask).to_bytes(BLOCK_SIZE, "big")


def encrypt_gcm(
    key: bytes, nonce: bytes, data: bytes, aad: bytes = b""
) -> bytes:
    cipher, subkey, start = _gcm_setup(key, nonce)
    keystream = _gcm_keystream(cipher, start, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, keystream))
    return ciphertext + _gcm_tag(cipher, subkey, start, aad, ciphertext)


def decrypt_gcm(
    key: bytes, nonce: bytes, data: bytes, aad: bytes = b""
) -> bytes:
    if len(data) < TAG_SIZE:
        raise AesError("ciphertext is too short to hold a tag")
    ciphertext, tag = data[:-TAG_SIZE], data[-TAG_SIZE:]
    cipher, subkey, start = _gcm_setup(key, nonce)
    expected = _gcm_tag(cipher, subkey, start, aad, ciphertext)
    if not secrets.compare_digest(tag, expected):
        raise AesError("the authentication tag does not match")
    keystream = _gcm_keystream(cipher, start, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, keystream))
