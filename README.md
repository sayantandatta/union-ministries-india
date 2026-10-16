# Union Ministries of India

A one-page guide to every Central Government ministry: who runs it, what it does, its website and X account, and a calling card for each minister (date and place of birth, official residence, official email, social accounts and declared assets).

It works the same way as the English Actors Register: GitHub keeps the data up to date on a schedule and hosts the page for free, and each visitor's browser keeps a saved copy.

## What's in the folder

| File | What it is |
|---|---|
| `index.html` | The page. It also carries a built-in copy of the data, so it opens instantly. |
| `sync.py` | Checks for changes and rebuilds the data. Needs Python 3.8 or later, nothing to install. |
| `data/ministries-meta.json` | Each ministry's name, description, sector, website and X accounts. Edit by hand. |
| `data/council.json` | Who holds which ministry. Updated automatically from Wikipedia. |
| `data/profiles.json` | Calling-card details checked by hand: residence, email, declared assets, birth details, social accounts. These always win over Wikidata. |
| `data/changes.json` | The "Recent changes" list on the page. New entries are added automatically. |
| `data/site.json` | Created by `sync.py`: everything the page needs, in one file. |
| `photos/` | Created by `sync.py`: one 240 px photo per minister from Wikimedia Commons. |

## How the updates work

Every day at 02:45 India time (and whenever you upload a change or press **Run workflow**), GitHub runs `sync.py`, which:

1. Reads the current ministers' tables on Wikipedia's *Third Modi ministry* article and compares them with `data/council.json`. Any change (a resignation, a new minister, a portfolio moving, an additional charge) is applied and added to the "Recent changes" list with the date.
2. Refreshes each minister's date and place of birth, social accounts and photo from Wikidata and Wikimedia Commons, downloading only photos that have changed.
3. Rebuilds `data/site.json` and the built-in copy inside `index.html`, saves them to the repository and republishes the site.

**Safety checks.** If Wikipedia's tables can't be read cleanly (for example the article is restructured), or more than 40 changes appear at once, nothing is changed: the site keeps its last good data and an issue titled "Ministers update needs a look" is opened in the repository. The same issue is used for anything that's been applied but deserves a look, such as a newly added ministry; later notes are added to it as comments. After a big reshuffle you've checked, open **Actions → Sync ministers and publish → Run workflow**, tick **force**, and run it.

**New, renamed and removed ministries.** The list of ministries isn't fixed: every run compares Wikipedia's portfolio column with `data/ministries-meta.json`.

- *New ministry.* A portfolio held by a Cabinet minister or a Minister of State (Independent Charge) that matches no known ministry is noted on the first run and added on the next run, at least 3 hours later (so a passing edit on Wikipedia doesn't reach the site). Its description, website and X account are taken from its Wikipedia article and Wikidata where they exist; its sector shows as "Other" until you set one. It appears in Recent changes, and the review issue asks you to check it and then delete `"auto": true`.
- *Renamed ministry.* If a ministry disappears and a new name appears the same day, held by the same minister, it's treated as a rename: the page, links and history carry over under the new name, and the old name is kept in `formerNames`.
- *Merged or wound-up ministry.* A ministry missing from the tables stays on the site with its last known ministers for a day. If it's still missing on the next day's run, it's marked `"retired"` and taken off the site. Delete that line to bring it back.
- *Big restructures.* If more than 6 ministries look new or gone at once, nothing is changed until you run the workflow with **force** ticked.

Every run also saves `data/wikipedia-portfolios.json`, listing each portfolio name read from Wikipedia and the ministry it was matched to, so you can see exactly what the check saw.

**Correcting something by hand.** Edit `data/council.json` and set `"lock": true` near the top so the daily check doesn't overwrite your edit. Set it back to `false` once Wikipedia is right. To fix a calling-card detail, edit that minister's entry in `data/profiles.json`; to let Wikidata supply a field instead, delete it there.

**Ministry budgets.** Each ministry shows its annual outlay from the Union Budget (Budget Estimates, revenue plus capital, from the Expenditure Budget's summary by ministry), its share of total spending and its rank. The figures live in `data/budget.json` and change once a year: after the Budget on 1 February, update the year, total and each ministry's figure from `indiabudget.gov.in/doc/eb/allsbe.pdf`. Until you do, the review issue gets a reminder every Monday. A new ministry shows "Not yet shown separately in the Union Budget" until it has a figure.

**What isn't automatic.** Official residence, official email and declared assets aren't published in any machine-readable source, so they live in `data/profiles.json`. Assets only change at elections; when a minister is new or re-elected, add their totals from their MyNeta page (myneta.info).

## In the visitor's browser

The page shows its built-in copy (or the copy saved in that browser, if newer) straight away, then fetches `data/site.json` and switches to it if it's newer. The newest copy is saved in the browser (IndexedDB), and minister photos are saved too (Cache Storage), so repeat visits are instant and still work offline. The line under the title says when the data was last updated.

## Hosting it on GitHub Pages

1. Create a new **public** repository, for example `union-ministries-india`.
2. Upload everything in this folder, including the hidden `.github` folder. If it won't drag in, create `.github/workflows/sync-and-publish.yml` by hand with **Add file → Create new file** and paste its contents.
3. Open **Settings → Pages** and set **Source** to **GitHub Actions**.
4. Open **Settings → Secrets and variables → Actions → Variables** and add `UMI_USER_AGENT` with a value like `UnionMinistriesIndia/1.0 (https://yourname.github.io/union-ministries-india; you@example.com)`. Wikimedia asks scripts to identify themselves.
5. The upload starts the first run (see the **Actions** tab); it takes a few minutes and downloads the photos. The site is then live at `https://<your-username>.github.io/union-ministries-india/`.

If you use your own domain, add an Actions variable `UMI_SITE_URL` with the full address ending in `/`.

## Running it yourself

```
UMI_USER_AGENT="UnionMinistriesIndia/1.0 (https://your.site; you@your.site)" python3 sync.py
python3 sync.py --offline    # rebuild from the files in data/ without going online
```
