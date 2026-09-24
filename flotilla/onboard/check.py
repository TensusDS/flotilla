"""Drift between the project profile and the repository as it is now (spec, section 4.5 rule 2).

Job comparisons are made only against a real push run: a fallback list read from workflow files is
unverified by definition, and comparing against it would report drift that nobody can act on.
"""

from __future__ import annotations


def check_drift(profile: dict, det: dict, measured: dict[str, float]) -> list[str]:
    findings: list[str] = []
    ci = profile.get("ci") or {}
    now = det.get("ci") or {}
    if ci.get("provider") == "github":
        if now.get("provider") != "github":
            findings.append("CI: the profile names GitHub Actions, but no workflow files were found")
        else:
            if ci.get("workflow_fingerprint") and now.get("fingerprint") != ci["workflow_fingerprint"]:
                findings.append("CI: workflow files changed since onboarding; review `required_jobs`")
            if now.get("jobs_source") != "last-push-run":
                findings.append("unknown: CI required jobs not verified (gh unavailable or no push run on trunk yet)")
            else:
                required = set(ci.get("required_jobs") or [])
                ran = set(now.get("jobs") or [])
                findings += [f"CI: job `{job}` ran on the last push but is not required" for job in sorted(ran - required)]
                findings += [f"CI: required job `{job}` did not run on the last push" for job in sorted(required - ran)]
    trunk = (profile.get("trunk") or {}).get("branch")
    if trunk and det.get("trunk") and det["trunk"] != trunk:
        findings.append(f"trunk: the profile says `{trunk}`, the repository says `{det['trunk']}`")
    for tier in (profile.get("tests") or {}).get("tier") or []:
        if tier.get("name") not in measured:
            findings.append(f"tests: tier `{tier.get('name')}` has never run green on this machine")
    return findings


def exit_code(findings: list[str]) -> int:
    """0 nothing drifted, 1 something drifted, 3 nothing drifted that could be checked but something could
    not be asked. "Could not ask" is never reported as "matches" (spec 1.2, principle 5)."""
    if any(not finding.startswith("unknown:") for finding in findings):
        return 1
    return 3 if findings else 0
