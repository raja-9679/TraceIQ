<!-- Generated from script-inventory.html by e2e/docs-capture/export_md.py. Edit the HTML, not this file. -->

# Script inventory

Security · Everyone with access to the project can see it · Editors can add pages and approve scripts

> **In short**
>
> TraceIQ opens your most sensitive pages, such as checkout, on a schedule and writes down every script that runs there. Each script needs someone to approve it with a reason. When a new script appears or an approved one changes, you are warned. It can also watch the page's text for signs that someone has defaced it.

## What it is for

A **script** is a piece of program code that a web page loads and runs in the visitor's browser: your own code, but often also code from other companies, for analytics, chat or payments. If an attacker manages to add a script, or change one at the supplier, it can quietly copy every card number typed on the page. This is called *skimming* (or a "Magecart" attack), and it is invisible to the customer.

The card-payment industry's security standard (**PCI DSS 4.0**) therefore asks for a list of every script on payment pages, a written reason for each one, a way to check each is unchanged (requirement 6.4.3), and a check at least once a week for unauthorised changes to those scripts and the page's security headers (11.6.1). This page does that, and answers **"Is anything running on our payment page that we didn't approve?"**

> **Security and compliance**
>
> Keep the script list and the reasons auditors ask for, with a weekly check.

> **Developers**
>
> See which scripts changed after a release, and approve the ones that should be there.

> **E-commerce teams**
>
> Hear quickly if the checkout page shows text it shouldn't.

**Where to find it:** in the sidebar, open **Security → Script inventory**.

## A tour of the screen

The page always works on the project chosen at the top of the sidebar. On the left is the list of pages that project watches; on the right, the selected page. When you arrive, the first page in the list is already open. The header counts the monitored pages and their unapproved scripts. The demo's web project watches one checkout page:

![The Script inventory page: 1 monitored page, 2 unapproved scripts, a Monitor a page button, the Monitored pages list with Checkout page (demo), 4 scripts, 2 unapproved, result alert, and on the right that page open on its Scripts tab](../media/security/script-inventory/page.png)

The numbers in the picture below match the sections below.

![The Script inventory page with six numbered parts: 1 the Monitor a page button, 2 the Monitored pages list, 3 the page's name, result, address and last check, 4 the Check now and Delete page buttons, 5 the Scripts, Change history and Settings and defacement tabs, 6 the table of scripts](../media/security/script-inventory/page-annotated.png)

*The demo's checkout page. Click the picture to see it larger.*

## 1. Monitor a page

**Monitor a page**, at the top right, opens an empty form on the right to start watching another page (see [The page's settings](#settings)). It is there only when a project is chosen in the sidebar.

## 2. Monitored pages

The pages this project watches, each with its number of scripts (and how many are unapproved) and its last result. Click one to open it on the right. To see another project's pages, change the project at the top of the sidebar.

| Result | What it means |
|---|---|
| ok | Nothing changed since the last check. |
| changed | Something harmless changed: a script disappeared, or a security header changed. |
| alert | A new script appeared, a script's content changed, or a defacement check failed. The alert people are told. |
| error | The check couldn't be done, for example the site is no longer on the allowed list or no test machine answered. The reason is shown on the page. |
| new | Not checked yet. |

## 3. The page and its result

The card on the right starts with the page's name and last result, then its address, when it was last checked and, with defacement checks on, how similar its text was to the baseline.

## 4. Check now and Delete page

- **Check now** asks for a check straight away; the button then says *Check queued*. The result appears when a test machine has visited the page.
- **Accept current content as baseline** appears only with defacement checks on: after you change the page's text on purpose, it starts the comparison afresh.
- **Delete page** stops watching the page, after asking, and deletes its inventory and history.

## 5. Scripts, Change history, Settings & defacement

Three tabs inside the card. The card opens on **Scripts** (with how many there are).

- **Change history**: every script added, changed, removed or approved, every header change and every defacement alert, newest first, with its time.
- **Settings & defacement**: the page's settings, the same fields as the *Monitor a page* form (see [below](#settings)). Saving them returns to the Scripts tab.

![The Change history tab: 2026-10-24, Script changed: https://tags.example.org/gtm.js; 2026-10-12, Baseline: Inventory started with 4 script(s)](../media/security/script-inventory/history.png)

*The demo's change history: the tag manager script changed, so it lost its approval and the page is on alert.*

## 6. The scripts

![The card for Checkout page (demo), result alert, https://example.com/checkout, last checked 2026-10-24, with Check now and Delete page and the tabs Scripts, Change history, Settings and defacement. The Scripts tab lists four scripts: https://tags.example.org/gtm.js (external, third party) and an inline script, both unapproved with an Approve… link; https://payments.example.net/v3/checkout.js (third party) and https://example.com/static/app.js (first party), both approved with their justification and a Withdraw approval link. Each has the start of its SHA-384 fingerprint and SRI none. At the bottom, Security headers last seen](../media/security/script-inventory/monitored-page.png)

*The demo's checkout page: two scripts approved with a reason, two waiting for approval.*

One row per script, unapproved ones first:

- **Script**: its address, or "inline script" for code written into the page itself; whether it is *external* or *inline*, *first party* (from your own site) or *third party*, and the day it was first seen. An approved script shows its justification underneath.
- **SHA-384**: the script's fingerprint; any change to the code changes it.
- **SRI**: whether the page itself tells the browser to check that fingerprint ("none" means it doesn't).
- **Approval**: approved (with *Withdraw approval*) or unapproved (with *Approve…*).

Tick **Include removed scripts** above the table to see ones that have gone. **Security headers last seen**, below it, opens to list the protective headers the page sent.

### The page's settings

The *Monitor a page* form and the *Settings & defacement* tab have the same fields.

![Fields: Name (Checkout), URL (host must be allowlisted) https://shop.example.com/checkout, Check schedule (cron, UTC; PCI 11.6.1 wants at least weekly) 0 6 * * 1, and Alert emails](../media/security/script-inventory/fields.png)

- **Name** and **URL** (both required). The page's site must be on the project's allowed list (*Scans & findings → Authorization*); otherwise saving is refused.
- **Check schedule**: a *cron* expression in UTC. The standard `0 6 * * 1` is every Monday at 06:00 UTC, which meets the weekly minimum. It can't be more often than once an hour.
- **Alert emails**: who is emailed when the result is *alert*. The project's Slack or Teams channel is told too, if it has one.

![Three ticked boxes: Scheduled checks, Script inventory, Defacement checks](../media/security/script-inventory/toggles.png)

- **Scheduled checks**: untick to check only when someone presses *Check now*.
- **Script inventory**: record every script (on by default).
- **Defacement checks**: also look at the page's text (off by default). *Defacement* is when an attacker changes what a page says, for example to their own message.

![Defacement settings: a slider Alert when text similarity to the baseline drops below 50%, a box for forbidden keywords (one per line, plain text), and a box for element checks (selector | must contain | forbidden; forbidden)](../media/security/script-inventory/defacement.png)

With defacement checks on, the first check records the page's text as the **baseline**. Later checks compare against it:

- **Similarity**: alert when the text is less than this much like the baseline (standard 50%).
- **Forbidden keywords**: words that should never be on the page, such as "hacked by". One per line.
- **Element checks**: one per line, a part of the page (a *selector*), the text it must contain, and words it must not contain, separated by `|`. For example `h1 | Checkout`.

All of these are plain text comparisons, ignoring capital letters. A failed check is a high-severity *Possible defacement* finding.

Nothing is kept until you press **Save page**; a new page then opens on the right. Its first check happens at the next time in the schedule; press **Check now** to run it at once.

## Watch it in use

[▶ Watch the video](../media/security/script-inventory/walkthrough.mp4)

*About a minute and a quarter, with a spoken commentary. The captions at the bottom show the same words, so it also works with the sound off. The page in the video is not saved.*

## How to…

### …start watching your checkout page

1. Make sure the site is on the project's allowed list (*Scans & findings → Authorization*, project admin).
2. Choose the project in the sidebar, press **Monitor a page** at the top of the page, type a name and the page's address, and add the alert email addresses.
3. Press **Save page**, then **Check now**. The first check records every script; that first list doesn't raise alerts.
4. Approve each script on the **Scripts** tab (below).

### …approve a script

1. Pick the page in the list. On its **Scripts** tab, find the *unapproved* script and press **Approve…**.
2. Write why the page needs it, for example "Card form from our payment provider". At least 10 characters.
3. Press **Approve script**. The reason is shown under the script and recorded in the *Audit log*.

### …deal with an alert

1. Pick the page in the list and open its **Change history** tab to see what changed.
2. If a new or changed script is expected (a release, a supplier update), approve it again with a reason.
3. If not, treat it as a security incident: remove the script from the page and find out how it got there.

## Good to know

- **A changed script loses its approval.** The approval was for the code someone looked at, so when a script's fingerprint changes, it goes back to unapproved and the page raises an alert.
- **Unapproved scripts are findings.** Each one is a finding in *Scans & findings* (medium for third-party scripts, low for your own) until it is approved or goes away.
- **Scripts that hide are caught too.** A script that adds another script and then removes itself still shows up, because TraceIQ also watches what the page downloads, not only what is in the page at the end.
- **It only looks.** The check is an ordinary page visit. Inline scripts are stored only as fingerprints; cookies are never stored; page text is kept only when defacement checks are on, with secrets removed.
- **A check gives up after 15 minutes** and the page shows the error.
- **Who can do what.** Anyone with access to the project can look. Adding, changing, checking and deleting pages, and approving or withdrawing approvals, need the *editor* role.

## Related pages

> **Controls**
>
> The next page in this guide: your compliance promises, checked against TraceIQ's records.

> **Estate**
>
> Your domains and certificates, checked from the outside.

> **Scans & findings**
>
> Where unapproved scripts and defacements are tracked, and where the allowed list is set.

> **Audit log**
>
> Who approved which script, when, and why.

## Video script

What the walkthrough says, in order. The video and every picture on this page are made by the Playwright script [`security-script-inventory.spec.ts`](../../../../e2e/docs-capture/pages/security-script-inventory.spec.ts).

1. Script inventory keeps a list of every script that runs on your most sensitive pages, such as checkout.
2. A script added or changed by an attacker can steal card details, so each one must be approved.
3. The list on the left shows each watched page: here a checkout page with two unapproved scripts.
4. On the right, every script on that page, with its fingerprint and whether it is approved.
5. Someone approves each script with a written reason.
6. Change history shows what changed and when.
7. Monitor a page adds another page to watch.
8. Give the page a name and its address. The site must be on the project's list of sites that may be tested.
9. The schedule says how often to look. Card-industry rules ask for at least once a week.
10. Script inventory records the scripts. Defacement checks also watch the page's text.
11. You can be warned when the text changes a lot, or when words that should never appear show up.
12. Save page keeps it; the first check then records every script on it.
