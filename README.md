# Sikker Hjem alarm – Home Assistant integration

This integration connects a **SikkertHjem S7Pro** alarm to Home Assistant **locally**, with no cloud and no MQTT.
Home Assistant acts as the alarm's *receiving station*. The panel pushes **SIA DC-09** frames with
**Ademco Contact-ID (ADM-CID)** events over TCP. The integration validates the CRC and length,
acknowledges each frame (so the panel stops retransmitting) and updates Home Assistant straight away.
It is fully event-driven (`local_push`), with no polling.

## What you get

| Entity / event | Description |
|---|---|
| `alarm_control_panel.sikker_hjem` | `disarmed`, `armed_away`, `armed_home`, `pending` (entry delay running), `triggered`. Read-only: arm and disarm on the panel or in the app. `changed_by` shows the user and the keypad, tag or app user name. |
| `sensor.sikker_hjem_last_event` | Description of the latest event (e.g. "Disarmed by user"), with cid, sensor, partition, user/zone and panel time as attributes. Hourly test reports (602/603) are **not** shown here. |
| `sensor.sikker_hjem_last_message` | Timestamp of the last frame of any kind, including test reports. Diagnostic; use it to detect a lost link. |
| `binary_sensor.sikker_hjem_<sensor>_connectivity` | One per wireless sensor, created automatically the first time the panel reports that sensor losing radio contact (1381/3381). `on` = connected. Diagnostic. |
| `sikker_hjem_event` (bus event) | Fired for **every** Contact-ID event, including test reports. |

States survive Home Assistant restarts.

## Contact-ID codes seen from the S7Pro

These codes have been **confirmed from a real S7Pro**:

| CID | Meaning | Name block `[I…]` | Home Assistant |
|---|---|---|---|
| `3401` | Armed | Keypad/remote/tag name, e.g. `Keyboard` | panel → `armed_away` |
| `1401` | Disarmed | Keypad/remote/tag name, e.g. `Keyboard`, `<name> tag` | panel → `disarmed` |
| `1101` | SOS / personal emergency | App user name who pressed SOS | panel → `triggered`. Repeated about every 5 s until disarmed, with no restore. |
| `1130` | Burglary alarm. Sent **when a delayed door opens** (start of the entry delay), and **again when the delay expires and the siren starts** (~61 s later for a 60 s delay, followed by two more within ~1.5 s) | Sensor name, e.g. `Fordør sensor` | first → `pending`; the repeat at the end of the delay → `triggered` |
| `3130` | Burglary restore, which means the sensor is back to normal (e.g. the door was closed) | Sensor name | no state change |
| `1602` | Periodic test report | none | no state change. Sent **every hour**; the timer restarts after any other message. |
| `1381` | Sensor lost radio contact (RF supervision) | Sensor name, e.g. `Fordør sensor` | `…_connectivity` → off |
| `3381` | Sensor radio contact restored | Sensor name | `…_connectivity` → on |

These codes are **expected but not yet confirmed** on the S7Pro (please report captures):

| CID | Meaning | Home Assistant |
|---|---|---|
| `1134` | Entry/exit burglary | like `1130` |
| `1406` | Cancel by user (not sent: disarming during the siren sends a normal `1401`) | event only |
| `3441` / `3456` | Armed home / partial | `armed_home` |
| `1110`, `1154`, `1162` | Fire / water leak / CO | `triggered` |
| `1301` / `3301` | AC power loss / restore | event only |
| `1302` / `3302` | Low system battery / restore | event only |
| `1384` / `3384` | Sensor low battery / restore | event only |
| `1137`, `1383` | Tamper | `1137` → `triggered`, `1383` → event only |

Every unknown code still fires `sikker_hjem_event`, described as "Contact-ID event NNN".

### S7Pro specifics discovered

* **The user number is always `001`.** Use the **name block** (`sensor` in the event) to tell keypads, tags, app users and sensors apart.
* **Burglary is reported twice for a delayed door.** Opening the door while armed sends `1130` at once, and closing it sends `3130`. If nobody disarms, `1130` is sent again when the entry delay expires and the siren starts. Disarming within the delay sends only `1401`; disarming during the siren also sends a plain `1401`.
  Home Assistant shows `pending` after the first `1130` and switches to `triggered` on the repeat (accepted from 10 s before the configured delay ends). If that repeat is lost, a backup timer triggers 5 s after the configured delay.
* **CRCs can be 2–4 hex digits** (e.g. `26`, `515`, `EF0`).
* **Window/door open and close are not reported** over DC-09 while the alarm is disarmed. The live status in the app goes through the cloud.
* **The CRC is not zero-padded.** The integration accepts this; the panel accepts zero-padded CRCs in our ACKs.
* **Non-ASCII names are sent as UTF-8**, e.g. `Fordør`.
* The panel opens a new TCP connection per event. It retries up to 3 times on the same connection after a NAK, then gives up on that event.
* **The panel clock (UTC) can drift** about 20 s from real time. Home Assistant uses its own receive time for states.

### Event payload

```json
{
  "cid": "1381", "code": "381", "qualifier": 1,
  "description": "Loss of RF supervision",
  "sensor": "Fordør sensor",
  "partition": "01", "user": "001", "zone": "001",
  "account": "1234", "sequence": "0068",
  "panel_time": "2026-11-23T23:37:22+00:00"
}
```

## Installation

### With HACS

1. In HACS, open the ⋮ menu → **Custom repositories**.
2. Add `https://github.com/johnnykudahl/ha-sikker-hjem` with the type **Integration**.
3. Install **Sikker Hjem**, then restart Home Assistant and continue from step 3 below.

### Manually

1. Copy `custom_components/sikker_hjem` into your Home Assistant `config/custom_components/` folder.
   On macOS over Samba, use Terminal (`cp -RX …`), because Finder may stop on `.DS_Store` files that the Samba add-on blocks.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Sikker Hjem**.
4. Enter the port (default `5555`). Optionally enter your account number so that only that account is accepted.
5. In the panel's alarm receiving centre settings, point it at Home Assistant's IP and that port. Use DC-09 / ADM-CID with **encryption off**.
6. Arm and disarm once. The panel entity should follow.

Requires Home Assistant 2024.11 or newer. There are no external Python dependencies.

## Entry delay options

Set these under **Settings → Devices & services → Sikker Hjem → Configure**:

* **Entry delay (seconds)**: match the panel's entry delay (default 60). `0` makes every burglary alarm trigger immediately.
* **Delayed sensors**: comma separated names exactly as shown in the S7Pro app, e.g. `Fordør sensor`. Only these sensors go to `pending`. All other sensors (windows, PIRs, doors without a delay) trigger immediately. Empty (the default) means every burglary alarm triggers immediately.

These are stored per installation in the integration's options. No sensor names are built into the code.

Alarm restores (`3130`) never end `triggered`: the panel stays triggered until it is disarmed.
SOS, fire, water and other non-burglary alarms always trigger immediately.

> Automations for real alarms should use the panel state `triggered`, **not** the raw `sikker_hjem_event` with `cid: "1130"`.
> That event fires every time someone opens a delayed door while the alarm is armed, including a normal homecoming.
> Use `pending` if you want something to happen during the entry delay (e.g. turn on the hall light).

## Automation examples

```yaml
alias: Alarm triggered – all lights 100 %
triggers:
  - trigger: state
    entity_id: alarm_control_panel.sikker_hjem
    to: triggered
actions:
  - action: light.turn_on
    target:
      entity_id: all
    data:
      brightness_pct: 100
```

```yaml
alias: Disarmed with a specific tag
triggers:
  - trigger: event
    event_type: sikker_hjem_event
    event_data:
      cid: "1401"
      sensor: "<name> tag"   # exactly as named in the S7Pro app
actions:
  - action: persistent_notification.create
    data:
      message: "Alarm disarmed with {{ trigger.event.data.sensor }}"
```

```yaml
alias: Sikker Hjem link lost
description: The panel sends a test report every hour
triggers:
  - trigger: template
    value_template: >
      {{ now().timestamp()
         - as_timestamp(states('sensor.sikker_hjem_last_message'), now().timestamp())
         > 2 * 3600 }}
actions:
  - action: persistent_notification.create
    data:
      title: Sikker Hjem
      message: "No message from the alarm panel for over 2 hours"
mode: single
```

## Testing without setting off the alarm

```bash
python3 tools/send_test_event.py <HA-IP> --cid 3401 --sensor Keyboard        # armed
python3 tools/send_test_event.py <HA-IP> --cid 1130 --sensor "Fordør sensor" # triggered
python3 tools/send_test_event.py <HA-IP> --cid 1381 --sensor "Fordør sensor" # sensor offline
python3 tools/send_test_event.py <HA-IP> --cid 1401 --sensor Keyboard        # disarmed
```

Protocol unit tests: `pytest tests`.

## Debug logging

Turn it on from the integration page (⋮ → **Enable debug logging**), or permanently:

```yaml
logger:
  logs:
    custom_components.sikker_hjem: debug
```

Each message is then logged as `Frame from …` followed by `Reply to …` (ACK/NAK/DUH).

## Protocol notes

* Frame: `<LF><CRC16-ARC:1-4 hex><LEN:4 hex>"ADM-CID"<seq>L<line>#<acct>[#<acct>|QEEE PP ZZZ][I<name>]_HH:MM:SS,MM-DD-YYYY<CR>`
* Replies: `ACK` (accepted). `DUH` (intact but unsupported: wrong account, encrypted, unknown format). `NAK` (bad CRC or length).
* Retransmissions with the same sequence within 2 minutes are re-ACKed but not processed twice.

## Security

DC-09 without encryption is not authenticated. Anyone who can reach the port on your LAN could send fake events,
or replay old ones. Keep the port off the internet, consider setting the account filter, and firewall the port so that only the panel's IP can reach it.

## Licence

MIT – see [LICENSE](LICENSE).

This project is not affiliated with or endorsed by SikkertHjem.
