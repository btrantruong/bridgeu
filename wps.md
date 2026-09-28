> **2026-09-28:** Revised scope drafted in `design/2026-09-28-mysky-integration/wps-revised.md`; this file is kept unchanged as the original.

1) Follow graph storage
- Snapshot of current graph (already done)
- Track follows/unfollows as they come in (Jetstream follower)
- Ability to rewind graph to earlier points in time (for research, not necessarily
queryable live)
2) Community detection algorithm
- Detect communities and assign each user to a set (static, done by DS),  updated by hand periodically.
- Finds the outgroup community (if it exists) for each community
- Provide the following data in the serving path:
- Mapping of user -> their communities
- Mapping of community -> ingroup users
- Mapping of community -> outgroup users
- Mapping of community -> "banner" users (users who exemplify it)
- Generated names for communities, if possible
3) Bridging-based candidate generator and API
- Gets bridging content from outgroup communities for each user
- Find posts with high engagement from people in ingroup + outgroup community
(posts liked by people in each of 2 sets of users).
- Implement main getFeedSkeleton endpoint
4) Assist with Skeets integration & UI
- Read config from Skeets user store in getFeedSkeleton
- Assist with porting MySky feed controls as needed
- Graph visualization showing ingroup/outgroup communities for a given post,
- Also shows community names / banner users