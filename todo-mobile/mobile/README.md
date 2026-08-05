# Todo Mobile (Flutter)

Mobile equivalent of the web todo app in this repo. It talks to the same
Express REST API (`server.js`):

| Action | Endpoint |
|---|---|
| List todos | `GET /api/todos` |
| Add todo | `POST /api/todos` |
| Toggle complete | `PATCH /api/todos/:id` |
| Delete todo | `DELETE /api/todos/:id` |

## Running

Start the backend first (from the repo root):

```bash
npm start          # listens on :3000
```

Then run the app. The API base URL defaults to `http://10.0.2.2:3000`
(the Android emulator's alias for the host machine). Override it with a
`--dart-define`:

```bash
cd mobile
flutter run                                            # Android emulator
flutter run --dart-define=API_BASE_URL=http://192.168.1.10:3000   # real device
```

Note: the manifest enables cleartext HTTP (`usesCleartextTraffic`) because the
dev API is plain `http://`. Remove that if the API moves behind HTTPS.

## Tests

```bash
flutter test
```

- `test/todo_api_test.dart` — unit tests for the REST client.
- `test/todo_list_screen_test.dart` — widget tests against an in-memory fake
  of the Express server.

## Test hooks (Appium / TraceIQ)

Interactive widgets carry stable semantics labels, exposed on Android as
`content-desc` (Appium accessibility-id, `~` locators):

| Element | Accessibility id |
|---|---|
| New-todo text field | `new-todo-input` |
| Add button | `add-todo-button` |
| Toggle checkbox | `Toggle <title>` |
| Delete button | `Delete <title>` |
