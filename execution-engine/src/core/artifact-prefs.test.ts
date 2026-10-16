/**
 * The run starter's artifact preferences (Settings → video recording, trace
 * files, screenshot on error). They can only narrow what the capture level
 * allows, and a job from an older backend (no preferences) behaves as before.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    NO_PREFS,
    artifactPrefsFrom,
    keepByPrefs,
    shouldRecordVideo,
} from './artifact-store';

test('no preferences = keep everything the capture level allows (old backends)', () => {
    assert.deepEqual(artifactPrefsFrom(undefined), NO_PREFS);
    assert.deepEqual(artifactPrefsFrom(null), NO_PREFS);
    assert.deepEqual(NO_PREFS, { video: 'always', trace: true, failureScreenshot: true });
});

test('preferences are parsed and unknown video values fall back to always', () => {
    assert.deepEqual(
        artifactPrefsFrom({ video: 'on-failure', trace: false, failure_screenshot: false }),
        { video: 'on-failure', trace: false, failureScreenshot: false });
    assert.equal(artifactPrefsFrom({ video: 'ON-FAILURE' }).video, 'on-failure');
    assert.equal(artifactPrefsFrom({ video: 'sometimes' }).video, 'always');
    // Missing booleans mean "on", matching the Settings page defaults.
    assert.deepEqual(artifactPrefsFrom({ video: 'off' }),
        { video: 'off', trace: true, failureScreenshot: true });
});

test('video is only recorded when the level allows it and the preference is not off', () => {
    assert.equal(shouldRecordVideo('full', NO_PREFS), true);
    assert.equal(shouldRecordVideo('full', { ...NO_PREFS, video: 'on-failure' }), true);
    assert.equal(shouldRecordVideo('full', { ...NO_PREFS, video: 'off' }), false);
    // A preference can never raise the capture level.
    assert.equal(shouldRecordVideo('standard', { ...NO_PREFS, video: 'always' }), false);
    assert.equal(shouldRecordVideo('none', NO_PREFS), false);
});

test('on-failure keeps the video only for failed jobs', () => {
    const p = { ...NO_PREFS, video: 'on-failure' as const };
    assert.equal(keepByPrefs('video', p, true), true);
    assert.equal(keepByPrefs('video', p, false), false);
});

test('always and off ignore the outcome', () => {
    for (const failed of [true, false]) {
        assert.equal(keepByPrefs('video', { ...NO_PREFS, video: 'always' }, failed), true);
        assert.equal(keepByPrefs('video', { ...NO_PREFS, video: 'off' }, failed), false);
    }
});

test('trace follows the trace-files switch', () => {
    assert.equal(keepByPrefs('trace', NO_PREFS, false), true);
    assert.equal(keepByPrefs('trace', { ...NO_PREFS, trace: false }, true), false);
});
