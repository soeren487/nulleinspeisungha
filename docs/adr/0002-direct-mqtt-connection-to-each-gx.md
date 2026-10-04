# Connect to each GX's own MQTT broker

The integration opens its own MQTT connection to the broker built into each Victron GX device, instead of using Home Assistant's MQTT integration. Home Assistant's MQTT integration talks to one broker, and in the owner's network that broker does not carry the GX topics: it has only the virtual grid meter's feed and custom read-only battery values. Reading and writing ESS settings (setpoint override, discharge limit, Dynamic ESS mode) is only possible at the GX's own broker, and a House's AC Battery is one GX, so each House names its GX address.

The Reported Grid Power is the exception: the grid meter driver on the GX reads it from the external broker, so the integration publishes it through Home Assistant's MQTT integration.

## Considered Options

- Bridging the GX topics to the external broker and using Home Assistant's MQTT integration: needs bridge configuration on every GX that the integration cannot check or repair.
- Modbus-TCP to the GX: equally capable, but polled, and the keepalive-free model gives no signal when values go stale.
