# HyperPasscode

A credential, policy and action broker for Home Assistant.

Most lock-code integrations manage the PINs stored inside a smart lock's firmware.
HyperPasscode keeps codes in Home Assistant instead, and lets any Home Assistant action be
what a code triggers:

- arm or disarm an alarm
- pulse a gate or garage relay
- unlock a lock that has no keypad of its own
- run a scene or a script

Each code can have a validity window, a use limit, conditions, rate limits and brute-force
lockout, and everything that happens goes into one audit log.

If you want PINs written into a smart lock's internal slots, use
[Lock Code Manager](https://github.com/raman325/lock_code_manager) or Home Assistant's own
lock credential management. HyperPasscode covers the cases those don't, and contains no
device-specific code.

## Features

Codes

- Numeric PINs or alphanumeric codes, either generated or chosen
- Labels, tags, notes, and an owning `person`
- One code can work on several scopes, each with its own grant
- Weak-code rejection for repeated digits, sequential runs and a blocklist you control
- Collision refusal, so two codes can never be confused in the log
- Hashed at rest by default, with a per-code "keep viewable" opt-out

Validity

- Absolute start and end times
- `schedule.*` helpers for recurring weekly windows
- Any on/off entity as a condition, including `calendar`, which gives booking-driven codes
- Lifetime use limits, rolling per-hour and per-day limits, and a cooldown between uses
- Source restrictions, so a code can work on the wall keypad but not the web UI
- Enable, disable and revoke, with revocation being final

Entry

- `submit` for a whole code, `submit_key` for one keystroke at a time
- Keystroke buffering with a terminator key, fixed-length auto-submit and an idle timeout
- Brute-force lockout, with a threshold and duration that can be set per scope or left to
  inherit the integration-wide default

Automations

- Device triggers and conditions, so nothing needs hand-written YAML
- An `event` entity per scope carrying the same information
- Default actions configured on the scope, for when no automation is wanted at all
- `test_code` to find out why a code is refused without opening anything

Monitoring

- A use counter per code, and last-used and failure counters per scope
- An audit log with structured rejection reasons, exportable as JSON or CSV
- Every submission also fires a bus event, so the recorder and logbook keep full history

### Entities

| Entity | Per | Purpose |
|---|---|---|
| `event.<scope>_code` | scope | Fires on every submission, with the outcome and reason |
| `sensor.<scope>_last_used` | scope | When a code was last accepted, and which one |
| `sensor.<scope>_failed_attempts` | scope | Consecutive failures since the last success |
| `binary_sensor.<scope>_lockout` | scope | On while the scope is refusing submissions |
| `button.<scope>_generate_delivery_code` | scope | Issues a single-use code valid for two hours |
| `sensor.<code>_uses` | code | Lifetime use count, with remaining uses and window as attributes |
| `switch.<code>_enabled` | code | Turns a code off without deleting it |

## Installing

In HACS, add `https://github.com/MarcBresson/ha-hyper-passcode` as a custom repository of
type Integration, install HyperPasscode, restart Home Assistant, then add the integration
from Settings → Devices & Services.

## Quick start

### 1. Add a scope

A scope is a thing codes are entered against. It shows up as a device with its own entities.

Go to Settings → Devices & Services → HyperPasscode and press **Add scope**. You give it a
name, and optionally:

- default actions to run whenever a valid code is entered here, which is what lets the
  common case work without any automation at all
- a fixed code length, terminator keys and an inter-key timeout, for physical keypads
- how many failed attempts lock this scope out, and for how long

Scopes can be edited or deleted from the same page afterwards. Editing one takes effect
immediately and leaves its lockout counters and any half-typed code alone.

Everything below is also exposed as an action, so a scope can be created from Developer
Tools → Actions or from an automation instead:

```yaml
action: hyper_passcode.create_scope
data:
  name: Front Door
  default_actions:
    - action: lock.unlock
      target:
        entity_id: lock.front_door
```

Note the returned `scope_id`, which the remaining steps need.

### 2. Create a code

```yaml
action: hyper_passcode.create_code
data:
  label: Household
  scope_ids: ["<scope_id>"]
  length: 6
response_variable: result
```

The generated code comes back in `result.code`, and is only shown once.

### 3. Submit a code

From the keypad card, an ESPHome keypad, a webhook, or by hand:

```yaml
action: hyper_passcode.submit
data:
  scope_id: "<scope_id>"
  code: "495162"
  source: keypad
```

### One-time codes for deliveries

A code valid this afternoon only, usable once:

```yaml
action: hyper_passcode.create_otp
data:
  scope_id: "<scope_id>"
  label: Grocery delivery
  duration: "02:00:00"
response_variable: otp
```

You can also press the scope's "Generate delivery code" button, and the code arrives as a
notification.

## Using codes in automations

Scopes register device triggers, so the automation editor offers them directly without any
YAML:

- Valid code entered
- Wrong code entered
- Expired code entered
- Code refused for exceeding its rate limit
- Locked out after too many failures

Any of the code triggers can be narrowed to a single credential. Trigger data carries
`label`, `credential_id`, `person`, `source` and `reason`.

There are two device conditions as well: "is locked out" and "a credential is currently
valid".

If you prefer entities, every scope has an `event` entity carrying the same information.

## Validity

Rules combine, and all of them have to pass:

| Rule | Notes |
|---|---|
| `valid_from` / `valid_until` | Absolute window, for something like "the 3rd to the 7th of October" |
| Schedule entities | `schedule.*` helpers. They recur weekly and can't express a date range on their own, which is what the absolute window is for |
| Condition entities | Any on/off entity: `binary_sensor`, `switch`, `input_boolean`, `calendar`. Point one at a calendar and a code is only live during matching events |
| `max_uses` | Lifetime limit. Set it to `1` for a one-time code |
| `uses_per_hour` / `uses_per_day` | Rolling windows rather than calendar-aligned, so a limit can't be doubled either side of midnight |
| `cooldown_seconds` | Minimum gap between uses |
| `allowed_sources` | Restrict a code to the wall keypad but not the web UI, for instance |

A condition entity that is missing, unavailable or unknown counts as off. An unreadable
condition is never treated as permission to enter.

### Why a code was refused

Rejections carry a structured reason: `expired`, `not_yet_valid`, `out_of_schedule`,
`condition_failed`, `max_uses_reached`, `rate_limited`, `locked_out`, `wrong_source`,
`unknown_code`, `disabled`, `revoked` or `no_grant`. Automations can then treat an expired
guest code differently from somebody guessing at the keypad.

To find out why a code isn't working without actually opening anything:

```yaml
action: hyper_passcode.test_code
data:
  scope_id: "<scope_id>"
  code: "495162"
response_variable: verdict
```

Nothing is recorded, no use is counted and no action runs.

## Physical keypads

Real keypads send one event per keystroke, so feed them through `submit_key`:

```yaml
action: hyper_passcode.submit_key
data:
  scope_id: "<scope_id>"
  key: "{{ trigger.event.data.key }}"
```

The buffer submits when it sees a terminator key (`#` by default), or automatically once it
reaches the scope's `code_length`. It clears itself after `inter_key_timeout` seconds so a
half-typed code doesn't linger.

## Settings

| Setting | Default | Effect |
|---|---|---|
| Reject weak codes | on | Refuses repeated digits, sequential runs and well-known codes, both for typed codes and generated ones |
| Weak code blocklist | empty | Extra values to treat as weak, such as your house number |
| Create entities per credential | on | Adds a use counter and an enable switch per credential. Turn it off if you have many |
| Default code length | 6 | Starting point for the generator |
| Failed attempts before lockout | 5 | `0` disables lockout. Can be overridden per scope |
| Lockout duration | 300s | Can be overridden per scope |
| Audit log size | 1000 | Ring buffer. Every submission also fires an event, so the recorder keeps the full history anyway |
| Log what was typed on failure | off | Off by default, because a failed attempt is usually a typo of a real code, and recording it would leak that code into your logs |

These are the defaults for the whole integration. The two lockout settings can be overridden
per scope from that scope's dialog: leave them blank there to inherit the values above, or
fill them in to give one door a stricter threshold than the rest of the house.

Collision refusal isn't configurable. Two identical active codes in one scope would make the
audit log unattributable, which defeats the point of the monitoring.

## Security

Codes are stored as `HMAC-SHA256(code, key)` lookup indexes, with a key generated once at
setup. This gives constant-time matching. With per-credential salted hashes you would have to
hash a submission once per stored credential, which at a few hundred credentials takes
seconds per keypad entry.

It's worth being clear about what that buys you. PINs are low-entropy, and the key lives in
`.storage` right next to the data it protects. This defends against casually reading a
backup. It does not defend against an attacker with filesystem access. Home Assistant
backups are unencrypted by default, so treat one as containing your door codes.

"Keep code viewable" stores the code in clear text so it can be read back and re-shared. It's
a trade-off, and the UI says so.

If Home Assistant is down, no code works. That comes with validating codes in software, and
it's why a lock with its own keypad is still a reasonable backup for a front door.

## Roadmap

The engine is done and tested. What's left is mostly what sits on top of it.

Planned

- A Lovelace keypad card, kiosk-friendly, with no code echoed back on screen
- A management card for creating and editing codes without going through Developer Tools
- A WebSocket API behind both cards, admin-only
- Blueprints for the common wiring: keypad to `submit_key`, code used to a notification with
  a camera snapshot, repeated failures to arming the alarm
- Diagnostics download, and repair issues for things like a code about to expire
- hassfest and HACS validation in CI, currently commented out

Under consideration, roughly in order of how useful they seem

- Duress and panic codes: one that opens the door but quietly raises the alarm, one that
  only raises the alarm
- Code classes, so issuing a "cleaner" or "delivery" code is one click rather than a form
- A validity window that starts on first use, for "four hours from whenever they arrive"
- RFID and NFC tags as credentials, through the `tag` integration, under the same policy engine
- A shareable guest pass: a signed, expiring link showing someone their code and when it works
- Usage reports, including which codes nobody has used in months
- An expiry grace period, so a delayed flight doesn't lock a guest out
- Automatic rotation on a schedule, with a notification
- A webhook endpoint for keypads that can't talk to Home Assistant directly
- Two-person rule, requiring two different codes within a few seconds
- TOTP credentials, so a household member uses an authenticator app instead of a fixed code
- A projection layer that pushes codes down into systems doing their own local validation,
  which would also cover the "works while Home Assistant is down" gap

Nothing in the second list is committed. If one of them matters to you, open an issue and say
so — that's the best signal for what to build next.

## Development

Dependencies are managed with [Poetry](https://python-poetry.org/). Python 3.14.2 or newer is
required (Home Assistant 2026.9 dropped 3.13).

```bash
poetry install
poetry run pytest tests/ -q
poetry run ruff check custom_components tests
```

To add or bump a development dependency:

```bash
poetry add --group dev <package>
```
