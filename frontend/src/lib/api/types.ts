/**
 * Hand-picked aliases over the generated OpenAPI types.
 *
 * `schema.d.ts` is generated from `openapi/blogops.json` and should never be
 * edited by hand. Regenerate with `npm run gen:api` after the backend contract
 * changes. Everything the UI touches is re-exported here with a short name so
 * components never reach into `components["schemas"][…]` directly.
 */
import type { components } from "@/lib/api/schema";

type S = components["schemas"];

/* Identity & workspaces */
export type User = S["UserView"];
export type Workspace = S["WorkspaceView"];
export type Membership = S["MembershipView"];
export type Role = S["RoleView"];
export type Session = S["SessionView"];
export type TokenPairResponse = S["TokenPair"];
export type LoginResponse = S["LoginResponse"];
export type SignupResponse = S["SignupResponse"];
export type MFAEnrollment = S["MFAEnrollmentResponse"];
export type AuditLog = S["AuditLogView"];
export type Invitation = S["InvitationView"];

/* Brand catalogue */
export type Brand = S["BrandRead"];
export type BrandVersion = S["BrandVersionRead"];
export type Product = S["ProductRead"];
export type ProductVersion = S["ProductVersionRead"];
export type Persona = S["AudiencePersonaRead"];
export type CatalogStatus = S["CatalogStatus"];

/* Knowledge */
export type KnowledgeSource = S["SourceResponse"];
export type KnowledgeSourceList = S["SourceListResponse"];

/* Keywords */
export type Keyword = S["KeywordView"];
export type KeywordList = S["KeywordListResponse"];
export type KeywordIntent = S["KeywordIntent"];
export type ProviderStatus = S["ProviderStatusView"];

/* Planning */
export type Campaign = S["CampaignRead"];
export type CampaignStatus = S["CampaignStatus"];
export type Brief = S["BriefRead"];
export type BriefVersion = S["BriefVersionRead"];
export type BriefStatus = S["BriefStatus"];
export type BoardColumn = S["BoardColumnRead"];
export type ContentIdea = S["ContentIdeaRead"];
export type TopicNode = S["TopicNodeRead"];
export type CalendarEntry = S["CalendarEntryRead"];
export type MonthlyProposal = S["MonthlyPlanProposalRead"];
export type Comment = S["CommentRead"];

/* Content */
export type Content = S["ContentRead"];
export type ContentVersion = S["ContentVersionRead"];
export type ContentJob = S["ContentJobRead"];

/* Quality & approvals */
export type QualityAssessment = S["QualityAssessmentRead"];
export type QualityReport = S["QualityReportRead"];
export type ReportKind = S["ReportKind"];
export type ApprovalRequest = S["ApprovalRequestRead"];
export type ApprovalRequestStatus = S["ApprovalRequestStatus"];
export type AssessmentDecision = S["AssessmentDecision"];

/* Media */
export type MediaAsset = S["MediaAssetRead"];

/* Publishing */
export type PublishingConnection = S["PublishingConnectionRead"];
export type PublishJob = S["PublishJobRead"];
export type PublishedPost = S["PublishedPostRead"];
export type PublishingProvider = S["PublishingProvider"];
export type PublishedPostState = S["PublishedPostState"];
export type NaverPackage = S["NaverPackageRead"];

/* Analytics */
export type AnalyticsConnection = S["AnalyticsConnectionRead"];

/* Bulk & repurpose */
export type BulkJob = S["BulkJobRead"];
export type ChannelTemplate = S["ChannelTemplateRead"];

/* Billing */
export type CreditAccount = S["CreditAccountRead"];
export type BillingSubscription = S["BillingSubscriptionRead"];
export type UsageRecord = S["UsageRecordRead"];

/* Developer */
export type ApiKey = S["ApiKeyRead"];
export type WebhookEndpoint = S["WebhookEndpointRead"];

/* Platform */
export type Notification = S["NotificationRead"];
export type Job = S["JobView"];
export type JobState = S["JobState"];

/** Every long-running job in the product shares this state machine. */
export const TERMINAL_JOB_STATES: readonly string[] = [
  "SUCCEEDED",
  "PARTIAL",
  "FINAL_FAILED",
  "CANCELLED",
  "EXPIRED",
];

export function isTerminalJobState(state: string): boolean {
  return TERMINAL_JOB_STATES.includes(state);
}
