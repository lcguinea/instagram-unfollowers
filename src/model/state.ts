import { UserNode } from "./user";
import { ScanningTab } from "./scanning-tab";
import { ScanningFilter } from "./scanning-filter";
import { UnfollowLogEntry } from "./unfollow-log-entry";
import { UnfollowFilter } from "./unfollow-filter";
import { LatestComparison } from "../utils/snapshot-history";

type ScanningState = {
  readonly status: 'scanning';
  readonly page: number;
  readonly currentTab: ScanningTab;
  readonly searchTerm: string;
  readonly percentage: number;
  readonly results: readonly UserNode[];
  readonly whitelistedResults: readonly UserNode[];
  readonly selectedResults: readonly UserNode[];
  readonly filter: ScanningFilter;
  // Set when following/followers could not be fully loaded: results are not actionable.
  readonly scanIncomplete?: boolean;
  // Set when these results come from a previously persisted complete scan
  // (see utils/scan-snapshot.ts), rather than one that finished in this
  // session. Restored results must never be actionable (see actionsLocked in
  // Searching.tsx) even though percentage is 100 and scanIncomplete isn't set.
  readonly isRestoredSnapshot?: boolean;
  // Timestamp (ms) the restored snapshot's scan originally completed at.
  // Only meaningful when isRestoredSnapshot is true.
  readonly restoredAt?: number;
  // Latest comparison from the complete-snapshot history (see
  // utils/snapshot-history.ts): who stopped following you since the
  // previous complete snapshot, as opposed to who doesn't follow you now.
  readonly unfollowHistory?: LatestComparison;
};

type UnfollowingState = {
  readonly status: 'unfollowing';
  readonly searchTerm: string;
  readonly percentage: number;
  readonly selectedResults: readonly UserNode[];
  readonly unfollowLog: readonly UnfollowLogEntry[];
  readonly filter: UnfollowFilter;
};

//TODO THIS TYPE OF MULTIPLE STATE NEEDS TO BE SEPARETED IN DIFFERENT FILES ASAP (Global state,unfollowing state, scanning state etc...)
export type State = { readonly status: 'initial' } | ScanningState | UnfollowingState;
