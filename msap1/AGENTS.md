# MSAP1 workspace guidance

<!--
This is the authoritative workspace-level guidance. setupWorkspace copies it
to the MSAP1 workspace root. Do not edit the generated workspace copy; update
monutchee-manifest/msap1/AGENTS.md and rerun setupWorkspace.
-->

## Workspace structure

- The workspace root is an orchestration directory, not a Git repository.
  `MSAP1_PL`, `MSAP1_RPU`, `MSAP1_APU`, and
  `yocto-build/sources/meta-monutchee` are independent repositories.
- Before changing a component, read its root `AGENTS.md`. Component guidance is
  more specific than this workspace overview.
- Inspect Git status separately in every repository you touch. Do not combine
  unrelated changes or assume that one branch/commit covers the whole system.

## System ownership

- `MSAP1_PL` captures AD7771 DCLK/DOUT data, validates and packetizes samples,
  and supplies the AXI4-Stream input to AXI DMA S2MM.
- R5 core 0 in `MSAP1_RPU` exclusively owns AD7771 SPI configuration, capture
  registers, and the bring-up AXI DMA channel. R5 core 1 does not own the ADC.
- `MSAP1_APU` visualizes a decimated diagnostic copy delivered by R5 core 0
  over RPMsg. It must not directly take ownership of SPI, capture registers, or
  the RPU-owned DMA path.
- `meta-msap1` packages the APU application, PL firmware, and both R5 firmware
  images into the Linux product image.
- The default ADC profile is 32 kSPS. RPMsg sample streaming is a bring-up and
  visualization path, not the final high-throughput meter data path.

## Cross-repository changes

- An RPMsg wire-ABI change must update compatible protocol definitions and
  tests in both `MSAP1_RPU` and `MSAP1_APU`.
- A PL address-map, interface, clock, or reset change requires BD validation,
  a new bitstream-inclusive XSA, and coordinated RPU/platform verification.
- ADC sample format, packet size, or default-rate changes require coordinated
  PL, RPU, APU, documentation, and target-test updates.
- Keep transient failures and measurements in component test/status documents;
  keep this file limited to durable architecture and workflow guidance.

## Build flow

The generated workspace commands form this handoff chain:

```sh
./make_PL.sh
./make_mconf.sh
./make_RPU.sh
./make_yocto.sh
```

- Follow the affected component `AGENTS.md` for focused verification before
  running the full chain.
- Preserve existing user changes and generated artifacts outside the requested
  scope. Do not commit, push, or deploy unless the user explicitly requests it.

## Maintaining this guidance

- The generated workspace-root copy is read-only guidance. Update the source
  at `monutchee-manifest/msap1/AGENTS.md`, then rerun any MSAP1
  `setupWorkspace` invocation to refresh it.
- Update component-level `AGENTS.md` files in their own repositories when a
  rule applies only to that component.
