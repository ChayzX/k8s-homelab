# Additional security scans

The existing Oculum/Semgrep/other CI checks remain unchanged.

- `additional-security.yml` runs on pull requests, main pushes, and Mondays at 13:17 UTC. Gitleaks inspects Git history for secrets; Trivy checks dependencies and configuration. Findings can fail the job. No production or deployment credentials are required.
- `cariddi-exposure.yml` is **manual only**. Set the repository Actions variable `CARIDDI_TARGET_URL` to an HTTPS root for a public site you control, then trigger **Controlled Public Web Exposure Scan** under Actions. No URL is configured by this change.
- Cariddi runs with low concurrency, delay, and limited depth. It may still trigger application behavior when following GET links; review public routes and run against staging first.
- Cariddi output may contain real secrets: its artifact is retained only one day. Restrict Actions artifact access, download it only into a trusted environment, and rotate any confirmed exposed credential. Do not post scan output to issues or PRs.
- The Gitleaks Docker image currently uses a moving tag; pin to a reviewed digest before enforcing in a high-trust environment. Trivy DB updates and scheduled jobs require network access and hosted Actions minutes.
- These checks complement, but do not replace, authenticated web security tests and post-build container-image scanning. Review failures and false positives before setting required checks.

The Cariddi scan is deliberately not scheduled until an approved, reachable target and its operational safety are confirmed.
