import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  SQLITE_UPLOAD_CONTENT_TYPE,
  SQLITE_UPLOAD_PATH,
  SQLITE_UPLOAD_VERSION,
  UPLOAD_PENDING_NOTICE,
  canStartUpload,
  getUploadErrorMessage,
  isValidUploadLabel,
  parseUploadError,
  parseUploadResponse,
  toUploadMessage,
  uploadSqliteDatabase,
  type UploadFetchLike,
} from '../../src/adaptive/sqliteUpload.ts';
import { isStaleResponse, nextOperation } from '../../src/adaptive/state.ts';

function successPayload() {
  return {
    version: 'private-sqlite-upload-v2',
    database_id: 'review-db-one',
    label: 'Review DB',
    status: 'PENDING_REVIEW',
    schema_fingerprint: 'a'.repeat(64),
    schema: {
      table_count: 1,
      column_count: 2,
      foreign_key_count: 0,
      tables: [
        {
          name: 'course_offerings',
          column_count: 2,
          columns: [
            {
              name: 'course_code',
              declared_type: 'TEXT',
              affinity: 'TEXT',
              nullable: false,
              primary_key_position: 1,
            },
            {
              name: 'course_name',
              declared_type: 'TEXT',
              affinity: 'TEXT',
              nullable: false,
              primary_key_position: 0,
            },
          ],
          primary_key: ['course_code'],
          foreign_key_count: 0,
        },
      ],
    },
    mapping: {
      ontology_version: 1,
      proposer: 'AI',
      field_count: 1,
      unmapped_count: 0,
      unmapped_fields: [],
      fields: [
        {
          semantic_field: 'Course.name',
          review_status: 'NEEDS_REVIEW',
          top_candidate: {
            table: 'course_offerings',
            column: 'course_name',
            confidence: 0.8,
            reasons: [
              { signal: 'COLUMN_NAME', score: 0.8, detail: 'name overlap' },
              { signal: 'AI_PROPOSAL', score: 1, detail: 'local model chose this column (ai-mapping-v2)' },
            ],
          },
        },
      ],
    },
  };
}

describe('phase6-18 upload parser and request contract', () => {
  it('accepts strict success and rejects extra keys/type/version/status', () => {
    const parsed = parseUploadResponse(successPayload());
    assert.ok(parsed !== null);
    assert.equal(parsed.status, 'PENDING_REVIEW');
    assert.equal(parsed.schema.table_count, 1);
    assert.equal(parsed.mapping.fields[0]?.semantic_field, 'Course.name');
    assert.equal(parseUploadResponse({ ...successPayload(), version: 'x' }), null);
    assert.equal(parseUploadResponse({ ...successPayload(), status: 'PUBLISHED' }), null);
    assert.equal(parseUploadResponse({ ...successPayload(), extra: 1 }), null);
    assert.equal(parseUploadResponse({ ...successPayload(), database_id: 'BAD ID' }), null);
    assert.equal(
      parseUploadResponse({ ...successPayload(), schema_fingerprint: 'short' }),
      null,
    );
    assert.equal(
      parseUploadResponse({
        ...successPayload(),
        schema: { ...successPayload().schema, column_count: -1 },
      }),
      null,
    );
    const invalidStatus = structuredClone(successPayload());
    invalidStatus.mapping.fields[0]!.review_status = 'PUBLISHED';
    assert.equal(parseUploadResponse(invalidStatus), null);
    const invalidConfidence = structuredClone(successPayload());
    invalidConfidence.mapping.fields[0]!.top_candidate!.confidence = 1.5;
    assert.equal(parseUploadResponse(invalidConfidence), null);
    const withSamples = structuredClone(successPayload()) as unknown as {
      schema: { tables: Array<{ columns: unknown[] }> };
    };
    withSamples.schema.tables[0]!.columns.push({
      name: 'injected',
      declared_type: 'TEXT',
      affinity: 'TEXT',
      nullable: true,
      primary_key_position: 0,
      sample_values: ['row-value'],
    });
    assert.equal(parseUploadResponse(withSamples), null);
  });

  it('phase8-12: requires a known proposer and accepts the AI reason', () => {
    const parsed = parseUploadResponse(successPayload());
    assert.equal(parsed?.mapping.proposer, 'AI');
    assert.equal(parsed?.mapping.fields[0]?.top_candidate?.reasons[1]?.signal, 'AI_PROPOSAL');
    for (const proposer of ['RULES', 'AI_UNAVAILABLE']) {
      const payload = structuredClone(successPayload());
      payload.mapping.proposer = proposer;
      assert.equal(parseUploadResponse(payload)?.mapping.proposer, proposer);
    }
    for (const proposer of [undefined, 'MODEL', 1]) {
      const payload = structuredClone(successPayload()) as unknown as { mapping: Record<string, unknown> };
      if (proposer === undefined) delete payload.mapping['proposer'];
      else payload.mapping['proposer'] = proposer;
      assert.equal(parseUploadResponse(payload), null);
    }
    const unknownSignal = structuredClone(successPayload());
    unknownSignal.mapping.fields[0]!.top_candidate!.reasons[1]!.signal = 'MODEL_GUESS';
    assert.equal(parseUploadResponse(unknownSignal), null);
  });

  it('parses fixed error shape and rejects detail reflection', () => {
    assert.deepEqual(parseUploadError({ error: { code: 'SQLITE_UPLOAD_CONFLICT' } }), {
      code: 'SQLITE_UPLOAD_CONFLICT',
    });
    assert.equal(
      parseUploadError({ error: { code: 'SQLITE_UPLOAD_CONFLICT', detail: 'secret' } }),
      null,
    );
    assert.equal(parseUploadError({ error: { code: 'lowercase' } }), null);
    assert.equal(isValidUploadLabel('   '), false);
    assert.equal(isValidUploadLabel('ok label'), true);
  });

  it('sends raw Blob body with fixed content type and strict query', async () => {
    const seen: Array<{ url: string; method: string; headers: Record<string, string>; body: unknown }> = [];
    const file = new Blob(['SQLite format 3\x00data'], { type: SQLITE_UPLOAD_CONTENT_TYPE });
    const fetchFn: UploadFetchLike = async (url, init) => {
      seen.push({ url, method: init.method, headers: init.headers, body: init.body });
      return { ok: true, status: 200, json: async () => successPayload() };
    };
    const out = await uploadSqliteDatabase(fetchFn, 'review-db-one', 'Review DB', file);
    assert.equal(out.database_id, 'review-db-one');
    assert.equal(seen.length, 1);
    assert.equal(seen[0]?.method, 'POST');
    assert.equal(seen[0]?.headers['Content-Type'], SQLITE_UPLOAD_CONTENT_TYPE);
    assert.ok(seen[0]?.url.startsWith(`${SQLITE_UPLOAD_PATH}?`));
    assert.ok(seen[0]?.url.includes('database_id=review-db-one'));
    assert.ok(seen[0]?.url.includes('label=Review+DB'));
    assert.ok(seen[0]?.body instanceof Blob);
    await assert.rejects(() => uploadSqliteDatabase(fetchFn, 'BAD ID', 'Review DB', file));
    await assert.rejects(() => uploadSqliteDatabase(fetchFn, 'review-db-one', '   ', file));
  });

  it('never reflects server bodies or exception strings', async () => {
    const secret = 'SECRET_PATH_sqlite_SELECT_traceback';
    const fetchFn: UploadFetchLike = async () => ({
      ok: false,
      status: 500,
      json: async () => ({ error: { code: 'SQLITE_UPLOAD_INTERNAL_ERROR', detail: secret } }),
    });
    const file = new Blob(['x'], { type: SQLITE_UPLOAD_CONTENT_TYPE });
    await assert.rejects(
      () => uploadSqliteDatabase(fetchFn, 'review-db-one', 'Review DB', file),
      (error: unknown) => {
        assert.ok(error instanceof Error);
        assert.equal(error.message.includes(secret), false);
        assert.equal(String(error).includes(secret), false);
        assert.equal(toUploadMessage(error).includes(secret), false);
        return true;
      },
    );
    assert.equal(getUploadErrorMessage('UNKNOWN_CODE_XYZ'), '처리 중 오류가 발생했습니다. 다시 시도해 주세요.');
    assert.ok(UPLOAD_PENDING_NOTICE.includes('검토 전에는 조회·질의에 사용할 수 없음'));
    assert.equal(SQLITE_UPLOAD_VERSION, 'private-sqlite-upload-v2');
  });
});

describe('phase6-18 upload state transitions', () => {
  it('blocks duplicate submit while uploading and allows retry', () => {
    assert.equal(canStartUpload('idle'), true);
    assert.equal(canStartUpload('uploading'), false);
    assert.equal(canStartUpload('success'), true);
    assert.equal(canStartUpload('error'), true);
  });

  it('propagates abort signal and maps abort to transport without stale overwrite', async () => {
    const controller = new AbortController();
    let observedSignal: AbortSignal | undefined;
    const fetchFn: UploadFetchLike = async (_url, init) => {
      observedSignal = init.signal;
      controller.abort();
      throw new DOMException('aborted', 'AbortError');
    };
    const file = new Blob(['x'], { type: SQLITE_UPLOAD_CONTENT_TYPE });
    await assert.rejects(
      () => uploadSqliteDatabase(fetchFn, 'review-db-one', 'Review DB', file, { signal: controller.signal }),
      (error: unknown) => {
        assert.equal(toUploadMessage(error), '연결에 실패했습니다. 다시 시도해 주세요.');
        return true;
      },
    );
    assert.equal(observedSignal, controller.signal);
    // stale responses must not overwrite the latest UI operation
    const current = nextOperation(0);
    const stale = 0;
    assert.equal(isStaleResponse(current, stale), true);
    assert.equal(isStaleResponse(current, current), false);
  });
});

describe('phase6-18 upload isolation in MainPage', () => {
  const source = readFileSync('src/pages/MainPage.tsx', 'utf-8').replace(/\r\n/g, '\n');

  it('resets an aborted upload when the database modal closes', () => {
    const start = source.indexOf('function closeDbModal');
    const end = source.indexOf('\n  }\n', start);
    const handler = source.slice(start, end);
    assert.ok(handler.includes('uploadAbortRef.current?.abort()'));
    assert.ok(handler.includes("setUploadStatus('idle')"));
    assert.ok(handler.includes('setUploadResult(null)'));
    assert.ok(handler.includes('setUploadError(null)'));
  });

  it('keeps selection, storage, and course UI untouched on upload success', () => {
    const start = source.indexOf('async function submitSqliteUpload');
    const end = source.indexOf('\n  }\n', start);
    const handler = source.slice(start, end);
    assert.ok(handler.includes('setUploadResult(result)'));
    for (const forbidden of [
      'setActiveDbId(',
      'setPendingDbId(',
      'setRegistry(',
      'writeStoredDatabaseId(',
      'setAllCourses(',
      'setSelectedCourses(',
    ]) {
      assert.equal(handler.includes(forbidden), false, forbidden);
    }
    assert.ok(source.includes('{UPLOAD_PENDING_NOTICE}'));
    assert.ok(source.includes('PENDING_REVIEW'));
  });

  it('does not add the registered DB to selection radios or clarification link', () => {
    const start = source.indexOf('SQLite 등록(검토 대기)');
    const end = source.indexOf('</fieldset>', start);
    const uploadSection = source.slice(start, end);
    assert.ok(uploadSection.includes('setUploadResult') || source.includes('setUploadResult(result)'));
    // radios render strictly from the server-owned registry snapshot
    assert.ok(source.includes('{registry.map((entry) => ('));
    assert.equal(uploadSection.includes('setRegistry('), false);
    assert.equal(uploadSection.includes('/adaptive-prototype?database='), false);
  });
});
