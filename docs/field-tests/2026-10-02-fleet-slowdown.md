# Why a fleet slows down after its first hours (measured 2026-10-02)

The person's observation: a fleet starts brisk, works well for three or four hours, and then delivery slows a lot.
Two explanations were on the table - the sessions' contexts filling up, or the project growing so each change takes
more reading. Neither was measured. This is the measurement, over the twosuns fleet (2026-09-29 to 2026-10-02).

## What was read

- 70 seat transcripts (`~/.claude/projects/-home-max-workspace-twosuns/*.jsonl`): every assistant message's usage
  (input + cache read + cache creation = the context the turn read) and the seconds from the record before it.
- The twosuns ledger (1777 moves): 88 rows with both a claim and a hand; claim, hand, accept and ship times; base
  and tip, for the diff size.
- The machine's lane journal (2557 records): when each booking started waiting and when it was held.

## 1. The model slows with context - about twofold

| context the turn read | median turn | seconds per 1000 output tokens |
|---|---|---|
| under 100k | 2.4 s | 9.2 |
| 200-400k | 3.7 s | 9.5 |
| 600-800k | 5.1 s | 12.1 |
| 800k and over | 4.7 s | 12.9 |

Seats live long: the owner's median context when claiming a task grew from about 150k in the first hours to
400-670k later. Only three compactions happened, each at about 968k. A turn at 800k also re-reads 800k cached tokens,
which is a cost, not only a delay.

## 2. The pipeline slows with the queue - about tenfold

Medians, in minutes, by the fleet's age:

| hours | claim to hand | hand to accept (review) | accept to ship (delivery) | lane wait, machine-wide |
|---|---|---|---|---|
| 0-4 | 9 | 4 | 2.5 | 0 |
| 24-28 | 42 | 49 | 42 | 5 |
| 44-48 | 122 | 130 | 25 | 14 |
| 48-56 | about 60 | about 40 | 37-50 | 10-17 |

Almost all of each later claim-to-hand window had one of the owner's runs waiting in the lane queue; review and
delivery wait for the same lane, since readers and the sender run tiers too. The lane is one per machine, shared by
every project's fleet: on 2026-10-02 a looping worldcore test held it 28 minutes while twosuns' push receipt waited.

**Caveat.** "Lane wait" counts the time a seat had a booking waiting; a seat may work while its run waits, so the
split between work and wait is rough. It locates the bottleneck; it does not divide the time exactly.

## What follows (flotilla 0.6.2)

- Nothing is tested twice: a tier green over the same files is reused, and a receipt with nothing to run does not
  queue for the lane (decision 201).
- A run in the lane has a ceiling and can be stopped by its holder (decision 202).
- A fresh tree is set up once, so a receipt does not fail for a missing `node_modules` (decision 203).
- Not yet done: replacing a seat after N tasks or past a context size, which would buy the twofold of part 1; it is
  worth measuring again once the lane fixes are in.
