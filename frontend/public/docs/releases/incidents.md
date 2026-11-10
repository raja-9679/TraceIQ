<!-- Generated from incidents.html by e2e/docs-capture/export_md.py. Edit the HTML, not this file. -->

# Incidents

Releases · Everyone with access to the project can see it · Editors can record and change incidents

> **In short**
>
> A list of the problems that reached your live site. For each one, TraceIQ notices when the same problem happened before, suggests a test that would catch it next time, and lets you record whether a test should have caught it. That last part gives the *escaped defects* count at the top.

## What it is for

An *incident* is something going wrong in production: an outage, an error page, a broken checkout. Incidents answers **"What got past our tests, and are we making sure it can't happen again?"** Incidents can be typed in by hand or sent in automatically by a paging tool such as PagerDuty or Opsgenie.

> **On-call and support**
>
> Record an incident and see straight away whether it has happened before.

> **Testers**
>
> Get a suggested test for each incident, so the same bug is caught before the next release.

> **Leads**
>
> Track escaped defects over time, and how often a test was added afterwards.

**Where to find it:** in the sidebar, open **Releases → Incidents**.

## A tour of the screen

The page always shows the project chosen in the sidebar's project picker; there is no project menu on the page itself (until a project is chosen, it says "Pick a project"). The header holds the buttons and the tabs, the escaped-defect counts come next, then the list of incidents on the left and the selected incident on the right, and a monthly table at the bottom. The demo's web project has four incidents: two sign-in outages that are the same problem, one escaped defect and one minor slowdown. The numbers on the screenshot match the sections below.

![The Incidents page with six numbered parts: 1 the Webhook setup and New incident buttons, 2 the All, Open and Escaped tabs, 3 the four escaped-defect tiles, 4 the list of incidents grouped Acknowledged and Resolved, 5 the selected incident Homepage loads slowly for visitors in APAC, 6 the Escaped defects by month table](../media/releases/incidents/page-annotated.png)

*The Incidents page for the demo's web project. Click the picture to see it larger.*

## 1. New incident and Webhook setup

![The page header: the title Incidents, a line about what the page does, the buttons Webhook setup and New incident, and along the bottom the tabs All, Open and Escaped](../media/releases/incidents/toolbar.png)

- **New incident** opens a form, just under the header, to record an incident by hand.
- **Webhook setup** shows how a paging tool can send incidents in (see [below](#webhook)). The button then reads **Hide webhook setup**.

![The New incident form, showing example text in grey in the first fields: Title, Severity (High), Service, Affected URL, then Incident link, Commit, Branch, Release, a ticked box Propose a regression test, Into suite, and the buttons Cancel and Record incident](../media/releases/incidents/new-incident.png)

| Field | What to enter |
|---|---|
| **Title** | What went wrong, in a line. The only required field. |
| **Severity** | critical, high (chosen at first), medium or low. |
| **Service** | The part of the system affected, for example `checkout-api`. Used to recognise repeats. |
| **Affected URL** | The page or address that failed. Used to find the test that covers it, and as the starting page of the suggested test. |
| **Incident link, Commit, Branch, Release** | Optional: where the incident is described, and which version of the app was live. |
| **Propose a regression test** / **Into suite** | On at first: TraceIQ drafts a test meant to catch this problem, in the suite you pick (or, if you don't, the suite of the matching test, else the project's first suite). |

Press **Record incident** to save it, or **Cancel**. A message says whether it is a repeat ("Recorded — recurrence #2") and the number of the suggested test.

Press **Record incident** to save it (it is greyed out until there is a title), or **Cancel**. A message says whether it is a repeat ("Recorded — recurrence #2") and the number of the suggested test.

### Webhook setup

![The Inbound webhook panel: the address to POST to with a Copy URL button, the three ways to authenticate, and a Create webhook secret button](../media/releases/incidents/webhook.png)

A *webhook* lets another tool send incidents to TraceIQ as they happen. The panel shows the address to send them to, and how the sender proves it is allowed: a signature made with a shared secret (TraceIQ's own or PagerDuty's format), or, for Opsgenie, which can't sign, the secret itself in a header. TraceIQ recognises plain JSON, PagerDuty and Opsgenie messages by itself.

- **Create webhook secret** makes the secret and shows it **once**; copy it into your paging tool then. Later the button reads **Rotate secret**, which replaces it (the old one stops working at once).
- Once there is a secret: **Regression proposals go to** (the suite for suggested tests), **Enabled**, **Delete webhook**, and when the last incident arrived.
- While there is no secret, or the webhook is switched off, the address answers as if it didn't exist.
- When the paging tool updates an incident it already sent (acknowledged, resolved), TraceIQ updates the same incident instead of adding a new one.

Seeing this panel needs the *editor* role; creating, rotating, changing or deleting the webhook needs project *admin*, and can't be done with an API key.

## 2. All, Open, Escaped

The tabs along the bottom of the header choose which incidents the list in ④ shows: **All**, only those with status *open*, or only those marked as escaped defects. The tiles in ③ and the table in ⑥ always count every escaped defect, whichever tab is chosen. When nothing matches, the page says "No incidents match this filter".

## 3. Escaped defects

![Four tiles: Escaped defects (180d) 1, Test added afterwards 100%, Missed by an existing test 1, No test existed 0](../media/releases/incidents/tiles.png)

An *escaped defect* is an incident that someone has marked as a bug the tests should have caught (see [The selected incident](#detail)). The tiles count those that **started in the last 180 days**:

| Tile | What it counts |
|---|---|
| **Escaped defects (180d)** | All incidents marked as escaped defects. |
| **Test added afterwards** | The share of them that got a new regression test, created after the incident started. Linking a test that already existed doesn't count. Shows "—" while there are none. |
| **Missed by an existing test** | A test existed but didn't catch it. |
| **No test existed** | Nothing tested that part at all. |

In the demo, the one escaped defect was missed by an existing test, and a regression test was added after it, hence 100%.

## 4. The incidents

![The list in two groups. Acknowledged 1: Homepage loads slowly for visitors in APAC (Low, marketing-site, generic, a date), selected. Resolved 3: Sign-in page returns 503 at 18:40 (High, todolite-web, pagerduty) with a #1 recurrence label; Sign-in page returns 502 at 19:12 (High, todolite-web, pagerduty); Todos disappear after adding a second item (Medium, todolite-web, manual, regression: accepted) with an Escaped label](../media/releases/incidents/list.png)

Up to 200 incidents, grouped by status: **Open**, **Acknowledged**, **Resolved**, each with how many it holds (an empty group is left out). Inside a group they are newest first, by when they started. Each row shows the severity on the left, then the title, the service, where it came from (*manual*, *pagerduty*, *opsgenie* or *generic*), when it started, and the state of its regression test. On the right, a **#N** label with a repeat icon when the same problem happened before (recurrence number N), and **Escaped** when it is marked as an escaped defect. In the demo, the 503 sign-in outage is recurrence #1 of the 502 one a week earlier: same service, same title once the numbers are taken out.

Click a row to show that incident in ⑤; the selected row is shaded with a dark bar on its left. When the page opens, or when you change tab, the first incident the list was given (the one that started most recently) is selected.

## 5. The selected incident

![The incident Todos disappear after adding a second item: labels Medium, Resolved and Escaped; todolite-web · manual · started with a date; a Status menu set to Resolved and a Delete button; Affected http://localhost:8080/todos, Release 2.1.0 with its commit, Regression test: Regression: two todos stay in the list, Escaped: missed by Add a todo with a note; a description; and the Escaped defect box with Should have been caught by set to Add a todo, the note, Mark escaped and Clear](../media/releases/incidents/detail.png)

*The escaped defect, selected. It was missed by the test *Add a todo*, which only checks that one item is visible.*

The card on the right shows one incident in full. At the top: its severity, its status, *Recurrence #N* and *Escaped* labels when they apply, its title, and the service, where it came from and when it started.

- **Status**: open, acknowledged or resolved. Changing it saves straight away (the incident then moves to that group in ④); *resolved* also records the time it was resolved.
- **Propose regression test** asks for a suggested test again (shown only when there is no regression test and no suggestion waiting for review).
- **Delete** removes the incident after you confirm.
- **Details**: the incident link, the affected address, the release and commit, the previous occurrence (*incident #12*), what TraceIQ matched it to (a test and/or a failure group, with the reason), its regression test or suggestion (which links to *Proposals*), and how it escaped, when it is marked.
- **Description**, when the incident has one.
- **Escaped defect**: under *Should have been caught by*, choose the test that should have caught it, or **No test existed**; add a *Note* on why it slipped through, and press **Mark escaped**. **Clear** removes the mark.

> **How TraceIQ spots a repeat, and the related test**
>
> - **A repeat** has the same service and the same title once numbers, IDs and times are taken out. "Checkout API 503 for 12% of requests at 10:42" and "Checkout API 502 for 40% of requests" are the same problem. The new incident is labelled *Recurrence #N* and points to the previous one.
> - **The related test** is one that opens the affected address (the best evidence), or else one whose name shares at least two significant words with the title. A failure group from *Failure triage* is matched by shared words too. These are guesses, shown with their reason.

## 6. Escaped defects by month

![The table Escaped defects by month with the columns Month, Escaped, Missed by a test, No test, Test added after, and one row: 2026-10, 1, 1, 0, 1 (100%)](../media/releases/incidents/months.png)

The same numbers as the tiles in ③, one row per month (the month the incident started), over the same 180 days. The table appears once there is at least one escaped defect.

## Watch it in use

[▶ Watch the video](../media/releases/incidents/walkthrough.mp4)

*Under a minute, with a spoken commentary. The captions at the bottom show the same words, so it also works with the sound off. Nothing is saved in the video.*

## How to…

### …record an incident by hand

1. Press **New incident**. (You need the *editor* role.)
2. Type the title, pick the severity, and fill in the service and affected address if you know them.
3. Leave **Propose a regression test** ticked, choose the suite, and press **Record incident**.
4. Review the suggested test on **Today → Proposals**.

### …send incidents in from PagerDuty or Opsgenie

1. Ask a project *admin* to open **Webhook setup** and press **Create webhook secret**.
2. Copy the address and the secret into the paging tool's webhook settings (the secret is shown only once).
3. Choose where suggested tests should go under **Regression proposals go to**. New incidents then arrive by themselves, each with a suggested test.

### …record that a test missed a bug

1. Click the incident in the list, so it shows on the right.
2. Under **Escaped defect**, choose the test that should have caught it, or **No test existed**.
3. Add a short note and press **Mark escaped**. The tiles at the top update.

## Good to know

- **Suggested tests are only suggestions.** They wait on *Proposals* until a person accepts them (a workspace can let confident ones through automatically; an incomplete draft never). Without an AI set up, the suggestion is an empty outline to fill in.
- **A repeat is recognised from the incident as it arrived.** Editing an incident later doesn't change what it is matched with.
- **Incidents feed the delivery report.** The *Delivery (DORA)* report uses them for "change failure rate" and "time to restore".
- **Who can do what.** Anyone with access to the project can look. Recording, editing, marking and deleting need the *editor* role. Every change is written to the audit log.
- **Times are shown in your own time zone.**

## Related pages

> **Proposals**
>
> Where suggested regression tests wait for review.

> **Delivery (DORA)**
>
> Change failure rate and time to restore, worked out from deployments and incidents.

> **Failure triage**
>
> Failures grouped by cause; an incident can be matched to one of these groups.

> **On-call**
>
> Who gets paged when a monitor finds the live site down.

## Video script

What the walkthrough says, in order. The video and every picture on this page are made by the Playwright script [`releases-incidents.spec.ts`](../../../../e2e/docs-capture/pages/releases-incidents.spec.ts).

1. Incidents records problems that happened in production, for the project chosen in the sidebar.
2. The tiles count escaped defects: bugs your tests let through, over the last hundred and eighty days.
3. The tabs show all incidents, only open ones, or only escaped defects.
4. The list is grouped by status. Recurrence and Escaped labels sit on the right of a row.
5. Click an incident to see it on the right, where its status is changed and escaped defects are marked.
6. New incident records one by hand.
7. Give it a title, a severity and the service. TraceIQ can then suggest a test that would catch it.
8. Cancel closes the form without saving anything.
9. Webhook setup lets your paging tool send incidents here by itself.
