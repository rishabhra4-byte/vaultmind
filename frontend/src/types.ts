export type Classification = "public" | "internal" | "confidential" | "restricted";

export interface User {
  id: number;
  email: string;
  full_name: string;
  title: string;
  team_id: number;
  team_name: string;
  position: number;
  position_label: string;
  is_admin: boolean;
  is_active: boolean;
}

export interface Team {
  id: number;
  name: string;
  description: string;
}

export interface AccessInfo {
  allowed: boolean;
  code: string;
  reason: string;
  can_manage: boolean;
  request_pending: boolean;
}

export interface Doc {
  id: number;
  title: string;
  filename: string;
  mime_type: string;
  size: number;
  owner_team_id: number;
  owner_team: string;
  classification: Classification;
  min_position: number;
  min_position_label: string;
  discoverable: boolean;
  shared_with: string[];
  summary: string;
  tags: string[];
  sensitivity_flags: string[];
  uploaded_by: string | null;
  uploaded_by_id: number | null;
  created_at: string;
  updated_at: string;
  access: AccessInfo;
  shared_with_groups: string[];
  version: number;
  /** False when admins or the document's managers keep it away from the AI. */
  ai_enabled: boolean;
  /** "mirror": synced with the sharing it has in its source (Drive, SharePoint). */
  acl_mode: "native" | "mirror";
  source: SourceRef | null;
  source_path: string;
  external_url: string | null;
}

export interface SourceRef {
  id: number;
  kind: string;
  label: string;
  name: string;
}

export interface Passage {
  ordinal: number;
  text: string;
}

export interface DocDetail extends Doc {
  preview: string | null;
  passages: Passage[];
}

export interface Version {
  version: number;
  created_at: string;
  created_by: string | null;
  change_summary: string;
  size: number;
}

export interface Changes {
  from_version: number | null;
  to_version: number;
  summary: string;
  diff: {
    added: string[];
    removed: string[];
    changed: { before: string; after: string }[];
    counts: { added: number; removed: number; changed: number };
  } | null;
}

export interface DocCard {
  id: number;
  title: string;
  team: string;
  classification: Classification;
  snippet?: string;
  why?: string;
  /** Paragraphs cited, by their position in the document. */
  passages?: number[];
  updated_at?: string;
  version?: number;
  /** Who uploaded it (documents synced from a source are owned there). */
  owner?: string;
  source?: { name: string; kind: string; synced_at: string | null; url: string | null };
  /** Why this person may open it, in words. */
  access?: string;
  /** Words of the question this document matched. */
  matched?: string[];
  /** Set when it may be out of date: a newer document disagrees, or it hasn't changed in a year. */
  outdated?: string;
}

/** Whether the documents hold enough to answer. */
export interface Evidence {
  enough: boolean;
  coverage: number;
  /** Words of the question that no document the person can open mentions. */
  missing?: string[];
}

/** Two documents that disagree about what was asked; `a` is the newer one. */
export interface Conflict {
  a: ConflictSide;
  b: ConflictSide;
  about: string[];
}

export interface ConflictSide {
  id: number;
  title: string;
  says: string[];
  sentence: string;
  passage: number;
  updated_at: string;
}

/** Narrow which documents the assistant answers from (never widens access). */
export interface AnswerFilters {
  team_id?: number;
  updated_within_days?: number;
}

export interface LockedHint {
  id: number;
  title: string;
  team: string;
  classification: Classification;
  reason: string;
  required_position: string;
  request_pending: boolean;
}

export type ActionKind = "request_access" | "share" | "save_summary" | "draft_message";

export interface AssistantAction {
  id: number;
  kind: ActionKind;
  label: string;
  payload: {
    document_id?: number;
    title?: string;
    reason?: string;
    duration_days?: number;
    target?: { kind: "team" | "group" | "user"; id: number; name: string; email?: string };
    message_id?: number;
    to?: string;
    email?: string;
    subject?: string;
    body?: string;
  };
  status: "proposed" | "done" | "dismissed" | "failed";
  result: { message?: string; link?: string; body?: string };
}

export type Intent = "summary" | "key_points" | "for_me" | "changes" | "compare";

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  meta: {
    /** ai: written by the model; offline: matching passages; direct: VaultMind's own records or a template. */
    mode?: "ai" | "offline" | "direct";
    intent?: string;
    citations?: DocCard[];
    locked?: LockedHint[];
    suggestions?: DocCard[];
    fallback?: { reason: string; message: string };
    notice?: string;
    ai_blocked?: DocCard[];
    search?: {
      expanded?: string[];
      rewritten?: string;
      corrected?: { from: string; to: string }[];
      acronyms?: { acronym: string; means: string }[];
      /** The filters the answer was limited to, in words. */
      scope?: string;
    };
    evidence?: Evidence;
    conflicts?: Conflict[];
    actions?: AssistantAction[];
    changes?: { id: number; title: string; version: number; from_version: number }[];
  };
  created_at: string;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  document_id: number | null;
}

export interface Notification {
  id: number;
  kind: string;
  title: string;
  body: string;
  link: string;
  created_at: string;
  read_at: string | null;
}

export interface Preferences {
  notify_email: boolean;
  email_available: boolean;
  slack_available: boolean;
  slack_linked: boolean;
}

export interface Onboarding {
  active: boolean;
  until: string | null;
  steps: { id: string; label: string; hint: string; done: boolean; link: string }[];
  done: number;
  total: number;
}

export interface GlossaryTerm {
  id: number | null;
  term: string;
  synonyms: string[];
  note: string;
  builtin: boolean;
}

export interface Group {
  id: number;
  name: string;
  description: string;
  provider: "manual" | "google" | "microsoft";
  members: number;
  member_ids?: number[];
}

export interface Recommendation {
  document: Doc;
  score: number;
  reasons: string[];
}

export interface AccessRequest {
  id: number;
  document_id: number;
  document_title: string;
  document_team: string;
  classification: Classification;
  requester: User;
  reason: string;
  duration_days: number;
  status: "pending" | "approved" | "denied" | "cancelled";
  decided_by: string | null;
  decision_note: string;
  created_at: string;
  decided_at: string | null;
}

export interface Grant {
  id: number;
  document_id: number;
  document_title: string;
  user_id: number;
  user_name: string;
  granted_by: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  actor: string | null;
  actor_id: number | null;
  action: string;
  target_type: string;
  target_id: number | null;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface DemoUser {
  email: string;
  password: string;
  full_name: string;
  title: string;
  team: string;
  position: string;
  is_admin: boolean;
}

export interface Meta {
  company: string;
  ai_mode: "ai" | "offline";
  ai_model: string | null;
  positions: { value: number; label: string }[];
  classifications: { value: Classification; label: string; help: string }[];
  teams: Team[];
  demo_users: DemoUser[];
  /** Company sign-in options that are set up on the server ("Continue with Google"). */
  sso_providers: { id: string; label: string }[];
  /** False when people must use their company account; administrators can still use a password. */
  password_login: boolean;
}

export interface ScanFinding {
  flag: string;
  label: string;
  count: number;
  level: Classification;
}

export interface AdminUser extends User {
  auth_provider: string;
  directory_managed: boolean;
  has_password: boolean;
  onboarding: boolean;
  notify_email: boolean;
  groups: string[];
  created_at: string;
}

export interface AdminTeam extends Team {
  members: number;
  documents: number;
}

export interface Source {
  id: number;
  kind: "folder" | "google_drive" | "microsoft_365";
  label: string;
  name: string;
  config: Record<string, unknown>;
  owner_team_id: number;
  owner_team: string;
  classification: Classification;
  permission_mode: "mirror" | "rules";
  ai_enabled: boolean;
  sync_directory: boolean;
  enabled: boolean;
  sync_interval_minutes: number;
  status: "idle" | "syncing" | "ok" | "error";
  last_synced_at: string | null;
  last_error: string;
  stats: Record<string, number | string[]>;
  document_count: number;
  created_at: string;
}

export interface Insights {
  days: number;
  questions: number;
  unanswered: number;
  channels: Record<string, number>;
  top_questions: { question: string; count: number; unanswered: number; last_asked: string }[];
  unanswered_questions: { question: string; count: number; last_asked: string }[];
  stale_documents: { id: number; title: string; team: string; updated_at: string; views: number }[];
  sources: {
    id: number;
    name: string;
    kind: string;
    label: string;
    status: string;
    enabled: boolean;
    last_synced_at: string | null;
    last_error: string;
    overdue: boolean;
    documents: number;
  }[];
  ai: { mode: "ai" | "offline"; documents_ai_off: number; sources_ai_off: number };
}
