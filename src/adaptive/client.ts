import {
  CONTRACT_ERROR_MESSAGE,
  START_PATH,
  SUBMIT_PATH,
  TRANSPORT_ERROR_MESSAGE,
  isOptionId,
  isToken,
  parseHttpError,
  parseStartResponse,
  parseSubmitResponse,
  type StartSuccess,
  type SubmitSuccess,
} from './contracts.ts';
import { isDatabaseId } from './databases.ts';

export interface FetchInit {
  method: string;
  headers: Record<string, string>;
  body: string;
  signal?: AbortSignal;
}

export interface FetchResponseLike {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type FetchLike = (url: string, init: FetchInit) => Promise<FetchResponseLike>;

export class PrototypeClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server';
  readonly serverCode: string | null;

  constructor(kind: 'transport' | 'contract' | 'server', serverCode: string | null = null) {
    super(kind === 'transport' ? TRANSPORT_ERROR_MESSAGE : CONTRACT_ERROR_MESSAGE);
    this.name = 'PrototypeClientError';
    this.kind = kind;
    this.serverCode = serverCode;
  }
}

function newRequestId(): string {
  try {
    const candidate = crypto.randomUUID();
    if (typeof candidate === 'string' && candidate.length > 0) return candidate;
  } catch {
    // fall through to deterministic fallback below
  }
  return `req-${Date.now()}-${Math.floor(Math.random() * 1000000)}`;
}

async function readJson(response: FetchResponseLike): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new PrototypeClientError('contract');
  }
}

function serverError(payload: unknown): PrototypeClientError {
  const parsed = parseHttpError(payload);
  return new PrototypeClientError('server', parsed?.code ?? null);
}

async function readServerError(response: FetchResponseLike): Promise<PrototypeClientError> {
  try {
    return serverError(await response.json());
  } catch {
    return new PrototypeClientError('server');
  }
}

export async function startClarification(
  fetchFn: FetchLike,
  question: string,
  options: { databaseId: string; requestId?: string; signal?: AbortSignal },
): Promise<StartSuccess> {
  const trimmed = question.trim();
  if (trimmed.length === 0 || trimmed.length > 2000) {
    throw new PrototypeClientError('contract');
  }
  const databaseId = options.databaseId;
  if (!isDatabaseId(databaseId)) throw new PrototypeClientError('contract');
  const body = {
    request_id: options.requestId ?? newRequestId(),
    database_id: databaseId,
    question: trimmed,
  };
  let response: FetchResponseLike;
  try {
    response = await fetchFn(START_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof PrototypeClientError) throw error;
    throw new PrototypeClientError('transport');
  }
  if (!response.ok) throw await readServerError(response);
  const payload = await readJson(response);
  const parsed = parseStartResponse(payload);
  if (parsed === null) throw new PrototypeClientError('contract');
  return parsed;
}

export async function submitClarification(
  fetchFn: FetchLike,
  sessionToken: string,
  selection: string[],
  cancelled: boolean,
  options?: { signal?: AbortSignal },
): Promise<SubmitSuccess> {
  if (!isToken(sessionToken)) {
    throw new PrototypeClientError('contract');
  }
  if (
    !Array.isArray(selection) ||
    (cancelled && selection.length !== 0) ||
    (!cancelled && (selection.length !== 1 || !isOptionId(selection[0])))
  ) {
    throw new PrototypeClientError('contract');
  }
  const body = {
    session_token: sessionToken,
    selection: [...selection],
    cancelled,
  };
  let response: FetchResponseLike;
  try {
    response = await fetchFn(SUBMIT_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof PrototypeClientError) throw error;
    throw new PrototypeClientError('transport');
  }
  if (!response.ok) throw await readServerError(response);
  const payload = await readJson(response);
  const parsed = parseSubmitResponse(payload);
  if (parsed === null) throw new PrototypeClientError('contract');
  return parsed;
}
