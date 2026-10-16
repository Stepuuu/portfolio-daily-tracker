# Credential handling

Shipped defaults contain no API credentials. Set `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY` in the process environment for the corresponding official
endpoint. These values are read at use time and are not saved by unrelated
settings changes. Other providers can use a private configuration selected by
`TRACKER_CONFIG_FILE`; keep that file outside the checkout. Do not add credentials
to `dashboard/config/settings.py`, example files, tests, or documentation.

Run `python scripts/check_public_artifacts.py` before publishing. CI also runs
this check. It rejects private runtime artifacts, common token formats, and
nonempty credentials in the Python default configuration, including custom
Authorization headers without a recognizable token prefix. The check prints
only file names, line numbers, and categories. It is a guardrail, not a complete
secret detector or a test that an old credential has been revoked.

For an independent scan, use Gitleaks locally or GitGuardian CLI with your own
account. Scan a clean checkout or an export of tracked files; do not accidentally
upload private portfolio data, ignored configuration, or local audit evidence.

## If a credential was published

1. Identify the provider, owner, and applications using the credential. Replace
   it in dependent applications and revoke the exposed value at the provider.
   Check access and billing logs for unexpected use.
2. Remove the value from shipped code and examples. `.gitignore` alone does not
   remove an already tracked file. Preserve any local configuration needed by
   running applications while fixing its tracking status.
3. Scan current files and reachable Git history separately. A clean branch does
   not prove an old commit, pull-request reference, or external clone is gone.
4. Record provider-side revocation separately from code cleanup. A failed request,
   an unsupported endpoint, or a detector without a validity checker does not
   establish revocation. Contact GitHub Support when inaccessible-to-push PR
   references or cached commit views still expose sensitive history.
5. Close an aggregated incident only after checking every affected source. A fix
   in this repository does not resolve exposures in other repositories.

Keep incident evidence and scan reports outside version control. Do not paste
credential values, private machine paths, customer data, or raw API responses
into public issues or commits.

See [GitGuardian's remediation guidance](https://docs.gitguardian.com/secrets-detection/secrets-detection-engine/leaks_remediation)
and [GitHub's sensitive-data removal guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
