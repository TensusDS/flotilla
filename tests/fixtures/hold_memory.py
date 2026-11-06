"""A stand-in for a test run that holds memory: writes N MB (argv[1]), says "ready", then keeps touching every
page until it is stopped - so a freeze shows as its resident memory leaving, and a thaw as it coming back."""
import sys
import time

SIZE = int(sys.argv[1]) * 1024 * 1024
PAGE = 4096
block = bytearray(SIZE)
for i in range(0, SIZE, PAGE):
    block[i] = 1
print("ready", flush=True)
while True:
    total = 0
    for i in range(0, SIZE, PAGE):
        total += block[i]
    time.sleep(0.2)
