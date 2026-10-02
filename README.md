# Price Atlas · Public Site

Public static website artifacts and public market-data snapshots. The application source repository remains private.

[Open website](https://justchatgpt.github.io/chatgpt-price-atlas-site/)

## Update data

An hourly check runs at minute 17 (UTC). Only changed prices, rates or availability cause a commit and deployment. Collection timestamps alone are ignored. Maintainers can also use Actions → Publish Pages → Run workflow.
Choose rates to refresh FX/USDT, all to also collect Apple prices, or deploy-only to publish existing files.
Apple and FX updates run independently. If one source fails, its usable cached file and timestamps are preserved while the other source can update. The Actions summary reports changed, unchanged, retained or failed for each source. If all requested sources fail, or a failed source has no usable cache, the run fails without deployment.
Visitors can read cached prices; no credentials are embedded in the website.

The update job uses GitHub's automatically scoped GITHUB_TOKEN. It does not access the private source repository.
Fonts are served from this site; license text is in THIRD_PARTY_NOTICES.txt.
