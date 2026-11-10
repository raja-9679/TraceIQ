<!-- Generated from estate.html by e2e/docs-capture/export_md.py. Edit the HTML, not this file. -->

# Estate

Security · Everyone with access to the project can see it · Editors can add domains and scan

> **In short**
>
> A list of your organisation's domains (web addresses such as `example.com`), each checked from the outside and given a score out of 100. It catches the slow, quiet problems: a certificate about to run out, email that anyone could fake in your name, or an old address still pointing at a service you stopped paying for.

## What it is for

Your **estate** is everything you have on the public internet: the main site, the shop, the help centre, the forgotten marketing site from three years ago. Each of them can let you down in ways that have nothing to do with your app's code. The Estate page answers **"Which of our domains are weak, and what exactly is wrong with each?"**

The checks are **passive**: TraceIQ only reads public records and visits each site the way a browser would. It never scans ports or sends attack traffic.

> **Security leads**
>
> One score per domain, worst first, with the reason for every point lost.

> **IT and operations**
>
> Get warned weeks before a certificate expires, instead of finding out when the site breaks.

> **Email and marketing**
>
> See whether others can send email that pretends to come from your domain.

**Where to find it:** in the sidebar, open **Security → Estate**.

## A tour of the screen

The page always works on the project chosen at the top of the sidebar. On the left is the list of that project's estates; on the right, the selected estate. When you arrive, the first estate in the list is already open. The demo's web project has one:

![The Estate page: a New estate button and the tabs Estates and Certificate calendar in the header, an Estates list with one estate (example.com estate (demo), 2 domains, average 94, worst score 87), and on the right that estate's card with Scan now, Settings and Delete estate, its two domains, and below it the line Choose a domain](../media/security/estate/page.png)

The picture below shows the same page with the domain *example.com* opened. The numbers match the sections below.

![The Estate page with eight numbered parts: 1 the New estate button, 2 the Estates and Certificate calendar tabs, 3 the Estates list, 4 the estate's name, last scan and buttons, 5 the domain table, 6 the Add domains box, 7 the checks for one domain, 8 the scan history](../media/security/estate/page-annotated.png)

*The demo estate with *example.com* opened. Click the picture to see it larger.*

## 1. New estate

**New estate**, at the top right of the page, opens an empty estate form on the right. It is there only when a project is chosen in the sidebar.

![The New estate form: Name (Public websites), Scan schedule (cron, UTC, at most hourly) 0 3 * * *, Certificate alerts (days before expiry) 30, 14, 7, Extra DKIM selectors, Alert emails, Scheduled scans ticked, Discover subdomains from certificate transparency ticked, and Save estate](../media/security/estate/new-estate.png)

| Field | What it does |
|---|---|
| **Name** | Required. |
| **Scan schedule** | When to check, written as a *cron* expression in UTC. The standard `0 3 * * *` means every day at 03:00 UTC (08:30 in India). It can't be more often than once an hour. |
| **Scheduled scans** | Untick to only scan when someone presses *Scan now*. |
| **Certificate alerts** | How many days before a certificate expires to warn you (standard: 30, 14 and 7). One warning per threshold, sent again after the certificate is renewed. |
| **Alert emails** | Who gets those warnings by email. They also go to the project's Slack or Teams channel, if it has one. |
| **Extra DKIM selectors** | DKIM is a signature on outgoing email. TraceIQ looks for the common names of these keys; add your own if your mail provider uses another name. |
| **Discover subdomains** | Every public certificate is written into open logs (*certificate transparency*). TraceIQ reads them to find more of your sub-domains, such as `shop.example.com`, and adds those on the project's allowed list (up to 200 per domain). |

Nothing is saved until you press **Save estate**; the new estate then opens.

## 2. Estates and Certificate calendar

Two tabs under the page title. **Estates** (with how many there are) is the list and the selected estate. **Certificate calendar** replaces them with one table of certificates.

A **certificate** is what lets a browser show the padlock for a site; it has an end date, and when it passes, visitors get a warning page instead of your site. The calendar lists every certificate seen on every domain, in every estate of the workspace that you can see (not only this project's), the soonest to expire first: the date, the days left, the host, the estate and the project. Seven days or fewer show in red, 30 or fewer in amber. Until a scan has run, it says "No certificates observed yet — scan an estate first."

![The Certificate calendar card with columns Expires, Days left, Host, Estate and Project: example.com and www.example.com, both expiring 2027-01-17, 67 days left, in example.com estate (demo), project Demo — Todo web app](../media/security/estate/certificates.png)

*The demo's two certificates.*

## 3. The list of estates

An **estate** is a named group of domains, for example "Public websites" or "Payment domains". The **Estates** list shows each one with how many domains it has, their average score, and on the right its **worst domain score** (the coloured number: 90 or more green, 75–89 amber, below 75 red). Click one to open it on the right. To see another project's estates, change the project at the top of the sidebar.

## 4. The estate and its buttons

The estate's card starts with its name and how its last scan ended, then three buttons:

- **Scan now** checks every domain straight away. While a scan runs the button says *Scanning…* and the page refreshes by itself until it has finished.
- **Settings** opens the same fields as the New estate form inside the card (the button then says *Close settings*); **Save estate** keeps the changes, **Cancel** drops them.
- **Delete estate** asks first, and also deletes its scan history.

Below them, a reminder that the checks are passive only.

## 5. The domains

![The demo estate card: example.com estate (demo), last scan completed, Scan now, Settings and Delete estate, Passive checks only. Domains, lowest score first: example.com, added, zone apex, score 87, 67 days left on its certificate; www.example.com, added, score 100, 67 days. Below, the Add domains box with Import domains](../media/security/estate/estate.png)

**Domains, lowest score first**: the domain (with "from CT logs" if it was discovered, "zone apex" for a main domain), its score, a small trend line of up to 12 recent scores from the last 120 days, and the days left on its certificate. Click a domain's name to see its checks (part ⑦). *Remove* takes a domain out of the estate, after asking (its past scan results are deleted too).

## 6. Add domains

Paste domains, one per line, comma-separated or the first column of a CSV, then press **Import domains**. Each one must be on the project's allowed list; it says how many were added, and refused ones are listed with the reason (and stay in the box). An estate holds up to 500 domains.

## 7. A domain's checks

Below the estate, a second card shows the domain you clicked: its score out of 100, a trend line, **How the score is made** (the arithmetic, line by line) and every **check** with Pass, Warn or Fail, what it found, why it matters, and the points it cost. Until you click a domain, this space says "Choose a domain".

![The checks for example.com, 87 out of 100: how the score is made (100 start, minus 3 SPF softfail, 6 DMARC p=none, 2 no CAA record, 2 no DNSSEC, equals 87) and every check with Pass or Warn: SPF, DMARC, DMARC reporting, MX, DKIM, CAA, DNSSEC, Subdomain takeover, Certificate, HSTS and HTTP to HTTPS](../media/security/estate/domain.png)

**example.com* lost 13 points to four email and DNS warnings.*

## 8. Scan history

The last card lists every scan of the estate, newest first: how it ended, its number, when, whether it was scheduled or manual, the average score and the number of domains. The demo estate has two scheduled scans, with averages 94 and 91.

### How the score is made

Every domain starts at 100. Each problem found takes off a fixed number of points, and the drill-down shows each line, for example `100 start − 10 dmarc_missing … = 78`. The score never goes below 0. In the list, 90 or more is green, 75–89 amber, below 75 red.

| Family | What is checked (largest deductions) |
|---|---|
| **Email** | *SPF*, *DKIM* and *DMARC* are DNS records that tell other mail servers which email really comes from you. Missing or weak ones let anyone send email in your name: up to 15 points (SPF that allows every server), 10 for no SPF or no DMARC, 6 for a DMARC that only watches. |
| **Certificate** | A certificate browsers don't trust (expired, wrong name, unknown issuer): 25 points. Expiring within 7 / 14 / 30 days: 10 / 5 / 2. |
| **HTTPS** | No *HSTS* header (which tells browsers to always use the secure version): 5; plain `http://` not sent on to `https://`: 3. |
| **Takeover** | A record pointing at a third-party service you no longer use, which someone else could claim and serve their content on your address: 40 points. A record pointing at a name that doesn't exist: 10. |
| **Hygiene** | No *CAA* record (any certificate authority may issue for you) or no *DNSSEC*: 2 each. |

## Watch it in use

[▶ Watch the video](../media/security/estate/walkthrough.mp4)

*About a minute, with a spoken commentary. The captions at the bottom show the same words, so it also works with the sound off. The estate in the video is not saved.*

## How to…

### …start watching your domains

1. Ask a project admin to allow security testing and list your domains under *Scans & findings → Authorization*. Estate refuses domains that aren't on that list.
2. Choose the project in the sidebar, press **New estate** at the top of the page, give it a name, add the alert email addresses and press **Save estate**.
3. In the new estate's card, paste your domains into **Add domains** and press **Import domains**.
4. Press **Scan now**. The page refreshes by itself until the scan has finished.
5. Click the lowest-scoring domain to see what to fix first.

### …see which certificates expire soon

1. Open the **Certificate calendar** tab under the page title.
2. The top rows expire first. Red numbers are a week or less away.

### …fix a problem and confirm it

1. Pick the estate in the list, click the domain in its table and read the check's explanation.
2. Fix the record or the certificate with whoever runs your DNS or hosting.
3. Press **Scan now**. When the check passes, its finding is marked verified fixed in *Scans & findings*.

## Good to know

- **Only allowed domains are checked.** Every domain must be on the project's allowed list when it is added and again at every scan. A domain removed from the list is skipped, and the estate says so.
- **Problems become findings.** DNS, certificate and takeover problems appear in *Scans & findings* (as *estate-dns*, *estate-tls*, *estate-takeover*) with an owner, due date and status. One finding per problem is kept up to date; a clean rescan marks it verified fixed.
- **A check that couldn't run proves nothing.** If a DNS lookup fails or the site can't be reached, that family's earlier findings are left as they were rather than marked fixed.
- **Certificates are checked like a browser would.** TraceIQ never skips certificate checks to get through; a failing certificate is reported as untrusted.
- **One scan at a time.** Scan now is refused while a scan of the same estate is running.
- **Who can do what.** Anyone with access to the project can look. Creating, changing and deleting an estate, adding or removing domains and scanning need the *editor* role. Every change and every manual scan goes into the *Audit log*.

## Related pages

> **Script inventory**
>
> The next page in this guide: every script on your payment pages, each one approved with a reason.

> **Scans & findings**
>
> Where the allowed list is set and where estate problems are tracked to a fix.

> **Monitors**
>
> Check on a timer that your sites are up, not just correctly set up.

> **Trust center**
>
> Show customers a summary of your security, including findings, without the details.

## Video script

What the walkthrough says, in order. The video and every picture on this page are made by the Playwright script [`security-estate.spec.ts`](../../../../e2e/docs-capture/pages/security-estate.spec.ts).

1. Your estate is every web address your organisation owns, as the outside world sees it.
2. TraceIQ checks each one and gives it a score out of one hundred, worst first.
3. An estate is a named group of domains. The number next to it is its worst domain's score.
4. The estate opens on the right, with its domains, lowest score first.
5. Click a domain to see every check and how its score is made.
6. New estate starts one: a name, how often to check, and who to warn.
7. Certificate alerts warn you a set number of days before a site's certificate runs out.
8. TraceIQ can also find more of your sub-domains in public certificate records.
9. After saving, you add the domains, and each one is only checked if it is on the project's list of sites that may be tested.
10. The Certificate calendar tab lists every certificate in the workspace, the soonest to run out first.
