# Price Atlas · Public Site

Public static website artifacts and public market-data snapshots. The application source repository remains private.

[Open website](https://justchatgpt.github.io/chatgpt-price-atlas-site/)

## Update data

Repository maintainers: open Actions → Publish Pages → Run workflow.
Choose rates to refresh FX/USDT, all to also collect Apple prices, or deploy-only to publish existing files.
Visitors can read cached prices; no credentials are embedded in the website.

The update job uses GitHub's automatically scoped GITHUB_TOKEN. It does not access the private source repository.
Fonts are served from this site; license text is in THIRD_PARTY_NOTICES.txt.
