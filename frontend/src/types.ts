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
  created_at: string;
  updated_at: string;
  access: AccessInfo;
}

export interface DocDetail extends Doc {
  preview: string | null;
}

export interface DocCard {
  id: number;
  title: string;
  team: string;
  classification: Classification;
  snippet?: string;
  why?: string;
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

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  meta: {
    mode?: "ai" | "offline";
    citations?: DocCard[];
    locked?: LockedHint[];
    suggestions?: DocCard[];
    fallback?: { reason: string; message: string };
  };
  created_at: string;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
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
}

export interface ScanFinding {
  flag: string;
  label: string;
  count: number;
  level: Classification;
}
