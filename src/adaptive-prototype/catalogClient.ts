import {
  CATALOG_CONTRACT_MESSAGE,
  CATALOG_TRANSPORT_MESSAGE,
  CATALOG_PATH,
  DATABASES_PATH,
  isDatabaseId,
  isValidKeyword,
  parseCatalogResponse,
  parseDatabaseRegistry,
  type CatalogResult,
  type RegistryDatabase,
} from './databases.ts';

export interface CatalogFetchInit {
  method: string;
  headers: Record<string, string>;
  signal?: AbortSignal;
}

export interface CatalogFetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type CatalogFetchLike = (url: string, init: CatalogFetchInit) => Promise<CatalogFetchResponse>;

export class CatalogClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server';
  readonly serverCode: string | null;

  constructor(kind: 'transport' | 'contract' | 'server', serverCode: string | null = null) {
    super(kind === 'transport' ? CATALOG_TRANSPORT_MESSAGE : CATALOG_CONTRACT_MESSAGE);
    this.name = 'CatalogClientError';
    this.kind = kind;
    this.serverCode = serverCode;
  }
}

function parseServerCode(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return null;
  const root = payload as Record<string, unknown>;
  if (Object.keys(root).length !== 1 || !('error' in root)) return null;
  const nested = root['error'];
  if (typeof nested !== 'object' || nested === null || Array.isArray(nested)) return null;
  const error = nested as Record<string, unknown>;
  if (Object.keys(error).length !== 1 || !('code' in error)) return null;
  const code = error['code'];
  if (typeof code !== 'string' || code.length === 0 || code.length > 128) return null;
  if (!/^[A-Z0-9_]{1,128}$/.test(code)) return null;
  return code;
}

async function readPayload(response: CatalogFetchResponse): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new CatalogClientError('contract');
  }
}

export async function listDatabases(
  fetchFn: CatalogFetchLike,
  options?: { signal?: AbortSignal; onWarning?: (message: string | null) => void },
): Promise<RegistryDatabase[]> {
  let response: CatalogFetchResponse;
  try {
    response = await fetchFn(DATABASES_PATH, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof CatalogClientError) throw error;
    throw new CatalogClientError('transport');
  }
  const payload = await readPayload(response);
  if (!response.ok) throw new CatalogClientError('server', parseServerCode(payload));
  const parsed = parseDatabaseRegistry(payload, options?.onWarning);
  if (parsed === null) throw new CatalogClientError('contract');
  return parsed;
}

export async function getCatalog(
  fetchFn: CatalogFetchLike,
  databaseId: string,
  options?: { keyword?: string; signal?: AbortSignal },
): Promise<CatalogResult> {
  if (!isDatabaseId(databaseId)) throw new CatalogClientError('contract');
  const keyword = options?.keyword ?? '';
  if (!isValidKeyword(keyword)) throw new CatalogClientError('contract');
  const params = new URLSearchParams();
  params.set('database_id', databaseId);
  const trimmed = keyword.trim();
  if (trimmed !== '') params.set('keyword', trimmed);
  const url = `${CATALOG_PATH}?${params.toString()}`;
  let response: CatalogFetchResponse;
  try {
    response = await fetchFn(url, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof CatalogClientError) throw error;
    throw new CatalogClientError('transport');
  }
  const payload = await readPayload(response);
  if (!response.ok) throw new CatalogClientError('server', parseServerCode(payload));
  const parsed = parseCatalogResponse(payload);
  if (parsed === null || parsed.database_id !== databaseId) throw new CatalogClientError('contract');
  return parsed;
}
