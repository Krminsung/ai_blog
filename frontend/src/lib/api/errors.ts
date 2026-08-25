/** Shape of the stable error envelope every backend route returns. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string | null;
    fields?: { path: string; reason: string }[];
    remediation?: Record<string, unknown>;
  };
}

/**
 * Thrown for any non-2xx response. `code` is the backend's stable error code
 * (`VALIDATION_FAILED`, `AUTHENTICATION_REQUIRED`, …) and is what callers
 * should branch on — never the human message, which is localized copy.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly fields: { path: string; reason: string }[];
  readonly remediation: Record<string, unknown> | null;

  constructor(
    status: number,
    body: Partial<ApiErrorBody["error"]> & { message?: string },
  ) {
    super(body.message ?? "요청을 처리하지 못했습니다.");
    this.name = "ApiError";
    this.status = status;
    this.code = body.code ?? "UNKNOWN_ERROR";
    this.requestId = body.request_id ?? null;
    this.fields = body.fields ?? [];
    this.remediation = body.remediation ?? null;
  }

  /** True when re-authenticating could plausibly resolve the failure. */
  get isAuthFailure(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** Field errors keyed by the dotted path the backend reported. */
  fieldErrors(): Record<string, string> {
    const map: Record<string, string> = {};
    for (const field of this.fields) {
      // Strip the FastAPI `body.` prefix so keys line up with form input names.
      const key = field.path.replace(/^body\./, "");
      map[key] = field.reason;
    }
    return map;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/** Human-facing message for anything that reaches an error boundary. */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error) return error.message;
  return "알 수 없는 오류가 발생했습니다.";
}
