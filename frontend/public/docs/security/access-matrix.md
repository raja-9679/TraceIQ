<!-- Generated from access-matrix.html by e2e/docs-capture/export_md.py. Edit the HTML, not this file. -->

# Access matrix

Security · Everyone with access to the project can see it · Editors can create, change and run matrices

> **In short**
>
> A table that says who may reach what in your app: one row per page or request, one column per kind of user, and in each cell "allow" or "deny". TraceIQ then tries every cell as that user and tells you where reality differs, for example when a visitor who isn't logged in can open an admin page.

## What it is for

**Access control** is the part of an app that decides who may see or do what. When it is broken, people see things they shouldn't: another customer's orders, an admin screen, someone else's account. It is one of the most common serious security problems, and ordinary tests rarely catch it, because they test what users *should* be able to do, not what they shouldn't.

An access matrix answers **"Can each kind of user reach only what they are meant to?"** You write down the rules once; TraceIQ checks them every time you run the matrix, and turns any rule that was broken into a security finding.

> **Security leads**
>
> Turn an access policy ("only admins see the user list") into a check that runs again and again.

> **Testers**
>
> Check every role against every important page in one go, instead of logging in and out by hand.

> **Developers**
>
> See exactly which user reached which address and what the server answered, before a release.

**Where to find it:** in the sidebar, open **Security → Access matrix**.

## A tour of the screen

The page always works on the project chosen at the top of the sidebar. On the left is the list of that project's matrices; on the right, the selected matrix in a card. When you arrive, the first matrix in the list is already open. The demo's web project has one matrix: three rows (the todo list as a request, the admin panel as a page, and one user's todo read by its ID) checked against two logged-in users, alice and bob, and someone who is not logged in.

![The Access matrix page: a New matrix button in the header, a Matrices list on the left with one matrix (Todo app roles (demo), 3 rows, 3 identities), and on the right a card for that matrix open on its Edit grid tab](../media/security/access-matrix/page.png)

The picture below shows the whole page. The numbers match the sections below.

![The Access matrix page with the demo matrix open, with nine numbered parts: 1 the New matrix button, 2 the Matrices list, 3 the matrix name and counts, 4 the Edit grid and Runs and results tabs, 5 the name, base URL, description and denied-statuses fields, 6 the identities, 7 the grid of rows and Allow or Deny cells, 8 the security-testing prerequisites, 9 the Save changes button](../media/security/access-matrix/page-annotated.png)

*The demo matrix in the web project. Click the picture to see it larger.*

## 1. New matrix

**New matrix**, at the top right of the page, opens an empty matrix in the card on the right. Fill it in and press **Create matrix**; nothing is saved before that. The button is there only when a project is chosen in the sidebar.

## 2. The list of matrices

The **Matrices** list holds the project's matrices, with how many there are, and for each one its number of rows and identities. Click one to open it on the right; the selected one is highlighted. To see another project's matrices, change the project at the top of the sidebar.

## 3. The matrix card

The card starts with the matrix's name, then its number of rows, its number of identities (columns) and its description.

## 4. Edit grid and Runs & results

Two tabs inside the card: **Edit grid** (parts ⑤ to ⑨, what you see here) and **Runs & results** (see [Reading the results](#results)). A new matrix has no tabs until it is created.

## 5. Name and address

![Four fields: Name (Todo app roles (demo)), Base URL for relative paths (http://localhost:8080), Description, and Statuses that count as denied (401, 403, 404, the default)](../media/security/access-matrix/fields.png)

- **Name** (required) and **Description**: for people.
- **Base URL**: the address of the app. Rows can then give just a path, such as `/api/todos`.
- **Statuses that count as denied**: when the app refuses someone, it answers with a status number. By default `401` (not logged in), `403` (not allowed) and `404` (not found) count as "denied". Change the list if your app refuses in another way.

## 6. Identities (columns)

![Identities (columns): alice (todolite) and bob (todolite) ticked, and Anonymous column (no session, no credential headers) ticked](../media/security/access-matrix/identities.png)

Each column is one **identity**: who TraceIQ pretends to be. A **persona** is a saved login for a particular user or role, for example a normal customer and an administrator; the list shows the project's personas and the workspace-wide ones. **Anonymous** is someone who is not logged in: TraceIQ sends no login and none of the project's login headers. Choose at least one column; a matrix can have up to 20.

## 7. The grid

![The grid of the demo matrix. Row List my todos (API), a GET request to /api/todos: Allow with status 200 for alice and bob, Deny for Anonymous. Row Admin panel, a Page at /admin with the element selector #admin-panel: Allow for alice, Deny for bob and Anonymous. Row Read alice's todo by id, a GET request to /api/todos/{id} with ID swap ticked, owner persona alice, placeholder {id}, owner URL /api/todos, extractor 0.id: Allow for alice, Deny for bob and Anonymous](../media/security/access-matrix/grid.png)

Each row is one thing to try. Press **Add row** for another, and **Remove row** to drop one (up to 200 rows).

| Kind of row | What TraceIQ does |
|---|---|
| **Request** | Sends one request (GET, POST, DELETE…) to a URL or path and looks at the answer's status. A *Body* field appears for methods that send data. |
| **Page** | Opens the page in a browser. With an *Element selector* (a description of one part of the page, such as the admin panel), it also checks whether that part is shown. |
| **Test case** | Runs one of your existing tests as each identity. These run as ordinary test runs, so they also appear under *Runs*. |

**Click a cell** to change what should happen for that identity. Each click moves on to the next choice:

| Cell | Meaning |
|---|---|
| Allow | This identity should get in. The small *Status* box can require one exact answer (for example 200); empty means any success. |
| Deny | This identity should be refused. The *+deny* box adds one more status that counts as a refusal for this cell. |
| Skip | Not checked. Cells start as Skip. |

> **ID swap: can one user open another user's things?**
>
> A common hole is that user B can open user A's order just by changing the number in the address. Tick **ID swap** on a row to test this: TraceIQ first logs in as the *owner persona*, asks the *owner URL* for a list of that person's things, takes an ID from the answer (the *extractor* says where), puts it in place of the placeholder (`{id}`) in the row's URL, and then tries that address as every identity. Usually you expect Allow for the owner and Deny for everyone else. With two personas from different customer accounts, this checks that one customer can't see another's data.

## 8. Security-testing prerequisites

![Security-testing prerequisites: Security testing enabled, Authorized domains example.com, localhost, OK: Security testing is enabled for this project, OK: All targets are on the authorized domain list, and an Edit security settings link](../media/security/access-matrix/prerequisites.png)

Request and page rows contact your app directly, so TraceIQ only runs them when the project allows security testing. This box checks your draft against those rules before you run it:

- **Security testing** must be enabled for the project, and
- every address must be on the project's **authorized domains**. Both are set under *Scans & findings → Authorization* (the **Edit security settings** link goes there).
- A row that **changes data** (any method other than GET, HEAD or OPTIONS, including the owner request of an ID swap) also needs you to tick "I am authorized…" when you run the matrix.

In the demo project both are in order, so the box shows two green *OK* lines; otherwise each problem is listed under *Needs attention*. Test-case rows need none of this. The box is only a preview: when you run the matrix, TraceIQ checks again, and if it refuses, its reason is shown word for word.

## 9. Save changes

On a saved matrix, **Save changes** saves your edits; it sits next to **Delete matrix**, which asks first and also deletes all of the matrix's runs. For a new matrix the button says **Create matrix** (greyed out until it has a name). Nothing is saved until you press the button; leaving the page throws your changes away.

### Reading the results

On a saved matrix, the **Runs & results** tab has a **Run matrix** button, the **Run history** (newest first, each run with its counts) and the results of the selected run (the newest one unless you pick another): the same grid, with each cell showing what was expected, what was observed, and a verdict. While a run is going, the page refreshes every 3 seconds.

![Run history with Run #3, completed: Pass 7, Fail 1, Inconclusive 1. Results of run #3: Pass 7, Fail 1, Inconclusive 1, 1 access granted that should be denied. In the grid every cell passes except Admin panel as bob, Fail — access granted (expected deny, observed 200, element visible), and Admin panel as Anonymous, Inconclusive (expected deny, observed timeout)](../media/security/access-matrix/results.png)

*The demo run: bob, a plain user, could open the admin panel, and the visit as Anonymous timed out.*

| Verdict | What it means |
|---|---|
| Pass | What happened matches the cell. |
| Fail — access granted | The cell said Deny, but the identity got in. This is the serious one: TraceIQ records it as a high-severity security finding. |
| Fail — allow regression | The cell said Allow, but the identity was refused. Something that should work is broken; it is counted, but not a security finding. |
| Inconclusive | TraceIQ couldn't tell, for example the request timed out, the server had an error, or it was sent somewhere else. It is never counted as a pass. |
| Pending | Still waiting for a result. |

**Click a cell** to see its evidence: the status, the address it ended up on, whether the element was shown, the ID that was swapped in, the response headers and a piece of the response (with secrets removed), a screenshot for page rows, and a link to the test run for test-case rows.

![The evidence panel for Admin panel as bob (todolite): Fail — access granted, Expected deny, Reason: the protected element is visible (status 200) — access granted; Status 200, Final URL http://localhost:8080/admin, Element present](../media/security/access-matrix/evidence.png)

*The evidence for the failed cell opens at the side of the page.*

Below the results, **Security findings from this matrix** lists the findings it raised, each with its status and a link to the run that showed the problem.

![Security findings from this matrix: one Open finding, Access granted that should be denied: Admin panel as bob (todolite), with a View evidence in run #3 link](../media/security/access-matrix/findings.png)

## Watch it in use

[▶ Watch the video](../media/security/access-matrix/walkthrough.mp4)

*About a minute, with a spoken commentary. The captions at the bottom show the same words, so it also works with the sound off. Nothing in the video is changed.*

## How to…

### …check that visitors can't reach admin pages

1. Make sure the project allows security testing and lists your app's domain (*Scans & findings → Authorization*, project admin).
2. Choose the project in the sidebar, press **New matrix** at the top of the page, and give the matrix a name and your app's **Base URL**.
3. Tick an administrator persona, a normal-user persona and **Anonymous**.
4. Add a row per admin page or address. Set the administrator's cell to **Allow** and the others to **Deny**.
5. Press **Create matrix**. In the matrix's card, open the **Runs & results** tab and press **Run matrix**.
6. Any red *Fail — access granted* cell is a hole. Click it for the evidence.

### …check that one customer can't see another's data

1. Use two personas logged in to two different customer accounts, A and B.
2. Add a request row whose URL contains `{id}`, for example `/api/orders/{id}`.
3. Tick **ID swap**: owner persona = A, owner URL = the address that lists A's orders, extractor = where the ID is in that answer.
4. Set A's cell to **Allow**, and B's and Anonymous's cells to **Deny**. Save and run.

### …follow up a problem it found

1. Pick the matrix in the list, open its **Runs & results** tab and look under **Security findings from this matrix**.
2. Press **View evidence in run #…** to see what happened.
3. Give the finding an owner and work on it in *Scans & findings → Findings register*. After the fix, run the matrix again.

## Good to know

- **Findings look after themselves.** A Deny cell that got through creates one high-severity finding per row and identity, and a later run that gets through again updates the same finding rather than adding another. When a later run denies it again, the finding is marked *Verified fixed* by itself; if access gets through after that, it reopens. A finding marked *false positive* stays that way. Inconclusive cells change nothing.
- **It only looks.** Request and page rows observe what the app answers; the only data they change is what a row itself sends (which is why those rows need your "I am authorized").
- **Old results keep their labels.** Each run stores a copy of the matrix as it was, so editing the matrix later doesn't relabel past results.
- **A run gives up after 45 minutes.** Cells still waiting then become *Inconclusive*, never Pass.
- **A failed test is not proof of "denied".** For test-case rows, a Deny cell passes only if the test failed with a refusal status (such as "expected 200 but got 403"). A test that failed for another reason, such as a timeout, is Inconclusive.
- **Runs count towards your plan.** Test-case rows start ordinary test runs, which count towards the monthly run allowance.
- **Who can do what.** Anyone with access to the project can look at matrices and results. Creating, changing, deleting and running a matrix needs the *editor* role.
- **Old run history.** Finished matrix runs and their screenshots are deleted after the project's retention period, like other runs. The matrix itself is kept.

## Related pages

> **Access reviews**
>
> The next page in this guide: check, by hand and on a schedule, who has access to TraceIQ itself.

> **Scans & findings**
>
> Where the project allows security testing, and where this matrix's findings are tracked.

> **Runs**
>
> The test runs started by test-case rows, with every step, screenshot and log.

> **Suites**
>
> Where the tests used by test-case rows are written.

## Video script

What the walkthrough says, in order. The video and every picture on this page are made by the Playwright script [`security-access-matrix.spec.ts`](../../../../e2e/docs-capture/pages/security-access-matrix.spec.ts).

1. An access matrix checks that each kind of user can reach only what they should.
2. The list on the left holds the matrices of the project chosen in the sidebar. The first one is already open on the right.
3. The columns are identities: saved logins called personas, plus Anonymous, someone not logged in.
4. Each row is something to try: a page, a request, or one of your tests.
5. Each cell says what should happen: Allow, Deny, or Skip. A click changes it.
6. Here only alice, the admin, should reach the admin panel.
7. The Runs and results tab shows what really happened, cell by cell.
8. Red means access was granted that should have been denied. bob, a plain user, reached the admin panel.
9. Click a cell to see its evidence.
10. Each such hole becomes a security finding, listed below the results.
