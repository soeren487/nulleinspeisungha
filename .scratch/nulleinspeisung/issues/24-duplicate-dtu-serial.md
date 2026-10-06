# 24: Tell a second DTU with the same serial from the same DTU

**What to build:** Two different DTUs can report the same DTU serial, which is a setting in OpenDTU. The integration identifies a DTU by that serial, so the second one is refused as "already added" although it never was, and the owner cannot see why. When a DTU is added whose serial is already in use by a different device, the form says so and tells the owner to give each DTU its own serial in OpenDTU. A DTU that really is already added is still reported as such.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Adding a different DTU whose serial equals that of an existing one shows an error naming the existing DTU and pointing to OpenDTU's DTU settings, in English and German
- [x] Adding the same DTU again still reports that it is already added
- [x] The two cases are told apart by the device's hardware address, also for DTUs added before this change
- [x] Reconfiguring an existing DTU behaves as before

## Comments

2026-10-06: Found on the owner's installation: two of his DTUs reported the same DTU serial, so the second could not be added and the message said it already had been. Implemented on branch `ticket-24-duplicate-dtu-serial`; 595 tests pass, linters clean. The DTU's identity now includes its hardware address from `/api/network/status`; the subentry's unique id stays the DTU serial. Checked read-only against the real DTUs: the hardware addresses are reported and differ. Not confirmed in a running Home Assistant: that the error text shows the existing DTU's name through the form's description placeholders.
