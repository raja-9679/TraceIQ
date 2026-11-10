<!-- Generated from frontend-quality.html by e2e/docs-capture/export_md.py. Edit the HTML, not this file. -->

# Frontend quality

Tests · Everyone with access to the project can see it · Editors can set up crawls and generate tests

> **In short**
>
> Checks on how well your website itself is built, beyond what single tests check: broken links and pictures across the whole site, whether pages stay fast and light, which page elements tests keep failing to find, and tests made straight from a component library.

## What it is for

A test checks one journey. Frontend quality looks across the site and answers **"What is quietly getting worse?"**: a link to a page that no longer exists, a picture that doesn't load, a page that got twice as heavy last month, a button the tests can only find by luck.

> **Testers**
>
> Find broken links and fragile tests before users or the next release do.

> **Developers**
>
> See which page got slower or heavier, and which run it started in.

> **Designers**
>
> Turn every component in the design system's Storybook into a visual and accessibility check.

**Where to find it:** in the sidebar, open **Tests → Frontend quality**.

## A tour of the screen

The page always works on the project chosen at the top of the sidebar. It has four tabs; each one starts with a short explanation strip, then a list on the left and the selected item in a card on the right (the first item is already selected). The demo's web project has one saved crawl of example.com, a month of page measurements and a few selectors that broke, so every tab has something to show. The numbers match the sections below.

![The Frontend quality page with six numbered parts: 1 the four tabs, 2 the crawler's explanation and the New crawl button, 3 the Crawls list, 4 the crawl example.com link check (demo) with Run now and Delete, 5 the Run menu with the run's counts, 6 the problem filters and the table of problems](../media/tests/frontend-quality/page-annotated.png)

*The Frontend quality page for the demo's web project, on its first tab. Click the picture to see it larger.*

## 1. The four tabs

Under the page title. To see another project, change the project at the top of the sidebar.

| Tab | What it shows |
|---|---|
| **Link & asset crawler** | Visits your site page by page and checks every link, picture, script and style sheet. Shown first, and described in parts ② to ⑥. |
| **Page budgets** | Speed and size of pages, measured every time a test with a *check-page-budget* step runs, against the limits ("budgets") set in that step. See [Page budgets](#budgets). |
| **Selector health** | The page elements tests keep failing to find, or that TraceIQ keeps having to repair. See [Selector health](#selectors). |
| **Storybook** | Makes one test per component story from your Storybook (a catalogue of a site's building blocks). See [Storybook](#storybook). |

## 2. New crawl

A **crawl** starts at one address, follows the links it finds to other pages of the same site, and checks everything each page uses. The strip says so, and **New crawl** opens its settings in a card above the list (the button is hidden while the card is open):

![The New crawl card: Name, Start URL with the note Must be one of this project's hosts: example.com, localhost; Max depth 3, Max pages 100, Delay between requests to one host 250, Slow threshold 3000, Also crawl these project hosts, Schedule (cron, UTC, optional), the ticked boxes Respect robots.txt and Check links to other sites, and Save crawl and Cancel](../media/tests/frontend-quality/crawl-form.png)

| Setting | What it does |
|---|---|
| **Start URL** | Where to begin. It must be on one of the project's own sites (the hosts of its environments and suites, listed under the field). |
| **Max depth**, **Max pages** | How many clicks away from the start page to go (0–10, default 3), and the most pages to visit (1–1000, default 100). |
| **Delay between requests** | How long to wait between two requests to the same site, so the crawl doesn't overload it (default 250 ms). |
| **Slow threshold** | Anything that takes longer than this to answer is reported as slow (default 3000 ms = 3 seconds). |
| **Also crawl these project hosts** | Other sites of the same project to follow links into, such as a blog on its own address. |
| **Schedule** | Optional cron expression, in UTC, to repeat the crawl, at most once an hour. See *Schedules* for how cron works. |
| **Respect robots.txt** | Skip the pages the site asks robots not to visit. On by default. |
| **Check links to other sites** | Also check that links to other websites still answer. Those sites are only asked for a status, never crawled. |

**Save crawl** keeps it; **Cancel** closes the card.

## 3. The list of crawls

The project's saved crawls, each with its start address, its schedule if it has one, and how its last run went (*completed*, *running*, *error*, or *not run*). Click one to open it on the right.

## 4. The crawl

![The crawl example.com link check (demo): https://example.com/, depth 2, up to 50 pages, Run now and Delete; Run #6, completed, 6 pages, 31 links checked, 1 skipped by robots.txt; tick boxes per problem type with their counts and per element kind, a Search URLs box, and a table of five problems: a broken link to /careers (HTTP 404), a stylesheet loaded over http (mixed content), a long redirect chain for /team, a slow /pricing page (3650 ms), and a link to maps.example.net that was not checked](../media/tests/frontend-quality/crawl.png)

*The demo's crawl, on its latest run.*

The card starts with the crawl's name, its start address, depth and page limit (and its schedule, or "ignores robots.txt", when set). **Run now** starts a run; **Delete** asks first, then deletes the crawl and its history.

## 5. Run

The **Run** menu picks one of the crawl's runs (the newest is shown first). For a finished run it says how many pages were crawled and links checked, and whether it stopped at a limit or skipped pages because of robots.txt. While a run is going it shows *running…* and the page checks again every 5 seconds. A crawl that has never run says "This crawl has not run yet."

## 6. Problems

A table of problems with what was wrong, the address, the kind of element, the status, the time it took, and the page it was found on. Tick the problem types (each shows its count) or element kinds to filter, or search the addresses. The table shows 100 problems at a time.

| Problem | Meaning |
|---|---|
| **Broken (4xx/5xx)** | The address answered with an error, such as 404 "not found". |
| **Network error** | No answer at all. |
| **Slow** | Answered, but slower than the slow threshold. |
| **Redirect loop**, **Long redirect chain** | The address sends the browser round in circles, or through many steps before arriving. |
| **Mixed content** | A secure (https) page loads something over plain http. |
| **Not checked (private address)** | The link points at a private or internal address, which TraceIQ doesn't visit. |

## Page budgets

![The Page budgets tab: the Pages list with https://example.com/ (default, 31 samples, 6 over budget); on the right its card: LCP 1749 ms, CLS 0.044, INP no data, TTFB 141 ms, page weight 884 KB, 23 requests, each with small bars for recent runs; Budgets (latest run) all within; SEO checks: title, h1 and lang ok, a warning for a missing meta description; Latest from run #800 (Homepage loads)](../media/tests/frontend-quality/budgets.png)

In the demo, the test *Homepage loads* measures example.com's home page. Nothing appears here until a test contains a **check-page-budget** step. Then every run of that test measures the page, and the **Pages** list shows the pages measured in the last 30 days, those that went over budget first, with their number of samples and how many were over budget. Click a page to see in its card, for its latest run and as small bars for up to 16 recent runs:

- **LCP** (how long the main content takes to appear), **CLS** (how much the layout jumps while loading), **INP** (how quickly the page reacts to a click), **TTFB** (how long the server takes to start answering), **page weight** and **request count**. A red bar is a run that went over that budget.
- **Budgets**: each limit set in the step, and whether the page was within it or over.
- **SEO checks**: basic search-engine checks such as a page title. They only fail the test if the step says so.
- A link to the run the latest numbers come from.

## Selector health

![The Selector health tab: a Window menu set to Last 90 days, the line 2 selectors, 1 heal, 7 failures, the list Selectors, worst first with #todo-list li (Add a todo, 6 failed, 1 heal proposal) and h1 (Homepage loads, 1 failed); on the right the card for #todo-list li: Failed 6, Heal proposals 1, Runtime-healed 0, heals and failures per week, the sturdier locator [data-testid=todo-item] with a Copy button, and recent runs](../media/tests/frontend-quality/selectors.png)

A **selector** is the short code a test step uses to find an element on the page. When a page changes, selectors break. The strip has a **Window** menu (30, 90, 180 or 365 days) and the totals for that window; the list shows the selectors with the most trouble, worst first. Click one to see in its card: the test (a link to edit it) and step it belongs to, how often it failed, how often TraceIQ proposed a repair or repaired it during a run, a bar per week, reasons it is fragile, **sturdier locators** worked out from the page as it was when the step failed, each with a **Copy** button, and links to recent runs. Suggestions are never applied for you: edit the test to use one.

## Storybook

![The Storybook tab: a description, a Storybook URL field and a Load stories button](../media/tests/frontend-quality/storybook.png)

1. Enter the address of your Storybook (it must be on one of the project's sites) and press **Load stories**. A **Stories** card appears below.
2. Tick the stories you want (**Filter stories** and **Select all shown** help), choose the suite under **Add cases to suite**, and whether each test checks the look (**Visual match**), **Accessibility**, or both.
3. Press **Generate**. You get one test per story. Generating again for a story updates its existing test instead of making a second one, and keeps its saved reference picture.

## Watch it in use

[▶ Watch the video](../media/tests/frontend-quality/walkthrough.mp4)

*About 50 seconds, with a spoken commentary. The captions at the bottom show the same words, so it also works with the sound off.*

## How to…

### …check a site for broken links every night

1. On the **Link & asset crawler** tab, press **New crawl**.
2. Give it a name, enter the home page as the **Start URL**, and under **Schedule** type `0 3 * * *` (3 a.m. UTC every day).
3. Press **Save crawl**, pick the crawl in the list, then press **Run now** in its card for a first result. The card shows "running…" until it is done.
4. Tick **Broken (4xx/5xx)** to see only the links that need fixing, and the page each was found on.

### …keep a page fast

1. In a test that opens the page, add a **check-page-budget** step with the limits you want, for example how long the main content may take.
2. Run the test. The page appears on the **Page budgets** tab.
3. Check back after a few runs: red bars show the runs that went over.

## Good to know

- **Who can do what.** Anyone with access to the project can see every tab. Creating, running and deleting crawls, and generating tests from Storybook, need the *editor* role.
- **Only your own sites are crawled.** The start address and extra hosts must belong to the project. Links to other websites are only checked for an answer, and private or internal addresses are never visited.
- **Limits.** A project can hold up to 20 crawls, run up to 3 at the same time, and a crawl stops after 15 minutes or 1,000 pages, whatever its settings say ("stopped at a limit").
- **Crawl schedules are in UTC**, unlike suite schedules, which have their own time zone.
- **Re-runs that check a suggested fix don't count.** Page budgets and selector health leave out the check runs TraceIQ makes for proposals.
- **Screen sizes, languages and themes** are not on this page: they are run options on a suite (*Screen sizes & locales*, see *Suites*).

## Related pages

> **Accessibility**
>
> The next page in this guide: every accessibility problem the tests found, and waivers for known ones.

> **Suites**
>
> Add the check-page-budget step, and run suites on several screen sizes and languages.

> **Proposals**
>
> Repairs TraceIQ suggests for broken selectors.

> **Visual review**
>
> Where the visual matches of Storybook tests are reviewed.

## Video script

What the walkthrough says, in order. The video and every picture on this page are made by the Playwright script [`tests-frontend-quality.spec.ts`](../../../../e2e/docs-capture/pages/tests-frontend-quality.spec.ts).

1. Frontend quality checks how well your website itself is built, beyond single tests.
2. It has four tabs. The first crawls your site and checks every link and file.
3. Saved crawls are listed on the left. The selected one shows its newest run and the problems it found.
4. New crawl sets one up: where to start, how deep to go, and how many pages.
5. Only this project’s own sites can be crawled. Cancel closes the form.
6. Page budgets tracks page speed and size, measured by a step in your tests.
7. Selector health lists the page elements that tests keep failing to find.
8. Storybook turns each story of a component library into a test.
