# Weekly source sync and fork workflows

The weekly job uses the repository's built-in `GITHUB_TOKEN`. That token can push application source with `contents: write`, but cannot update GitHub Actions workflow definitions. A normal upstream merge started failing on 28 September 2026 when it brought changes to `.github/workflows` into the push.

The job now creates a real merge commit, retaining upstream ancestry and application changes while preserving this fork's reviewed `.github/workflows` tree. Incoming upstream workflow changes are saved as a patch and JSON source record in the run artifacts and release assets. They require a separate reviewed update. When there are no new workflow changes since the merge base, the JSON records an empty change list and no patch asset is created; GitHub rejects zero-byte release assets. This fork's custom release and Docker workflows remain maintained here.

The current upstream workflow fixes were reviewed and merged as part of the October repair. Future source conflicts outside `.github/workflows` fail and abort only the owned merge. A dirty checkout is rejected before any merge. The normal, non-forced push occurs after unit tests, APK build and signature verification, and refuses to advance a branch that another actor updated meanwhile. Built-in-token pushes do not trigger ordinary push workflows, so the weekly job runs the unit tests itself.

Dispatching the weekly workflow on a repair branch validates and stages its artifacts without pushing to `master` or publishing a release. Dispatch on `master` preserves normal weekly release behavior. Tags include the date, exact prepared source commit, run ID and attempt, so reruns do not reuse an earlier release's assets.

Run the local regression checks with:

```sh
python3 -B -m unittest discover -s .github/scripts -p test_upstream_sync.py -v
```

GitHub's [token documentation](https://docs.github.com/en/actions/concepts/security/github_token) and [authentication guidance](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token) describe the token's permission and workflow-trigger boundaries.
