# Union Ministries of India

A one-page guide to every Central Government ministry: who runs it, what it does, its budget, website, email and X account, and a calling card for each minister.

**Live site:** https://sayantandatta.github.io/union-ministries-india/

---

## What updates by itself

GitHub runs `sync.py` every day at **02:45 India time**. You don't need to do anything.

| What | How often | Where it comes from |
|---|---|---|
| Ministers and who holds which ministry | Daily | Wikipedia (*Third Modi ministry*) |
| New, renamed or closed ministries | Daily (confirmed on the next run) | Wikipedia |
| Ministers' birth details, social accounts, photos | Daily | Wikidata and Wikimedia Commons |
| Ministry websites still working | Every Sunday | Each website |

Every change is listed under **Recent changes** on the site with its date.

## What you update by hand

| What | When | File |
|---|---|---|
| Ministry budgets | Every February, after the Union Budget | `data/budget.json` |
| Ministers' residence, email, declared wealth | When a new minister joins, or after an election | `data/profiles.json` |
| A ministry's description, website, email, X account | When a new ministry is added, or a link changes | `data/ministries-meta.json` |

To edit a file: open it on GitHub, click the **pencil** icon, make the change, click **Commit changes**. The site updates by itself a few minutes later.

## When GitHub opens an issue

If something needs your attention, the daily run opens an issue called **"Ministers update needs a look"** in this repository (later notes are added to it as comments). Turn on notifications for the repository so you see it.

It will tell you what happened. The usual cases:

- **A new ministry was added.** Open `data/ministries-meta.json`, find it (it has `"auto": true`), fix its sector (`"s"`), description (`"d"`), website and email, then delete the `"auto": true` line.
- **Too many changes at once** (a big reshuffle). Nothing was changed. Check Wikipedia, then run the update with **force** ticked (see below).
- **Wikipedia couldn't be read.** Nothing was changed; the site keeps the last good data. Usually it fixes itself the next day.
- **A website doesn't open.** Fix the address in `data/ministries-meta.json`.
- **The budget is out of date** (reminder every Monday from February). Update `data/budget.json`.

Close the issue when you're done.

## How to…

**Run an update now**
Go to **Actions → Sync ministers and publish → Run workflow → Run workflow**.

**Apply a big reshuffle**
Same as above, but tick **force** before pressing the green button.

**Check all websites now**
Same as above, but tick **check_links**. Results are saved in `data/link-check.json`.

**Fix a wrong minister or portfolio**
Edit `data/council.json`, and change `"lock": false` to `"lock": true` so the daily run doesn't undo your fix. Set it back to `false` once Wikipedia is right.

**Fix a wrong fact on a minister's card**
Edit that minister's entry in `data/profiles.json`. Anything there wins over Wikidata.

**Update the budget (each February)**
Open the Budget's summary by ministry at https://www.indiabudget.gov.in/doc/eb/allsbe.pdf. In `data/budget.json`, change `"year"` (e.g. `"2027-28"`), `"presented"`, `"total"`, and each ministry's figure (total of revenue and capital, in Rs crore).

## Good to know

- **Ministry websites.** Some ministries have no single website, only one per department. In that case the main department's site is shown with its name (e.g. "Dept of Telecommunications"), and the other departments' sites are listed next to it.
- **Ministry emails.** None of the ministries publishes a general enquiry inbox, so the email shown is usually the Secretary's office (sometimes the Minister's office). The label under the email says which. Entries marked `"emailChecked": false` came from a directory rather than a government page; check them when you can.
- **Ministers' residences** come from parliament records and can lag a move.
- **Declared wealth** is the total from each minister's latest election affidavit, as published by ADR / MyNeta.
- **Photos** come from Wikimedia Commons and are credited on each calling card.

## Files

| File | What it is |
|---|---|
| `index.html` | The page. |
| `sync.py` | The update script GitHub runs. |
| `data/ministries-meta.json` | Each ministry: name, description, sector, websites, email, X account. |
| `data/council.json` | Who holds which ministry (updated automatically). |
| `data/profiles.json` | Calling-card details checked by hand. |
| `data/budget.json` | Budget figures per ministry. |
| `data/changes.json` | The "Recent changes" list. |
| `data/site.json` | Everything the page shows, built by `sync.py`. Don't edit. |
| `data/wikipedia-portfolios.json` | What the last run read from Wikipedia. Don't edit. |
| `data/link-check.json` | Results of the last website check. Don't edit. |
| `photos/` | Minister photos. Don't edit. |

## In visitors' browsers

The page opens instantly from a copy saved in the browser, then switches to newer data if there is any. Data and photos are saved in the browser, so repeat visits are fast and work offline. The line under the title shows when the data was last updated.

## One-time setup (already done)

1. Public repository with these files, including the `.github` folder.
2. **Settings → Pages → Source:** GitHub Actions.
3. **Settings → Secrets and variables → Actions → Variables:** `UMI_USER_AGENT` = `UnionMinistriesIndia/1.0 (https://sayantandatta.github.io/union-ministries-india; your email)`.
