import axios from "axios";

const baseURL = import.meta.env.VITE_API_URL || "http://localhost:8003";

export const api = axios.create({ baseURL });

export type DocStatus =
  | "uploaded"
  | "queued"
  | "parsing"
  | "failed"
  | "extracted:digital"
  | "extracted:scanned"
  | "verified"
  | "needs_review"
  | "approved"
  | "rejected";

export interface Document {
  id: number;
  filename: string;
  sha256: string;
  status: DocStatus;
  current_job_id?: string | null;
  created_at?: string;
  account_holder?: string | null;
  account_number?: string | null;
  statement_period?: string | null;
  opening_balance?: number | null;
  closing_balance?: number | null;
}

export interface Transaction {
  id?: number;
  date: string;
  description: string;
  amount: number;
  type: "deposit" | "withdrawal";
  balance: number | null;
}

export interface Extraction {
  account_holder: string | null;
  account_number: string | null;
  statement_period: string | null;
  opening_balance: number | null;
  closing_balance: number | null;
  transactions: Transaction[];
}

export interface TransactionUpdate {
  date: string;
  description: string;
  amount: number;
  type: "deposit" | "withdrawal";
  balance: number | null;
}

export interface ParseResponse {
  id: number;
  status: DocStatus;
  reconciliation: {
    passed: boolean;
    deposits_total: number;
    withdrawals_total: number;
    sign_corrections: number;
    checks: { name: string; passed: boolean; detail: string }[];
  };
  extraction: Extraction;
}

export type JobStatus = "queued" | "started" | "success" | "failed" | "retrying";

export interface JobResponse {
  job_id: string;
  status: JobStatus;
  result?: ParseResponse;
  error?: string;
}

export type ParseJob = JobResponse;

export interface ReviewItem {
  id: number;
  reason: string;
  status: string;
  created_at: string;
}

export interface DocumentDetail extends Document {
  created_at: string;
  account_holder: string | null;
  account_number: string | null;
  statement_period: string | null;
  opening_balance: number | null;
  closing_balance: number | null;
  reconciliation: ParseResponse["reconciliation"];
  transactions: Transaction[];
  review_items: ReviewItem[];
  audit_log: AuditEntry[];
}

export interface AuditEntry {
  id: number;
  document_id: number;
  action: "edit_txn" | "approve" | "reject" | string;
  details: Record<string, unknown>;
  actor: string;
  created_at: string;
}
