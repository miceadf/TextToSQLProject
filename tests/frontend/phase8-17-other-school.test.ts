import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  PUBLISH_RUNTIME_BLOCKED_MESSAGE,
  publishReview,
  toReviewMessage,
  type ReviewFetchLike,
} from '../../src/adaptive/mappingReview.ts';
import {
  PostgresRegistrationClientError,
  toPostgresMessage,
} from '../../src/adaptive/postgresRegistration.ts';
import { catalogToCourses, parseCatalogResponse } from '../../src/adaptive/databases.ts';

function blocked(fields: string[]): ReviewFetchLike {
  return async () => ({
    ok: false,
    status: 422,
    json: async () => ({ error: { code: 'MAPPING_REVIEW_PUBLISH_BLOCKED', fields } }),
  });
}

async function messageFor(fields: string[]): Promise<string> {
  try {
    await publishReview(blocked(fields), 'campus-pg', 'admin');
  } catch (error) {
    return toReviewMessage(error);
  }
  throw new Error('publish should fail');
}

describe('phase8-17 publish-blocked message', () => {
  it('names a refused query path when every field was reviewed', async () => {
    assert.equal(await messageFor([]), PUBLISH_RUNTIME_BLOCKED_MESSAGE);
  });

  it('keeps the unfinished-review message when fields are listed', async () => {
    const message = await messageFor(['Course.name']);
    assert.notEqual(message, PUBLISH_RUNTIME_BLOCKED_MESSAGE);
    assert.match(message, /검토가 끝나지 않은/);
  });
});

describe('phase8-17 alias length message', () => {
  it('does not call a short alias a collision', () => {
    const length = toPostgresMessage(new PostgresRegistrationClientError('server', 'POSTGRES_REGISTRATION_ALIAS_LENGTH'));
    const conflict = toPostgresMessage(new PostgresRegistrationClientError('server', 'POSTGRES_REGISTRATION_ALIAS_CONFLICT'));
    assert.match(length, /2~20자/);
    assert.notEqual(length, conflict);
  });
});

const offering = {
  course_code: 'SW1001', course_name: '프로그래밍기초', credits: 3, category: '전공필수',
  department_code: 'SW', department_name: '소프트웨어학부', instructor_name: '교원A',
  year: 2026, semester: '1학기', section: '001', capacity: 40, enrolled: 32,
  day: '월요일', start_time: '09:00', end_time: '10:15',
};

function catalog(items: Record<string, unknown>[]) {
  return { version: 'synthetic-course-catalog-v1', database_id: 'other-school', items, total: items.length };
}

describe('phase8-17 offerings that meet twice a week', () => {
  it('accepts one row per meeting and shows one course with two slots', () => {
    const parsed = parseCatalogResponse(catalog([offering, { ...offering, day: '수요일' }]));
    assert.ok(parsed);
    const courses = catalogToCourses(parsed.items);
    assert.equal(courses.length, 1);
    assert.deepEqual(courses[0]?.slots.map((slot) => slot.day), ['Mon', 'Wed']);
    assert.equal(courses[0]?.timeText, '월요일 09:00-10:15, 수요일 09:00-10:15');
  });

  it('still rejects rows that disagree or repeat', () => {
    assert.equal(parseCatalogResponse(catalog([offering, { ...offering, day: '수요일', capacity: 41 }])), null);
    assert.equal(parseCatalogResponse(catalog([offering, { ...offering }])), null);
  });

  it('places Korean weekdays on the timetable', () => {
    const [course] = catalogToCourses([{ ...offering, day: '금' }] as never);
    assert.deepEqual(course?.slots, [{ day: 'Fri', startHour: 9, endHour: 10.25 }]);
  });
});
