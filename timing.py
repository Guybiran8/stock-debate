"""Times every step of a report, including steps that run at the same time in other threads.

    with step("research search"):
        ...
    print_table()   # start time and length of every step, in start order
"""
import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()
_steps = []
_t0 = time.perf_counter()


def reset():
    global _t0
    with _lock:
        _steps.clear()
        _t0 = time.perf_counter()


@contextmanager
def step(label):
    start = time.perf_counter()
    try:
        yield
    finally:
        with _lock:
            _steps.append((start - _t0, time.perf_counter() - start, label))


def total():
    return time.perf_counter() - _t0


def table():
    with _lock:
        rows = sorted(_steps)
    lines = [f"{'starts at':>9}  {'takes':>7}  step"]
    lines += [f"{s:8.1f}s  {d:6.1f}s  {label}" for s, d, label in rows]
    lines.append(f"{'':9}  {total():6.1f}s  TOTAL (wall clock)")
    return "\n".join(lines)


def print_table():
    print("\nTIMING (steps that overlap ran at the same time)\n" + table())
