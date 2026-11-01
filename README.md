# Samsung AC Local

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Local control of Samsung OCF air conditioners via DTLS/CoAP - no cloud, no SmartThings dependency after initial pairing.

> [!IMPORTANT]
> This is a personal project built for my own Samsung AC. I'm sharing it publicly in case it helps someone else, but please keep in mind that I can't guarantee it will work with your setup. I won't necessarily respond to issues or feature requests, and I only plan to extend the integration as far as my own needs go. See [LICENSE](LICENSE) for details. If it works for you - awesome! If not - you're on your own.

## Features

- Direct local communication over DTLS (encrypted UDP)
- Persistent connection with ~50ms command latency
- Zero-config certificate - just enter the AC's IP address
- Full climate entity (HVAC modes, temperature, fan, swing, presets)
- WindFree, Sleep, Quiet, Smart, Speed, DryComfort preset modes
- Sensors: outdoor temperature, energy consumption, filter usage, WiFi RSSI
- Optional external whole-AC power sensor (indoor + outdoor) with persistent local learning and conservative activity estimation
- Switches: display light, beep, air purify, auto-clean
- Binary sensors: cloud connection status, auto-clean activity, freeze wash activity
- Buttons: start/stop auto-clean cycle, start/stop freeze wash

## Requirements

- Samsung OCF air conditioner (WindFree, AR series, etc.)
- AC must have been paired with SmartThings at least once (this provisions the necessary ACL)
- AC on the same local network as Home Assistant

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Install "Samsung AC Local"
3. Restart Home Assistant

### Manual

Copy `custom_components/samsung_ac_local` to your HA `config/custom_components/` directory.

## Configuration

1. Go to Settings > Devices & Services > Add Integration
2. Search for "Samsung AC Local"
3. Enter the AC's IP address
4. The integration will test the connection during setup

That's it. No certificate generation or file management needed.

### Options

- **Polling interval** (default 30s): configurable via integration options after setup
- **External whole-AC power sensor** (optional): pick an existing Home Assistant power sensor in W/kW, including both indoor and outdoor units. This is read-only; the integration will never modify the sensor or the AC based on power.
- **Publish estimated HVAC action** (optional, default **off**): only publish `heating` or `cooling` when the learning model has adequate evidence; otherwise return no `hvac_action`, allowing a supervising thermostat to use its existing behavior. This is an *estimate*, not a compressor signal.

### Adaptive external-power activity monitor

After selecting a power sensor in **Samsung AC Local > Configure**, two entities appear:
- **External electrical power**: a mirror of the selected total power measurement.
- **Estimated activity**: `learning`, `heating_probable`, `cooling_probable`, `idle_probable`, `uncertain`, `fan`, `off`, or `unknown`.

The activity entity exposes the inferred baseline, activation threshold, number of samples and confidence. The model learns from readings during known Samsung modes (especially OFF/FAN), ignores the first two minutes after mode changes and saves its reference samples in Home Assistant storage. It needs fresh reports over time; if the sensor is unavailable/stale, no HVAC action is published. It starts with no learned reference.

**Limitations:** total indoor+outdoor power does not prove compressor status, low inverter output may look like indoor fan consumption, and defrost can mimic heating load. It cannot reliably detect defrost or guarantee an accurate `idle` state. Therefore, `idle_probable` is **not** published as `HVACAction.IDLE`, and the optional `hvac_action` setting should be enabled only after observing sensible estimates for your specific system. When disabled, the climate entity's HVAC action behavior remains unchanged.

## How it works

The integration uses a bundled certificate that authenticates as the Samsung cloud server UUID.
Every Samsung OCF device trusts this UUID after SmartThings pairing. The AC does not verify
certificate chains - it only checks the UUID against its access control list.

After setup, the integration communicates directly with the AC over the local network.
SmartThings cloud control continues to work in parallel.

## Supported models

Tested on Samsung WindFree 2 (AR series). Should work with any Samsung OCF air conditioner that uses DTLS/CoAP on port 49154 after SmartThings pairing.

## Credits

Built on reverse-engineering research of the Samsung OCF protocol, including BLE pairing flow capture and IoTivity protocol analysis.
