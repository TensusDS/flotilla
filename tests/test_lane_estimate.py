import datetime as dt

from flotilla.lane import book, estimate as est

NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc)


def done(i, ladder, *, seconds=10.0, peak=500, cores=2.0, busy=0.5, verdict="green", days_ago=1, cut=False,
         ran=None, project="p"):
    at = (NOW - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
    return book.Booking(id=f"b{i}", state=book.RELEASED, ended=at, ladder=list(ladder), project=project,
                        seconds=None if cut else seconds, peak_mb=None if cut else peak,
                        cores=None if cut else cores, busy=None if cut else busy, verdict="" if cut else verdict,
                        cut=cut, ran=ran or [])


LADDER = ["exact:npx vitest run", "prog:vitest run", "project:p"]


def bookings(*items):
    return {item.id: item for item in items}


def test_the_most_exact_step_with_three_runs_answers():
    hist = est.history(bookings(*[done(i, LADDER, seconds=s) for i, s in enumerate([30, 40, 50])]), now=NOW)
    e = est.estimate(LADDER, hist, project="p")
    assert (e.seconds, e.cores, e.peak_mb) == (40.0, 2.0, 500) and "3 runs" in e.source and "exact" in e.source


def test_two_runs_fall_through_to_a_coarser_step():
    other = ["exact:npx vitest run b", "prog:vitest run", "project:p"]
    hist = est.history(bookings(done(1, LADDER), done(2, LADDER), done(3, other), done(4, other)), now=NOW)
    assert "prog" in est.estimate(LADDER, hist, project="p").source


def test_duration_is_the_median_of_green_runs_only():
    runs = [done(1, LADDER, seconds=40), done(2, LADDER, seconds=42), done(3, LADDER, seconds=44),
            done(4, LADDER, seconds=2, verdict="red"), done(5, LADDER, seconds=1, verdict="killed")]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").seconds == 42.0


def test_a_run_stopped_at_the_ceiling_counts_as_at_least_its_time():
    runs = [done(1, LADDER, seconds=10), done(2, LADDER, seconds=12), done(3, LADDER, seconds=600, verdict="ceiling")]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").seconds == 12.0
    runs += [done(4, LADDER, seconds=600, verdict="ceiling"), done(5, LADDER, seconds=600, verdict="ceiling")]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").seconds == 600.0


def test_cores_come_from_unsaturated_runs_and_take_the_maximum():
    runs = [done(1, LADDER, cores=6.8, busy=0.6), done(2, LADDER, cores=3.1, busy=0.98), done(3, LADDER, cores=5.0,
                                                                                           busy=0.4)]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").cores == 6.8


def test_unknown_busy_counts_as_unsaturated():
    runs = [done(i, LADDER, cores=2.5, busy=None) for i in range(3)]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").cores == 2.5


def test_only_the_last_ten_within_thirty_days_count():
    old = [done(i, LADDER, seconds=500, days_ago=40) for i in range(5)]
    new = [done(10 + i, LADDER, seconds=10 + i, days_ago=1) for i in range(12)]
    e = est.estimate(LADDER, est.history(bookings(*old, *new), now=NOW), project="p")
    assert e.seconds == 16.5   # median of 12..21 - the last ten


def test_cut_runs_feed_no_estimate():
    runs = [done(i, LADDER, cut=True) for i in range(5)]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p") == est.PRIOR


def test_a_new_command_takes_the_projects_ninetieth_percentile():
    runs = [done(i, [f"exact:cmd {i}", "project:p"], cores=float(i), peak=100 * i) for i in range(1, 11)]
    e = est.estimate(["exact:new", "project:p"], est.history(bookings(*runs), now=NOW), project="p")
    assert (e.cores, e.peak_mb, e.seconds) == (9.0, 900, None) and "project prior" in e.source


def test_a_project_with_no_runs_gets_the_fixed_prior():
    assert est.estimate(["exact:new", "project:q"], {}, project="q") == est.PRIOR


def test_a_receipt_sums_the_tiers_it_will_run():
    ran = [{"name": "unit", "status": "green", "seconds": 40.0, "peak_mb": 1700, "cores": 6.9, "busy": 0.5},
           {"name": "lint", "status": "green", "seconds": 5.0, "peak_mb": 300, "cores": 1.0, "busy": 0.5}]
    runs = [done(i, ["receipt:p:handover:lint+unit", "project:p"], ran=ran) for i in range(3)]
    hist = est.history(bookings(*runs), now=NOW)
    both = est.receipt_estimate(["unit", "lint"], hist, project="p")
    assert (both.seconds, both.peak_mb, both.cores) == (45.0, 1700, 6.9)
    assert est.receipt_estimate(["lint"], hist, project="p").seconds == 5.0


def test_a_saturated_runs_cores_are_left_out_even_when_larger():
    runs = [done(1, LADDER, cores=2.0, busy=0.5), done(2, LADDER, cores=2.5, busy=0.6),
            done(3, LADDER, cores=9.0, busy=0.99)]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").cores == 2.5


def test_runs_older_than_thirty_days_are_left_out_even_when_few():
    runs = [done(1, LADDER, seconds=500, days_ago=40), done(2, LADDER, seconds=10), done(3, LADDER, seconds=12)]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p") == est.PRIOR


def test_a_cut_booking_never_feeds_history_even_if_it_carries_figures():
    at = (NOW - dt.timedelta(days=1)).isoformat(timespec="seconds")
    cut = [book.Booking(id=f"c{i}", state=book.RELEASED, ended=at, ladder=list(LADDER), project="p", seconds=99.0,
                        verdict="green", cut=True) for i in range(3)]
    assert est.history(bookings(*cut), now=NOW) == {}
