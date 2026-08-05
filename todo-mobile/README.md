# Todo Mobile — sample app for TraceIQ mobile testing

A deliberately small todo app with one REST backend and two native Android
clients, used to exercise TraceIQ's mobile (Appium) test path end-to-end.
The web/API demo app is [`todolite/`](../todolite/); this one exists for
`executor: mobile_appium` cases.

| Piece | Stack | Notes |
|---|---|---|
| `server.js` | Express, in-memory | REST API + tiny web UI on port 3000 |
| `mobile/` | Flutter | The client the sample TraceIQ suite drives |
| `android-kotlin/` | Kotlin + Views | Same features, classic native (own README) |

## Run the server

```bash
docker compose up -d --build       # joins traceiq_traceiq_internal as `todoapp`
curl http://localhost:3000/api/todos
```

The compose file expects the TraceIQ community stack's network
(`traceiq_traceiq_internal`) to exist — start TraceIQ first.

## Build the Flutter APK

```bash
cd mobile
flutter build apk --debug
# → build/app/outputs/flutter-apk/app-debug.apk
```

Upload it as an app build (`POST /api/projects/{id}/app-builds`) and pin
runs to it via `app_build_id`.

**Bump the version on every rebuild you intend to test**
(`version: 1.0.x+N` in `pubspec.yaml`): Appium's UiAutomator2 skips
reinstall when the on-device `versionCode` matches, so an un-bumped rebuild
silently runs the previous binary. (Alternatively set
`MOBILE_ENFORCE_APP_INSTALL=true` on the mobile worker.)

## How the app reaches the server from the emulator

The APK defaults to `http://10.0.2.2:3000`, which inside the bundled
docker-android emulator resolves to the **emulator container's** localhost.
Bridge it to the server container:

```bash
docker exec -d traceiq-android-emulator-1 sh -c \
  'nohup socat TCP-LISTEN:3000,fork,reuseaddr,bind=127.0.0.1 TCP:todoapp:3000 >/tmp/socat3000.log 2>&1 &'
```

Re-run this if the emulator container restarts. Or bake a different URL at
build time: `flutter build apk --debug --dart-define=API_BASE_URL=http://host:3000`.

## Appium-testability notes (why the Flutter code looks the way it does)

Hard-won rules — all four bit us before they were written down (they are
also in the agent-facing AGENT_GUIDE):

1. `main()` calls `SemanticsBinding.instance.ensureSemantics()`. Without
   it Flutter never populates the Android accessibility tree and every
   `Semantics` widget in the app is invisible to Appium.
2. The text field uses `Semantics(identifier: 'new-todo-input')`, not
   `label:`. On editable fields a label merges into the hint text instead
   of becoming `content-desc`, so accessibility-id lookups can never match
   it. `identifier` maps to Android `resource-id`; find it with
   `xpath=//*[@resource-id="new-todo-input"]`.
3. Buttons and static text can use plain `Semantics(label:)` — those do
   become `content-desc` (`~add-todo-button`, `~Toggle <title>`, …).
4. Test the app through the TraceIQ suite ("Todo · Mobile (Flutter)"),
   which encodes the working selectors.
