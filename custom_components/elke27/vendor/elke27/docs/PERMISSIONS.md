DESIGN SUMMARY — OUTBOUND GENERATORS, PERMISSIONS, AND RESPONSE CORRELATION (E27)

GOAL
- Add an outbound “generators” subsystem that mirrors inbound “handlers”, while:
  - enforcing Dealer API permission levels correctly
  - correlating responses (single + multi-message) reliably

STRUCTURE
- handlers/
  - Existing inbound receive/dispatch modules (area.py, zone.py, control.py, etc.)
  - Dispatcher routes inbound messages -> handlers

- generators/
  - New outbound request builders (message generators)
  - One module per domain (area.py, zone.py, rule.py, etc.)
  - Generators ONLY build request payloads (no policy enforcement)

- generators/registry.py
  - Canonical registry exporting: COMMANDS: dict[str, CommandSpec]
  - Single source of truth for:
    - command identity (domain + command)
    - required permission level
    - response correlation rules
    - paging / multi-message behavior + merge strategy
    - “automation authority” layering notes (where applicable)

PERMISSIONS (DOCX-BASED)
- Permission levels are NOT bitmasks.
- Use the DOCX PERMISSION_LEVEL_TYPE exactly:
  - PLT_ENCRYPTION_KEY
  - PLT_ANY_USER
  - PLT_MASTER_USER
  - PLT_INSTALLER_USER
  - and the *_DISARMED variants
- *_DISARMED means:
  - same permission level PLUS “all areas disarmed” (panel state gate)

ENFORCEMENT LOCATION
- Permissions are enforced in the HIGH-LEVEL CLIENT API, BEFORE calling generators:
  - verify encryption/session state (if needed)
  - verify user role (any/master/installer)
  - if *_DISARMED: verify panel fully disarmed
  - apply automation-authority checks (for lights/locks/tstats/etc) if required
- Generators never enforce permissions.

FEATURE FILES
- Feature files (if used) are ONLY for capability detection (e.g., supports Z-Wave).
- Feature files MUST NOT define:
  - permissions
  - command registry entries
  - request/response bindings

RESPONSE CORRELATION (DEALER API)
- Primary correlation key is `seq`.
  - client assigns seq per request
  - panel echoes the same seq in the direct response
  - seq == 0 indicates broadcast/unsolicited messages
- session_id is NOT reliable for correlation (often absent in responses).

MULTI-MESSAGE / BLOCK RESPONSES
- Some commands return data in multiple blocks.
- Each response block includes:
  - block_id (current block number, starting at 1)
  - block_count (total blocks expected)
- Completion:
  - paged result is complete when blocks 1..block_count have been received
- Paging model is CLIENT-DRIVEN:
  - client issues one request per block_id = 1..N
  - each block request has its own seq
- Accumulation:
  - per-command merge strategy merges blocks into one logical result
    - (append lists, merge dicts, etc.)

ACK + BROADCAST PATTERN
- Some commands produce:
  1) direct ACK (seq != 0, correlated) — completes the request future
  2) follow-on broadcasts (seq == 0) — update state only, DO NOT resolve the request
- Optional higher-level helpers may “wait for broadcast state convergence” above core correlation.

COMMANDSPEC (SHAPE)
Each CommandSpec in generators/registry.py describes:

1) Identity
- key: "area.get_configured"
- domain: "area"
- command: "get_configured"
- generator: callable -> builds request payload

2) Permissions
- min_permission: PLT_* (from DOCX)
- requires_disarmed: bool (derived from *_DISARMED)
- requires_pin: bool (derived from permission level only)
- required_role: "any_user" | "master" | "installer"
- requires_automation_authority: bool

3) Response binding
- expected_response_key: (domain, command)
- response_mode: "single" | "paged_blocks"

4) Paging (if paged_blocks)
- block_field: "block_id"
- block_count_field: "block_count"
- first_block: 1
- completion_rule: all blocks received
- merge_strategy: per-command

5) Correlation
- Primary: seq
- Broadcasts (seq == 0): bypass pending-request resolution; still dispatched to handlers

GENERATOR CONTRACT
- generator returns: (request_payload, expected_response_key)
- generator does NOT:
  - enforce permissions
  - allocate/manage seq
  - run paging loops

CLIENT SEND PATH
1) Look up CommandSpec in registry
2) Enforce permissions & authority gates
3) Allocate seq
4) Call generator to build request payload
5) Send request (transport)
6) Correlate response(s) by seq
7) If paged: loop blocks, merge via merge_strategy
8) Dispatch all inbound messages (including broadcasts) through existing handlers

RATIONALE
- Mirrors inbound handlers with outbound generators
- Matches DOCX permission tables exactly
- Uses Dealer API’s native seq + block model
- Keeps policy/capability/transport separated
- Scales to paging, broadcasts, and future extensions

SOURCE
- Dealer API reference set:  [oai_citation:0‡control_pc214d_app_DealerAPI_UL_CHM.pdf](file-service://file-Uv42GdDB5cDgbb5t82MzPW)
