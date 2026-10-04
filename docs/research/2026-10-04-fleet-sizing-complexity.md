# Sizing a flotilla fleet from code complexity, churn and coupling — research notes

> Researched 2026-10-04 by a delegated research session; re-checked in the session that commissioned it:
> Nagappan & Ball 2005's 89.0% (relative churn discriminating fault-prone binaries, Windows Server 2003) was
> confirmed against the paper's record; the `git log` timing was re-measured on this repository (477 commits,
> 90 days: `--name-only` 0.31 s, `--numstat` 0.72 s - the same direction as the ai-os measurement below). Items the
> report itself marks unverified stay so. Every threshold in section 5 is a heuristic until flotilla's own conflict
> and return rates calibrate it.

Date: 2026-10-04. Scope: what the literature and tools say, and what flotilla (Python stdlib + `git`, Linux/macOS, any project language) can compute cheaply for `/flotilla:spawn`. Every claim carries a source tag `[n]` (list at the end). **Unverified** marks what I could not confirm in a primary source.

---

## 1. Established complexity measures

**Cyclomatic complexity (McCabe 1976)** — number of linearly independent paths through a function's control-flow graph, `V(G) = E − N + 2P`; in practice 1 + number of decision points [1][2]. The usual ceiling is 10 per function (NIST SP 500-235, Watson & McCabe 1996) [2]. Its weakness is that it barely adds anything over size: Herraiz & Hassan (2010) found all the complexity measures they studied to be highly correlated with lines of code [3]. Jay et al. (2009) report a stable, near-linear CC–LOC relationship across languages [3b].

**Cognitive complexity (Campbell, SonarSource, 2017)** — increments for breaks in linear flow (`if`, loops, `catch`, jumps, boolean-operator sequences), plus an extra increment per nesting level. It was designed to fix CC's blindness to nesting [4]. Muñoz Barón, Wyrich & Wagner (2020) ran a meta-analysis over ~24,000 understandability ratings of 427 snippets. Cognitive complexity correlated positively with comprehension time and with subjective difficulty. The results for correctness of comprehension and for physiological measures were mixed [5].

**Halstead measures (Halstead 1977)** — counts of distinct and total operators and operands (η1, η2, N1, N2). From them: vocabulary, length, volume `V = N·log2 η`, difficulty and effort [6]. Each language needs its own tokenizer and its own definition of an "operator".

**Maintainability Index (Oman & Hagemeister 1992)** — `171 − 5.2·ln(HV) − 0.23·CC − 16.2·ln(LOC) [+ a comment-percentage term]` (the exact form of the comment term differs between sources — unverified). Visual Studio rescales it to 0–100 [7]. Van Deursen (2014) gives four criticisms [8]:
- the coefficients were curve-fitted on small C/Pascal programs from the late 1980s;
- every input is confounded with size;
- averaging hides high-risk parts;
- nobody has recalibrated it since.

**Not recommended.**

**Indentation complexity (Hindle, Godfrey & Holt 2008; Tornhill)** — statistical moments (sum, mean, standard deviation, max) of leading-whitespace depth per line. These are linearly and rank-correlated with classical complexity metrics, the measure is language-independent, and it works on diffs [9]. CodeScene counts logical indentations, translating tabs to spaces and stripping blank and comment lines. Its own documentation names the weaknesses [10]:
- it is sensitive to changes of style;
- it is blind to dense constructs such as comprehensions and streams;
- it is "a heuristic – not an absolute truth".

**Code churn and relative churn (Nagappan & Ball 2005, Windows Server 2003)** — churned LOC, files churned and churn duration. Absolute churn is a poor predictor. Churn normalised by size (churned LOC / total LOC, files churned / file count, …) is highly predictive of defect density, and discriminated fault-prone binaries with 89% accuracy [11]. Graves et al. (2000) found that the number of past changes predicts faults better than module length. In their best model the fault potential is a sum over past changes, with large and recent changes weighted most [12].

**Change entropy (Hassan 2009)** — Shannon entropy of how a period's changes scatter across files. Scattered change predicts faults better than prior-fault and prior-modification counts [13]. Kamei et al. (2013) built just-in-time defect prediction from 14 change-level VCS metrics (subsystems, directories and files touched, lines added and deleted, entropy, developer experience, …). With 20% of inspection effort it found up to 35% of defect-inducing changes [14].

**Hotspots = complexity × change frequency (Tornhill, *Your Code as a Crime Scene*, 2015; CodeScene)** — CodeScene's default hotspot uses LOC as the complexity proxy and commit count as the effort proxy, citing research that "change alone" is the strongest quality signal [15]. Tornhill & Borg (2022) studied 39 proprietary codebases and 30,737 files. Low-"Code Health" files had 15× more defects, 124% longer issue resolution and 9× longer maximum cycle times [16]. Caveat: the study is written by the vendor, and Code Health is CodeScene's own composite metric, not a raw hotspot score.

**Change, logical or evolutionary coupling (co-change)** — Gall, Hajek & Jazayeri (1998) introduced logical coupling from release history [17]. Zimmermann et al. (2004/2005, ROSE) mined association rules from co-changes: after one change, ROSE correctly predicted 26% of further files to change [18]. D'Ambros, Lanza & Robbes (2009) found change coupling correlated with defects, more strongly for severe ones [19]. In code-maat the degree of coupling is shared revisions divided by average revisions. Its defaults are `min-revs 5`, `min-shared-revs 5`, `min-coupling 30%`, and commits touching more than 30 files are dropped (`max-changeset-size 30`) [20].

**Ownership (Bird et al. 2011)** — on Windows Vista and 7, the number of low-expertise contributors and the top owner's share of commits were related to both pre-release faults and post-release failures [21].

**Truck factor (Avelino et al. 2016)** — the minimal number of developers whose departure would leave a project unmaintained, computed from "degree of authorship" in the VCS. In 65% of 133 popular GitHub projects it was ≤ 2 [22].

**Process versus product metrics.** Majumder, Mody & Menzies (2022) re-ran the comparison on 700 GitHub projects and 722k commits. Process-metric models reached 98% recall against 44% for product (static) metrics [23]. Moser et al. (2008) earlier reported that change metrics beat static code attributes on Eclipse (title-level claim; abstract not re-read — **unverified detail**) [24].

**Takeaway.** History-based signals (churn, change frequency, entropy, coupling) predict defects and effort at least as well as static complexity, and usually better. Static complexity is largely a function of size [3][23][12].

---

## 2. Computability with stdlib + git

| Measure | Language-agnostic? | stdlib + git only? | Usual tools (flotilla cannot depend on them) |
|---|---|---|---|
| LOC / file count | yes | yes (`git ls-files`, read files; or blob sizes via `git ls-tree -r -l HEAD`) | cloc (GPL-2.0) [25], scc (MIT) [26] |
| Cyclomatic | no (a parser per language) | **only for Python**, via `ast` (count `If/For/While/Try/BoolOp/comprehension`…) | lizard (MIT), radon (MIT, Python) [27] |
| Cognitive | no | Python only via `ast` (nesting-aware walk) | SonarQube |
| Halstead / MI | no | Python only (`tokenize` + `ast`) | radon |
| Indentation complexity | **yes** | **yes** — read lines, expand tabs, skip blank lines | CodeScene, code-maat (no) |
| Churn, relative churn | yes | yes — `git log --numstat` | code-maat (GPL-3.0) [20] |
| Change frequency / hotspots | yes | yes — `git log --name-only` × indentation | CodeScene (proprietary), code-maat |
| Change coupling | yes | yes — `git log --name-only`, pair counting | code-maat, CodeScene |
| Change entropy | yes | yes | — |
| Ownership / truck factor | yes | yes — `git log --format=%aN --name-only` | Truck-Factor (aserg-ufmg) [22] |

**Measured cost on one repository.** I ran this on the `ai-os` umbrella repository with git 2.53 on this machine, over the last 90 days (4,198 non-merge commits, 1,173 tracked files):
- `git log --since=90.days --no-merges --no-renames --name-only` took **0.12 s**;
- the same command with `--numstat` took **6.5 s**, because numstat has to diff blobs.

So the co-change and frequency signals should use `--name-only`. Line-level churn should be optional, or sampled. **Default `git log` prints no file list for merge commits** (I checked locally on git 2.53), so squash/merge style changes what is counted (see §6).

---

## 3. Parallel development: what predicts conflicts and coordination cost

- **Parallel work on the same component causes quality problems.** In a large Lucent system, the degree of parallelism was far higher than tool builders assumed, the distributions had long tails, and parallel work on a component correlated significantly with its quality problems (Perry, Siy & Votta 2001) [28].
- **About 1 in 5 merges conflict.** In 143 open-source projects, 75% of those conflicts needed reasoning about program logic to resolve. Code involved in a conflict was 2× more likely to carry a bug, and 26× more likely when the conflict was resolved by hand (Brindescu et al. 2020) [29].
- **What predicts a conflict.** Over 182,273 merge scenarios in 80 projects, the strongest predictors were the number of committers, the number of commits and the number of changed files — especially on the branch being integrated. Branch duration, and the overlap of developers between branches, also correlated with conflicts. Changes that were large, long-lived or crossed layers were conflict-prone (Dias, Borba & Barreto 2020) [30].
- **Lightweight git features predict *safe* merges well and conflicting ones poorly.** On 267,657 merge scenarios, F1 was 0.95–0.97 for safe merges and only 0.57–0.68 for conflicting ones (Owhadi-Kareshk, Nadi & Rubin 2019). That makes them a filter, not an oracle [31].
- **Logical (co-change) dependencies drive coordination needs, more than call or data dependencies.** When developers' coordination matched those needs ("socio-technical congruence"), modification-request resolution time fell by 32% on average (Cataldo, Herbsleb & Carley, ESEM 2008) [32].
- **Scheduling tasks to avoid overlapping files prevents most conflicts.** Cassandra encodes likely conflicts as constraints and orders the tasks; it would have avoided a majority of conflicts in four OSS projects (Kasi & Sarma 2013) [33].
- **How many people can coordinate informally.** Mockus, Fielding & Herbsleb (2002), Hypothesis 1a: a core that coordinates informally will be "no larger than 10-15 people". Hypothesis 2a: beyond that, "explicit development processes, individual or group code ownership, and required inspections" are needed. Mozilla handled this with module owners [34].

**What this means for flotilla.** The number of authors who can work in parallel without stepping on each other is bounded by the number of **disjoint co-change areas** with pending work. It is not bounded by repository size. The cheapest proxy is clustering recently changed files by co-change. This proxy is evidence-backed: logical coupling predicts coordination needs [32], and file overlap, change size and branch lifetime predict conflicts [29][30]. Flotilla already has the "inspections" and the single merge point that Mockus's Hypothesis 2a calls for. The 10–15 figure is a human-team hypothesis and should **not** be carried over to LLM sessions — any equivalent is **unverified**.

---

## 4. Review capacity

- **Inspection rate and size.** SmartBear's Cisco study (a vendor study, not peer-reviewed): review 200–400 LOC at a time; defect density drops sharply above ~500 LOC/hour; 200–400 LOC over 60–90 minutes should find 70–90% of defects [35][36]. Secondary sources date it to 2006 with 2,500 reviews and 3.2 M LOC (**unverified** in the primary source; the PDF text could not be fully extracted).
- **Change size in modern review.** Median changes are 11–32 lines in OSS and at Google, and up to 263 lines in some Microsoft projects. Google's median time to first feedback is under 1 h for small changes and ~5 h for very large ones. Other companies take 14.7–19.8 h to approval (Sadowski et al. 2018) [37]. Rigby & Bird (2013) found review parameters (interval, number of reviewers, ~2 reviewers per change) converging across Google, Microsoft, AMD and OSS projects [38] (the reviewer count comes from my memory of the paper — **unverified**).
- **Larger changes get less useful review.** The more files in a change, the lower the share of useful review comments (Bosu, Greiler & Bird 2015, 1.5 M comments) [39].
- **Review is mostly about understanding.** It is less about defects than expected, and more about understanding, knowledge transfer and alternatives (Bacchelli & Bird 2013) [40].
- **Low review coverage and participation lead to more defects.** Components with low coverage carried up to 2 more post-release defects, and with low participation up to 5 more (McIntosh et al. 2014) [41].

**What this means for flotilla.** The human figures (LOC/hour) do not transfer to LLM reviewers. What does transfer:
- review cost grows with the number of files and the complexity of a change;
- quality drops when review is spread thin.

Size reviewers from flotilla's **own measured** review durations (the time from `handed` to verdict in the ledger). Do not use LOC/hour.

---

## 5. Proposed minimal signal set

Window: last 90 days, falling back to the last 500 commits if 90 days hold fewer than 50 commits. Exclude merges and bots, and drop commits touching more than 30 files (code-maat convention [20]). Target cost: under 5 s at 10^5 files / 10^4 commits.

| # | Signal | Recipe | Maps to | Basis |
|---|---|---|---|---|
| S1 | **Code volume** | `git ls-tree -r -l HEAD` → sum the blob sizes of text files after the §6 exclusions; LOC ≈ bytes/40, or read the files when there are fewer than 2×10^4 | worktree disk cost; context, not fleet size | heuristic |
| S2 | **Independent areas K** | `git log --since=90.days --no-merges --no-renames --name-only --format=%x00%H%x09%aE`. Pairs with ≥ 3 shared commits and degree ≥ 30% are edges. Union-find gives components. Collapse to directory depth 2. **K_eff = exp(Shannon entropy of commits per component)** | **max parallel authors = min(K_eff, …)** | coupling → coordination [32]; overlap → conflicts [29][30]; entropy form is a heuristic after [13] |
| S3 | **Hotspot concentration** | Per file: commits in the window × indentation complexity (sum of `(len(line) − len(line.lstrip())) / indent_unit` over non-blank lines, tabs expanded to 4). H = the top 5% of files' share of total hotspot score | **reviewer weight**: H > 0.5 → +1 reviewer, or a deeper (stronger-model) review | hotspot evidence [15][16][11][12]; threshold is a heuristic |
| S4 | **Verification cost** | Measured tier duration T and peak memory M (flotilla already has both); free memory F; per-session memory S, measured from running sessions | concurrent full runs R = floor((F − n·S) / M). Lane throughput ≈ 3600/T verifications per hour. **Cap authors so that the expected handovers per hour ≤ R·3600/T** | queueing arithmetic, not literature; memory contention is a measured local precedent (two full `curve-no` suites do not fit on one 16 GB machine) |
| S5 | **Disk** | Worktree size W = S1 bytes + build-artefact size, measured once; free disk D | authors + reviewers ≤ floor(0.5·D / W) | heuristic |
| S6 | **Backlog demand** | Open items B, split by size label (main/minor) | authors ≤ B; main posts ≤ number of substantial items | trivial |
| S7 | **Review load** | Mean files per change and mean review duration from flotilla's ledger | reviewers = ceil(authors × handovers per author-hour × review hours) | review cost grows with files [39]; coverage matters [41] |
| S8 | **Judge** | Does a runnable build or deploy target exist (a manifest or start script)? | judge yes/no | not complexity-based |

**Recommendation rule (heuristic):**

`authors = min(K_eff, B, R-cap (S4), disk-cap (S5), memory-cap (S4))`

Floor it at 1. Split main/minor by the backlog mix. Set `reviewers = max(1, ceil(S7))`, plus S3 weight. Keep one sender and one orchestrator always.

Print every term and say which cap is binding. "4 authors — capped by memory (two full test runs fit)" is actionable; a bare "4" is not.

**Evidence-backed versus heuristic.** Directionally backed: coupling and overlap limit parallelism, and history signals predict effort. Every threshold (90 days, 30%, ≥ 3 shared commits, the 5% top share, 0.5 disk factor) is a heuristic, to be calibrated against flotilla's own conflict and return rates.

---

## 6. Pitfalls

- **Generated and vendored files** inflate volume and coupling. Honour `.gitattributes` `linguist-generated` / `linguist-vendored` (`git check-attr`) [42]. Add path heuristics: `vendor/`, `node_modules/`, `dist/`, `build/`, `*.min.*`, lockfiles, snapshots, golden files. Lockfiles co-change with everything and create a giant false cluster.
- **Binary files** show `-\t-` in `--numstat` [43]. Skip them in churn and complexity, but keep them for co-change if they are real assets.
- **Renames.** `diff.renames` defaults to true for porcelain `git log` [43], and rename detection is what makes `--numstat` slow. Use `--no-renames` for speed and accept that a renamed file splits into two histories, or map renames once with `-M` on a sampled set.
- **Squash-merged history** turns a branch into one large commit, which inflates co-change. The `max-changeset-size` cutoff mitigates this. **Merge-based history** hides diffs on merges by default (checked locally); `--first-parent` gives the trunk view [44]. Detect the style (merge-commit ratio) and report it.
- **Bots** (dependabot, renovate, CI committers) add volume without parallel human work. Filter authors matching `[bot]` or `bot@`, and authors whose commits all have the same templated message. BIMAN detects bots from message templates, file associations and names [45].
- **Monorepos.** K_eff is the point of the exercise here, but run it per top-level package. Dependency co-changes across packages (version bumps) create spurious edges, so drop config and manifest files from the pair counting.
- **Shallow clones**: `git log` stops at the shallow boundary [46]. Check `git rev-parse --is-shallow-repository`, report "history truncated", and fall back to S1/S4/S6.
- **Very young repositories** (fewer than ~50 commits): coupling is noise (code-maat requires ≥ 5 revisions per file [20]). Fall back to top-level directory count as K, and say so.
- **Formatting churn** (a reformat commit) breaks indentation trends and churn [10]. Ignore commits whose file count exceeds the cutoff, or commits whose message matches `format|lint|prettier|black`.
- **Mining in general**: a repository is not a project, and history may live elsewhere (Kalliamvakou et al. 2014) [47].

---

## Sources

1. McCabe, T. J. (1976). A Complexity Measure. *IEEE TSE* SE-2(4). https://doi.org/10.1109/TSE.1976.233837
2. Cyclomatic complexity (formula; NIST SP 500-235 limit of 10). https://en.wikipedia.org/wiki/Cyclomatic_complexity
3. Herraiz, I. & Hassan, A. E. (2010). Beyond Lines of Code: Do We Need More Complexity Metrics? In *Making Software*, O'Reilly. https://www.oreilly.com/library/view/making-software/9780596808310/ch08.html
3b. Jay, G. et al. (2009). Cyclomatic Complexity and Lines of Code: Empirical Evidence of a Stable Linear Relationship. *JSEA*. https://www.researchgate.net/publication/220204439
4. Campbell, G. A. (2017/2023). Cognitive Complexity white paper, SonarSource. https://www.sonarsource.com/docs/CognitiveComplexity.pdf
5. Muñoz Barón, M., Wyrich, M., Wagner, S. (2020). An Empirical Validation of Cognitive Complexity… ESEM. https://arxiv.org/abs/2007.12520
6. Halstead, M. H. (1977). *Elements of Software Science*. Elsevier. Summary: https://en.wikipedia.org/wiki/Halstead_complexity_measures
7. Maintainability Index formula and VS rescaling. https://www.sourcery.ai/blog/maintainability-index
8. van Deursen, A. (2014). Think Twice Before Using the "Maintainability Index". https://avandeursen.com/2014/08/29/think-twice-before-using-the-maintainability-index/
9. Hindle, A., Godfrey, M. W., Holt, R. C. (2008). Reading Beside the Lines: Indentation as a Proxy for Complexity Metrics. ICPC. https://softwareprocess.es/homepage/papers/2008-abram2008icpc08rbtliaapfcm/
10. CodeScene docs — Complexity Trends. https://codescene.io/docs/guides/technical/complexity-trends.html
11. Nagappan, N. & Ball, T. (2005). Use of Relative Code Churn Measures to Predict System Defect Density. ICSE. https://dl.acm.org/doi/10.1145/1062455.1062514
12. Graves, T. L., Karr, A. F., Marron, J. S., Siy, H. (2000). Predicting Fault Incidence Using Software Change History. *IEEE TSE* 26(7). https://www.niss.org/publications/predicting-fault-incidence-using-software-change-history
13. Hassan, A. E. (2009). Predicting Faults Using the Complexity of Code Changes. ICSE. https://sailresearch.github.io/sail-website/data/pdfs/ICSE2009_PredictingFaultsUsingTheComplexityOfCodeChanges.pdf
14. Kamei, Y. et al. (2013). A Large-Scale Empirical Study of Just-in-Time Quality Assurance. *IEEE TSE*. https://posl.ait.kyushu-u.ac.jp/~kamei/publications/Kamei_TSE2013.pdf
15. CodeScene docs — Hotspots; Tornhill, A. (2015). *Your Code as a Crime Scene*, Pragmatic Bookshelf. https://docs.enterprise.codescene.io/versions/4.0.16/guides/technical/hotspots.html
16. Tornhill, A. & Borg, M. (2022). Code Red: The Business Impact of Code Quality. TechDebt. https://arxiv.org/abs/2203.04374
17. Gall, H., Hajek, K., Jazayeri, M. (1998). Detection of Logical Coupling Based on Product Release History. ICSM, 190–198. (Bibliographic record only; full text not read.)
18. Zimmermann, T., Weißgerber, P., Diehl, S., Zeller, A. (2005). Mining Version Histories to Guide Software Changes. *IEEE TSE*. https://thomas-zimmermann.com/publications/files/zimmermann-tse-2005.pdf
19. D'Ambros, M., Lanza, M., Robbes, R. (2009). On the Relationship Between Change Coupling and Software Defects. WCRE. https://www.semanticscholar.org/paper/937b72f93fe2cb74549b08f1f984c0b9e723e580
20. Tornhill, A. code-maat (GPL-3.0), README and defaults. https://github.com/adamtornhill/code-maat
21. Bird, C., Nagappan, N., Murphy, B., Gall, H., Devanbu, P. (2011). Don't Touch My Code! ESEC/FSE. https://www.zora.uzh.ch/id/eprint/55799/
22. Avelino, G., Passos, L., Hora, A., Valente, M. T. (2016). A Novel Approach for Estimating Truck Factors. ICPC. https://arxiv.org/abs/1604.06766 ; tool: https://github.com/aserg-ufmg/Truck-Factor
23. Majumder, S., Mody, P., Menzies, T. (2022). Revisiting Process versus Product Metrics: a Large Scale Analysis. *EMSE* 27(3). https://arxiv.org/abs/2008.09569
24. Moser, R., Pedrycz, W., Succi, G. (2008). A Comparative Analysis of the Efficiency of Change Metrics and Static Code Attributes for Defect Prediction. ICSE, 181–190. (Title-level; detail unverified.)
25. cloc (GPL-2.0). https://github.com/AlDanial/cloc
26. scc (MIT; dual licence with Unlicense — unverified). https://github.com/boyter/scc
27. lizard (MIT) https://github.com/terryyin/lizard ; radon (MIT) https://pypi.org/project/radon/
28. Perry, D. E., Siy, H. P., Votta, L. G. (2001). Parallel Changes in Large-Scale Software Development. *TOSEM* 10(3). https://dl.acm.org/doi/10.1145/383876.383878
29. Brindescu, C., Ahmed, I., Jensen, C., Sarma, A. (2020). An Empirical Investigation into Merge Conflicts and Their Effect on Software Quality. *EMSE* 25. https://link.springer.com/article/10.1007/s10664-019-09735-4
30. Dias, K., Borba, P., Barreto, M. (2020). Understanding Predictive Factors for Merge Conflicts. *IST*. https://pauloborba.cin.ufpe.br/publication/2020understanding_predictive_factors_for_merge_conflicts/
31. Owhadi-Kareshk, M., Nadi, S., Rubin, J. (2019). Predicting Merge Conflicts in Collaborative Software Development. ESEM. https://people.ece.ubc.ca/mjulia/publications/Predicting_Merge_Conflicts_in_Collaborative_Software_Development_2019.pdf
32. Cataldo, M., Herbsleb, J. D., Carley, K. M. (2008). Socio-Technical Congruence… ESEM. https://herbsleb.org/web-pubs/pdfs/cataldo-socio-2008.pdf
33. Kasi, B. K. & Sarma, A. (2013). Cassandra: Proactive Conflict Minimization through Optimized Task Scheduling. ICSE. https://www.semanticscholar.org/paper/61423b1a0920ede7d54595469b4d644e2a3a62bc
34. Mockus, A., Fielding, R. T., Herbsleb, J. D. (2002). Two Case Studies of Open Source Software Development: Apache and Mozilla. *TOSEM* 11(3). https://mockus.org/papers/mozilla.pdf
35. SmartBear. Best Practices for Peer Code Review. https://smartbear.com/learn/code-review/best-practices-for-peer-code-review/
36. SmartBear. The Largest Case Study of Code Review Ever (Cisco). https://static1.smartbear.co/support/media/resources/cc/episode_4_thelargestcasestudyofcodereviewever.pdf
37. Sadowski, C. et al. (2018). Modern Code Review: A Case Study at Google. ICSE-SEIP. https://sback.it/publications/icse2018seip.pdf
38. Rigby, P. C. & Bird, C. (2013). Convergent Contemporary Software Peer Review Practices. ESEC/FSE. https://dl.acm.org/doi/10.1145/2491411.2491444
39. Bosu, A., Greiler, M., Bird, C. (2015). Characteristics of Useful Code Reviews: An Empirical Study at Microsoft. MSR. https://www.semanticscholar.org/paper/4c535cd6557b148cc048686ec64e20291b61c698
40. Bacchelli, A. & Bird, C. (2013). Expectations, Outcomes, and Challenges of Modern Code Review. ICSE. https://www.microsoft.com/en-us/research/publication/expectations-outcomes-and-challenges-of-modern-code-review/
41. McIntosh, S., Kamei, Y., Adams, B., Hassan, A. E. (2014). The Impact of Code Review Coverage and Participation on Software Quality. MSR. https://dl.acm.org/doi/10.1145/2597073.2597076
42. GitHub Linguist — overrides (`linguist-generated`, `linguist-vendored`). https://github.com/github-linguist/linguist/blob/main/docs/overrides.md
43. git-diff documentation (`--numstat` on binaries; `diff.renames` default). https://git-scm.com/docs/git-diff
44. git-log documentation (`--first-parent`, `--no-merges`, `--since`). https://git-scm.com/docs/git-log
45. Dey, T. et al. (2020). Detecting and Characterizing Bots that Commit Code. MSR. https://arxiv.org/abs/2003.03172
46. GitHub Blog (2020). Get up to speed with partial clone and shallow clone. https://github.blog/open-source/git/get-up-to-speed-with-partial-clone-and-shallow-clone/
47. Kalliamvakou, E. et al. (2014). The Promises and Perils of Mining GitHub. MSR. https://kblincoe.github.io/publications/2014_MSR_Promises_Perils.pdf
