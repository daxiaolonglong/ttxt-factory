def djb2(s):
    h = 5381
    for ch in s.encode():
        h = ((h * 33) + ch) & 0xFFFFFFFF
    return h


def djb2_xor(s):
    h = 5381
    for ch in s.encode():
        h = ((h * 33) ^ ch) & 0xFFFFFFFF
    return h


def sdbm(s):
    h = 0
    for ch in s.encode():
        h = (ch + (h << 6) + (h << 16) - h) & 0xFFFFFFFF
    return h


def fnv1a(s):
    h = 0x811C9DC5
    for ch in s.encode():
        h = ((h ^ ch) * 0x01000193) & 0xFFFFFFFF
    return h


def java(s):
    h = 0
    for ch in s.encode():
        h = ((h * 31) + ch) & 0xFFFFFFFF
    return h


algos = {"djb2": djb2, "djb2_xor": djb2_xor, "sdbm": sdbm, "fnv1a": fnv1a, "java": java}
targets = {"qwen3.8-flash-next": 3306088066, "QWEN3.8": 3295152258}

for name, want in targets.items():
    print(f"--- {name} expected {want}")
    for algo, fn in algos.items():
        got = fn(name)
        print(f"    {algo:9s} {got:>12d} {'MATCH' if got == want else ''}")
