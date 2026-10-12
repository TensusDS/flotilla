#!/usr/bin/env bash
# bash tools/render.sh [out.png]: the scene's picture, out/khrushchevka.png by default.
#   on the rented GPU:  flotilla rig run --get out -- bash tools/render.sh
#   on this machine:    bash tools/render.sh        (the CPU: slow on a big scene)
# The rented machine runs the Playwright image (mcr.microsoft.com/playwright:v1.64.0-noble): its browsers under
# /ms-playwright, Node, no playwright-core. This installs playwright-core of that version outside the tree, in this
# user's cache, and draws through ANGLE's Vulkan backend when the machine has an NVIDIA GPU, else on the CPU
# (GPU_ANGLE overrides).
set -uo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
version=${PW_VERSION:-1.64.0}
# In a directory only this user writes: code is loaded from it, so never a shared temporary directory.
pw=${PW_PREFIX:-${XDG_CACHE_HOME:-$HOME/.cache}/khrushchevka-pw}
mkdir -p -m 700 "$pw" || { echo "cannot make $pw"; exit 2; }
if [ ! -O "$pw" ] || [ -L "$pw" ] || [ -n "$(find "$pw" -maxdepth 0 -perm /022)" ]; then
  echo "$pw is not a directory only this user writes: nothing is loaded from it"; exit 2
fi
if [ ! -d "$pw/node_modules/playwright-core" ]; then
  npm i --no-save --no-audit --no-fund --prefix "$pw" "playwright-core@$version" > "$pw/install.log" 2>&1 \
    || { echo "playwright-core@$version did not install:"; tail -20 "$pw/install.log"; exit 2; }
fi
export PLAYWRIGHT_CORE="$pw/node_modules/playwright-core"
browsers=${PLAYWRIGHT_BROWSERS_PATH:-/ms-playwright}
[ -d "$browsers" ] || browsers="$HOME/.cache/ms-playwright"
# The full Chromium, not the headless shell: the GPU process with ANGLE's backends.
if [ -z "${CHROME:-}" ]; then
  CHROME=$(ls -d "$browsers"/chromium-*/chrome-linux*/chrome 2>/dev/null | sort -V | tail -1)
fi
[ -x "${CHROME:-}" ] || { echo "no Chromium under $browsers"; exit 2; }
if [ -z "${GPU_ANGLE:-}" ]; then
  if nvidia-smi > /dev/null 2>&1; then GPU_ANGLE=vulkan; else GPU_ANGLE=swiftshader; fi
fi
export CHROME GPU_ANGLE
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/gpu: /'
exec node "$root/tools/render.mjs" "$@"
