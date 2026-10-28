"""Tiny probe: enable the mouse modes given in MODES (default 1000;1002;1006), echo received bytes as repr."""
import os, sys, termios, tty
modes = os.environ.get("MODES", "1000;1002;1006").split(";")
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    for m in modes:
        sys.stdout.write("\x1b[?%sh" % m)
    sys.stdout.write("READY\r\n"); sys.stdout.flush()
    while True:
        data = os.read(fd, 1024)
        if not data or b"q" in data:
            break
        sys.stdout.write(repr(data) + "\r\n"); sys.stdout.flush()
finally:
    for m in modes:
        sys.stdout.write("\x1b[?%sl" % m)
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
