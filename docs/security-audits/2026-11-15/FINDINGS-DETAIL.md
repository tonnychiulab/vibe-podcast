# Detailed Findings (`confirmed` Medium / High / Critical)

Run ID: `vibe-podcast-run-1` (`tonnychiulab/vibe-podcast` @ `434b429d9aab31f30e34fedc9e532c39179b3724`, profile: `quick`)

No `confirmed` medium, high, or critical vulnerabilities were finalized in this run (`0` `confirmed` records in `findings.json`), because target-controlled execution required to establish bounded local `observed_result` values is blocked on this macOS host (missing OS-enforced nested sandbox controls).

All **9 source-verified security leads** awaiting sandboxed local execution or owner-observed deployment verification are documented in [NEEDS-VALIDATION.md](NEEDS-VALIDATION.md) and summarized in [REPORT.md](REPORT.md).
