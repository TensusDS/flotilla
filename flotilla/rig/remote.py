"""The remote half of `rig run` (rig design, section 7): the ssh command line, and the scripts the machine runs.

Every script is a constant. What varies - paths, a tag, a command's words - travels as positional arguments, each
quoted for the remote shell, so no text a session chose is ever spliced into a script. ssh reads none of the
person's configuration: a `ForwardAgent yes` there would hand their agent to root on a stranger's host.

The scripts need bash, GNU coreutils and tar, util-linux `flock` and Linux `/proc`, as the default image has.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

PROGRAM = "ssh"
BASE = "/work"
BEAT = "/root/flotilla-heartbeat"
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
OPTIONS = ("-F", "/dev/null", "-a", "-x", "-T", "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=accept-new", "-o", "ClearAllForwardings=yes", "-o", "ControlMaster=no",
           "-o", "ControlPath=none", "-o", "PermitLocalCommand=no", "-o", "UpdateHostKeys=no",
           "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4", "-o", "ConnectTimeout=20")
#: Seconds a call may take: short questions, and the ones that move a tree.
SHORT, LONG = 60, 600


def address(text: str) -> tuple[str, int]:
    """`host:port` from the journal, which any shell on this machine may write: a host that could be read as an
    option, or carry anything but a host name, is refused."""
    host, _, port = (text or "").rpartition(":")
    if not _HOST.fullmatch(host) or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"not an ssh address: {text!r}")
    return host, int(port)


def ssh_argv(address_text: str, key: Path, known_hosts: Path, remote_line: str, *, stdin: bool = False) -> list[str]:
    host, port = address(address_text)
    return [PROGRAM, *OPTIONS, *(() if stdin else ("-n",)), "-i", str(key), "-o", f"UserKnownHostsFile={known_hosts}",
            "-p", str(port), f"root@{host}", remote_line]


def line(script: str, *args) -> str:
    """One remote command line: bash runs the constant script with the arguments as $1, $2, ..."""
    return " ".join(["bash", "-c", shlex.quote(script), "flotilla", *(shlex.quote(str(a)) for a in args)])


@dataclass(frozen=True)
class Paths:
    project: str
    rev: str
    run: str
    setup_status: str
    run_status: str
    cache: str


def paths(project_slug: str, revision: str, tag: str) -> Paths:
    project = f"{BASE}/{project_slug}"
    return Paths(project, f"{project}/rev/{revision}", f"{project}/runs/{tag}", f"{project}/runs/{tag}.setup",
                 f"{project}/runs/{tag}.run", f"{BASE}/cache")


_TAGGED = r'''
tagged() {  # the pids whose environment holds exactly FLOTILLA_RUN=$1 (j1 never matches j10): one grep for all
  grep -lzxF "FLOTILLA_RUN=$1" /proc/[0-9]*/environ 2>/dev/null | sed -n 's|^/proc/\([0-9]*\)/environ$|\1|p' |
    grep -vx "$$"
  return 0
}
group_of() {  # the process group a run wrote, or nothing
  [ -f "$1" ] || return 0
  local g
  g=$(sed -n 's/^pgrp=\([0-9][0-9]*\).*/\1/p' "$1")
  [ -n "$g" ] && [ "$g" -gt 1 ] && echo "$g"
  return 0
}
'''

CHECK = r'''set -u
rev=$1
project=$(dirname "$(dirname "$rev")")
mkdir -p "$project"
exec 9> "$project/.lock"
flock 9
if [ -f "$rev/.flotilla-complete" ]; then echo present; else echo absent; fi
if tar --version 2>/dev/null | head -1 | grep -q GNU; then echo "gnu-tar yes"; else echo "gnu-tar no"; fi
'''

_SPOOL = r'''
spool() {  # $1 dir for the spool, $2 sha256, $3 size: the whole stream, checked, or exit 65
  local file got n
  file=$(mktemp "$1/.incoming.XXXXXX")
  cat > "$file"
  got=$(sha256sum < "$file" | cut -d' ' -f1)
  n=$(wc -c < "$file")
  if [ "$got" != "$2" ] || [ "$n" != "$3" ]; then rm -f "$file"; echo incomplete; exit 65; fi
  echo "$file"
}
'''

RECEIVE = r'''set -u
''' + _SPOOL + r'''
rev=$1
project=$(dirname "$(dirname "$rev")")
mkdir -p "$project/rev"
file=$(spool "$project" "$2" "$3") || exit 65
[ "$file" = incomplete ] && { echo incomplete; exit 65; }
exec 9> "$project/.lock"
flock 9
if [ -f "$rev/.flotilla-complete" ]; then rm -f "$file"; echo present; exit 0; fi
tmp="$rev.incoming.$$"
rm -rf "$tmp"
mkdir -p "$tmp"
if ! tar -x -C "$tmp" -f "$file"; then
  rm -rf "$tmp" "$file"; echo "flotilla: the archive did not unpack" >&2; exit 65
fi
rm -f "$file"
touch "$tmp/.flotilla-complete"
rm -rf "$rev"
mv -T "$tmp" "$rev"
echo received
'''

READINGS = r'''set -u
mkdir -p "$1"
mem=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
total=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
cpus=$(nproc)
if [ -r /sys/fs/cgroup/memory.max ]; then
  max=$(cat /sys/fs/cgroup/memory.max); cur=$(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo 0)
  if [ "$max" != max ]; then room=$(( (max - cur) / 1024 )); [ "$room" -lt "$mem" ] && mem=$room; fi
elif [ -r /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then
  max=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)
  cur=$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || echo 0)
  if [ "$max" -lt 1000000000000000 ]; then room=$(( (max - cur) / 1024 )); [ "$room" -lt "$mem" ] && mem=$room; fi
fi
if [ -r /sys/fs/cgroup/cpu.max ]; then
  read -r quota period < /sys/fs/cgroup/cpu.max
  if [ "$quota" != max ]; then
    capped=$(( (quota + period - 1) / period )); [ "$capped" -lt "$cpus" ] && cpus=$capped
  fi
fi
gfree=-1 gtotal=-1
if command -v nvidia-smi > /dev/null 2>&1; then
  gfree=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | awk '{s += $1} END {print s + 0}')
  gtotal=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | awk '{s += $1} END {print s + 0}')
fi
disk=$(df -Pm "$1" | awk 'NR == 2 {print $4}')
echo "mem_kb=$mem mem_total_kb=$total cpus=$cpus gpu_free_mb=$gfree gpu_total_mb=$gtotal disk_free_mb=$disk"
'''

STAGE = r'''set -u
rev=$1 run=$2
project=$(dirname "$(dirname "$rev")")
exec 9> "$project/.lock"
flock 9
[ -f "$rev/.flotilla-complete" ] || { echo "flotilla: the revision is not on the machine" >&2; exit 66; }
touch "$rev"
rm -rf "$run"
mkdir -p "$run"
cp -a "$rev/." "$run/"
rm -f "$run/.flotilla-complete"
'''

PUT = r'''set -u
''' + _SPOOL + r'''
run=$1
file=$(spool "$(dirname "$run")" "$2" "$3") || exit 65
[ "$file" = incomplete ] && { echo incomplete; exit 65; }
tmp=$(mktemp -d "$(dirname "$run")/.put.XXXXXX")
if ! tar -x --no-same-owner -C "$tmp" -f "$file"; then rm -rf "$tmp" "$file"; exit 65; fi
cp -a "$tmp/." "$run/"
rm -rf "$tmp" "$file"
'''

_SAMPLER = r'''tag=$1 out=$2 parent=$3
peak=0 gpu=0 shared=0
declare -A ticks
gpu_used() {
  nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | awk '{s += $1} END {print s + 0}'
}
base=0
command -v nvidia-smi > /dev/null 2>&1 && base=$(gpu_used)
while kill -0 "$parent" 2>/dev/null; do   # it ends with its run, however the run ends
  total=0
  found=$(grep -lzxF "FLOTILLA_RUN=$tag" /proc/[0-9]*/environ 2>/dev/null | sed -n 's|^/proc/\([0-9]*\)/environ$|\1|p')
  for pid in $found; do
    kb=$(awk '/^VmRSS:/ {print $2}' "/proc/$pid/status" 2>/dev/null)
    total=$((total + ${kb:-0}))
    line=$(< "/proc/$pid/stat") 2>/dev/null || continue
    read -r -a st <<< "${line##*) }"   # after the name, which may hold spaces ("Isolated Web Co")
    t=$(( st[11] + st[12] ))           # utime + stime: helpers that left the group count too, once each
    [ "$t" -gt "${ticks[$pid]:-0}" ] && ticks[$pid]=$t
  done
  if command -v nvidia-smi > /dev/null 2>&1; then
    used=$(( $(gpu_used) - base ))
    [ "$used" -gt "$gpu" ] && gpu=$used
  fi
  # another run on this machine makes the machine-wide GPU figure no measure of this run alone
  if grep -hzoE '^FLOTILLA_RUN=j[0-9]+-[0-9a-f]{8}$' /proc/[0-9]*/environ 2>/dev/null | tr '\0' '\n' | sort -u |
     grep -qvxF "FLOTILLA_RUN=$tag"; then shared=1; fi
  [ "$total" -gt "$peak" ] && peak=$total
  sum=0
  for t in "${ticks[@]}"; do sum=$((sum + t)); done
  echo "$peak $gpu $sum $shared" > "$out.tmp" && mv "$out.tmp" "$out"
  sleep 2
done
'''

_RUN = r'''#MARK
set -u
dir=$1 status=$2 tag=$3 cache=$4 limit=$5 count=$6
shift 6
pairs=()
for ((n = 0; n < count; n++)); do pairs+=("$1"); shift; done
cd "$dir" || { echo "flotilla: the run directory is missing" >&2; exit 70; }
rm -f "$status" "$status.tmp" "$status.peak"
for pair in ${pairs[@]+"${pairs[@]}"}; do
  name=${pair%%=*}
  [[ $name =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "flotilla: not a variable name: $name" >&2; exit 70; }
  export -- "$pair"
done
read -r -a stat < "/proc/$$/stat"
echo "pgrp=${stat[4]} started=$(date +%s)" > "$status.running"
started=$(date +%s)
env -u FLOTILLA_RUN bash -c ''' + "'" + _SAMPLER.replace("'", "'\\''") + "'" + r''' sampler "$tag" "$status.peak" "$$" &
sampler=$!
export FLOTILLA_RUN="$tag" npm_config_cache="$cache/npm" UV_CACHE_DIR="$cache/uv" PIP_CACHE_DIR="$cache/pip"
export YARN_CACHE_FOLDER="$cache/yarn"
stopped=0
timeout -k 10 "$limit" "$@" &
cmd=$!
trap 'stopped=1; kill -TERM "$cmd" 2>/dev/null' TERM
wait "$cmd" 2>/dev/null; code=$?   # bash's own job notice must not die on a connection that broke (SIGPIPE)
while kill -0 "$cmd" 2>/dev/null; do wait "$cmd" 2>/dev/null; code=$?; done
trap - TERM
kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
read -r -a stat < "/proc/$$/stat"
ticks=$(getconf CLK_TCK)
cpu=$(( (stat[15] + stat[16]) / ticks ))
peak=0 gpu=0 sampled=0 shared=0
[ -f "$status.peak" ] && read -r peak gpu sampled shared < "$status.peak"
sampled=$(( ${sampled:-0} / ticks ))
[ "$sampled" -gt "$cpu" ] && cpu=$sampled
if [ "$stopped" = 1 ]; then said=stopped
elif { [ "$code" = 124 ] || [ "$code" = 137 ]; } && [ $(( $(date +%s) - started )) -ge "$limit" ]; then said=timeout
else said=$code; fi
printf 'exit=%s seconds=%s cpu_s=%s peak_kb=%s gpu_mb=%s shared=%s\n' "$said" "$(( $(date +%s) - started ))" \
  "$cpu" "${peak:-0}" "${gpu:-0}" "${shared:-0}" > "$status.tmp"
mv "$status.tmp" "$status"
rm -f "$status.peak"
# the run is over: what it left running (a server, a browser) would hold the channel open until the ceiling
left() { grep -lzxF "FLOTILLA_RUN=$tag" /proc/[0-9]*/environ 2>/dev/null | sed -n 's|^/proc/\([0-9]*\)/environ$|\1|p'; }
for p in $(left); do kill -TERM "$p" 2>/dev/null; done
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -z "$(left)" ] && break; sleep 0.3; done
for p in $(left); do kill -KILL "$p" 2>/dev/null; done
exit "$code"
'''
RUN = _RUN.replace("#MARK", "# flotilla-run")
SETUP = _RUN.replace("#MARK", "# flotilla-setup")

STATUS = r'''set -u
''' + _TAGGED + r'''
st=$1
if [ -f "$st" ]; then cat "$st"; exit 0; fi
g=$(group_of "$st.running")
if [ -n "$g" ] && kill -0 -- "-$g" 2>/dev/null; then echo "running pgrp=$g"; exit 0; fi
echo vanished
'''

STOP = r'''set -u
''' + _TAGGED + r'''
st=$1 tag=$2
g=$(group_of "$st.running")
n=0
if [ -n "$g" ] && kill -TERM -- "-$g" 2>/dev/null; then n=$((n + 1)); fi
for p in $(tagged "$tag"); do kill -TERM "$p" 2>/dev/null && n=$((n + 1)); done
for _ in $(seq 50); do
  left=$(tagged "$tag")
  if [ -z "$left" ] && { [ -z "$g" ] || ! kill -0 -- "-$g" 2>/dev/null; }; then break; fi
  sleep 0.1
done
if [ -n "$g" ] && kill -KILL -- "-$g" 2>/dev/null; then n=$((n + 1)); fi
for p in $(tagged "$tag"); do kill -KILL "$p" 2>/dev/null && n=$((n + 1)); done
echo "stopped $n"
exit 0
'''

PACK = r'''set -u
cd "$1" || exit 70
shift
have=()
for p in "$@"; do if [ -e "./$p" ] || [ -L "./$p" ]; then have+=("./$p"); fi; done
[ ${#have[@]} -eq 0 ] && exit 0
exec tar -c -f - -- "${have[@]}"
'''

DROP = r'''set -u
run=$1
shift
rm -rf -- "$run"
for st in "$@"; do rm -f -- "$st" "$st.running" "$st.tmp" "$st.peak"; done
'''

TOUCH = r'''set -u
mkdir -p "$(dirname "$1")"
touch -- "$1"
'''

PRUNE = r'''set -u
''' + _TAGGED + r'''
project=$1 keep=$2 cache=$3 margin=$4
mkdir -p "$project/rev" "$project/runs"
exec 9> "$project/.lock"
flock 9
for item in "$project"/runs/*; do
  [ -e "$item" ] || continue
  case "$item" in *.running) continue ;; esac
  [ -n "$(find "$item" -maxdepth 0 -mtime +0 2>/dev/null)" ] || continue
  base=${item%.setup}; base=${base%.run}
  live=0
  for st in "$base.setup.running" "$base.run.running"; do
    g=$(group_of "$st")
    if [ -n "$g" ] && kill -0 -- "-$g" 2>/dev/null; then live=1; fi
  done
  [ "$live" = 1 ] && continue
  rm -rf -- "$item" "$item.running"
done
n=0
for rev in $(ls -1dt "$project"/rev/*/ 2>/dev/null); do
  n=$((n + 1))
  [ "$n" -le "$keep" ] && continue
  [ -n "$(find "$rev" -maxdepth 0 -mtime +0 2>/dev/null)" ] || continue
  rm -rf -- "$rev"
done
free=$(df -Pm "$project" | awk 'NR == 2 {print $4}')
if [ "$free" -lt "$margin" ]; then
  busy=0
  for st in "$(dirname "$project")"/*/runs/*.setup.running "$(dirname "$project")"/*/runs/*.run.running; do
    g=$(group_of "$st")
    if [ -n "$g" ] && kill -0 -- "-$g" 2>/dev/null; then busy=1; fi
  done
  if [ "$busy" = 0 ]; then rm -rf -- "$cache"; free=$(df -Pm "$project" | awk 'NR == 2 {print $4}'); fi
fi
echo "free_mb=$free"
'''
