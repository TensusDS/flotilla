from flotilla.lane import signature as sig

P = "proj"


def test_a_tree_placeholder_matches_whole_path_components():
    one = sig.ladder(["npx", "vitest", "run", "/w/twosuns-main-1/tests/a.test.ts"], tree="/w/twosuns-main-1", project=P)
    twelve = sig.ladder(["npx", "vitest", "run", "/w/twosuns-main-12/tests/a.test.ts"], tree="/w/twosuns-main-1",
                        project=P)
    assert one[0] == "exact:npx vitest run <tree>/tests/a.test.ts"
    assert twelve[0] == "exact:npx vitest run /w/twosuns-main-12/tests/a.test.ts"


def test_two_seats_running_one_command_share_its_exact_signature():
    a = sig.ladder(["npx", "vitest", "run", "tests/a.test.ts"], tree="/w/twosuns-main-3", project=P)
    b = sig.ladder(["npx", "vitest", "run", "tests/a.test.ts"], tree="/w/twosuns-minor-7", project=P)
    assert a == b


def test_normalising_drops_hashes_numbers_temporary_paths_and_redirections():
    cmd = ["node", "/home/u/.claude/jobs/2e692fab/tmp/shoot.mjs", "--port", "5091", "/tmp/out-81723", ">", "log.txt"]
    steps = sig.ladder(cmd, tree=None, project=P)
    assert steps[1] == "norm:node /home/u/.claude/jobs/<hex>/tmp/shoot.mjs --port <n> <tmp>"
    assert steps[2] == "prog:node shoot.mjs"


def test_parallelism_flags_stay_in_the_program_step():
    one = sig.ladder(["npx", "vitest", "run", "--maxWorkers=1", "tests/a.test.ts"], tree=None, project=P)
    full = sig.ladder(["npx", "vitest", "run"], tree=None, project=P)
    assert one[2] == "prog:vitest run --maxWorkers=1" and full[2] == "prog:vitest run"


def test_a_shell_wrapper_is_read_through():
    steps = sig.ladder(["sh", "-c", "cd /w/twosuns-main-9 && npx vite build"], tree="/w/twosuns-main-9", project=P)
    assert steps[0] == "exact:npx vite build" and steps[2] == "prog:vite build"


def test_the_last_step_is_the_project_and_steps_are_not_repeated():
    steps = sig.ladder(["make"], tree=None, project=P)
    assert steps[-1] == "project:proj" and len(steps) == len(set(steps))


def test_receipts_and_tiers_have_their_own_signatures():
    assert sig.receipt_ladder("handover", ["unit", "lint"], project=P) == [
        "receipt:proj:handover:lint+unit", "receipt:proj:handover", "project:proj"]
    assert sig.tier_signature("unit", project=P) == "tier:proj:unit"


def test_a_flags_value_is_not_taken_for_a_subcommand():
    assert sig.ladder(["pytest", "-k", "smoke", "tests/"], tree=None, project=P)[2] == "prog:pytest"
    # `--lf unit`: a boolean flag and a valued one look alike; the word after a bare flag is read as its value, so
    # the program step is coarser, never wrong
    assert sig.ladder(["uv", "run", "pytest", "--lf", "unit"], tree=None, project=P)[2] == "prog:pytest"
