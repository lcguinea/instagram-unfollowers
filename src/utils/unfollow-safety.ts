// Pure helpers for the scan/unfollow safety checks. Kept free of DOM, fetch and
// localStorage so they can be tested in isolation.

export class InstagramHttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "InstagramHttpError";
    this.status = status;
    // Needed for `instanceof` to work when compiling Error subclasses to ES5.
    Object.setPrototypeOf(this, InstagramHttpError.prototype);
  }
}

/**
 * Instagram exposes user IDs as numbers (`pk`) or numeric strings (`pk_id`,
 * stored `id`). Returns the canonical digit string, or null when the value
 * isn't a usable ID (empty, "undefined", "null", non-numeric, unsafe number...).
 */
export function normalizeInstagramId(value: unknown): string | null {
  let text: string;
  if (typeof value === "number") {
    // Numbers above 2^53 have already lost precision, so they can't identify an account.
    if (!Number.isSafeInteger(value) || value < 0) {
      return null;
    }
    text = String(value);
  } else if (typeof value === "string") {
    text = value.trim();
  } else {
    return null;
  }
  return /^\d+$/.test(text) ? text : null;
}

export function getRawUserId(raw: { readonly pk_id?: unknown; readonly pk?: unknown }): string | null {
  return normalizeInstagramId(raw.pk_id) ?? normalizeInstagramId(raw.pk);
}

export function isWhitelistedId(id: unknown, whitelist: readonly { readonly id?: unknown }[]): boolean {
  const target = normalizeInstagramId(id);
  if (target === null) {
    return false;
  }
  return whitelist.some(user => normalizeInstagramId(user.id) === target);
}

const MAX_USERNAMES_IN_CONFIRMATION = 50;

export function buildUnfollowConfirmationMessage(users: readonly { readonly username: string }[]): string {
  const total = users.length;
  const listed = users.slice(0, MAX_USERNAMES_IN_CONFIRMATION).map(user => `- @${user.username}`);
  const remaining = total - listed.length;
  const lines = [
    `You are about to unfollow ${total} account${total === 1 ? "" : "s"} (total: ${total}):`,
    ...listed,
  ];
  if (remaining > 0) {
    lines.push(`...and ${remaining} more.`);
  }
  lines.push("", "Whitelisted accounts will be skipped. Do you want to continue?");
  return lines.join("\n");
}

export function isBlockingHttpStatus(status: number): boolean {
  return status === 401 || status === 403 || status === 429;
}

export function getBlockingStatusReason(status: number): string | null {
  switch (status) {
    case 401:
      return "HTTP 401: your Instagram session is not valid or has expired. Log in again.";
    case 403:
      return "HTTP 403: Instagram denied access (possible block or checkpoint). Check your account on instagram.com.";
    case 429:
      return "HTTP 429: too many requests, Instagram is rate limiting you. Wait before trying again.";
    default:
      return null;
  }
}

export type UnfollowOutcome = {
  readonly kind: "success" | "failed" | "stop";
  readonly reason: string;
};

/**
 * Only a 2xx response whose JSON body is `{ status: "ok" }` counts as a
 * confirmed unfollow. 401/403/429 mean the whole process must stop.
 */
export function classifyUnfollowResponse(status: number, body: unknown): UnfollowOutcome {
  const blockingReason = getBlockingStatusReason(status);
  if (blockingReason !== null) {
    return { kind: "stop", reason: blockingReason };
  }
  if (status < 200 || status > 299) {
    return { kind: "failed", reason: `HTTP ${status}: Instagram did not accept the unfollow.` };
  }
  const bodyStatus =
    typeof body === "object" && body !== null ? (body as { readonly status?: unknown }).status : undefined;
  if (bodyStatus === "ok") {
    return { kind: "success", reason: "Unfollow confirmed by Instagram." };
  }
  return { kind: "failed", reason: `HTTP ${status}: Instagram did not confirm the unfollow.` };
}
