/**
 * Typed wrappers around the `/v1` surface.
 *
 * Only the routes the console actually calls are wrapped here; everything is
 * grouped by the backend's own domain boundaries so a route change lands in
 * exactly one place.
 */
import { api, apiRaw, newIdempotencyKey, type Query } from "@/lib/api/client";
import type * as T from "@/lib/api/types";

function idempotentPost<TResponse>(
  path: string,
  body: unknown,
): Promise<TResponse> {
  return api<TResponse>(path, {
    method: "POST",
    body,
    idempotencyKey: newIdempotencyKey(),
  });
}

/** Consent record required at signup and whenever terms are re-issued. */
export interface TermsAcceptance {
  document_type: string;
  document_version: string;
  required?: boolean;
}

/** Terms the product requires before an account can be created. */
export const REQUIRED_TERMS: TermsAcceptance[] = [
  { document_type: "terms_of_service", document_version: "2025-01-01" },
  { document_type: "privacy_policy", document_version: "2025-01-01" },
];

/* ------------------------------------------------------------------ system */

export const system = {
  meta: () =>
    api<{ api_version: string; default_locale: string; storage_timezone: string }>(
      "/v1/meta",
    ),
  live: () => api<{ status: string }>("/health/live", { anonymous: true }),
  ready: () => api<Record<string, unknown>>("/health/ready", { anonymous: true }),
  publicStatus: () =>
    api<Record<string, unknown>[]>("/v1/operations/status", { anonymous: true }),
};

/* -------------------------------------------------------------------- auth */

export const auth = {
  signup: (body: {
    email: string;
    password: string;
    display_name: string;
    workspace_name: string;
    industry?: string | null;
    country_code?: string;
    timezone?: string;
    locale?: string;
    terms: TermsAcceptance[];
  }) =>
    api<T.SignupResponse>("/v1/auth/signup", {
      method: "POST",
      body,
      anonymous: true,
    }),

  login: (body: {
    email: string;
    password: string;
    workspace_id?: string | null;
    device_name?: string | null;
    device_id?: string | null;
    country_code?: string | null;
  }) =>
    api<T.LoginResponse>("/v1/auth/login", {
      method: "POST",
      body,
      anonymous: true,
    }),

  verifyMfaLogin: (challenge_token: string, code: string) =>
    api<T.TokenPairResponse>("/v1/auth/mfa/verify", {
      method: "POST",
      body: { challenge_token, code },
      anonymous: true,
    }),

  refresh: (refresh_token: string, workspace_id?: string) =>
    api<T.TokenPairResponse>("/v1/auth/token/refresh", {
      method: "POST",
      body: { refresh_token, workspace_id },
      anonymous: true,
    }),

  logout: () => api<void>("/v1/auth/logout", { method: "POST", body: {} }),

  me: () => api<T.User>("/v1/auth/me"),

  updateMe: (body: {
    display_name?: string;
    locale?: string;
    timezone?: string;
  }) => api<T.User>("/v1/auth/me", { method: "PATCH", body }),

  verifyEmail: (token: string) =>
    api<T.User>("/v1/auth/email/verify", {
      method: "POST",
      body: { token },
      anonymous: true,
    }),

  resendVerification: (email: string) =>
    api<{ message: string }>("/v1/auth/email/resend", {
      method: "POST",
      body: { email },
      anonymous: true,
    }),

  forgotPassword: (email: string) =>
    api<{ message: string }>("/v1/auth/password/forgot", {
      method: "POST",
      body: { email },
      anonymous: true,
    }),

  resetPassword: (
    token: string,
    new_password: string,
    revoke_all_sessions = true,
  ) =>
    api<{ message: string }>("/v1/auth/password/reset", {
      method: "POST",
      body: { token, new_password, revoke_all_sessions },
      anonymous: true,
    }),

  sessions: () => api<T.Session[]>("/v1/auth/sessions"),

  revokeSession: (sessionId: string) =>
    api<void>(`/v1/auth/sessions/${sessionId}`, { method: "DELETE" }),

  revokeAllSessions: () => api<void>("/v1/auth/sessions", { method: "DELETE" }),

  enrollMfa: () =>
    api<T.MFAEnrollment>("/v1/auth/mfa/enroll", { method: "POST", body: {} }),

  confirmMfa: (factor_id: string, code: string) =>
    api<{ recovery_codes: string[] }>("/v1/auth/mfa/confirm", {
      method: "POST",
      body: { factor_id, code },
    }),

  disableMfa: (code: string) =>
    api<void>("/v1/auth/mfa", { method: "DELETE", body: { code } }),

  acceptTerms: (consents: TermsAcceptance[]) =>
    api<{ message: string }>("/v1/auth/terms/consents", {
      method: "POST",
      body: { consents },
    }),
};

/* -------------------------------------------------------------- workspaces */

export const workspaces = {
  list: () => api<T.Workspace[]>("/v1/workspaces"),
  get: (id: string) => api<T.Workspace>(`/v1/workspaces/${id}`),
  create: (body: { name: string; industry?: string | null; timezone?: string }) =>
    api<T.Workspace>("/v1/workspaces", { method: "POST", body }),
  update: (
    id: string,
    body: Partial<{
      name: string;
      industry: string | null;
      timezone: string;
      default_locale: string;
      default_channel_ref: string | null;
    }>,
  ) => api<T.Workspace>(`/v1/workspaces/${id}`, { method: "PATCH", body }),
  members: (id: string) => api<T.Membership[]>(`/v1/workspaces/${id}/members`),
  roles: (id: string) => api<T.Role[]>(`/v1/workspaces/${id}/roles`),
  invite: (id: string, body: { email: string; role_id: string }) =>
    api<T.Invitation>(`/v1/workspaces/${id}/members/invite`, {
      method: "POST",
      body,
    }),
  removeMember: (id: string, userId: string) =>
    api<void>(`/v1/workspaces/${id}/members/${userId}`, { method: "DELETE" }),
  updateMemberRole: (id: string, userId: string, role_id: string) =>
    api<T.Membership>(`/v1/workspaces/${id}/members/${userId}`, {
      method: "PATCH",
      body: { role_id },
    }),
  auditLogs: (id: string, query?: Query) =>
    api<T.AuditLog[]>(`/v1/workspaces/${id}/audit-logs`, { query }),
};

/* ------------------------------------------------------------------ brands */

export const brands = {
  list: (query?: Query) => api<T.Brand[]>("/v1/brands", { query }),
  get: (id: string) => api<T.Brand>(`/v1/brands/${id}`),
  create: (body: Record<string, unknown>) =>
    api<T.Brand>("/v1/brands", { method: "POST", body }),
  update: (id: string, body: Record<string, unknown>) =>
    api<T.Brand>(`/v1/brands/${id}`, { method: "PATCH", body }),
  deactivate: (id: string, lock_version: number) =>
    api<T.Brand>(`/v1/brands/${id}/deactivate`, {
      method: "POST",
      body: { lock_version },
    }),
  versions: (id: string) => api<T.BrandVersion[]>(`/v1/brands/${id}/versions`),
  version: (id: string, versionNumber: number) =>
    api<T.BrandVersion>(`/v1/brands/${id}/versions/${versionNumber}`),
  createVersion: (id: string, body: Record<string, unknown>) =>
    api<T.BrandVersion>(`/v1/brands/${id}/versions`, { method: "POST", body }),
};

export const products = {
  list: (query?: Query) => api<T.Product[]>("/v1/products", { query }),
  get: (id: string) => api<T.Product>(`/v1/products/${id}`),
  create: (body: Record<string, unknown>) =>
    api<T.Product>("/v1/products", { method: "POST", body }),
  update: (id: string, body: Record<string, unknown>) =>
    api<T.Product>(`/v1/products/${id}`, { method: "PATCH", body }),
  deactivate: (id: string, lock_version: number) =>
    api<T.Product>(`/v1/products/${id}/deactivate`, {
      method: "POST",
      body: { lock_version },
    }),
  versions: (id: string) => api<T.ProductVersion[]>(`/v1/products/${id}/versions`),
};

export const personas = {
  list: (query?: Query) => api<T.Persona[]>("/v1/personas", { query }),
  get: (id: string) => api<T.Persona>(`/v1/personas/${id}`),
  create: (body: Record<string, unknown>) =>
    api<T.Persona>("/v1/personas", { method: "POST", body }),
  update: (id: string, body: Record<string, unknown>) =>
    api<T.Persona>(`/v1/personas/${id}`, { method: "PATCH", body }),
  deactivate: (id: string, lock_version: number) =>
    api<T.Persona>(`/v1/personas/${id}/deactivate`, {
      method: "POST",
      body: { lock_version },
    }),
};

/* --------------------------------------------------------------- knowledge */

export const knowledge = {
  list: (query?: Query) =>
    api<T.KnowledgeSourceList>("/v1/knowledge/sources", { query }),
  get: (id: string) => api<T.KnowledgeSource>(`/v1/knowledge/sources/${id}`),
  create: (body: Record<string, unknown>) =>
    idempotentPost<T.KnowledgeSource>("/v1/knowledge/sources", body),
  remove: (id: string) =>
    api<void>(`/v1/knowledge/sources/${id}`, { method: "DELETE" }),
  sync: (id: string) =>
    idempotentPost<Record<string, unknown>>(
      `/v1/knowledge/sources/${id}/sync`,
      {},
    ),
  versions: (id: string) =>
    api<Record<string, unknown>[]>(`/v1/knowledge/sources/${id}/versions`),
  search: (query: Query) =>
    api<Record<string, unknown>>("/v1/knowledge/search", { query }),
  job: (jobId: string) =>
    api<Record<string, unknown>>(`/v1/knowledge/jobs/${jobId}`),
};

/* ---------------------------------------------------------------- keywords */

export const keywords = {
  list: (query?: Query) => api<T.KeywordList>("/v1/keywords", { query }),
  metrics: (id: string, query?: Query) =>
    api<Record<string, unknown>[]>(`/v1/keywords/${id}/metrics`, { query }),
  trend: (id: string, query?: Query) =>
    api<Record<string, unknown>>(`/v1/keywords/${id}/trend`, { query }),
  setIntent: (id: string, body: Record<string, unknown>) =>
    api<T.Keyword>(`/v1/keywords/${id}/intent`, { method: "PATCH", body }),
  research: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>("/v1/keywords/research", body),
  job: (jobId: string) =>
    api<Record<string, unknown>>(`/v1/keywords/jobs/${jobId}`),
  jobItems: (jobId: string, query?: Query) =>
    api<Record<string, unknown>>(`/v1/keywords/jobs/${jobId}/items`, { query }),
  cancelJob: (jobId: string) =>
    api<Record<string, unknown>>(`/v1/keywords/jobs/${jobId}/cancel`, {
      method: "POST",
      body: {},
    }),
  retryJob: (jobId: string) =>
    api<Record<string, unknown>>(`/v1/keywords/jobs/${jobId}/retry`, {
      method: "POST",
      body: {},
    }),
  providerStatus: () => api<T.ProviderStatus[]>("/v1/keywords/provider-status"),
  providerConnections: () =>
    api<Record<string, unknown>[]>("/v1/keywords/provider-connections"),
  cluster: (body: Record<string, unknown>) =>
    api<Record<string, unknown>>("/v1/keywords/cluster", { method: "POST", body }),
  compare: (body: Record<string, unknown>) =>
    api<Record<string, unknown>>("/v1/keywords/compare", { method: "POST", body }),
};

/* ---------------------------------------------------------------- planning */

export const planning = {
  campaigns: (query?: Query) => api<T.Campaign[]>("/v1/campaigns", { query }),
  campaign: (id: string) => api<T.Campaign>(`/v1/campaigns/${id}`),
  createCampaign: (body: Record<string, unknown>) =>
    api<T.Campaign>("/v1/campaigns", { method: "POST", body }),
  updateCampaign: (id: string, body: Record<string, unknown>) =>
    api<T.Campaign>(`/v1/campaigns/${id}`, { method: "PATCH", body }),

  briefs: (query?: Query) => api<T.Brief[]>("/v1/briefs", { query }),
  brief: (id: string) => api<T.Brief>(`/v1/briefs/${id}`),
  createBrief: (body: Record<string, unknown>) =>
    api<T.Brief>("/v1/briefs", { method: "POST", body }),
  briefVersions: (id: string) =>
    api<T.BriefVersion[]>(`/v1/briefs/${id}/versions`),
  submitBrief: (id: string, lock_version: number) =>
    api<T.Brief>(`/v1/briefs/${id}/submit`, {
      method: "POST",
      body: { lock_version },
    }),
  decideBrief: (id: string, body: Record<string, unknown>) =>
    api<T.Brief>(`/v1/briefs/${id}/decisions`, { method: "POST", body }),
  moveBriefToColumn: (id: string, body: Record<string, unknown>) =>
    api<T.Brief>(`/v1/briefs/${id}/board`, { method: "POST", body }),
  archiveBrief: (id: string, lock_version: number) =>
    api<T.Brief>(`/v1/briefs/${id}/archive`, {
      method: "POST",
      body: { lock_version },
    }),
  generationInput: (id: string) =>
    api<Record<string, unknown>>(`/v1/briefs/${id}/generation-input`),

  boardColumns: () => api<T.BoardColumn[]>("/v1/board/columns"),
  createBoardColumn: (body: Record<string, unknown>) =>
    api<T.BoardColumn>("/v1/board/columns", { method: "POST", body }),

  ideas: (query?: Query) => api<T.ContentIdea[]>("/v1/ideas", { query }),
  createIdeas: (body: Record<string, unknown>) =>
    api<T.ContentIdea[]>("/v1/ideas/batch", { method: "POST", body }),

  topics: (query?: Query) => api<T.TopicNode[]>("/v1/topics", { query }),
  createTopic: (body: Record<string, unknown>) =>
    api<T.TopicNode>("/v1/topics", { method: "POST", body }),

  calendar: (starts_at: string, ends_at: string, query?: Query) =>
    api<T.CalendarEntry[]>("/v1/calendar", {
      query: { starts_at, ends_at, ...query },
    }),
  createCalendarEntry: (body: Record<string, unknown>) =>
    api<T.CalendarEntry>("/v1/calendar", { method: "POST", body }),
  moveCalendarEntry: (id: string, body: Record<string, unknown>) =>
    api<T.CalendarEntry>(`/v1/calendar/${id}/move`, { method: "POST", body }),
  deleteCalendarEntry: (id: string, lock_version: number) =>
    api<void>(`/v1/calendar/${id}`, {
      method: "DELETE",
      query: { lock_version },
    }),
  exportCalendarIcs: (starts_at: string, ends_at: string) =>
    apiRaw("/v1/calendar/export.ics", { query: { starts_at, ends_at } }),
  exportCalendarCsv: (starts_at: string, ends_at: string) =>
    apiRaw("/v1/calendar/export.csv", { query: { starts_at, ends_at } }),

  monthlyProposals: (query?: Query) =>
    api<T.MonthlyProposal[]>("/v1/monthly-proposals", { query }),
  monthlyProposal: (id: string) =>
    api<T.MonthlyProposal>(`/v1/monthly-proposals/${id}`),
  approveProposal: (id: string, body: Record<string, unknown>) =>
    api<T.MonthlyProposal>(`/v1/monthly-proposals/${id}/approve`, {
      method: "POST",
      body,
    }),
  rejectProposal: (id: string, body: Record<string, unknown>) =>
    api<T.MonthlyProposal>(`/v1/monthly-proposals/${id}/reject`, {
      method: "POST",
      body,
    }),

  comments: (query?: Query) => api<T.Comment[]>("/v1/comments", { query }),
  createComment: (body: Record<string, unknown>) =>
    api<T.Comment>("/v1/comments", { method: "POST", body }),
  resolveComment: (id: string, lock_version: number) =>
    api<T.Comment>(`/v1/comments/${id}/resolve`, {
      method: "POST",
      body: { lock_version },
    }),
};

/* ----------------------------------------------------------------- content */

export const content = {
  list: (query?: Query) => api<T.Content[]>("/v1/content", { query }),
  get: (id: string) => api<T.Content>(`/v1/content/${id}`),
  create: (body: Record<string, unknown>) =>
    api<T.Content>("/v1/content", { method: "POST", body }),
  update: (id: string, body: Record<string, unknown>) =>
    api<T.Content>(`/v1/content/${id}`, { method: "PATCH", body }),
  remove: (id: string, lock_version: number) =>
    api<void>(`/v1/content/${id}`, {
      method: "DELETE",
      query: { lock_version },
    }),
  versions: (id: string) => api<T.ContentVersion[]>(`/v1/content/${id}/versions`),
  version: (id: string, versionId: string) =>
    api<T.ContentVersion>(`/v1/content/${id}/versions/${versionId}`),
  createVersion: (id: string, body: Record<string, unknown>) =>
    api<T.ContentVersion>(`/v1/content/${id}/versions`, { method: "POST", body }),
  restoreVersion: (id: string, versionId: string, body: Record<string, unknown>) =>
    api<T.ContentVersion>(`/v1/content/${id}/versions/${versionId}/restore`, {
      method: "POST",
      body,
    }),
  exportContent: (id: string, query?: Query) =>
    apiRaw(`/v1/content/${id}/export`, { query }),
  feedback: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/content/${id}/feedback`, {
      method: "POST",
      body,
    }),

  createJob: (body: Record<string, unknown>) =>
    idempotentPost<T.ContentJob>("/v1/content-jobs", body),
  job: (jobId: string) => api<T.ContentJob>(`/v1/content-jobs/${jobId}`),
  jobSteps: (jobId: string) =>
    api<Record<string, unknown>[]>(`/v1/content-jobs/${jobId}/steps`),
  cancelJob: (jobId: string) =>
    api<T.ContentJob>(`/v1/content-jobs/${jobId}/cancel`, {
      method: "POST",
      body: {},
    }),
  retryJob: (jobId: string) =>
    api<T.ContentJob>(`/v1/content-jobs/${jobId}/retry`, {
      method: "POST",
      body: {},
    }),
};

/* ---------------------------------------------------------------- research */

export const research = {
  forContent: (contentId: string) =>
    api<Record<string, unknown>>(`/v1/content/${contentId}/research`),
  claims: (contentId: string) =>
    api<Record<string, unknown>[]>(`/v1/content/${contentId}/claims`),
  decideClaim: (claimId: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/claims/${claimId}/decisions`, {
      method: "POST",
      body,
    }),
  run: (runId: string) =>
    api<Record<string, unknown>>(`/v1/research-runs/${runId}`),
  artifacts: (runId: string) =>
    api<Record<string, unknown>[]>(`/v1/research-runs/${runId}/artifacts`),
};

/* ----------------------------------------------------- quality & approvals */

export const quality = {
  assessments: (query?: Query) =>
    api<T.QualityAssessment[]>("/v1/quality/assessments", { query }),
  assessment: (id: string) =>
    api<T.QualityAssessment>(`/v1/quality/assessments/${id}`),
  createAssessment: (body: Record<string, unknown>) =>
    api<T.QualityAssessment>("/v1/quality/assessments", { method: "POST", body }),
  reports: (query?: Query) =>
    api<T.QualityReport[]>("/v1/quality/reports", { query }),
  report: (id: string) => api<T.QualityReport>(`/v1/quality/reports/${id}`),
  configurations: () =>
    api<Record<string, unknown>[]>("/v1/quality/configurations"),
  ruleSets: () => api<Record<string, unknown>[]>("/v1/quality/rule-sets"),
  policyEvents: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/quality/policy-events", { query }),
  overridePolicyEvent: (eventId: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(
      `/v1/quality/policy-events/${eventId}/overrides`,
      { method: "POST", body },
    ),
  activity: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/quality/activity", { query }),
  comments: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/quality/comments", { query }),
  createComment: (body: Record<string, unknown>) =>
    api<Record<string, unknown>>("/v1/quality/comments", { method: "POST", body }),
  resolveComment: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/quality/comments/${id}/resolve`, {
      method: "POST",
      body,
    }),
};

export const approvals = {
  list: (query?: Query) => api<T.ApprovalRequest[]>("/v1/approvals", { query }),
  get: (id: string) => api<T.ApprovalRequest>(`/v1/approvals/${id}`),
  create: (body: Record<string, unknown>) =>
    api<T.ApprovalRequest>("/v1/approvals", { method: "POST", body }),
  decisions: (id: string) =>
    api<Record<string, unknown>[]>(`/v1/approvals/${id}/decisions`),
  decide: (id: string, body: Record<string, unknown>) =>
    idempotentPost<T.ApprovalRequest>(`/v1/approvals/${id}/decisions`, body),
  proof: (id: string) => api<Record<string, unknown>>(`/v1/approvals/${id}/proof`),
  invalidateForContent: (contentId: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(
      `/v1/approvals/contents/${contentId}/invalidate`,
      { method: "POST", body },
    ),
};

/* ------------------------------------------------------------------- media */

export const media = {
  assets: (query?: Query) => api<T.MediaAsset[]>("/v1/media/assets", { query }),
  asset: (id: string) => api<T.MediaAsset>(`/v1/media/assets/${id}`),
  versions: (id: string) =>
    api<Record<string, unknown>[]>(`/v1/media/assets/${id}/versions`),
  license: (id: string) =>
    api<Record<string, unknown>>(`/v1/media/assets/${id}/license`),
  licenseReport: (query?: Query) =>
    api<Record<string, unknown>>("/v1/media/license-report", { query }),
  remove: (id: string, lock_version: number) =>
    api<void>(`/v1/media/assets/${id}`, {
      method: "DELETE",
      query: { lock_version },
    }),
  restore: (id: string, body: Record<string, unknown>) =>
    api<T.MediaAsset>(`/v1/media/assets/${id}/restore`, { method: "POST", body }),
  requestUpload: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>("/v1/media/uploads", body),
  createOperation: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>("/v1/media/operations", body),
  job: (jobId: string) => api<Record<string, unknown>>(`/v1/media/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    api<Record<string, unknown>>(`/v1/media/jobs/${jobId}/cancel`, {
      method: "POST",
      body: {},
    }),
  retryJob: (jobId: string) =>
    api<Record<string, unknown>>(`/v1/media/jobs/${jobId}/retry`, {
      method: "POST",
      body: {},
    }),
  providerConnections: () =>
    api<Record<string, unknown>[]>("/v1/media/provider-connections"),
  plan: (planId: string) =>
    api<Record<string, unknown>>(`/v1/media/plans/${planId}`),
};

/* -------------------------------------------------------------- publishing */

export const publishing = {
  connections: () =>
    api<T.PublishingConnection[]>("/v1/publishing/connections"),
  connection: (id: string) =>
    api<T.PublishingConnection>(`/v1/publishing/connections/${id}`),
  createConnection: (body: Record<string, unknown>) =>
    idempotentPost<T.PublishingConnection>(
      "/v1/publishing/connections",
      body,
    ),
  diagnoseConnection: (id: string) =>
    api<Record<string, unknown>>(`/v1/publishing/connections/${id}/diagnose`, {
      method: "POST",
      body: {},
    }),
  refreshConnection: (id: string) =>
    api<Record<string, unknown>>(`/v1/publishing/connections/${id}/refresh`, {
      method: "POST",
      body: {},
    }),
  syncConnectionSettings: (id: string) =>
    api<Record<string, unknown>>(
      `/v1/publishing/connections/${id}/sync-settings`,
      { method: "POST", body: {} },
    ),
  deleteConnection: (id: string, lock_version: number) =>
    api<void>(`/v1/publishing/connections/${id}`, {
      method: "DELETE",
      query: { lock_version },
    }),

  jobs: (query?: Query) => api<T.PublishJob[]>("/v1/publishing/jobs", { query }),
  job: (id: string) => api<T.PublishJob>(`/v1/publishing/jobs/${id}`),
  jobSteps: (id: string) =>
    api<Record<string, unknown>[]>(`/v1/publishing/jobs/${id}/steps`),
  jobAttempts: (id: string) =>
    api<Record<string, unknown>[]>(`/v1/publishing/jobs/${id}/attempts`),
  cancelJob: (id: string) =>
    api<T.PublishJob>(`/v1/publishing/jobs/${id}/cancel`, {
      method: "POST",
      body: {},
    }),
  retryJob: (id: string) =>
    api<T.PublishJob>(`/v1/publishing/jobs/${id}/retry`, {
      method: "POST",
      body: {},
    }),

  publish: (contentId: string, body: Record<string, unknown>) =>
    idempotentPost<T.PublishJob>(`/v1/content/${contentId}/publish`, body),
  preview: (contentId: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/content/${contentId}/publishing-preview`, {
      method: "POST",
      body,
    }),
  createNaverPackage: (contentId: string, body: Record<string, unknown>) =>
    idempotentPost<T.NaverPackage>(
      `/v1/content/${contentId}/naver-package`,
      body,
    ),

  posts: (query?: Query) => api<T.PublishedPost[]>("/v1/published-posts", { query }),
  post: (id: string) => api<T.PublishedPost>(`/v1/published-posts/${id}`),
  reconcilePost: (id: string) =>
    api<T.PublishedPost>(`/v1/published-posts/${id}/reconcile`, {
      method: "POST",
      body: {},
    }),
  rollbackPost: (id: string, body: Record<string, unknown>) =>
    api<T.PublishedPost>(`/v1/published-posts/${id}/rollback`, {
      method: "POST",
      body,
    }),

  naverPackages: (query?: Query) =>
    api<T.NaverPackage[]>("/v1/publishing/naver-packages", { query }),
  naverPackage: (id: string) =>
    api<T.NaverPackage>(`/v1/publishing/naver-packages/${id}`),
  naverChecklist: (id: string) =>
    api<Record<string, unknown>[]>(
      `/v1/publishing/naver-packages/${id}/checklist`,
    ),
  updateNaverChecklist: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(
      `/v1/publishing/naver-packages/${id}/checklist`,
      { method: "POST", body },
    ),
  confirmNaverPackage: (id: string, body: Record<string, unknown>) =>
    api<T.NaverPackage>(`/v1/publishing/naver-packages/${id}/confirm`, {
      method: "POST",
      body,
    }),

  policies: () => api<Record<string, unknown>[]>("/v1/publishing/policies"),
  notifications: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/publishing/notifications", { query }),
};

/* --------------------------------------------------------------- analytics */

export const analytics = {
  connections: () => api<T.AnalyticsConnection[]>("/v1/analytics/connections"),
  createConnection: (body: Record<string, unknown>) =>
    api<T.AnalyticsConnection>("/v1/analytics/connections", {
      method: "POST",
      body,
    }),
  contentFacts: (contentId: string, query?: Query) =>
    api<Record<string, unknown>[]>(`/v1/analytics/content/${contentId}/facts`, {
      query,
    }),
  recommendations: (contentId: string) =>
    api<Record<string, unknown>[]>(
      `/v1/analytics/content/${contentId}/recommendations`,
    ),
  decideRecommendation: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(
      `/v1/analytics/recommendations/${id}/decisions`,
      { method: "POST", body },
    ),
  startSync: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>("/v1/analytics/sync-runs", body),
  syncRun: (id: string) =>
    api<Record<string, unknown>>(`/v1/analytics/sync-runs/${id}`),
  reportRun: (id: string) =>
    api<Record<string, unknown>>(`/v1/analytics/report-runs/${id}`),
  createReportDefinition: (body: Record<string, unknown>) =>
    api<Record<string, unknown>>("/v1/analytics/report-definitions", {
      method: "POST",
      body,
    }),
  runReport: (definitionId: string, body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>(
      `/v1/analytics/report-definitions/${definitionId}/runs`,
      body,
    ),
  roiSnapshot: (body: Record<string, unknown>) =>
    api<Record<string, unknown>>("/v1/analytics/roi-snapshots", {
      method: "POST",
      body,
    }),
};

/* -------------------------------------------------------- bulk & repurpose */

export const bulk = {
  jobs: (query?: Query) => api<T.BulkJob[]>("/v1/bulk/jobs", { query }),
  job: (id: string) => api<T.BulkJob>(`/v1/bulk/jobs/${id}`),
  rows: (id: string, query?: Query) =>
    api<Record<string, unknown>>(`/v1/bulk/jobs/${id}/rows`, { query }),
  create: (body: Record<string, unknown>) =>
    idempotentPost<T.BulkJob>("/v1/bulk/jobs", body),
  pause: (id: string) =>
    api<T.BulkJob>(`/v1/bulk/jobs/${id}/pause`, { method: "POST", body: {} }),
  resume: (id: string) =>
    api<T.BulkJob>(`/v1/bulk/jobs/${id}/resume`, { method: "POST", body: {} }),
  cancel: (id: string) =>
    api<T.BulkJob>(`/v1/bulk/jobs/${id}/cancel`, { method: "POST", body: {} }),
  approveRows: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/bulk/jobs/${id}/rows/approve`, {
      method: "POST",
      body,
    }),
  retryRows: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/bulk/jobs/${id}/rows/retry`, {
      method: "POST",
      body,
    }),
  regenerateRows: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/bulk/jobs/${id}/rows/regenerate`, {
      method: "POST",
      body,
    }),
  export: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/bulk/jobs/${id}/exports`, {
      method: "POST",
      body,
    }),
  previewCsv: (body: Record<string, unknown>) =>
    api<Record<string, unknown>>("/v1/bulk/preview/csv", { method: "POST", body }),
};

export const repurpose = {
  templates: () => api<T.ChannelTemplate[]>("/v1/repurpose/templates"),
  createTemplate: (body: Record<string, unknown>) =>
    api<T.ChannelTemplate>("/v1/repurpose/templates", { method: "POST", body }),
  createJob: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>("/v1/repurpose/jobs", body),
  job: (id: string) => api<Record<string, unknown>>(`/v1/repurpose/jobs/${id}`),
  jobItems: (id: string) =>
    api<Record<string, unknown>[]>(`/v1/repurpose/jobs/${id}/items`),
  jobVariants: (id: string) =>
    api<Record<string, unknown>[]>(`/v1/repurpose/jobs/${id}/variants`),
  command: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/repurpose/jobs/${id}/commands`, {
      method: "POST",
      body,
    }),
  approveVariant: (id: string, body: Record<string, unknown>) =>
    api<Record<string, unknown>>(`/v1/repurpose/variants/${id}/approvals`, {
      method: "POST",
      body,
    }),
};

/* ----------------------------------------------------------------- billing */

export const billing = {
  subscription: () => api<T.BillingSubscription>("/v1/billing/subscription"),
  credits: () => api<T.CreditAccount>("/v1/billing/credits"),
  creditLedger: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/billing/credits/ledger", { query }),
  usage: (query?: Query) =>
    api<Record<string, unknown>>("/v1/billing/usage", { query }),
  usageRecords: (query?: Query) => api<T.UsageRecord[]>("/v1/usage", { query }),
  subscribe: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>("/v1/billing/subscribe", body),
  changePlan: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>("/v1/billing/change-plan", body),
  purchaseCredits: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>(
      "/v1/billing/credits/purchase",
      body,
    ),
};

/* --------------------------------------------------------------- developer */

export const developer = {
  apiKeys: () => api<T.ApiKey[]>("/v1/developer/api-keys"),
  createApiKey: (body: Record<string, unknown>) =>
    api<{ api_key: T.ApiKey; secret: string }>("/v1/developer/api-keys", {
      method: "POST",
      body,
    }),
  rotateApiKey: (id: string, body: Record<string, unknown>) =>
    api<{ api_key: T.ApiKey; secret: string }>(
      `/v1/developer/api-keys/${id}/rotate`,
      { method: "POST", body },
    ),
  revokeApiKey: (id: string, body: Record<string, unknown>) =>
    api<T.ApiKey>(`/v1/developer/api-keys/${id}/revoke`, {
      method: "POST",
      body,
    }),
  webhooks: () => api<T.WebhookEndpoint[]>("/v1/developer/webhooks"),
  createWebhook: (body: Record<string, unknown>) =>
    api<Record<string, unknown>>("/v1/developer/webhooks", {
      method: "POST",
      body,
    }),
  replayDelivery: (deliveryId: string) =>
    api<Record<string, unknown>>(
      `/v1/developer/webhook-deliveries/${deliveryId}/replay`,
      { method: "POST", body: {} },
    ),
};

/* ------------------------------------------------ notifications & platform */

export const notifications = {
  list: (query?: Query) => api<T.Notification[]>("/v1/notifications", { query }),
  markRead: (id: string) =>
    api<T.Notification>(`/v1/notifications/${id}/read`, {
      method: "POST",
      body: {},
    }),
  snooze: (id: string, until: string) =>
    api<T.Notification>(`/v1/notifications/${id}/snooze`, {
      method: "POST",
      body: { snoozed_until: until },
    }),
};

export const jobs = {
  get: (id: string) => api<T.Job>(`/v1/jobs/${id}`),
  cancel: (id: string) =>
    api<T.Job>(`/v1/jobs/${id}/cancel`, { method: "POST", body: {} }),
  retry: (id: string) =>
    api<T.Job>(`/v1/jobs/${id}/retry`, { method: "POST", body: {} }),
};

export const operations = {
  components: () => api<Record<string, unknown>[]>("/v1/operations/components"),
  incidents: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/operations/incidents", { query }),
  incident: (id: string) =>
    api<Record<string, unknown>>(`/v1/operations/incidents/${id}`),
  incidentEvents: (id: string) =>
    api<Record<string, unknown>[]>(`/v1/operations/incidents/${id}/events`),
  runbooks: () => api<Record<string, unknown>[]>("/v1/operations/runbooks"),
  backupPolicies: () =>
    api<Record<string, unknown>[]>("/v1/operations/backup-policies"),
};

export const privacy = {
  requests: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/privacy/requests", { query }),
  request: (id: string) =>
    api<Record<string, unknown>>(`/v1/privacy/requests/${id}`),
  createRequest: (body: Record<string, unknown>) =>
    idempotentPost<Record<string, unknown>>("/v1/privacy/requests", body),
  retentionPolicies: () =>
    api<Record<string, unknown>[]>("/v1/privacy/retention-policies"),
  legalHolds: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/privacy/legal-holds", { query }),
  subprocessors: () =>
    api<Record<string, unknown>[]>("/v1/privacy/subprocessors"),
  consents: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/privacy/consents", { query }),
  accessEvents: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/privacy/access-events", { query }),
  securityIncidents: (query?: Query) =>
    api<Record<string, unknown>[]>("/v1/security/incidents", { query }),
};
