import type { Course } from '../types/course.ts';

export const DATABASES_PATH = '/api/adaptive/v1/databases';
export const CATALOG_PATH = '/api/adaptive/v1/courses';
export const REGISTRY_VERSION = 'synthetic-database-registry-v1';
export const CATALOG_VERSION = 'synthetic-course-catalog-v1';

export const LEGACY_SELECTION_ID = 'legacy';
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
  year: number;
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
  if (value['database_id'] === LEGACY_SELECTION_ID) return null;
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

export function parseDatabaseRegistry(value: unknown): RegistryDatabase[] | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['version', 'databases'])) return null;
  if (value['version'] !== REGISTRY_VERSION) return null;
  if (!Array.isArray(value['databases'])) return null;
  const list = value['databases'];
  if (list.length < 1 || list.length > MAX_DATABASES) return null;
  const parsed: RegistryDatabase[] = [];
  for (const entry of list) {
    const item = parseRegistryEntry(entry);
    if (item === null) return null;
    parsed.push(item);
  }
  const ids = parsed.map((item) => item.database_id);
  if (new Set(ids).size !== ids.length) return null;
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
  if (typeof value['year'] !== 'number' || !Number.isInteger(value['year']) || value['year'] < 2000 || value['year'] > 2100) {
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
  const keys = items.map((item) => `${item.year}|${item.semester}|${item.course_code}|${item.section}`);
  if (new Set(keys).size !== keys.length) return null;
  return { database_id: value['database_id'], items, total: items.length };
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

function weekdayOf(day: string): Course['slots'][number]['day'] | null {
  const upper = day.trim().toUpperCase();
  if (upper === 'MON') return 'Mon';
  if (upper === 'TUE') return 'Tue';
  if (upper === 'WED') return 'Wed';
  if (upper === 'THU') return 'Thu';
  if (upper === 'FRI') return 'Fri';
  if (upper === 'SAT') return 'Sat';
  return null;
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
