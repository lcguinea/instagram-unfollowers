import React, { ChangeEvent, useEffect, useState } from "react";
import { render } from "react-dom";
import "./styles.scss";

import { Typename, UserNode } from "./model/user";
import { Toast } from "./components/Toast";
import { UserCheckIcon } from "./components/icons/UserCheckIcon";
import { UserUncheckIcon } from "./components/icons/UserUncheckIcon";
import {
  DEFAULT_TIME_BETWEEN_SEARCH_CYCLES,
  DEFAULT_TIME_BETWEEN_UNFOLLOWS,
  DEFAULT_TIME_TO_WAIT_AFTER_FIVE_SEARCH_CYCLES,
  DEFAULT_TIME_TO_WAIT_AFTER_FIVE_UNFOLLOWS,
  DEFAULT_USERS_PER_SEARCH_CYCLE,
  FOLLOWERS_PAGE_SAFETY_LIMIT,
  FOLLOWING_PAGE_SAFETY_LIMIT,
  INSTAGRAM_HOSTNAME,
} from "./constants/constants";
import {
  assertUnreachable,
  fetchFriendshipsPage,
  FriendshipsListKind,
  getCookie,
  getCurrentPageUnfollowers,
  getUsersForDisplay,
  RawFriendshipUser,
  rawFriendshipUserToUserNode,
  sleep,
  unfollowUserUrlGenerator,
} from "./utils/utils";
import { NotSearching } from "./components/NotSearching";
import { State } from "./model/state";
import { Searching } from "./components/Searching";
import { Toolbar } from "./components/Toolbar";
import { Unfollowing } from "./components/Unfollowing";
import { Timings } from "./model/timings";
import { loadTimings, loadWhitelist, saveTimings, saveWhitelist } from "./utils/whitelist-manager";
import {
  classifyUnfollowResponse,
  getBlockingStatusReason,
  getRawUserId,
  InstagramHttpError,
  isWhitelistedId,
  normalizeInstagramId,
} from "./utils/unfollow-safety";
import { UnfollowLogEntry } from "./model/unfollow-log-entry";
import { loadScanSnapshot, saveScanSnapshot } from "./utils/scan-snapshot";
import { getLatestComparison, HistoryUserInput, loadSnapshotHistory, recordCompleteScan } from "./utils/snapshot-history";

const LOCAL_PREVIEW_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);
const isLocalPreview = LOCAL_PREVIEW_HOSTS.has(location.hostname);

const _avatarUrl = (seed: string): string =>
  `https://api.dicebear.com/9.x/initials/svg?seed=${encodeURIComponent(seed)}&backgroundColor=0f172a,1f2937,312e81&fontFamily=Verdana`;

const _createPreviewUser = (
  id: string,
  username: string,
  fullName: string,
  options: { readonly isPrivate?: boolean; readonly isVerified?: boolean; readonly followsViewer?: boolean } = {},
): UserNode => ({
  id,
  username,
  full_name: fullName,
  profile_pic_url: _avatarUrl(username),
  is_private: options.isPrivate ?? false,
  is_verified: options.isVerified ?? false,
  followed_by_viewer: true,
  follows_viewer: options.followsViewer ?? false,
  requested_by_viewer: false,
  reel: {
    id,
    expiring_at: 0,
    has_pride_media: false,
    latest_reel_media: 0,
    seen: null,
    owner: {
      __typename: Typename.GraphUser,
      id,
      profile_pic_url: _avatarUrl(username),
      username,
    },
  },
});

const _getPreviewUsers = (): readonly UserNode[] => [
  _createPreviewUser("1", "alina.frames", "Alina Moreno", { isVerified: true }),
  _createPreviewUser("2", "brassandbone", "Theo Walsh", { isPrivate: true }),
  _createPreviewUser("3", "citrus.archive", "Mara Kim", { followsViewer: true }),
  _createPreviewUser("4", "dawnledger", "Jon Bell", { isPrivate: true }),
  _createPreviewUser("5", "elias.market", "Elias Noor", { isVerified: true }),
  _createPreviewUser("6", "fieldnotes.studio", "Nadia Reyes"),
  _createPreviewUser("7", "glint.supply", "Remy Park", { followsViewer: true }),
  _createPreviewUser("8", "harbor.sequence", "Ivy Chen", { isPrivate: true }),
  _createPreviewUser("9", "inkline.daily", "Sofia Grant"),
  _createPreviewUser("10", "juniper.signal", "Cal Reed", { isVerified: true }),
  _createPreviewUser("11", "keystone.labs", "Mina Torres"),
  _createPreviewUser("12", "lowlight.club", "Owen Voss", { isPrivate: true }),
];

// pause
let scanningPaused = false;

function pauseScan() {
  scanningPaused = !scanningPaused;
}


// On app start, restores the last COMPLETE scan persisted to localStorage
// (see utils/scan-snapshot.ts), shown clearly as saved data rather than a
// fresh scan and never actionable (see actionsLocked in Searching.tsx).
// Falls back to the plain "initial" state when there's no valid snapshot.
function _buildInitialState(): State {
  const restored = loadScanSnapshot();
  if (!restored.found) {
    return { status: "initial" };
  }
  return {
    status: "scanning",
    page: 1,
    searchTerm: "",
    currentTab: "non_whitelisted",
    percentage: 100,
    results: restored.users,
    selectedResults: [],
    whitelistedResults: loadWhitelist(),
    filter: {
      showNonFollowers: true,
      showFollowers: false,
      showVerified: true,
      showPrivate: true,
      showWithOutProfilePicture: true,
    },
    isRestoredSnapshot: true,
    restoredAt: restored.completedAt,
    // Read-only: restoring never records a snapshot in the history.
    unfollowHistory: getLatestComparison(loadSnapshotHistory(), getCookie("ds_user_id")),
  };
}

function App() {
  const [state, setState] = useState<State>({
    ...(
      isLocalPreview && new URLSearchParams(location.search).get("preview") === "scanning"
        ? {
          status: "scanning",
          page: 1,
          searchTerm: "",
          currentTab: "non_whitelisted",
          percentage: 100,
          results: _getPreviewUsers(),
          selectedResults: _getPreviewUsers().slice(0, 3),
          whitelistedResults: _getPreviewUsers().slice(10, 12),
          filter: {
            showNonFollowers: true,
            showFollowers: false,
            showVerified: true,
            showPrivate: true,
            showWithOutProfilePicture: true,
          },
        } as State
        : isLocalPreview
          ? { status: "initial" as const }
          : _buildInitialState()
    ),
  });

  const [toast, setToast] = useState<{ readonly show: false } | { readonly show: true; readonly text: string }>({
    show: false,
  });

  const [timings, setTimings] = useState<Timings>(() => {
    const storedTimings = loadTimings();
    return {
      timeBetweenSearchCycles: storedTimings?.timeBetweenSearchCycles ?? DEFAULT_TIME_BETWEEN_SEARCH_CYCLES,
      timeToWaitAfterFiveSearchCycles: storedTimings?.timeToWaitAfterFiveSearchCycles ?? DEFAULT_TIME_TO_WAIT_AFTER_FIVE_SEARCH_CYCLES,
      timeBetweenUnfollows: storedTimings?.timeBetweenUnfollows ?? DEFAULT_TIME_BETWEEN_UNFOLLOWS,
      timeToWaitAfterFiveUnfollows: storedTimings?.timeToWaitAfterFiveUnfollows ?? DEFAULT_TIME_TO_WAIT_AFTER_FIVE_UNFOLLOWS,
      usersPerSearchCycle: storedTimings?.usersPerSearchCycle ?? DEFAULT_USERS_PER_SEARCH_CYCLE,
    };
  });

  // Save timings whenever they change
  useEffect(() => {
    saveTimings(timings);
  }, [timings]);


  let isActiveProcess: boolean;
  switch (state.status) {
    case "initial":
      isActiveProcess = false;
      break;
    case "scanning":
    case "unfollowing":
      isActiveProcess = state.percentage < 100;
      break;
    default:
      assertUnreachable(state);
  }

  const onScan = async () => {
    if (state.status !== "initial") {
      return;
    }
    if (isLocalPreview) {
      const previewUsers = _getPreviewUsers();
      setState({
        status: "scanning",
        page: 1,
        searchTerm: "",
        currentTab: "non_whitelisted",
        percentage: 100,
        results: previewUsers,
        selectedResults: previewUsers.slice(0, 3),
        whitelistedResults: previewUsers.slice(10, 12),
        filter: {
          showNonFollowers: true,
          showFollowers: false,
          showVerified: true,
          showPrivate: true,
          showWithOutProfilePicture: true,
        },
      });
      return;
    }
    const whitelistedResults = loadWhitelist();
    setState({
      status: "scanning",
      page: 1,
      searchTerm: "",
      currentTab: "non_whitelisted",
      percentage: 0,
      results: [],
      selectedResults: [],
      whitelistedResults,
      filter: {
        showNonFollowers: true,
        showFollowers: false,
        showVerified: true,
        showPrivate: true,
        showWithOutProfilePicture: true,
      },
    });
  };

  const handleScanFilter = (e: ChangeEvent<HTMLInputElement>) => {
    if (state.status !== "scanning") {
      return;
    }
    if (state.selectedResults.length > 0) {
      if (!confirm("Changing filter options will clear selected users")) {
        // Force re-render. Bit of a hack but had an issue where the checkbox state was still
        // changing in the UI even even when not confirming. So updating the state fixes this
        // by synchronizing the checkboxes with the filter statuses in the state.
        setState({ ...state });
        return;
      }
    }
    setState({
      ...state,
      // Make sure to clear selected results when changing filter options. This is to avoid having
      // users selected in the unfollow queue but not visible in the UI, which would be confusing.
      selectedResults: [],
      filter: {
        ...state.filter,
        [e.currentTarget.name]: e.currentTarget.checked,
      },
    });
  };

  const handleUnfollowFilter = (e: ChangeEvent<HTMLInputElement>) => {
    if (state.status !== "unfollowing") {
      return;
    }
    setState({
      ...state,
      filter: {
        ...state.filter,
        [e.currentTarget.name]: e.currentTarget.checked,
      },
    });
  };

  const toggleUser = (newStatus: boolean, user: UserNode) => {
    if (state.status !== "scanning") {
      return;
    }
    if (newStatus) {
      setState({
        ...state,
        selectedResults: [...state.selectedResults, user],
      });
    } else {
      setState({
        ...state,
        selectedResults: state.selectedResults.filter(result => result.id !== user.id),
      });
    }
  };

  const toggleAllUsers = (e: ChangeEvent<HTMLInputElement>) => {
    if (state.status !== "scanning") {
      return;
    }
    const displayed = getUsersForDisplay(
      state.results,
      state.whitelistedResults,
      state.currentTab,
      state.searchTerm,
      state.filter,
    );
    if (e.currentTarget.checked) {
      const currentIds = new Set(state.selectedResults.map(u => u.id));
      const toAdd = displayed.filter(u => !currentIds.has(u.id));
      setState({
        ...state,
        selectedResults: [...state.selectedResults, ...toAdd],
      });
    } else {
      const displayedIds = new Set(displayed.map(u => u.id));
      setState({
        ...state,
        selectedResults: state.selectedResults.filter(u => !displayedIds.has(u.id)),
      });
    }
  };

  // it will work the same as toggleAllUsers, but it will select everyone on the current page.
  const toggleCurrentePageUsers = (e: ChangeEvent<HTMLInputElement>) => {
    if (state.status !== "scanning") {
      return;
    }
    const pageUsers = getCurrentPageUnfollowers(
      getUsersForDisplay(
        state.results,
        state.whitelistedResults,
        state.currentTab,
        state.searchTerm,
        state.filter,
      ),
      state.page,
    );
    if (e.currentTarget.checked) {
      const currentIds = new Set(state.selectedResults.map(u => u.id));
      const toAdd = pageUsers.filter(u => !currentIds.has(u.id));
      setState({
        ...state,
        selectedResults: [...state.selectedResults, ...toAdd],
      });
    } else {
      const pageUserIds = new Set(pageUsers.map(u => u.id));
      setState({
        ...state,
        selectedResults: state.selectedResults.filter(u => !pageUserIds.has(u.id)),
      });
    }
  };

  const onWhitelistUpdate = (updatedWhitelist: readonly UserNode[]) => {
    saveWhitelist(updatedWhitelist);
    if (state.status === "scanning") {
      setState({
        ...state,
        whitelistedResults: updatedWhitelist,
      });
    }
  };

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      // Prompt user if he tries to leave while in the middle of a process (searching / unfollowing / etc..)
      // This is especially good for avoiding accidental tab closing which would result in a frustrating experience.
      if (!isActiveProcess) {
        return;
      }

      // `e` Might be undefined in older browsers, so silence linter for this one.
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
      e = e || window.event;

      // `e` Might be undefined in older browsers, so silence linter for this one.
      // For IE and Firefox prior to version 4
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
      if (e) {
        e.returnValue = "Changes you made may not be saved.";
      }

      // For Safari
      return "Changes you made may not be saved.";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [isActiveProcess, state]);

  useEffect(() => {
    // Instagram's private following/followers endpoints (see
    // utils/utils.ts) don't expose a reliable total count up front the way
    // the old GraphQL endpoint did, so we can't compute an exact
    // percentage. This gives a smooth, ever-increasing estimate within a
    // phase's share of the progress bar without ever overselling 100%
    // before the phase is actually done.
    const estimatePhaseProgress = (usersFetchedSoFar: number): number =>
      100 * (1 - 1 / (1 + usersFetchedSoFar / 150));

    // Fetches every page of `kind` (following or followers), applying the
    // same pacing/pause/backoff behavior the original single-endpoint scan
    // used, and reports progress within [progressRangeStart, progressRangeEnd]
    // of the overall percentage bar. `blockingReason` is set when Instagram
    // answered 401/403/429, in which case no further requests must be made.
    const fetchList = async (
      kind: FriendshipsListKind,
      pageSafetyLimit: number,
      progressRangeStart: number,
      progressRangeEnd: number,
      onPageUsers: (pageUsers: readonly RawFriendshipUser[]) => void,
    ): Promise<{ readonly completed: boolean; readonly blockingReason: string | null }> => {
      let maxId: string | undefined;
      let pagesFetched = 0;
      let scrollCycle = 0;
      let totalUsersFetched = 0;

      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
      while (true) {
        let page;
        try {
          page = await fetchFriendshipsPage(kind, maxId, timings.usersPerSearchCycle);
        } catch (e) {
          console.error(`Stopping ${kind} scan early:`, e);
          const blockingReason = e instanceof InstagramHttpError ? getBlockingStatusReason(e.status) : null;
          return { completed: false, blockingReason };
        }

        // A 200 without a user list (e.g. `{"status":"fail"}`) is a failed page, not an empty one.
        if (!Array.isArray(page.users)) {
          console.error(`Stopping ${kind} scan early: Instagram returned a page without a user list.`, page);
          return { completed: false, blockingReason: null };
        }
        const pageUsers = page.users;
        totalUsersFetched += pageUsers.length;
        onPageUsers(pageUsers);

        setState(prevState => {
          if (prevState.status !== "scanning") {
            return prevState;
          }
          const rangeSize = progressRangeEnd - progressRangeStart;
          return {
            ...prevState,
            percentage: Math.round(progressRangeStart + estimatePhaseProgress(totalUsersFetched) * (rangeSize / 100)),
          };
        });

        const paginationIncomplete =
          (page.has_more === true && !page.next_max_id) ||
          (page.has_more !== false && pageUsers.length === 0 && Boolean(page.next_max_id));
        if (paginationIncomplete) {
          console.error(`Stopping ${kind} scan early: Instagram returned inconsistent pagination.`, page);
          return { completed: false, blockingReason: null };
        }

        const hasMore = Boolean(page.next_max_id) && page.has_more !== false;
        if (!hasMore) {
          break;
        }

        pagesFetched += 1;
        if (pagesFetched >= pageSafetyLimit) {
          console.error(`Stopping ${kind} scan early: hit the safety cap of ${pageSafetyLimit} pages with ${totalUsersFetched} users fetched.`);
          return { completed: false, blockingReason: null };
        }
        maxId = page.next_max_id;

        // Pause scanning if user requested so.
        while (scanningPaused) {
          await sleep(1000);
          console.info("Scan paused");
        }

        // Human-like behavior: Micro-pause between fetching chunks
        const microPause = Math.floor(Math.random() * 1500) + 500; // 500ms - 2000ms
        await sleep(microPause);

        // Standard delay between cycles
        await sleep(Math.floor(Math.random() * (timings.timeBetweenSearchCycles - timings.timeBetweenSearchCycles * 0.7)) + timings.timeBetweenSearchCycles);

        scrollCycle++;
        if (scrollCycle > 6) {
          scrollCycle = 0;
          // Variable long sleep to avoid patterns
          const longSleepVar = Math.max(
            0,
            timings.timeToWaitAfterFiveSearchCycles + (Math.random() * 10000 - 5000), // +/- 5 seconds
          );
          setToast({
            show: true,
            text: `Sleeping ${Math.round(longSleepVar / 1000)} seconds to prevent getting temp blocked`,
          });
          await sleep(longSleepVar);
        }
        setToast({ show: false });
      }

      setState(prevState => {
        if (prevState.status !== "scanning") {
          return prevState;
        }
        return {
          ...prevState,
          percentage: progressRangeEnd,
        };
      });

      return { completed: true, blockingReason: null };
    };

    const scan = async () => {
      if (state.status !== "scanning" || isLocalPreview) {
        return;
      }

      // Ends the scan without actionable results: a partial following or
      // followers list would show people who do follow you as non-followers.
      const finishIncomplete = (message: string) => {
        setState(prevState => {
          if (prevState.status !== "scanning") {
            return prevState;
          }
          return {
            ...prevState,
            percentage: 100,
            results: [],
            selectedResults: [],
            scanIncomplete: true,
          };
        });
        setToast({
          show: true,
          text: `Scan incomplete: ${message} No actionable results are shown. Please run the scan again later.`,
        });
      };

      // Account the lists are fetched for (see friendshipsUrlGenerator).
      const scanOwnerId = getCookie("ds_user_id");

      // 1. Fetch all accounts you follow.
      // We push directly into followingUsers to avoid allocating new arrays on every page.
      const followingUsers: RawFriendshipUser[] = [];
      const following = await fetchList(
        "following",
        FOLLOWING_PAGE_SAFETY_LIMIT,
        0,
        45,
        pageUsers => {
          followingUsers.push(...pageUsers);
        },
      );

      // Without the complete following list results can't be trusted, so don't
      // waste (or keep hitting Instagram with) requests for followers.
      if (!following.completed) {
        finishIncomplete(following.blockingReason ?? "could not load your full following list from Instagram.");
        return;
      }

      // 2. Fetch follower IDs only.
      // We only store IDs in a Set<string> (plus id/username for the snapshot
      // history) and discard the rest of the follower objects immediately to
      // minimize memory usage.
      const followerIds = new Set<string>();
      const followerUsers: HistoryUserInput[] = [];
      let invalidIdCount = 0;
      const followers = await fetchList(
        "followers",
        FOLLOWERS_PAGE_SAFETY_LIMIT,
        45,
        95,
        pageUsers => {
          for (const user of pageUsers) {
            const id = getRawUserId(user);
            if (id === null) {
              invalidIdCount += 1;
            } else {
              followerIds.add(id);
              followerUsers.push({ id, username: user.username });
            }
          }
        },
      );

      if (!followers.completed) {
        finishIncomplete(followers.blockingReason ?? "could not load your full followers list from Instagram.");
        return;
      }

      const results: UserNode[] = [];
      for (const user of followingUsers) {
        const id = getRawUserId(user);
        const node = id === null ? null : rawFriendshipUserToUserNode(user, followerIds.has(id));
        if (node === null) {
          invalidIdCount += 1;
        } else {
          results.push(node);
        }
      }

      if (invalidIdCount > 0) {
        finishIncomplete(`${invalidIdCount} account(s) came back from Instagram without a valid ID.`);
        return;
      }

      // Scan confirmed complete and valid: safe to persist as the latest
      // snapshot for restoration on the next app load.
      saveScanSnapshot(results);
      // Same confirmed-complete path: add it to the snapshot history and
      // detect unfollows against the previous complete snapshot.
      // If the session changed mid-scan the lists can't be attributed to one
      // account: a null owner makes the history reject the scan.
      const ownerId = getCookie("ds_user_id") === scanOwnerId ? scanOwnerId : null;
      recordCompleteScan({
        outcome: "complete",
        completedAt: Date.now(),
        ownerId,
        followers: followerUsers,
        following: results,
      });
      const unfollowHistory = getLatestComparison(loadSnapshotHistory(), ownerId);

      setState(prevState => {
        if (prevState.status !== "scanning") {
          return prevState;
        }
        return {
          ...prevState,
          percentage: 100,
          results,
          isRestoredSnapshot: false,
          unfollowHistory,
        };
      });

      setToast({
        show: true,
        text: "Scanning completed!",
      });
    };
    scan();
    // Dependency array not entirely legit, but works this way. TODO: Find a way to fix.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status]);

  useEffect(() => {
    const unfollow = async () => {
      if (state.status !== "unfollowing" || isLocalPreview) {
        return;
      }

      const csrftoken = getCookie("csrftoken");
      if (csrftoken === null) {
        setState(prevState => {
          if (prevState.status !== "unfollowing") {
            return prevState;
          }
          return { ...prevState, percentage: 100 };
        });
        setToast({
          show: true,
          text: "Unfollow not started: Instagram's csrftoken cookie was not found. Make sure you are logged in on instagram.com and reload the page.",
        });
        return;
      }

      let counter = 0;
      for (const user of state.selectedResults) {
        counter += 1;
        // Fix: Changed from Math.floor to Math.round to ensure progress reaches 100%
        // Math.floor would leave progress at 99% when near completion
        const percentage = Math.round((counter / state.selectedResults.length) * 100);
        const userId = normalizeInstagramId(user.id);
        let entry: UnfollowLogEntry;
        let requestSent = false;
        let stopReason: string | null = null;
        if (userId === null) {
          entry = { user, unfollowedSuccessfully: false, reason: "Skipped: this account has no valid Instagram ID." };
        } else if (isWhitelistedId(userId, loadWhitelist())) {
          // Re-read the whitelist right before each unfollow: it may have changed since the scan.
          entry = { user, unfollowedSuccessfully: false, reason: "Skipped: this account is whitelisted." };
        } else {
          requestSent = true;
          try {
            const response = await fetch(unfollowUserUrlGenerator(userId), {
              headers: {
                "content-type": "application/x-www-form-urlencoded",
                "x-csrftoken": csrftoken,
              },
              method: "POST",
              mode: "cors",
              credentials: "include",
            });
            let body: unknown = null;
            try {
              body = await response.json();
            } catch {
              // Not JSON (e.g. a login page): can't be a confirmed unfollow.
            }
            const outcome = classifyUnfollowResponse(response.status, body);
            entry = {
              user,
              unfollowedSuccessfully: outcome.kind === "success",
              reason: outcome.kind === "success" ? undefined : outcome.reason,
            };
            if (outcome.kind === "stop") {
              stopReason = outcome.reason;
            }
          } catch (e) {
            console.error(e);
            entry = { user, unfollowedSuccessfully: false, reason: "Network error: the request did not complete." };
          }
        }
        setState(prevState => {
          if (prevState.status !== "unfollowing") {
            return prevState;
          }
          return {
            ...prevState,
            percentage: stopReason === null ? percentage : 100,
            unfollowLog: [...prevState.unfollowLog, entry],
          };
        });
        if (stopReason !== null) {
          setToast({
            show: true,
            text: `Unfollow stopped, no more requests will be sent. ${stopReason}`,
          });
          break;
        }
        // No request was made for skipped accounts, so there's nothing to pace.
        if (!requestSent) {
          continue;
        }
        // If unfollowing the last user in the list, no reason to wait.
        if (user === state.selectedResults[state.selectedResults.length - 1]) {
          break;
        }
        await sleep(Math.floor(Math.random() * (timings.timeBetweenUnfollows * 1.2 - timings.timeBetweenUnfollows)) + timings.timeBetweenUnfollows);

        if (counter % 5 === 0) {
          setToast({
            show: true,
            text: `Sleeping ${timings.timeToWaitAfterFiveUnfollows / 60000} minutes to prevent getting temp blocked`,
          });
          await sleep(timings.timeToWaitAfterFiveUnfollows);
        }
        setToast({ show: false });
      }
    };
    unfollow();
    // Dependency array not entirely legit, but works this way. TODO: Find a way to fix.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status]);

  let markup: React.JSX.Element;
  switch (state.status) {
    case "initial":
      markup = <NotSearching onScan={onScan}></NotSearching>;
      break;

    case "scanning": {
      markup = <Searching
        state={state}
        handleScanFilter={handleScanFilter}
        toggleUser={toggleUser}
        pauseScan={pauseScan}
        setState={setState}
        scanningPaused={scanningPaused}
        UserCheckIcon={UserCheckIcon}
        UserUncheckIcon={UserUncheckIcon}
      ></Searching>;
      break;
    }

    case "unfollowing":
      markup = <Unfollowing
        state={state}
        handleUnfollowFilter={handleUnfollowFilter}
      ></Unfollowing>;
      break;

    default:
      assertUnreachable(state);
  }

  const showScanWarning = state.status === "scanning" && state.results.length === 0;

  return (
    <main id="main" role="main" className={`iu ${showScanWarning ? "has-scan-warning" : ""}`}>
      <section className="overlay">
        <Toolbar
          state={state}
          setState={setState}
          isActiveProcess={isActiveProcess}
          toggleAllUsers={toggleAllUsers}
          toggleCurrentePageUsers={toggleCurrentePageUsers}
          setTimings={setTimings}
          currentTimings={timings}
          whitelistedUsers={state.status === "scanning" ? state.whitelistedResults : loadWhitelist()}
          onWhitelistUpdate={onWhitelistUpdate}
        ></Toolbar>

        {markup}

        {toast.show && <Toast show={toast.show} message={toast.text} onClose={() => setToast({ show: false })} />}
      </section>
    </main>
  );
}

if (location.hostname !== INSTAGRAM_HOSTNAME && !isLocalPreview) {
  alert("Can be used only on Instagram routes");
} else {
  document.title = "InstagramUnfollowers";
  document.body.innerHTML = "";
  render(<App />, document.body);
}
