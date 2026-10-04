# Nulleinspeisung

A Home Assistant custom integration that regulates photovoltaic inverters so that each house exchanges a chosen amount of power with the grid, keeps stuck DTUs running, and charges AC batteries from the grid when electricity is cheap.

## Status

Early development. The integration can currently only be added and removed; none of the control features exist yet.

## Installation

### HACS (custom repository)

1. In HACS, open the menu and choose "Custom repositories".
2. Add `https://github.com/soeren487/nulleinspeisungha` with the category "Integration".
3. Download "Nulleinspeisung" and restart Home Assistant.
4. Add the integration under Settings > Devices & services.

### Manual

Copy the folder `custom_components/nulleinspeisung` into the `custom_components` folder of your Home Assistant configuration, restart Home Assistant, and add the integration under Settings > Devices & services.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```
uv run pytest                                  # tests
uv run ruff check . && uv run ruff format --check .   # lint and format check
```
