from flotilla.lane import signature as sig

P = "proj"


def test_a_tree_placeholder_matches_whole_path_components():
    one = sig.ladder(["npx", "vitest", "run", "/w/alpha-main-1/tests/a.test.ts"], tree="/w/alpha-main-1", project=P)
    twelve = sig.ladder(["npx", "vitest", "run", "/w/alpha-main-12/tests/a.test.ts"], tree="/w/alpha-main-1",
                        project=P)
    assert one[0] == "exact:proj:npx vitest run <tree>/tests/a.test.ts"
    assert twelve[0] == "exact:proj:npx vitest run /w/alpha-main-12/tests/a.test.ts"


def test_two_seats_running_one_command_share_its_exact_signature():
    a = sig.ladder(["npx", "vitest", "run", "tests/a.test.ts"], tree="/w/alpha-main-3", project=P)
    b = sig.ladder(["npx", "vitest", "run", "tests/a.test.ts"], tree="/w/alpha-minor-7", project=P)
    assert a == b


def test_normalising_drops_hashes_numbers_temporary_paths_and_redirections():
    cmd = ["node", "/home/u/.claude/jobs/2e692fab/tmp/shoot.mjs", "--port", "5091", "/tmp/out-81723", ">", "log.txt"]
    steps = sig.ladder(cmd, tree=None, project=P)
    assert steps[1] == "norm:proj:node /home/u/.claude/jobs/<hex>/tmp/shoot.mjs --port <n> <tmp>"
    assert steps[2] == "prog:proj:node shoot.mjs"


def test_parallelism_flags_stay_in_the_program_step():
    one = sig.ladder(["npx", "vitest", "run", "--maxWorkers=1", "tests/a.test.ts"], tree=None, project=P)
    full = sig.ladder(["npx", "vitest", "run"], tree=None, project=P)
    assert one[2] == "prog:proj:vitest run --maxWorkers=1" and full[2] == "prog:proj:vitest run"


def test_a_shell_wrapper_is_read_through():
    steps = sig.ladder(["sh", "-c", "cd /w/alpha-main-9 && npx vite build"], tree="/w/alpha-main-9", project=P)
    assert steps[0] == "exact:proj:npx vite build" and steps[2] == "prog:proj:vite build"


def test_the_last_step_is_the_project_and_steps_are_not_repeated():
    steps = sig.ladder(["make"], tree=None, project=P)
    assert steps[-1] == "project:proj" and len(steps) == len(set(steps))


def test_receipts_and_tiers_have_their_own_signatures():
    assert sig.receipt_ladder("handover", ["unit", "lint"], project=P) == [
        "receipt:proj:handover:lint+unit", "receipt:proj:handover", "project:proj"]
    assert sig.tier_signature("unit", project=P) == "tier:proj:unit"


def test_a_flags_value_is_not_taken_for_a_subcommand():
    assert sig.ladder(["pytest", "-k", "smoke", "tests/"], tree=None, project=P)[2] == "prog:proj:pytest"
    # `--lf unit`: a boolean flag and a valued one look alike; the word after a bare flag is read as its value, so
    # the program step is coarser, never wrong
    assert sig.ladder(["uv", "run", "pytest", "--lf", "unit"], tree=None, project=P)[2] == "prog:proj:pytest"


def test_every_step_carries_the_project():
    """Review of stage 1: `npm test` in two repositories is two commands; without the project they shared a history."""
    a = sig.ladder(["npm", "test"], tree=None, project="alpha")
    b = sig.ladder(["npm", "test"], tree=None, project="client-repo")
    assert not set(a) & set(b)


def prog(command, tree=None):
    return next(step for step in sig.ladder(command, tree=tree, project=P) if step.startswith("prog:"))


def test_the_program_step_reads_through_the_fields_wrappers():
    """Command shapes measured on the field journal (review of stage 1): each named its wrapper, not its program."""
    assert prog(["timeout", "600", "sh", "-c", "cd /w/t-main-9 && npx vite build"]) == "prog:proj:vite build"
    assert prog(["timeout", "900", "sh", "-c", "npm test"]) == "prog:proj:npm test"
    assert prog(["bash", "-c", "set -o pipefail; cd /w/t && npx vitest run"]) == "prog:proj:vitest run"
    assert prog(["timeout", "60", "env", "NODE_OPTIONS=--max-old-space-size=3072", "npx", "vitest", "run"]) == \
        "prog:proj:vitest run"
    assert prog(["sh", "-c", "NODE_OPTIONS=--max-old-space-size=3072 npx vitest run --maxWorkers=1"]) == \
        "prog:proj:vitest run --maxWorkers=1"
    assert prog(["uv", "run", "--with", "pytest", "python", "-m", "pytest", "-q"]) == "prog:proj:pytest"
    assert prog(["sh", "-c", "npx tsc --noEmit && npx vitest run"]) == "prog:proj:vitest run"


def test_a_build_and_a_test_suite_never_share_the_program_step():
    build = prog(["timeout", "600", "sh", "-c", "npx vite build"])
    suite = prog(["timeout", "600", "sh", "-c", "npx vitest run"])
    assert build != suite


def test_parallelism_values_stay_apart():
    assert prog(["pytest", "-n", "4"]) != prog(["pytest", "-n", "8"])
    sixteen = sig.ladder(["npx", "vitest", "run", "--maxWorkers=16"], tree=None, project=P)
    many = sig.ladder(["npx", "vitest", "run", "--maxWorkers=32"], tree=None, project=P)
    assert sixteen[1] != many[1]


def test_a_very_long_command_is_cut_before_it_is_parsed():
    long = ["sh", "-c", "cd x && " * 20000 + "make"]
    steps = sig.ladder(long, tree=None, project=P)
    assert all(len(step) <= sig.MAX_TEXT + 40 for step in steps)


def test_redirects_pipes_and_trailing_trivia_inside_a_shell_string():
    assert prog(["sh", "-c", "npx vitest run > /tmp/o.log 2>&1"]) == "prog:proj:vitest run"
    assert prog(["sh", "-c", "node shoot.mjs 2>&1 | tail -5"]) == "prog:proj:node shoot.mjs"
    assert prog(["sh", "-c", "cd /w/x && npm run build && echo ok"]) == "prog:proj:npm run build"
    assert prog(["npm", "run", "build"]) == "prog:proj:npm run build"
    exact = sig.ladder(["sh", "-c", "npx vitest run > /tmp/o.log 2>&1"], tree=None, project=P)[0]
    assert exact == "exact:proj:npx vitest run"
    assert prog(["sh", "-c", "node gen.mjs | python3 check.py"]) == "prog:proj:node gen.mjs"   # the first stage


def test_a_shell_running_a_script_is_named_by_its_script():   # field test 2026-11-11: every `sh <script>` was `sh`
    assert prog(["sh", "/home/u/jobs/6cd5d245/tmp/probe79.sh"]) == "prog:proj:sh probe79.sh"
    assert prog(["bash", "./ci/run.sh"]) == "prog:proj:bash run.sh"
    assert prog(["bash", "-e", "-o", "pipefail", "build.sh"]) == "prog:proj:bash build.sh"   # `-o` takes a value
    assert prog(["timeout", "600", "bash", "-x", "check.sh", "fast"]) == "prog:proj:bash check.sh fast"
    assert prog(["sh", "big.sh"]) != prog(["sh", "small.sh"])


def test_a_shell_with_no_script_keeps_its_name():
    assert prog(["sh"]) == "prog:proj:sh"
    assert prog(["bash", "-s", "fast"]) == "prog:proj:bash"           # the script comes from stdin: `fast` is $1
    assert prog(["bash", "-es"]) == "prog:proj:bash"
    assert prog(["bash", "-c", "npx vitest run"]) == "prog:proj:vitest run"   # `-c` is still read through


def test_a_shells_option_cluster_ending_in_o_takes_its_value():   # review of 0.31.3: `-euo pipefail` is no script
    assert prog(["bash", "-euo", "pipefail", "a.sh"]) == "prog:proj:bash a.sh"
    assert prog(["bash", "-euo", "pipefail", "a.sh"]) != prog(["bash", "-euo", "pipefail", "b.sh"])
    assert prog(["sh", "-c", "bash -eO extglob ./build.sh"]) == "prog:proj:bash build.sh"
    assert prog(["ksh", "x.sh"]) == "prog:proj:ksh x.sh"


def test_a_script_in_a_temporary_folder_keeps_its_name_at_the_normalised_step():   # review of 0.31.3
    norm = lambda command: sig.ladder(command, tree=None, project=P)[1]   # noqa: E731
    assert norm(["sh", "/tmp/probe79.sh"]) == "norm:proj:sh <tmp>/probe<n>.sh"
    assert norm(["sh", "/tmp/probe79.sh"]) == norm(["sh", "/tmp/probe80.sh"])   # one script, numbered per run
    assert norm(["sh", "/tmp/probe79.sh"]) != norm(["sh", "/tmp/build-all.sh"])
    assert norm(["python3", "/var/tmp/x/check.py"]) == "norm:proj:python3 <tmp>/check.py"
    assert norm(["pytest", "--basetemp", "/tmp/pytest-of-u/7"]) == "norm:proj:pytest --basetemp <tmp>"   # a file


def test_every_o_in_a_cluster_takes_a_value():   # review of 0.31.3: bash reads `-oe pipefail` as `-o pipefail -e`
    assert prog(["bash", "-oe", "pipefail", "x.sh"]) == "prog:proj:bash x.sh"
    assert prog(["bash", "-oO", "pipefail", "extglob", "x.sh"]) == "prog:proj:bash x.sh"


def test_a_random_temporary_script_name_is_one_signature():   # review of 0.31.3: tempfile and mktemp names
    norm = lambda command: sig.ladder(command, tree=None, project=P)[1]   # noqa: E731
    assert norm(["python3", "/tmp/tmpk3j_9x2a.py"]) == norm(["python3", "/tmp/tmpq8z0w1rt.py"]) == \
        "norm:proj:python3 <tmp>/<rand>.py"
    assert norm(["sh", "/tmp/tmp.Xa9bC2dEfG.sh"]) == "norm:proj:sh <tmp>/<rand>.sh"
    assert norm(["sh", "/tmp/A.SH"]) == "norm:proj:sh <tmp>/A.SH" and norm(["fish", "/tmp/x.fish"]).endswith("x.fish")
    assert norm(["sh", "/tmp/tmpfiles-cleanup.sh"]) == "norm:proj:sh <tmp>/tmpfiles-cleanup.sh"   # a name, not random
