import type { Course } from '../types/course.ts';

export const DATABASES_PATH = '/api/adaptive/v1/databases';
export const CATALOG_PATH = '/api/adaptive/v1/courses';
export const REGISTRY_VERSION = 'synthetic-database-registry-v1';
export const CATALOG_VERSION = 'synthetic-course-catalog-v1';

export const LEGACY_SELECTION_ID = 'legacy';
export const RESERVED_DATABASE_IDS: readonly string[] = [LEGACY_SELECTION_ID];
export const RESERVED_DATABASE_ID_MESSAGE = '이 ID는 시스템이 쓰는 이름이라 쓸 수 없습니다. 다른 ID를 입력해 주세요.';
export const DATABASE_ID_INPUT_MESSAGE = 'ID는 영문 소문자·숫자·하이픈(-)만 쓸 수 있고, 첫 글자는 영문 소문자나 숫자여야 합니다(64자 이내). 예: legacy-pg';

/** Shared by registration forms; selection and response parsing keep their own rules. */
export function registrationDatabaseIdMessage(value: string): string | null {
  if (isReservedDatabaseId(value)) return RESERVED_DATABASE_ID_MESSAGE;
  return isDatabaseId(value) ? null : DATABASE_ID_INPUT_MESSAGE;
}

export function isReservedDatabaseId(value: unknown): boolean {
  return typeof value === 'string' && RESERVED_DATABASE_IDS.includes(value);
}
export const STORAGE_KEY = 'adaptive-active-database-v1';

const DB_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const LABEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 _().-]{0,63}$/;
const TIME_PATTERN = /^\d{2}:\d{2}$/;

const MAX_DATABASES = 8;
const MAX_ITEMS = 200;
const MAX_KEYWORD = 100;

export interface RegistryDatabase {
  database_id: string;
  label: string;
  schema: 'flat' | 'legacy' | 'private' | 'postgres';
}

const PRIVATE_LABEL_MAX = 64;

function isValidPrivateLabel(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  if (value.length < 1 || value.length > PRIVATE_LABEL_MAX) return false;
  if (value.trim().length === 0) return false;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return false;
  return true;
}

export interface CanonicalCourse {
  course_code: string;
  course_name: string;
  credits: number;
  category: string;
  department_code: string;
  department_name: string;
  instructor_name: string;
  year: number | null;
  semester: string;
  section: string;
  capacity: number;
  enrolled: number;
  day: string;
  start_time: string;
  end_time: string;
}

export interface CatalogResult {
  database_id: string;
  items: CanonicalCourse[];
  total: number;
}

export function isDatabaseId(value: unknown): value is string {
  return typeof value === 'string' && DB_ID_PATTERN.test(value);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function parseRegistryEntry(value: unknown): RegistryDatabase | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['database_id', 'label', 'schema'])) return null;
  if (!isDatabaseId(value['database_id'])) return null;
  if (isReservedDatabaseId(value['database_id'])) return null;
  if (value['schema'] !== 'flat' && value['schema'] !== 'legacy' && value['schema'] !== 'private' && value['schema'] !== 'postgres') return null;
  if (value['schema'] === 'private') {
    if (!isValidPrivateLabel(value['label'])) return null;
    return {
      database_id: value['database_id'],
      label: value['label'],
      schema: value['schema'],
    };
  }
  if (
    typeof value['label'] !== 'string' ||
    value['label'].length < 1 ||
    value['label'].length > 64 ||
    !LABEL_PATTERN.test(value['label'])
  ) {
    return null;
  }
  return {
    database_id: value['database_id'],
    label: value['label'],
    schema: value['schema'],
  };
}

export function parseDatabaseRegistry(value: unknown, onWarning?: (message: string | null) => void): RegistryDatabase[] | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['version', 'databases'])) return null;
  if (value['version'] !== REGISTRY_VERSION) return null;
  if (!Array.isArray(value['databases'])) return null;
  const list = value['databases'];
  if (list.length < 1 || list.length > MAX_DATABASES) return null;
  const parsed: RegistryDatabase[] = [];
  const seen = new Set<string>();
  const reserved = new Set<string>();
  let malformed = 0;
  for (const entry of list) {
    if (isPlainObject(entry) && isDatabaseId(entry['database_id'])) {
      const id = entry['database_id'];
      if (seen.has(id)) return null;
      seen.add(id);
    }
    const item = parseRegistryEntry(entry);
    if (item === null) {
      if (isPlainObject(entry) && isReservedDatabaseId(entry['database_id'])) {
        reserved.add(entry['database_id'] as string);
      } else {
        malformed += 1;
      }
      continue;
    }
    parsed.push(item);
  }
  const ids = parsed.map((item) => item.database_id);
  if (new Set(ids).size !== ids.length) return null;
  const warnings: string[] = [];
  if (reserved.size > 0) warnings.push(`일부 DB(${[...reserved].join(', ')})는 이름이 예약어와 겹쳐 목록에서 뺐습니다.`);
  if (malformed > 0) warnings.push(`형식이 올바르지 않은 DB ${malformed}개를 목록에서 뺐습니다.`);
  onWarning?.(warnings.length > 0 ? warnings.join(' ') : null);
  return parsed;
}

function isNonEmptyString(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= max;
}

function parseCanonicalItem(value: unknown): CanonicalCourse | null {
  if (!isPlainObject(value)) return null;
  if (
    !hasOnlyKeys(value, [
      'course_code',
      'course_name',
      'credits',
      'category',
      'department_code',
      'department_name',
      'instructor_name',
      'year',
      'semester',
      'section',
      'capacity',
      'enrolled',
      'day',
      'start_time',
      'end_time',
    ])
  ) {
    return null;
  }
  if (!isNonEmptyString(value['course_code'], 32)) return null;
  if (!isNonEmptyString(value['course_name'], 200)) return null;
  if (typeof value['credits'] !== 'number' || !Number.isFinite(value['credits']) || value['credits'] <= 0 || value['credits'] > 10) {
    return null;
  }
  if (!isNonEmptyString(value['category'], 32)) return null;
  if (!isNonEmptyString(value['department_code'], 32)) return null;
  if (!isNonEmptyString(value['department_name'], 200)) return null;
  if (!isNonEmptyString(value['instructor_name'], 200)) return null;
  if (value['year'] !== null && (typeof value['year'] !== 'number' || !Number.isInteger(value['year']) || value['year'] < 2000 || value['year'] > 2100)) {
    return null;
  }
  if (!isNonEmptyString(value['semester'], 16)) return null;
  if (!isNonEmptyString(value['section'], 16)) return null;
  if (typeof value['capacity'] !== 'number' || !Number.isInteger(value['capacity']) || value['capacity'] <= 0 || value['capacity'] > 10000) {
    return null;
  }
  if (typeof value['enrolled'] !== 'number' || !Number.isInteger(value['enrolled']) || value['enrolled'] < 0 || value['enrolled'] > 10000) {
    return null;
  }
  if (value['enrolled'] > value['capacity']) return null;
  if (!isNonEmptyString(value['day'], 16)) return null;
  if (typeof value['start_time'] !== 'string' || !TIME_PATTERN.test(value['start_time'])) return null;
  if (typeof value['end_time'] !== 'string' || !TIME_PATTERN.test(value['end_time'])) return null;
  if (value['start_time'] >= value['end_time']) return null;
  return {
    course_code: value['course_code'],
    course_name: value['course_name'],
    credits: value['credits'],
    category: value['category'],
    department_code: value['department_code'],
    department_name: value['department_name'],
    instructor_name: value['instructor_name'],
    year: value['year'],
    semester: value['semester'],
    section: value['section'],
    capacity: value['capacity'],
    enrolled: value['enrolled'],
    day: value['day'],
    start_time: value['start_time'],
    end_time: value['end_time'],
  };
}

export function parseCatalogResponse(value: unknown): CatalogResult | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['version', 'database_id', 'items', 'total'])) return null;
  if (value['version'] !== CATALOG_VERSION) return null;
  if (!isDatabaseId(value['database_id'])) return null;
  if (!Array.isArray(value['items'])) return null;
  if (value['items'].length > MAX_ITEMS) return null;
  if (typeof value['total'] !== 'number' || !Number.isInteger(value['total'])) return null;
  const items: CanonicalCourse[] = [];
  for (const entry of value['items']) {
    const item = parseCanonicalItem(entry);
    if (item === null) return null;
    items.push(item);
  }
  if (value['total'] !== items.length) return null;
  // Phase 8-17: an offering that meets more than once a week arrives as one
  // item per timetable row. Those rows must agree on everything but the time,
  // and no time may repeat.
  const firstByKey = new Map<string, CanonicalCourse>();
  const times = new Set<string>();
  for (const item of items) {
    const key = offeringKey(item);
    const time = `${key}|${item.day}|${item.start_time}|${item.end_time}`;
    if (times.has(time)) return null;
    times.add(time);
    const first = firstByKey.get(key);
    if (first === undefined) firstByKey.set(key, item);
    else if (!sameOffering(first, item)) return null;
  }
  return { database_id: value['database_id'], items, total: items.length };
}

function offeringKey(item: CanonicalCourse): string {
  return `${item.year}|${item.semester}|${item.course_code}|${item.section}`;
}

const OFFERING_FIELDS = [
  'course_code', 'course_name', 'credits', 'category', 'department_code', 'department_name',
  'instructor_name', 'year', 'semester', 'section', 'capacity', 'enrolled',
] as const;

function sameOffering(left: CanonicalCourse, right: CanonicalCourse): boolean {
  return OFFERING_FIELDS.every((field) => left[field] === right[field]);
}

export function isValidKeyword(value: unknown): value is string {
  return typeof value === 'string' && value.length <= MAX_KEYWORD;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function resolveDatabaseSelection(
  stored: unknown,
  knownIds: readonly string[],
): string {
  if (typeof stored === 'string' && (stored === LEGACY_SELECTION_ID || (isDatabaseId(stored) && knownIds.includes(stored)))) {
    return stored;
  }
  return LEGACY_SELECTION_ID;
}

export function readStoredDatabaseId(storage: StorageLike | null | undefined, knownIds: readonly string[]): string {
  try {
    if (storage === null || storage === undefined) return LEGACY_SELECTION_ID;
    return resolveDatabaseSelection(storage.getItem(STORAGE_KEY), knownIds);
  } catch {
    return LEGACY_SELECTION_ID;
  }
}

export function writeStoredDatabaseId(
  storage: StorageLike | null | undefined,
  databaseId: string,
  knownIds: readonly string[],
): void {
  try {
    if (storage === null || storage === undefined) return;
    if (databaseId !== LEGACY_SELECTION_ID && !(isDatabaseId(databaseId) && knownIds.includes(databaseId))) {
      return;
    }
    storage.setItem(STORAGE_KEY, databaseId);
  } catch {
    // fail closed without surfacing storage errors
  }
}

export function shouldResetCourseUi(prevId: string, nextId: string): boolean {
  return prevId !== nextId;
}

export function databaseLabel(databases: readonly RegistryDatabase[], databaseId: string): string {
  if (databaseId === LEGACY_SELECTION_ID) return 'Legacy 기준선';
  const found = databases.find((entry) => entry.database_id === databaseId);
  return found?.label ?? '알 수 없는 DB';
}

const KOREAN_WEEKDAYS: Record<string, Course['slots'][number]['day']> = {
  월: 'Mon', 화: 'Tue', 수: 'Wed', 목: 'Thu', 금: 'Fri', 토: 'Sat',
};

function weekdayOf(day: string): Course['slots'][number]['day'] | null {
  const upper = day.trim().toUpperCase();
  if (upper === 'MON') return 'Mon';
  if (upper === 'TUE') return 'Tue';
  if (upper === 'WED') return 'Wed';
  if (upper === 'THU') return 'Thu';
  if (upper === 'FRI') return 'Fri';
  if (upper === 'SAT') return 'Sat';
  // Phase 8-17: Korean weekdays ("월", or a dictionary label "월요일").
  const korean = /^([월화수목금토])(?:요일)?$/.exec(day.trim());
  return korean ? KOREAN_WEEKDAYS[korean[1]!] ?? null : null;
}

function clockHour(text: string): number | null {
  const match = /^(\d{2}):(\d{2})$/.exec(text);
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour > 23 || minute > 59) return null;
  return hour + minute / 60;
}

export function canonicalToCourse(item: CanonicalCourse): Course {
  const id = `${item.year}-${item.semester}-${item.course_code}-${item.section}`;
  const timeText = `${item.day} ${item.start_time}-${item.end_time}`;
  const day = weekdayOf(item.day);
  const startHour = clockHour(item.start_time);
  const endHour = clockHour(item.end_time);
  const slots: Course['slots'] =
    day !== null && startHour !== null && endHour !== null && endHour > startHour
      ? [{ day, startHour, endHour }]
      : [];
  const closed = item.capacity > 0 && item.enrolled >= item.capacity;
  return {
    id,
    name: item.course_name,
    departmentName: item.department_name,
    professor: item.instructor_name,
    credits: item.credits,
    status: closed ? 'Closed' : 'Open',
    capacity: item.capacity,
    enrolled: item.enrolled,
    timeText,
    locationText: undefined,
    slots,
  };
}

/** One course per offering; timetable rows of the same offering become its slots (Phase 8-17). */
export function catalogToCourses(items: readonly CanonicalCourse[]): Course[] {
  const courses = new Map<string, Course>();
  for (const item of items) {
    const course = canonicalToCourse(item);
    const existing = courses.get(course.id);
    if (existing === undefined) {
      courses.set(course.id, course);
      continue;
    }
    courses.set(course.id, {
      ...existing,
      timeText: `${existing.timeText}, ${course.timeText}`,
      slots: [...existing.slots, ...course.slots],
    });
  }
  return [...courses.values()];
}

export function parsePrototypeDatabaseParam(search: string): string | null {
  try {
    const params = new URLSearchParams(search.startsWith('?') ? search : `?${search}`);
    let databaseCount = 0;
    let hasUnexpectedKey = false;
    params.forEach((_value, key) => {
      if (key !== 'database') {
        hasUnexpectedKey = true;
        return;
      }
      databaseCount += 1;
    });
    if (databaseCount !== 1 || hasUnexpectedKey) return null;
    const candidate = params.get('database');
    if (candidate === null || candidate === '') return null;
    if (!isDatabaseId(candidate)) return null;
    return candidate;
  } catch {
    return null;
  }
}

export const CATALOG_TRANSPORT_MESSAGE = '연결에 실패했습니다. 다시 시도해 주세요.';
export const CATALOG_CONTRACT_MESSAGE = '응답 형식이 올바르지 않습니다. 다시 시도해 주세요.';
export const CATALOG_GENERIC_MESSAGE = '처리 중 오류가 발생했습니다. 다시 시도해 주세요.';
export const CATALOG_UNAVAILABLE_MESSAGE = '합성 기준 정보를 사용할 수 없습니다. 다시 시도해 주세요.';
// Phase 7-14: a partially mapped PostgreSQL DB is queried through the chatbot only.
export const CATALOG_VIA_CHAT_MESSAGE =
  '이 DB는 강좌 목록 대신 오른쪽 아래 AI Course Assistant로 질문해 조회합니다(QueryIR 경로).';
