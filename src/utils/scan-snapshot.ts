// Persists the LAST COMPLETE scan of followers/following to localStorage so
// it can be safely restored on the next app load, marked as saved data
// rather than a fresh scan. Single-slot: this is a base for a future
// multi-snapshot/history feature, not a history itself.
//
// IMPORTANT: saveScanSnapshot must only ever be called from a code path that
// has already confirmed a scan is fully complete and valid (see main.tsx's
// `scan` effect). It must never be called for incomplete/failed/aborted
// scans.

import { UserNode } from "../model/user";
import { SCAN_SNAPSHOT_STORAGE_KEY } from "../constants/constants";
import { normalizeInstagramId } from "./unfollow-safety";

export const SCAN_SNAPSHOT_SCHEMA_VERSION = 1;

export interface ScanSnapshotData {
  readonly schemaVersion: 1;
  readonly completedAt: number;
  readonly users: Readonly<Record<string, UserNode>>;
}

export type ScanSnapshotRestoreResult =
  | { readonly found: false }
  | { readonly found: true; readonly completedAt: number; readonly users: readonly UserNode[] };

/**
 * Builds a serializable snapshot from an already-validated complete scan
 * result. Users whose id doesn't normalize to a valid Instagram id are
 * skipped (they shouldn't occur in a complete scan, but this keeps the
 * snapshot's identity model consistent with the rest of the app). Entries
 * that normalize to the same id collapse into a single identity, keeping the
 * last one seen.
 */
export function buildScanSnapshot(results: readonly UserNode[]): ScanSnapshotData {
  const users: Record<string, UserNode> = {};
  for (const user of results) {
    const id = normalizeInstagramId(user.id);
    if (id === null) {
      continue;
    }
    users[id] = { ...user, id };
  }
  return {
    schemaVersion: SCAN_SNAPSHOT_SCHEMA_VERSION,
    completedAt: Date.now(),
    users,
  };
}

/**
 * Persists the given complete scan results as the single latest snapshot,
 * overwriting any previous one. Must only be called from a confirmed
 * complete-scan code path.
 */
export function saveScanSnapshot(
  results: readonly UserNode[],
): { readonly ok: true; readonly completedAt: number } | { readonly ok: false } {
  try {
    const snapshot = buildScanSnapshot(results);
    const serialized = JSON.stringify(snapshot);
    localStorage.setItem(SCAN_SNAPSHOT_STORAGE_KEY, serialized);
    // Confirm it really landed before reporting success.
    if (localStorage.getItem(SCAN_SNAPSHOT_STORAGE_KEY) !== serialized) {
      return { ok: false };
    }
    return { ok: true, completedAt: snapshot.completedAt };
  } catch {
    return { ok: false };
  }
}

function isRecordOfUsers(value: unknown): value is Readonly<Record<string, UserNode>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isValidSnapshotShape(value: unknown): value is ScanSnapshotData {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  if (candidate.schemaVersion !== SCAN_SNAPSHOT_SCHEMA_VERSION) {
    return false;
  }
  if (typeof candidate.completedAt !== "number" || !Number.isFinite(candidate.completedAt)) {
    return false;
  }
  return isRecordOfUsers(candidate.users);
}

/**
 * Reads the stored snapshot, if any. Returns `{ found: false }` safely on
 * any failure: missing key, corrupt JSON, or a schemaVersion that doesn't
 * match what this build understands. Never throws, never fabricates data.
 */
export function loadScanSnapshot(): ScanSnapshotRestoreResult {
  const raw = localStorage.getItem(SCAN_SNAPSHOT_STORAGE_KEY);
  if (raw === null) {
    return { found: false };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { found: false };
  }
  if (!isValidSnapshotShape(parsed)) {
    return { found: false };
  }
  return { found: true, completedAt: parsed.completedAt, users: Object.values(parsed.users) };
}

export function clearScanSnapshot(): void {
  localStorage.removeItem(SCAN_SNAPSHOT_STORAGE_KEY);
}
