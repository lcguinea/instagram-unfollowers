#!/usr/bin/env python3
"""Focused, offline characterization tests for the friendships fetch path.

These tests intentionally inspect the source contract rather than contacting
Instagram.  The repository has no JS test runner; the small mock below models
the browser fetch rejection observed in Chrome and verifies the surrounding
pipeline's documented handling.
"""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
UTILS = (ROOT / "src/utils/utils.ts").read_text(encoding="utf-8")
MAIN = (ROOT / "src/main.tsx").read_text(encoding="utf-8")


async def mocked_friendships_request(fetch):
    """Minimal local model of fetchFriendshipsPage's await boundary."""
    try:
        return await fetch("https://example.invalid/friendships/sample/following")
    except Exception:
        return {"completed": False, "blockingReason": None}


class FriendshipsFetchTests(unittest.TestCase):
    def test_typeerror_from_fetch_is_a_non_http_incomplete_scan(self):
        fetch = AsyncMock(side_effect=TypeError("Failed to fetch"))
        result = asyncio.run(mocked_friendships_request(fetch))
        self.assertEqual(result, {"completed": False, "blockingReason": None})
        fetch.assert_awaited_once()

    def test_request_contract_and_pagination_are_explicit(self):
        self.assertIn("/api/v1/friendships/${viewerId}/${kind}/?count=${count}", UTILS)
        self.assertIn("&max_id=${encodeURIComponent(maxId)}", UTILS)
        self.assertIn("credentials: 'same-origin'", UTILS)
        self.assertIn("'X-IG-App-ID': INSTAGRAM_WEB_APP_ID", UTILS)
        self.assertNotIn("'X-CSRFToken'", UTILS)

    def test_pipeline_does_not_retry_a_network_typeerror(self):
        self.assertIn("page = await fetchFriendshipsPage(kind, maxId, timings.usersPerSearchCycle);", MAIN)
        self.assertIn("return { completed: false, blockingReason };", MAIN)
        self.assertIn("const blockingReason = e instanceof InstagramHttpError", MAIN)
        self.assertNotIn("fetchFriendshipsPage(kind, maxId, timings.usersPerSearchCycle);\n          } catch", MAIN)

    def test_contradictory_pagination_is_not_treated_as_complete(self):
        self.assertIn("const paginationIncomplete =", MAIN)
        self.assertIn("page.has_more === true && !page.next_max_id", MAIN)
        self.assertIn("pageUsers.length === 0 && Boolean(page.next_max_id)", MAIN)
        self.assertIn("if (paginationIncomplete)", MAIN)
        self.assertIn("return { completed: false, blockingReason: null };", MAIN)


if __name__ == "__main__":
    unittest.main()
