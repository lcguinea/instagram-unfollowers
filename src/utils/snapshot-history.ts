// Local history of COMPLETE scans and detection of accounts that stopped
// following you between two consecutive snapshots of the same account.
//
// Unlike utils/scan-snapshot.ts (single slot, only used for restoring the
// last results), this keeps a bounded, ordered list of snapshots holding the
// full followers/following id sets, plus the unfollow events detected between
// them. Events carry `notifiedAt` so a future notification system can consume
// new ones; nothing here schedules scans or sends notifications.
//
// Rules:
// - Only a scan explicitly marked `outcome: "complete"` with valid owner,
//   timestamp and normalized ids for every follower/following is accepted.
//   Anything else is rejected: nothing is written and no event is produced.
// - The first snapshot of an account is only a baseline (no events).
// - An unfollow is a normalized id present in the previous snapshot's
//   followers and absent from the current one. Usernames never define
//   identity. (A snapshot can't tell an unfollow from a block, a removal or
//   a deactivated account: all of them leave your followers list.)
// - A snapshot with no followers right after one that had some is rejected
//   as a suspicious data-empty response rather than attributed as unfollows.
// - Re-recording the same snapshot (same deterministic id) is a no-op, and a
//   snapshot not newer than the account's latest one is rejected.
// - A corrupt or incompatible stored history is never overwritten: recording
//   is refused until it's dealt with.
//
// IMPORTANT: recordCompleteScan must only be called from the confirmed
// complete-scan code path (see main.tsx's `scan` effect), never on restore.

import { SNAPSHOT_HISTORY_STORAGE_KEY } from "../constants/constants";
import { normalizeInstagramId } from "./unfollow-safety";

export const SNAPSHOT_HISTORY_SCHEMA_VERSION = 1;
// Detection only needs the latest snapshot per account; older ones are kept
// for context but bounded so full id lists don't exhaust localStorage, which
// is shared with instagram.com and also holds the Phase 2 scan snapshot.
export const MAX_HISTORY_SNAPSHOTS = 5;
// Serialized size budget (characters). Oldest snapshots are pruned to fit;
// a history that can't fit with just the new snapshot isn't written.
export const MAX_HISTORY_CHARS = 1500000;

export interface HistorySnapshot {
  readonly schemaVersion: 1;
  // `${ownerId}:${completedAt}:${hash of followers/following}`.
  readonly id: string;
  readonly completedAt: number;
  readonly ownerId: string;
  // Sorted, unique, normalized ids.
  readonly followers: readonly string[];
  readonly following: readonly string[];
  // Follower id -> username, display-only; never used for identity.
  readonly usernames: Readonly<Record<string, string>>;
  // Snapshot this one was compared against when recorded; null = baseline.
  readonly previousSnapshotId: string | null;
  readonly previousCompletedAt: number | null;
}

export interface UnfollowEvent {
  // `${previousSnapshotId}>${currentSnapshotId}:${userId}`.
  readonly id: string;
  readonly userId: string;
  readonly username: string | null;
  readonly previousSnapshotId: string;
  readonly currentSnapshotId: string;
  readonly previousCompletedAt: number;
  readonly currentCompletedAt: number;
  readonly detectedAt: number;
  // Whether you follow them in the current snapshot; null = unknown.
  readonly youFollowThem: boolean | null;
  // For a future notification consumer; always null when detected.
  readonly notifiedAt: number | null;
}

export interface SnapshotHistoryData {
  readonly schemaVersion: 1;
  readonly snapshots: readonly HistorySnapshot[];
  readonly events: readonly UnfollowEvent[];
}

export interface HistoryUserInput {
  readonly id: unknown;
  readonly username?: unknown;
}

export interface CompleteScanInput {
  readonly outcome: "complete" | "incomplete" | "failed" | "aborted";
  readonly completedAt: number;
  readonly ownerId: unknown;
  readonly followers: readonly HistoryUserInput[];
  readonly following: readonly HistoryUserInput[];
}

export type BuildSnapshotResult =
  | { readonly ok: true; readonly snapshot: HistorySnapshot }
  | { readonly ok: false; readonly reason: string };

export type AppendOutcome = "baseline" | "appended" | "duplicate" | "rejected";

export interface AppendResult {
  readonly outcome: AppendOutcome;
  readonly reason?: string;
  readonly history: SnapshotHistoryData;
  readonly newEvents: readonly UnfollowEvent[];
}

export type HistoryLoadResult =
  | { readonly status: "empty" | "ok"; readonly history: SnapshotHistoryData }
  | { readonly status: "unreadable"; readonly reason: string };

export interface RecordResult {
  readonly outcome: AppendOutcome;
  readonly reason?: string;
  readonly newEvents: readonly UnfollowEvent[];
}

export type LatestComparison =
  | { readonly kind: "unavailable" }
  | { readonly kind: "none" }
  | { readonly kind: "baseline"; readonly currentCompletedAt: number }
  | {
    readonly kind: "compared";
    readonly previousSnapshotId: string;
    readonly currentSnapshotId: string;
    readonly previousCompletedAt: number;
    readonly currentCompletedAt: number;
    readonly events: readonly UnfollowEvent[];
  };

// What the "Recent Unfollowers" view shows: only the events of the pair
// formed by the account's penultimate and latest complete snapshots.
export type RecentUnfollowers =
  | { readonly kind: "unavailable" }
  // Fewer than two complete snapshots; baselineCompletedAt is the only one, if any.
  | { readonly kind: "needs_another_scan"; readonly baselineCompletedAt: number | null }
  | { readonly kind: "no_unfollowers"; readonly previousCompletedAt: number; readonly currentCompletedAt: number }
  | {
    readonly kind: "unfollowers";
    readonly previousCompletedAt: number;
    readonly currentCompletedAt: number;
    readonly events: readonly UnfollowEvent[];
  };

export function createEmptyHistory(): SnapshotHistoryData {
  return { schemaVersion: SNAPSHOT_HISTORY_SCHEMA_VERSION, snapshots: [], events: [] };
}

// FNV-1a 32-bit: enough to make snapshot ids content-dependent; not security.
function hashString(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return ("0000000" + hash.toString(16)).slice(-8);
}

function computeSnapshotId(
  ownerId: string,
  completedAt: number,
  followers: readonly string[],
  following: readonly string[],
): string {
  return `${ownerId}:${completedAt}:${hashString(followers.join(",") + "|" + following.join(","))}`;
}

function isValidTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function isCanonicalId(value: unknown): value is string {
  return typeof value === "string" && normalizeInstagramId(value) === value;
}

// Sorted strictly ascending (so also unique) canonical ids.
function isSortedIdList(value: unknown): value is readonly string[] {
  if (!Array.isArray(value)) {
    return false;
  }
  for (let i = 0; i < value.length; i++) {
    if (!isCanonicalId(value[i]) || (i > 0 && !(value[i - 1] < value[i]))) {
      return false;
    }
  }
  return true;
}

function collectIds(
  list: unknown,
  usernames: Record<string, string> | null,
): readonly string[] | null {
  if (!Array.isArray(list)) {
    return null;
  }
  const ids = new Set<string>();
  for (const entry of list as readonly unknown[]) {
    if (typeof entry !== "object" || entry === null) {
      return null;
    }
    const user = entry as HistoryUserInput;
    const id = normalizeInstagramId(user.id);
    if (id === null) {
      return null;
    }
    ids.add(id);
    if (usernames !== null && typeof user.username === "string" && user.username !== "") {
      usernames[id] = user.username;
    }
  }
  const sorted: string[] = [];
  ids.forEach(id => sorted.push(id));
  return sorted.sort();
}

/**
 * Validates a finished scan and turns it into a history snapshot. Rejects
 * anything that isn't explicitly complete, lacks a valid owner/timestamp or
 * has any follower/following entry without a valid normalized id (Phase 1
 * treats that as an incomplete scan too).
 */
export function buildHistorySnapshot(scan: CompleteScanInput): BuildSnapshotResult {
  if (scan.outcome !== "complete") {
    return { ok: false, reason: "scan_not_complete" };
  }
  if (!isValidTimestamp(scan.completedAt)) {
    return { ok: false, reason: "invalid_timestamp" };
  }
  const ownerId = normalizeInstagramId(scan.ownerId);
  if (ownerId === null) {
    return { ok: false, reason: "missing_owner" };
  }
  const usernames: Record<string, string> = {};
  const followers = collectIds(scan.followers, usernames);
  if (followers === null) {
    return { ok: false, reason: "invalid_followers" };
  }
  const following = collectIds(scan.following, null);
  if (following === null) {
    return { ok: false, reason: "invalid_following" };
  }
  return {
    ok: true,
    snapshot: {
      schemaVersion: SNAPSHOT_HISTORY_SCHEMA_VERSION,
      id: computeSnapshotId(ownerId, scan.completedAt, followers, following),
      completedAt: scan.completedAt,
      ownerId,
      followers,
      following,
      usernames,
      previousSnapshotId: null,
      previousCompletedAt: null,
    },
  };
}

function isValidUsernames(value: unknown): value is Readonly<Record<string, string>> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return Object.keys(record).every(key => isCanonicalId(key) && typeof record[key] === "string");
}

function isValidSnapshot(value: unknown): value is HistorySnapshot {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const s = value as Record<string, unknown>;
  if (
    s.schemaVersion !== SNAPSHOT_HISTORY_SCHEMA_VERSION ||
    !isValidTimestamp(s.completedAt) ||
    !isCanonicalId(s.ownerId) ||
    !isSortedIdList(s.followers) ||
    !isSortedIdList(s.following) ||
    !isValidUsernames(s.usernames)
  ) {
    return false;
  }
  const isBaseline = s.previousSnapshotId === null && s.previousCompletedAt === null;
  const hasPrevious = typeof s.previousSnapshotId === "string" && isValidTimestamp(s.previousCompletedAt);
  if (!isBaseline && !hasPrevious) {
    return false;
  }
  // The id must match the content: catches tampering and partial writes.
  return s.id === computeSnapshotId(s.ownerId, s.completedAt, s.followers, s.following);
}

function isValidEvent(value: unknown): value is UnfollowEvent {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const e = value as Record<string, unknown>;
  return typeof e.id === "string" &&
    isCanonicalId(e.userId) &&
    (typeof e.username === "string" || e.username === null) &&
    typeof e.previousSnapshotId === "string" &&
    typeof e.currentSnapshotId === "string" &&
    isValidTimestamp(e.previousCompletedAt) &&
    isValidTimestamp(e.currentCompletedAt) &&
    isValidTimestamp(e.detectedAt) &&
    (typeof e.youFollowThem === "boolean" || e.youFollowThem === null) &&
    (isValidTimestamp(e.notifiedAt) || e.notifiedAt === null);
}

function findUsername(snapshot: HistorySnapshot, id: string): string | null {
  return Object.prototype.hasOwnProperty.call(snapshot.usernames, id) ? snapshot.usernames[id] : null;
}

function findLatestForOwner(snapshots: readonly HistorySnapshot[], ownerId: string): HistorySnapshot | null {
  for (let i = snapshots.length - 1; i >= 0; i--) {
    if (snapshots[i].ownerId === ownerId) {
      return snapshots[i];
    }
  }
  return null;
}

function isValidHistory(value: unknown): value is SnapshotHistoryData {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const h = value as Record<string, unknown>;
  if (!Array.isArray(h.snapshots) || !Array.isArray(h.events)) {
    return false;
  }
  const snapshots = h.snapshots as unknown[];
  for (let i = 0; i < snapshots.length; i++) {
    const snapshot = snapshots[i];
    if (!isValidSnapshot(snapshot)) {
      return false;
    }
    // Per account, snapshots must be strictly increasing in time.
    const previous = findLatestForOwner(snapshots.slice(0, i) as HistorySnapshot[], snapshot.ownerId);
    if (previous !== null && previous.completedAt >= snapshot.completedAt) {
      return false;
    }
  }
  return (h.events as unknown[]).every(isValidEvent);
}

/**
 * Pure: appends an already built snapshot to `history`, detecting unfollows
 * against the latest snapshot of the same account. Never mutates `history`;
 * when nothing changes the same `history` object is returned.
 */
export function appendSnapshotToHistory(
  history: SnapshotHistoryData,
  candidate: unknown,
  detectedAt: number,
): AppendResult {
  const unchanged = (outcome: AppendOutcome, reason?: string): AppendResult =>
    ({ outcome, reason, history, newEvents: [] });

  if (!isValidSnapshot(candidate)) {
    return unchanged("rejected", "invalid_snapshot");
  }
  if (!isValidTimestamp(detectedAt)) {
    return unchanged("rejected", "invalid_detected_at");
  }
  if (history.snapshots.some(snapshot => snapshot.id === candidate.id)) {
    return unchanged("duplicate");
  }
  const previous = findLatestForOwner(history.snapshots, candidate.ownerId);
  if (previous !== null && candidate.completedAt <= previous.completedAt) {
    return unchanged("rejected", "not_newer_than_latest");
  }
  // Instagram has answered with structurally valid but data-empty lists
  // before; losing every follower at once is far more likely to be that
  // than real, so don't let it replace the reference or produce events.
  if (previous !== null && previous.followers.length > 0 && candidate.followers.length === 0) {
    return unchanged("rejected", "suspicious_empty_followers");
  }

  const snapshot: HistorySnapshot = {
    ...candidate,
    previousSnapshotId: previous === null ? null : previous.id,
    previousCompletedAt: previous === null ? null : previous.completedAt,
  };

  const newEvents: UnfollowEvent[] = [];
  if (previous !== null) {
    const currentFollowers = new Set(snapshot.followers);
    const currentFollowing = new Set(snapshot.following);
    const existingEventIds = new Set(history.events.map(event => event.id));
    for (const userId of previous.followers) {
      if (currentFollowers.has(userId)) {
        continue;
      }
      const id = `${previous.id}>${snapshot.id}:${userId}`;
      if (existingEventIds.has(id)) {
        continue;
      }
      // Prefer the newest known username (it may have changed).
      newEvents.push({
        id,
        userId,
        username: findUsername(snapshot, userId) ?? findUsername(previous, userId),
        previousSnapshotId: previous.id,
        currentSnapshotId: snapshot.id,
        previousCompletedAt: previous.completedAt,
        currentCompletedAt: snapshot.completedAt,
        detectedAt,
        // Only complete snapshots get here, so following is always known.
        youFollowThem: currentFollowing.has(userId),
        notifiedAt: null,
      });
    }
  }

  return {
    outcome: previous === null ? "baseline" : "appended",
    history: {
      schemaVersion: SNAPSHOT_HISTORY_SCHEMA_VERSION,
      snapshots: [...history.snapshots, snapshot].slice(-MAX_HISTORY_SNAPSHOTS),
      events: [...history.events, ...newEvents],
    },
    newEvents,
  };
}

/**
 * Reads the stored history. Never throws and never writes: a corrupt or
 * incompatible store is reported as "unreadable" and left untouched.
 */
export function loadSnapshotHistory(): HistoryLoadResult {
  let raw: string | null;
  try {
    raw = localStorage.getItem(SNAPSHOT_HISTORY_STORAGE_KEY);
  } catch {
    return { status: "unreadable", reason: "storage_unavailable" };
  }
  if (raw === null) {
    return { status: "empty", history: createEmptyHistory() };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { status: "unreadable", reason: "corrupt_json" };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { status: "unreadable", reason: "invalid_shape" };
  }
  if ((parsed as { readonly schemaVersion?: unknown }).schemaVersion !== SNAPSHOT_HISTORY_SCHEMA_VERSION) {
    return { status: "unreadable", reason: "incompatible_version" };
  }
  if (!isValidHistory(parsed)) {
    return { status: "unreadable", reason: "invalid_shape" };
  }
  return { status: "ok", history: parsed };
}

/**
 * Records a finished scan in the stored history. Must only be called from a
 * confirmed complete-scan code path. Never throws: any rejection or storage
 * failure leaves the stored history as it was and reports no events.
 */
export function recordCompleteScan(scan: CompleteScanInput, detectedAt: number = Date.now()): RecordResult {
  const built = buildHistorySnapshot(scan);
  if (!built.ok) {
    return { outcome: "rejected", reason: built.reason, newEvents: [] };
  }
  const loaded = loadSnapshotHistory();
  if (loaded.status === "unreadable") {
    return { outcome: "rejected", reason: `history_${loaded.reason}`, newEvents: [] };
  }
  const result = appendSnapshotToHistory(loaded.history, built.snapshot, detectedAt);
  if (result.outcome === "rejected" || result.outcome === "duplicate") {
    return { outcome: result.outcome, reason: result.reason, newEvents: [] };
  }
  try {
    let history = result.history;
    let serialized = JSON.stringify(history);
    // Prune oldest snapshots to fit; the new one is always the last.
    while (serialized.length > MAX_HISTORY_CHARS && history.snapshots.length > 1) {
      history = { ...history, snapshots: history.snapshots.slice(1) };
      serialized = JSON.stringify(history);
    }
    if (serialized.length > MAX_HISTORY_CHARS) {
      return { outcome: "rejected", reason: "history_too_large", newEvents: [] };
    }
    localStorage.setItem(SNAPSHOT_HISTORY_STORAGE_KEY, serialized);
  } catch {
    return { outcome: "rejected", reason: "storage_write_failed", newEvents: [] };
  }
  return { outcome: result.outcome, newEvents: result.newEvents };
}

/**
 * What to show for an account: the comparison its latest snapshot was
 * recorded with (baseline, or previous/current dates plus the unfollows
 * detected between them).
 */
export function getLatestComparison(loaded: HistoryLoadResult, ownerId: unknown): LatestComparison {
  if (loaded.status === "unreadable") {
    return { kind: "unavailable" };
  }
  const owner = normalizeInstagramId(ownerId);
  const latest = owner === null ? null : findLatestForOwner(loaded.history.snapshots, owner);
  if (latest === null) {
    return { kind: "none" };
  }
  if (latest.previousSnapshotId === null || latest.previousCompletedAt === null) {
    return { kind: "baseline", currentCompletedAt: latest.completedAt };
  }
  const previousSnapshotId = latest.previousSnapshotId;
  return {
    kind: "compared",
    previousSnapshotId,
    currentSnapshotId: latest.id,
    previousCompletedAt: latest.previousCompletedAt,
    currentCompletedAt: latest.completedAt,
    events: loaded.history.events.filter(
      event => event.previousSnapshotId === previousSnapshotId && event.currentSnapshotId === latest.id,
    ),
  };
}

/**
 * Read-only view of getLatestComparison for the "Recent Unfollowers" filter.
 * Its events are the stored ones as detected, never recomputed here; older
 * events stay stored but aren't part of the latest pair.
 */
export function getRecentUnfollowers(comparison: LatestComparison | undefined): RecentUnfollowers {
  if (comparison === undefined || comparison.kind === "none") {
    return { kind: "needs_another_scan", baselineCompletedAt: null };
  }
  switch (comparison.kind) {
    case "unavailable":
      return { kind: "unavailable" };
    case "baseline":
      return { kind: "needs_another_scan", baselineCompletedAt: comparison.currentCompletedAt };
    case "compared": {
      const { previousCompletedAt, currentCompletedAt, events } = comparison;
      return events.length === 0
        ? { kind: "no_unfollowers", previousCompletedAt, currentCompletedAt }
        : { kind: "unfollowers", previousCompletedAt, currentCompletedAt, events };
    }
  }
}
