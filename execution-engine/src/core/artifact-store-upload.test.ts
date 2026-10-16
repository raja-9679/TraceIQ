/**
 * Uploads always hand the SDK an object for metadata.
 *
 * minio-js throws `TypeError: metaData should be of type "object"` for
 * `undefined`, and putFile used to pass `undefined` whenever the caller gave
 * no content type — which is how the worker uploads videos and traces. Every
 * video/trace upload at capture level `full` failed; screenshots (which pass
 * image/png) worked, so it went unnoticed.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { ArtifactStore, artifactKeys, contentTypeFor } from './artifact-store';

/** Mirrors the real SDK's argument check, and records what it was given. */
function fakeClient() {
    const calls: { op: string; key: string; meta: any }[] = [];
    const check = (meta: any) => {
        if (meta === null || typeof meta !== 'object') {
            throw new TypeError('metaData should be of type "object"');
        }
    };
    return {
        calls,
        async bucketExists() { return true; },
        async makeBucket() { /* not reached */ },
        async fPutObject(_b: string, key: string, _p: string, meta: any) {
            check(meta); calls.push({ op: 'fPutObject', key, meta });
        },
        async putObject(_b: string, key: string, _body: Buffer, _n: number, meta: any) {
            check(meta); calls.push({ op: 'putObject', key, meta });
        },
    };
}

function storeWith(client: any): ArtifactStore {
    const store = new ArtifactStore('full', { endPoint: 'unused', port: 9000, useSSL: false, accessKey: 'a', secretKey: 'b' } as any, 'bucket');
    (store as any).client = client;
    return store;
}

function tmpFile(name: string): string {
    const p = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'tiq-up-')), name);
    fs.writeFileSync(p, 'x');
    return p;
}

test('video and trace upload without an explicit content type (the worker call shape)', async () => {
    const client = fakeClient();
    const store = storeWith(client);
    const video = await store.putFile('video', artifactKeys.video(6, 'job'), tmpFile('v.webm'));
    const trace = await store.putFile('trace', artifactKeys.trace(6, 'job'), tmpFile('trace.zip'));
    assert.equal(video.key, 'runs/6/videos/job.webm');
    assert.ok(trace.key);
    assert.deepEqual(client.calls.map(c => c.meta['Content-Type']), ['video/webm', 'application/zip']);
});

test('an explicit content type still wins', async () => {
    const client = fakeClient();
    await storeWith(client).putFile('screenshot', 'runs/1/screenshots/a.png', tmpFile('a.png'), 'image/x-custom');
    assert.equal(client.calls[0].meta['Content-Type'], 'image/x-custom');
});

test('buffers get an object too', async () => {
    const client = fakeClient();
    await storeWith(client).putBuffer('video', 'runs/1/videos/m.mp4', Buffer.from('x'));
    assert.equal(client.calls[0].meta['Content-Type'], 'video/mp4');
});

test('content type by extension', () => {
    assert.equal(contentTypeFor('runs/1/videos/j.webm'), 'video/webm');
    assert.equal(contentTypeFor('runs/1/videos/j.MP4'), 'video/mp4');
    assert.equal(contentTypeFor('runs/1/traces/j.zip'), 'application/zip');
    assert.equal(contentTypeFor('runs/1/har/j.har'), 'application/json');
    assert.equal(contentTypeFor('runs/1/x.bin'), 'application/octet-stream');
    assert.equal(contentTypeFor('noext'), 'application/octet-stream');
});
