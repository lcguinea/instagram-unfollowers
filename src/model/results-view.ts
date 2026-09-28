// Scanning results views: the current scan's accounts that don't follow you
// (actionable) vs. who stopped following you between the last two complete
// snapshots (read-only, see getRecentUnfollowers).
export type ResultsView = "not_following_you" | "recent_unfollowers";
