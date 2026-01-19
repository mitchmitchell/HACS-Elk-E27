Home Assistant ↔ elke27_lib Integration Contract

Status: Authoritative (Architecture-level)

Purpose
-------

This document defines the contractual boundary between the Home Assistant
ElkE27 integration and the elke27_lib library.

It specifies:
- what Home Assistant may assume about the library
- what responsibilities Home Assistant retains
- how state, events, commands, identity, and lifecycle are coordinated

This document is complementary to:
  elke27_lib/docs/CLIENT_CONTRACT.md

CLIENT_CONTRACT.md defines the library’s public API and guarantees.
This document defines how Home Assistant must consume that API.

No protocol, framing, crypto, or transport details appear here.


1. Contract Scope and Authority
-------------------------------

- elke27_lib is the authoritative owner of:
  - protocol mechanics
  - framing, crypto, padding
  - session and authorization state
  - inventory-driven API semantics
  - unsolicited message handling
  - internal PanelState consistency

- Home Assistant is the authoritative owner of:
  - configuration UX
  - credential and link-key persistence
  - entity lifecycle and registry
  - user-facing services and error reporting
  - reconnect and reauth orchestration

- Home Assistant MUST NOT:
  - parse raw protocol messages
  - depend on undocumented library internals
  - infer protocol state from partial data
  - store secrets inside the library


2. Lifecycle Ownership
----------------------

Home Assistant owns the lifecycle of a single library client per config entry.

For each HA config entry:
- exactly one elke27_lib client instance exists
- HA controls when connect() and disconnect() are called
- the library controls internal reconnect, handshake, and session behavior

Home Assistant treats the library as “not ready” until the library signals
baseline readiness (see Section 4).


3. Connection and Session Model
-------------------------------

- Home Assistant provides:
  - host / port (as hints, not identity)
  - persisted link keys
  - optional known MAC address

- The library:
  - establishes or resumes link/session
  - manages authorization and session_id changes
  - emits semantic events reflecting lifecycle changes

Home Assistant never attempts to manage session state directly.


4. Readiness and Bootstrap Boundary
-----------------------------------

Home Assistant must not create entities until the library reports readiness.

Readiness is defined as:
- session established
- panel identity known
- panel_info available
- table / capability information available

The library communicates readiness via:
- an explicit “ready” signal OR
- a defined semantic event indicating bootstrap completion

Until readiness:
- no HA platforms are set up
- no entities are registered
- no services are exposed


5. State Model
--------------

The library owns the canonical state snapshot.

The library exposes read-only accessors for:
- panel_info
- table_info / capabilities
- areas
- zones
- outputs
- lights
- thermostats

State is updated by:
- unsolicited panel messages
- request/response completion
- authorization changes

Home Assistant:
- caches this state in memory
- does not persist it across restarts
- treats the library snapshot as authoritative


6. Event Model
--------------

The library emits semantic events only.

Events:
- are domain-oriented (panel, area, zone, output, etc.)
- include stable identifiers (e.g., area_id, zone_id)
- do not expose raw protocol fields

Home Assistant:
- subscribes to events via the library API
- updates its in-memory state snapshot
- triggers entity updates accordingly

Events are never synthesized in HA to “fix” missing library behavior.


7. Commands and Requests
------------------------

Home Assistant issues commands exclusively through library request APIs.

Entities and services:
- call hub methods
- hub methods call library request() functions

The library returns:
- success results, or
- typed failures

Home Assistant:
- does not retry mutating commands automatically
- may retry read-only commands
- maps typed failures to HA UX (errors, reauth flows, service errors)


8. Error Semantics
------------------

The library classifies failures using typed errors
(as defined in CLIENT_CONTRACT.md).

Home Assistant responsibilities:
- Transport / connection errors → reconnect / unavailable
- AuthorizationRequired → trigger reauth / PIN UX
- InvalidCredentials / InvalidLinkKeys → user remediation
- Protocol / Crypto errors → reset session, log diagnostics

Home Assistant must not downgrade or reclassify library errors.


9. Identity and Uniqueness
--------------------------

Identity rules are HA-owned but must be library-compatible.

- Device identity:
  - Primary: MAC address
  - Secondary: serial number (when available)

- Entity unique_id scheme:
  <mac>_<domain>_<index>

The library provides identity data;
Home Assistant decides how it is registered.


10. Persistence Boundaries
--------------------------

The library:
- does not write files
- does not persist secrets
- does not store HA configuration

Home Assistant:
- persists link keys
- persists MAC identity
- persists host/port hints
- persists config entry metadata

Loss of persisted link keys implies re-linking.


11. Temporary Integration Domain Constraint
-------------------------------------------

During initial development, the HA integration uses the domain “elke27”.

This is a staging decision only.

The HA integration MUST:
- avoid E27-specific shortcuts
- maintain compatibility with eventual reconciliation into elkm1
- keep identity and entity semantics stable across domains

See ADR-0108 and ADR-0109.


12. Non-Goals
-------------

This contract does NOT define:
- protocol byte formats
- encryption algorithms
- retry timing
- entity UX details
- Home Assistant platform selection

Those are defined elsewhere.


13. References
--------------

- elke27_lib/docs/CLIENT_CONTRACT.md
- ADR-0001: E27 as a First-Class Protocol Backend
- ADR-0006: Semantic Translation Lives Inside Each Backend
- ADR-0012: Stable Inventory-Driven E27 API Surface
- ADR-0108: Temporary Separate HA Integration Domain
- ADR-0109: HA Adapts to E27 Library Model
