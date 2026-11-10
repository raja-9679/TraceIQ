<!-- Generated from requirements.html by e2e/docs-capture/export_md.py. Edit the HTML, not this file. -->

# Requirements

Releases · Everyone with access to the project can see it · Editors can import and generate

> **In short**
>
> Bring your tickets (user stories, requirements) in from Jira or Linear. TraceIQ reads each ticket's acceptance criteria, can turn every criterion into a suggested test, and shows for each ticket whether its tests are passing, failing, not run yet, or missing.

## What it is for

Requirements answers **"Is everything we promised actually tested, and does it work?"** A ticket usually lists its *acceptance criteria*: the conditions that must be true for the work to count as done, such as "a guest can pay by card". This page keeps a copy of those tickets next to your tests, points out criteria that are too vague to test, and drafts a test for each of the others.

> **Product owners**
>
> See at a glance which tickets are covered by passing tests and which have none yet.

> **Testers**
>
> Get a first draft of a test for each acceptance criterion, and a warning when a criterion is too vague.

> **Leads**
>
> Find the gaps before a release: tickets that are failing or not covered at all.

**Where to find it:** in the sidebar, open **Releases → Requirements**.

## A tour of the screen

The page always shows the project chosen in the sidebar's project picker; there is no project menu on the page itself (until a project is chosen, it says "Pick a project"). The buttons are in the header, the four counts under it, then the list of imported tickets on the left and the selected ticket on the right. The numbers on the screenshot match the sections below.

![The Requirements page with four numbered parts: 1 the Sync all and Import buttons, 2 the four coverage counts, 3 the list of tickets grouped Failing, Not covered, Not run yet and Covered, 4 the selected ticket DEMO-101 with its criteria, tests and Generate tests](../media/releases/requirements/page-annotated.png)

*The Requirements page for the demo's web project. Click the picture to see it larger.*

## 1. Sync all and Import

![The page header: the title Requirements, a line about what the page does, and the buttons Sync all and Import](../media/releases/requirements/toolbar.png)

The two buttons sit in the page header, under the title.

- **Sync all** fetches every imported ticket again from Jira or Linear, so its title, status, description and acceptance criteria are up to date. A message says how many were synced and how many failed. It is greyed out while nothing has been imported.
- **Import** opens the import panel just under the header (the button then reads **Close import**).

![The import panel, saying Your Jira connection is turned off. Enable it on the Issue trackers page to import from it.](../media/releases/requirements/import.png)

*The demo workspace's Jira connection is switched off, and the panel only offers connections that are switched on, so it says where to turn it on. With no connection at all, it says where to add one.*

Once a connection exists (set up on **Setup → Issue trackers**, and switched on), the panel has:

- **Connection**: which Jira or Linear connection to use.
- **For Jira, JQL**: a Jira search, for example `project = SHOP AND sprint in openSprints()`. **For Linear**: a team key, words in the title, and a label.
- **Search** shows up to 25 matching tickets with tick boxes; ones you already imported are marked "imported". **Import selected** brings in the ticked ones.
- Or type ticket keys (`SHOP-12, SHOP-14`) and press **Import by key**. Keys that don't exist are listed in a warning.

Importing a ticket that is already there updates it instead of adding a second copy.

## 2. The four counts

![Four tiles, each with a coloured dot: Covered 2, Failing 1, Not run yet 1, Not covered 1](../media/releases/requirements/tiles.png)

Every imported ticket falls into exactly one of these. The list in ③ is grouped the same way.

| Count | A ticket counts here when… |
|---|---|
| Covered | …it has linked tests, and the newest result of every one of them passed. |
| Failing | …the newest result of at least one linked test failed (or ended in error). |
| Not run yet | …it has linked tests, none failing, but at least one has never run. |
| Not covered | …no test is linked to it yet. |

> **"Linked" and "newest result"**
>
> A test is linked to a ticket when its requirement reference is the ticket's key (for example `DEMO-101`). Links come from accepting a test generated here, or are added by hand on the *Traceability* page; both count. "Newest result" means the test's most recent result in this project, whichever version of the app it ran on. To judge one particular version, use *Release readiness*.

## 3. The list of tickets

![The list in four groups, each with its count: Failing 1 (Paying for Pro works end to end, DEMO-104, 2 criteria, 2 linked test(s)); Not covered 1 (Users can export their todos as CSV, DEMO-105, 0 linked test(s)); Not run yet 1 (A user can reset a forgotten password, DEMO-103); Covered 2 (A user can sign in, DEMO-101, selected; A user can add a todo, DEMO-102, 1 ambiguous)](../media/releases/requirements/list.png)

The tickets are grouped by their count from ②, in the order that needs attention first: **Failing**, **Not covered**, **Not run yet**, **Covered**. Each group shows how many tickets it holds, and an empty group is left out. Inside a group, tickets are in order of their keys.

Each row shows the ticket's title, then its key, how many acceptance criteria were found, how many tests are linked, how many criteria are too vague ("ambiguous", in amber), and "sync error" in red when the last fetch from Jira or Linear failed. On the right is its count from ②. Click a row to show that ticket in ④; the selected row is shaded with a dark bar on its left. When the page opens, the first ticket by key is selected.

The demo's web project has five tickets, one or more in each count: `DEMO-101` and `DEMO-102` are covered (102 has one vague criterion), `DEMO-103`'s test has never run, `DEMO-104` has a failing test, and `DEMO-105` has no test yet, only a suggested one.

## 4. The selected ticket

![The selected ticket: DEMO-101 with the labels Covered and Done, the title A user can sign in, the line 2 criteria · 0 proposed · 1 linked test(s) · synced with a date; the buttons Open in jira, Re-sync and Remove; two acceptance criteria written as Given/When/Then scenarios (AC1 Sign in with valid credentials, AC2 Wrong password); Covered by Sign in as alice · passed; and a Generate tests from the criteria box with Target suite, Target URL (optional), Include ambiguous and Generate tests](../media/releases/requirements/detail.png)

The card on the right shows one ticket in full. At the top: its key, its count from ②, its status in Jira or Linear (for example *Done*), its title, and a line with the number of criteria, how many already have a suggested test ("proposed"), how many are ambiguous, how many tests are linked, and when it was last fetched. If the last fetch failed, a red *Sync error* box says why.

- **Open in jira / linear** (when the ticket has an address) opens the original ticket. **Re-sync** fetches just this one again. **Remove** takes the ticket off this page after you confirm; the tests and their links stay.
- **Acceptance criteria**: each criterion found in the ticket, numbered AC1, AC2… A criterion that already has a suggested test shows its number and state (for example *#41 pending*), which links to the *Proposals* page. A vague one says why in amber, for example *Ambiguous: vague term "fast"*. In the picture, `DEMO-101`'s two criteria are Given/When/Then scenarios.
- **Covered by**: the linked tests and the result of each one's newest run ("not run" if it never ran).
- **Generate tests from the criteria**: pick the **Target suite** the suggested tests should go into, optionally the **Target URL** of the page they should start on, and whether to **Include ambiguous** criteria. Then press **Generate tests** (greyed out until a suite is chosen).

> **Generated tests are suggestions, not tests yet**
>
> Generate tests makes one *proposal* per criterion and puts it in the queue on *Today → Proposals*, where a person accepts or rejects it. Only an accepted proposal becomes a real test, and it is then linked to the ticket by itself. A workspace can allow confident suggestions to be accepted automatically; a draft that still needs work never is.
>
> If no AI is set up for TraceIQ (or the AI allowance has run out), each proposal is an empty outline marked *needs authoring* that someone fills in.

| How criteria are found | Example |
|---|---|
| Given / When / Then scenarios | *Given a guest with items in the cart, When they pay by card, Then they see the order number* |
| A heading "Acceptance criteria" followed by lines | The lines under it, one criterion each |
| Bullet lists and checklists | *- [ ] The total includes VAT* |
| Numbered labels | *AC1: The page shows the delivery date* |

If the ticket's description contains none of these, its **title** becomes its one criterion. When a ticket is synced again, a criterion whose wording hasn't changed keeps its suggested test; a reworded one starts afresh.

A criterion is flagged as **ambiguous** when it is shorter than three words, uses a vague word such as *fast*, *easy*, *intuitive* or *works as expected*, or names nothing a test could check (Given/When/Then without a Then). Speed words are fine when a number goes with them: "loads within 2 s" is testable, "loads fast" is not.

## Watch it in use

[▶ Watch the video](../media/releases/requirements/walkthrough.mp4)

*Under a minute, with a spoken commentary. The captions at the bottom show the same words, so it also works with the sound off.*

## How to…

### …bring in this sprint's tickets

1. Press **Import**. (You need the *editor* role and to be a member of the workspace.)
2. Choose the connection and type a search, for example `project = SHOP AND sprint in openSprints()`.
3. Press **Search**, tick the tickets you want, and press **Import selected**.

### …get draft tests for a ticket

1. Click the ticket in the list, and read its acceptance criteria in the card on the right. Reword vague ones in Jira or Linear, then press **Re-sync**.
2. Under **Generate tests from the criteria**, choose the **Target suite** and, if you know it, the page address.
3. Press **Generate tests**. A message says how many proposals were queued.
4. Open **Today → Proposals** to review and accept them.

### …find what isn't tested before a release

1. Look at **Failing** and **Not covered** in the counts.
2. Those two groups come first in the list. Click each ticket in them to see, under **Covered by**, which test is failing, or that there is none.
3. For a release check of one version, use **Release readiness**, which judges the same links on that version's runs.

## Good to know

- **TraceIQ only reads from Jira and Linear.** Importing and syncing never change the ticket. (Tickets *to* Jira are filed from *Failure triage*; Linear connections are import-only.)
- **Who can do what.** Anyone with access to the project can look. Importing, syncing, removing and generating need the *editor* role; searching and importing also need workspace membership, because the connection belongs to the whole workspace.
- **One project at a time.** A ticket is imported into the project chosen in the sidebar, and only that project's tests count towards it. To see another project's tickets, switch project in the sidebar.
- **Criteria are not re-generated by accident.** A criterion whose suggestion is still waiting, or was already accepted, is skipped when you press Generate tests again.
- **Check runs don't count.** The runs TraceIQ makes to check a suggested fix are left out of the coverage.
- **Imports, removals and generated proposals are recorded** in the audit log.

## Related pages

> **Traceability**
>
> The next page in this guide: link tests to tickets by hand, and find tests linked to nothing.

> **Proposals**
>
> Where the generated tests wait for someone to accept or reject them.

> **Issue trackers**
>
> Connect Jira or Linear, so tickets can be imported.

> **Release readiness**
>
> The same tickets, judged on the runs of one version of your app.

## Video script

What the walkthrough says, in order. The video and every picture on this page are made by the Playwright script [`releases-requirements.spec.ts`](../../../../e2e/docs-capture/pages/releases-requirements.spec.ts).

1. Requirements lists the tickets imported from Jira or Linear, and how well they are tested.
2. The four counts sort them: covered, failing, not run yet, and not covered.
3. The list on the left groups the tickets the same way, failing ones first.
4. Click a ticket to see it on the right: its acceptance criteria and the tests that cover it.
5. Generate tests turns each criterion into a suggested test, for a person to review.
6. Import, at the top, brings in more tickets, from a search or by their keys.
7. Sync all fetches the latest title, status and criteria of every ticket.
