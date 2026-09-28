import { UserNode } from "./user";

export interface UnfollowLogEntry {
  readonly user: UserNode;
  readonly unfollowedSuccessfully: boolean;
  // Why the unfollow was not confirmed (failed, skipped or stopped).
  readonly reason?: string;
}
