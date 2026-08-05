# Todo Native (Kotlin)

Native Android equivalent of the web/Flutter todo apps in this repo,
written in Kotlin with classic Views (AppCompat + RecyclerView + Material).
Talks to the same Express REST API (`server.js`).

## Building

Requires JDK 17+ and the Android SDK (`ANDROID_HOME`).

```bash
cd android-kotlin
./gradlew assembleDebug          # APK at app/build/outputs/apk/debug/
./gradlew test                   # JVM unit tests (MockWebServer)
```

The API base URL defaults to `http://10.0.2.2:3000` (Android emulator →
host machine). Override for a real device:

```bash
./gradlew assembleDebug -PapiBaseUrl=http://192.168.1.10:3000
```

Note: cleartext HTTP is enabled in the manifest because the dev API is
plain `http://`. Remove `usesCleartextTraffic` if the API moves to HTTPS.

## Tests

- `app/src/test/.../TodoApiTest.kt` — unit tests for the REST client
  against OkHttp's MockWebServer (list, add, validation error, patch,
  delete, 404).
- TraceIQ suite **"Todo · Mobile (Kotlin)"** — 6 Appium end-to-end cases
  (launch, add, blank-input validation, toggle, delete, restart
  persistence) that run against the debug APK on the Android emulator.

## Test hooks (Appium / TraceIQ)

| Element | Locator |
|---|---|
| New-todo input | `id=co.thehindu.todonative:id/new_todo_input` |
| Add button | `id=co.thehindu.todonative:id/add_todo_button` |
| Empty state | `id=co.thehindu.todonative:id/empty_state` |
| Todo title | content-desc = `<title>` (`~<title>`) |
| Toggle checkbox | content-desc = `Toggle <title>` |
| Delete button | content-desc = `Delete <title>` |

The content-descs intentionally mirror the Flutter app so the two mobile
suites stay symmetric.
