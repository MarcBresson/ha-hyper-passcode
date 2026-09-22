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
- A re-entry grace period, so a one-time code means one visit rather than one door opening
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

Read-only:

| Entity | Per | Purpose |
|---|---|---|
| `event.<scope>_code` | scope | Fires on every submission, with the outcome, the reason and the code that matched |
| `sensor.<scope>_last_used` | scope | When a code was last accepted, which one, and whether the use was inside its grace period |
| `sensor.<scope>_last_result` | scope | Verdict on the last attempt, with the reason, the code it matched and whether it was only a test |
| `sensor.<scope>_failed_attempts` | scope | Consecutive failures since the last success |
| `binary_sensor.<scope>_lockout` | scope | On while the scope is refusing submissions |
| `sensor.<code>_uses` | code | Lifetime use count, with remaining uses and window as attributes |

Settings, editable from the device page, a dashboard or an automation:

| Entity | Per | Purpose |
|---|---|---|
| `number.<scope>_code_length` | scope | Auto-submit at this length. `0` waits for a terminator |
| `number.<scope>_inter_key_timeout` | scope | Seconds before a half-typed code is dropped |
| `number.<scope>_lockout_threshold` | scope | Failures before lockout, `5` by default. `0` disables it |
| `number.<scope>_lockout_duration` | scope | How long a lockout lasts, `300s` by default |
| `number.<code>_max_uses` | code | Lifetime limit. `0` is unlimited |
| `number.<code>_uses_per_hour` | code | Rolling hourly limit. `0` is unlimited |
| `number.<code>_uses_per_day` | code | Rolling 24-hour limit. `0` is unlimited |
| `number.<code>_cooldown` | code | Minimum seconds between uses |
| `number.<code>_re_entry_grace_period` | code | Seconds of re-entry that don't count towards `max_uses`. `0` is off |
| `select.<code>_re_entry_grace_window` | code | Whether that window is `fixed` or `sliding` |
| `datetime.<code>_valid_from` | code | Start of the validity window |
| `datetime.<code>_valid_until` | code | End of the validity window |
| `text.<code>_notes` | code | Free text |
| `text.<code>_tags` | code | Comma-separated, and what `revoke_all` filters on |
| `switch.<code>_enabled` | code | Turns a code off without deleting it |
| `switch.<code>_keep_viewable` | code | Off discards the stored copy of the code |
| `sensor.<code>_uses` | code | Every accepted use, counted or not |
| `sensor.<code>_uncounted_uses` | code | How many of those a grace period exempted from `max_uses` |
| `sensor.<code>_last_used` | code | When it was last accepted |
| `sensor.<code>_last_uncounted_use` | code | When a grace period last exempted a use; unknown if never |
| `sensor.<code>_code` | code | The code in clear, when it is kept viewable; unknown otherwise. Its history is purged when the code is discarded |
| `sensor.<code>_store_method` | code | `plaintext` or `hashed` |

Actions:

| Entity | Per | Purpose |
|---|---|---|
| `button.<scope>_generate_delivery_code` | scope | Issues a single-use code valid for two hours |
| `button.<code>_clear_validity_window` | code | Removes both ends of the window at once |

## Installing

In HACS, add `https://github.com/MarcBresson/ha-hyper-passcode` as a custom repository of
type Integration, install HyperPasscode, restart Home Assistant, then add the integration
from Settings → Devices & Services.

## Quick start

### 1. Add a scope

A scope is a thing codes are entered against. It shows up as a device with its own entities.

Go to Settings → Devices & Services → HyperPasscode and press **Add scope**. You give it a
name, and optionally an icon, terminator keys, and default actions to run whenever a valid
code is entered here — which is what lets the common case work without any automation at
all.

Everything else about a scope is a number entity on its device: the code length,
the inter-key timeout, and the two lockout settings. Open the scope's device page to
change them, or set them from an automation like any other number.

Scopes can be edited or deleted from the integration page afterwards. Editing one takes
effect immediately and leaves its lockout counters and any half-typed code alone.

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

### 2. Add a code

Press **Add code** on the same page. Give it a name, pick which scopes it opens, and leave
the code blank to have one generated.

Tick "Keep code viewable" and the next step prints the code, which then stays readable on the
code's own device as `sensor.<code>_code`. Leave it off and the code is never shown — the
confirmation step masks it as `****`, and `sensor.<code>_store_method` reads `hashed`. That
tick box is the one thing that has to be decided up front: a code nobody kept a copy of
cannot be recovered later, only regenerated.

The dialog also takes the schedule and condition entities, since those are entity pickers.
The rest of the validity rules — the window, the use limits, the cooldown, the re-entry
grace period — are entities on
the new code's own device, so "extend the guest code to Sunday" is a datetime you set rather
than a dialog you reopen.

Codes can be edited or deleted from the integration page, and editing one never touches its
use count or history.

As an action:

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

#### From the integration page

Settings → Devices & Services → HyperPasscode → **Configure** → **Test a code** gives you a
page for trying a code by hand. Pick a scope, type the code, and it tells you what the engine
made of it — accepted, or refused with the reason.

Three things about it are worth knowing:

- **"Test only" is on by default.** With it on, nothing happens beyond the verdict: no use is
  counted, no failure counts towards the lockout, nothing is written to the audit log and the
  scope's actions do not run. Turn it off and the page really submits.
- **The source field matters.** A code restricted with `allowed_sources` only works from the
  inputs it names, so a code pinned to the wall keypad will read `wrong_source` until you set
  the source to `keypad`.
- **The page stays open.** It redraws with the verdict after each submission, so you can work
  through several codes without reopening it. The code field is cleared every time and never
  filled back in.

Whichever way it was submitted, the verdict lands on `sensor.<scope>_last_result`.

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
`label`, `credential_id`, `person`, `source`, `reason` and `in_grace_period` — the last
being true only on an accepted use that a re-entry grace period excused from `max_uses`.

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
| `grace_period_seconds` / `grace_mode` | Re-entry window whose uses don't count towards `max_uses`. See below |
| `allowed_sources` | Restrict a code to the wall keypad but not the web UI, for instance |

Everything in that table except the entity lists and `allowed_sources` is an entity on the
code's device, so a rule can be read in a template and changed from an automation. A zero
means "no limit" there, because a number entity has no way to be blank. The two dates are
the exception: a datetime entity can report that a bound is absent but has nothing to set
it back to, so the **Clear validity window** button on the same device is what removes them.

All of it is also reachable as an action, which is what to use if you have turned per-code
entities off. Only the fields you fill in are touched, and an empty one removes that rule:

```yaml
action: hyper_passcode.update_code
data:
  credential_id: "<credential_id>"
  valid_until: null
  uses_per_day: 3
```

A condition entity that is missing, unavailable or unknown counts as off. An unreadable
condition is never treated as permission to enter.

### Coming straight back in

A one-time code is no use to a delivery driver who has to step back out to the van. The
re-entry grace period is a window after a use during which further uses don't count
towards `max_uses` — so `max_uses: 1` means one *visit* rather than one door opening.

The uses still happen: the door opens, the actions run, the audit log records them and
`sensor.<code>_uses` counts them. What changes is only what they're charged against, which
`sensor.<code>_uncounted_uses` reports — the difference between the two is what came off
`max_uses`. `sensor.<code>_last_uncounted_use` says when a grace period last did anything,
and stays unknown until one does. Both sit under Diagnostic on the code's device.

On the scope's side the same fact travels with the activity: `in_grace_period` on the
event entity, the bus event and `sensor.<scope>_last_used`, and an `in_grace` column in
the audit log and its exports. It is only ever true of a use that was accepted.

`select.<code>_re_entry_grace_window` decides where the window is measured from. With a
five-minute grace on a one-time code first used at 12:00:

| Entry | `fixed` | `sliding` |
|---|---|---|
| 12:00 | counted | counted |
| 12:03 | free | free |
| 12:04 | free | free |
| 12:09 | refused — the window ran from 12:00 | free — 12:04 restarted it |

`fixed` is the default and the safer one: `sliding` puts no upper bound on how long a
single-use code stays alive, since every re-entry pushes the window out again.

Two things worth knowing. A grace period never exempts a use from `cooldown_seconds` or
from the hourly and daily limits — setting a cooldown and a grace period on the same code
leaves only the band between them usable, since one says "come straight back" and the other
says "not yet". And switching a code from `sliding` to `fixed` can refuse it immediately,
because the window snaps back to the last counted use; that is what "does not extend"
means, rather than a bug.

The quickest way to hand one out:

```yaml
action: hyper_passcode.create_otp
data:
  scope_id: "<scope_id>"
  label: Delivery
  grace_period_seconds: 300
```

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
| Create entities per credential | on | Adds the use counter, switches, dates and limits per credential. Turn it off if you have many, but then those settings are only reachable through actions |
| Default code length | 6 | Starting point for the generator |
| Audit log size | 1000 | Ring buffer. Every submission also fires an event, so the recorder keeps the full history anyway |
| Log what was typed on failure | off | Off by default, because a failed attempt is usually a typo of a real code, and recording it would leak that code into your logs |

Lockout is not here: it belongs to the door rather than the integration, since a keypad on the
street and a panel in the hallway want different answers. Each scope carries its own
`number.<scope>_lockout_threshold` (5) and `number.<scope>_lockout_duration` (300s), editable
from the scope's device page, a dashboard or an automation.

Lockout moved out of these settings in 0.1.3, and there is no migration: if you had tuned the
integration-wide values, set them again on each scope that needs them.

Collision refusal isn't configurable. Two identical active codes in one scope would make the
audit log unattributable, which defeats the point of the monitoring.

## How things are laid out

Scopes and codes each become a device. A code granted on exactly one scope is linked to
that scope's device, so the scope's page lists the codes that open it rather than leaving
both kinds in one flat list. A code granted on several stays top level: grants are
many-to-many, and nesting it under one of its scopes would hide the others.

Almost every setting sits on one of those devices as an entity rather than in a dialog.
The add and edit forms keep only what an entity cannot express: the name, the code itself,
which scopes it opens, the action sequence, and the schedule and condition pickers.
Everything else — thresholds, limits, dates, notes, tags — is a number, datetime, text or
switch entity, which means it is readable in a template, settable from an automation, and
recorded in history. Writing one goes straight back to the subentry it came from, so a value
set from a dashboard persists exactly as a dialog field did.

## How things are stored

Scopes and codes are Home Assistant config subentries, which is what gives them the
Add and Edit buttons. Only their *configuration* lives there. Config entries are not
written with restricted permissions, so the code itself never goes in one: the lookup
index, any viewable copy, and the use counters live in the integration's own store,
which is written private and atomically.

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
a trade-off, and the UI says so. It also puts the code on `sensor.<code>_code`, which means
the recorder keeps its history like any other state — a code that must not reach the database
is one to leave un-viewable.

Turning the switch back off, or deleting the code, calls `recorder.purge_entities` on that
sensor with `keep_days: 0`, so the history goes with the stored copy rather than lingering
for another `purge_keep_days`. Every row of that entity's history is a copy of the code, so
deleting it whole is the right scope. If the purge cannot be done — the recorder refusing, or
erroring — a repair appears in **Settings > Repairs** naming the code and the entity, because
the alternative is telling you the code is gone when it is not. No recorder means nothing
recorded it, and nothing is attempted.

If Home Assistant is down, no code works. That comes with validating codes in software, and
it's why a lock with its own keypad is still a reasonable backup for a front door.

### Where typed codes end up

A code submitted with `hyper_passcode.submit` or `hyper_passcode.test_code` is in your
recorder database. Not because of anything this integration does — every service call fires
a `call_service` event carrying its data, and the recorder keeps those by default. Script and
automation traces store service data too. If that matters to you:

```yaml
recorder:
  exclude:
    event_types:
      - call_service
```

The "Test a code" page is the one input surface without this problem, and that is why it is a
configuration page rather than a text entity on each scope's device. A flow's input travels
over the websocket into memory and never becomes an event, so nothing about it is recorded.
The verdict it publishes to `sensor.<scope>_last_result` names the code's label, never the
code.

### Who can submit from the page

The page is admin-only, because Home Assistant's configuration screens are. That is the right
default for something that can open a door: with "Test only" unticked it runs whatever the
scope's default actions run, and a wrong code counts towards the same lockout that protects
your physical keypad — so an admin fumbling codes in the UI can lock out the front door.

It also means the page is not a household keypad. The Lovelace keypad card on the roadmap is
what that needs.

### Testing is not rate limited

"Test only" counts no failures, trips no lockout and writes no audit row. That is the point,
but it also makes the page an oracle: an admin can work through the code space without
leaving any of the usual traces. `sensor.<scope>_last_result` is the trace — a run of
`unknown_code` verdicts in its history is what that looks like.

## Roadmap

The engine is done and tested. What's left is mostly what sits on top of it.

Planned

- A Lovelace keypad card, kiosk-friendly, with no code echoed back on screen. This is what
  household entry from the UI needs: the "Test a code" page is admin-only
- A management card, mainly for a readable audit view and bulk operations; scopes and
  codes themselves are already managed from the integration page and their own entities
- A WebSocket API behind the cards, admin-only
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
