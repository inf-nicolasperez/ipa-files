# Family Apps — a Feather repo source

A GitHub repo that acts as a **source** for [Feather](https://github.com/claration/Feather).
You publish an `.ipa` here, your family adds one URL in Feather, and the app shows up
in their Sources tab with a download button.

```
repo.json            <- the file Feather actually reads (commit this)
repo.config.json     <- your name/subtitle/colour settings for the repo
icons/               <- app icons, auto-extracted from the IPAs
meta/                <- optional per-app text overrides (developer, description…)
apps/                <- drop IPAs here to keep them handy (never committed)
tools/repogen.py     <- the engine: reads an IPA, uploads it, updates repo.json
tools/setup.ps1      <- one-time: create the GitHub repo + turn on Pages
tools/publish.ps1    <- publish an IPA
```

---

## 1. One-time setup

Needs [git](https://git-scm.com/download/win), [GitHub CLI](https://cli.github.com/)
and Python. All three are already on this machine.

```powershell
gh auth login                 # only if you are not already logged in
.\tools\setup.ps1 -RepoName family-apps
```

That script:

1. writes your GitHub username and repo name into `repo.config.json`,
2. creates the **public** repo (it must be public — Feather has to fetch it),
3. pushes everything,
4. enables **GitHub Pages** on `main` / root,
5. prints the source URL and waits until it is live.

At the end it prints something like:

```
https://inf-nicolasperez.github.io/family-apps/repo.json
```

**That URL is the whole deliverable.** That is what you send to your family.

---

## 2. Publish an app

```powershell
.\tools\publish.ps1 .\apps\Spotify_9.1.78.ipa -Publish
```

What happens:

| step | detail |
| --- | --- |
| inspect | unzips the IPA, reads `Info.plist` → name, bundle id, version, minimum iOS |
| icon | pulls `AppIcon*.png` out of the bundle into `icons/` |
| upload | `gh release upload` → the `.ipa` becomes a **Release asset** |
| update | adds/updates the app in `repo.json` with both the flat fields and `versions[]` |
| push | commits `repo.json` + `icons/` and pushes (`-Publish`) |

GitHub Pages rebuilds in about a minute and Feather picks it up.

**Why Releases and not git?** GitHub hard-rejects any file over 100 MB, and your IPAs
are bigger than that. Release assets allow up to 2 GB and give you a permanent
`https://github.com/…/releases/download/…` link. Keeping the IPA out of git also keeps
the repo tiny so Pages deploys fast.

Useful flags:

```powershell
.\tools\publish.ps1 .\apps\App.ipa -DryRun      # show what would happen, write nothing
.\tools\publish.ps1 .\apps\App.ipa -Icon .\logo.png
.\tools\publish.ps1 .\apps\App.ipa -Tag app-1.2 -Publish
```

### Shipping an update
Just publish the new IPA. Same bundle id → a second entry is added to that app's
`versions` list, and the flat fields move to the newest build. Feather then offers the
update, and the old version stays downloadable.

---

## 3. The file format

`repo.json` and every key in it. This is the superset that Feather, AltStore and
SideStore all accept — this is why it works even if someone in the family uses a
different installer.

```jsonc
{
  "name": "Family Apps",          // shown in the Sources list
  "identifier": "com.family.apps",// any unique reverse-dns string, never change it
  "subtitle": "Apps for the family",
  "description": "Long text shown on the source page.",
  "iconURL": "https://…/icons/repo.png",   // square PNG, https
  "headerURL": "",                // optional wide banner
  "website": "https://github.com/…",
  "tintColor": "0A84FF",          // hex WITHOUT the #
  "featuredApps": [],             // array of bundle ids to pin at the top
  "apps": [                       // one object per app (per bundle id)
    {
      "name": "Spotify",
      "bundleIdentifier": "app.walnut1118.quartz7798",
      "developerName": "Spotify AB",
      "subtitle": "Music and podcasts",
      "localizedDescription": "Long description.",
      "iconURL": "https://…/icons/app.walnut1118.quartz7798.png",
      "tintColor": "1DB954",
      "category": "music",
      "screenshotURLs": [],
      "appPermissions": { "entitlements": [], "privacy": {} },

      "versions": [               // newest first — AltStore/SideStore read this
        {
          "version": "9.1.78",
          "buildVersion": "917802214",
          "date": "2026-11-06T04:34:42Z",   // RFC 3339, UTC
          "downloadURL": "https://github.com/…/releases/download/…/Spotify.ipa",
          "size": 271241086,                // bytes, integer - must be accurate
          "minOSVersion": "16.1",
          "localizedDescription": ""
        }
      ],

      // duplicated flat fields — Feather reads THESE first
      "version": "9.1.78",
      "versionDate": "2026-11-06T04:34:42Z",
      "size": 271241086,
      "downloadURL": "https://github.com/…/releases/download/…/Spotify.ipa"
    }
  ],
  "news": []          // optional {title, identifier, caption, date, url, tintColor, notify}
}
```

Rules that actually matter:

- `size` must match the file **exactly**, in bytes, as an integer. A wrong size makes
  installs fail with a confusing network error.
- `tintColor` is hex **without** `#`.
- `identifier` must stay the same forever, or Feather treats it as a new source.
- Keep `versions[0]` and the flat fields pointing at the same build.
- Everything is `https://`.

You can hand-edit `repo.json`; the tools merge into it rather than overwriting it.
After any manual edit run:

```powershell
py tools\repogen.py validate
```

CI runs the same check on every push, so a broken `repo.json` shows up as a red X
instead of a broken source for your family.

---

## 4. Per-app text (developer, description, colour)

The IPA only contains the app's *name* and *version*. To fill in the rest, create
`meta/<bundleIdentifier>.json` — copy `meta/_template.json`:

```json
{
  "developerName": "Spotify AB",
  "subtitle": "Music and podcasts",
  "localizedDescription": "Spotify gives you instant access to millions of songs.",
  "tintColor": "1DB954",
  "category": "music",
  "screenshotURLs": []
}
```

Publish again (or `py tools\repogen.py add <ipa>`) and those values are applied.

---

## 5. Troubleshooting

| symptom | cause |
| --- | --- |
| Feather says the source is invalid | `repo.json` is not valid JSON, or not reachable. Open the URL in a browser first. |
| Source loads but is empty | `apps` is `[]` — nothing published yet. |
| App appears, download fails | `size` is wrong, or the release asset was deleted. |
| Installs but crashes on launch | The IPA was FairPlay-encrypted (straight from the App Store). It must be a **decrypted** build. `py tools\repogen.py info <ipa>` tells you. |
| Pages 404 | Pages not enabled, or the build is still running: Settings → Pages. |
| Release upload rejected | Asset over 2 GB, or you are out of Actions/release storage. |

> **Heads-up on public repos:** this repo is public because Feather needs to fetch it
> without authentication. Redistributing proprietary App Store apps this way is against
> GitHub's and Apple's terms and can get the account or repo taken down. Keep it to
> apps you have the right to share.

---

## 6. Reference

- Feather: <https://github.com/claration/Feather>
- AltStore source format: <https://faq.altstore.io/developers/make-a-source>
