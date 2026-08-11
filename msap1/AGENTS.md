# MSAP1 workspace guidance

<!--
This is the authoritative workspace-level guidance. setupWorkspace copies it
to the MSAP1 workspace root. Do not edit the generated workspace copy; update
monutchee-manifest/msap1/AGENTS.md and rerun setupWorkspace.
-->

## Workspace structure

- The workspace root is an orchestration directory, not a repo client or Git
  repository.
- `applications` is the `applications.xml` repo client. It manages
  `MSAP1_PL`, `MSAP1_RPU`, `MSAP1_APU`, and `MSAP1_WEB`.
- `yocto-build` is the independent `yocto.xml` repo client.
  `yocto-build/sources/meta-monutchee` remains an independent Git repository.
- APU and RPU Git submodules remain pinned by their owning repositories and
  are not part of root `repo start ... --all --this-manifest-only` operations.
- On first synchronization, `setupWorkspace` starts local `main` branches for
  new top-level product checkouts and `meta-monutchee`. Later runs preserve
  existing active branches.
- Before changing a component, read its root `AGENTS.md`. Component guidance is
  more specific than this workspace overview.
- Inspect Git status separately in every repository you touch. Do not combine
  unrelated changes or assume that one branch/commit covers the whole system.

## System ownership

- `MSAP1_PL` captures AD7771 DCLK/DOUT data, validates and packetizes samples,
  and supplies the AXI4-Stream input to AXI DMA S2MM.
- R5 core 0 in `MSAP1_RPU` exclusively owns AD7771 SPI configuration, reset,
  synchronization, capture registers, and health. R5 core 1 does not own the
  ADC. R5 core 0 does not own the AXI DMA data path.
- Linux owns AXI DMA S2MM, scatter-gather descriptors, interrupts, and
  CMA-backed DDR buffers. `MSAP1_APU` receives full-rate samples through IIO;
  `msap1-fpga-acquisition` publishes a multi-reader shared-memory ring.
- RPMsg carries ADC capture START/STOP and health only. It must not carry ADC
  sample payloads.
- `MSAP1_WEB` owns the product frontend. It is separate from the platform-neutral
  WebEngine library pinned at `MSAP1_APU/libs/webengine`.
- `meta-msap1` packages the APU application, PL firmware, and both R5 firmware
  images into the Linux product image.
- The default ADC profile is 32 kSPS, eight signed 24-bit channels stored in
  eight 32-bit words per frame, with 256 frames per PL packet.

## Cross-repository changes

- An RPMsg wire-ABI change must update compatible protocol definitions and
  tests in both `MSAP1_RPU` and `MSAP1_APU`.
- A PL address-map, interface, clock, or reset change requires BD validation,
  a new bitstream-inclusive XSA, regenerated machine configuration/PL overlay,
  and coordinated RPU/Linux verification.
- ADC sample format, packet size, or default-rate changes require coordinated
  PL, RPU, APU, documentation, and target-test updates.
- Use neutral MSAP1 sensor-board identifiers throughout source, profile IDs,
  filenames, UI, tests, documentation, and packaging. Do not introduce
  third-party vendor or product branding into the repositories.
- Keep transient failures and measurements in component test/status documents;
  keep this file limited to durable architecture and workflow guidance.

## Build flow

The generated workspace commands form two XSA/contract branches that join at
Yocto:

```sh
./make_HLS.sh
./make_PL.sh
./make_mconf.sh
./make_RPU.sh
./make_yocto.sh
```

- `make_HLS.sh` rebuilds every Vitis HLS component under
  `MSAP1_PL/SourceData/HLS_DesignFile` through the Vitis Python CLI
  (csim, synthesis, cosim, packaging), unpacks the packaged IPs into the
  untracked `HLS_DesignFile/ip_repo` Vivado IP repository, and then
  refreshes the Vivado project (`update_ip_catalog -rebuild` plus
  upgrading stale HLS IP customizations) so the next synthesis consumes
  the newest output. Run it on a fresh checkout and after HLS source
  changes; it recreates the gitignored `_ide` workspace metadata itself,
  so no Vitis GUI session is required. Vivado does not lock projects: if
  a Vivado session is running, the script skips the project refresh and
  prints the `refresh_hls_ip.tcl` command to source in that session's
  Tcl console instead.
- `make_RPU.sh` consumes the raw XSA plus the installed MSAP1 OpenAMP
  contract; it must not consume mconf or invoke Lopper.
- `make_mconf.sh` consumes the PL SDT artifact and renders its OpenAMP domain
  from the same contract.
- `make_yocto.sh` requires matching XSA and canonical contract digests in the
  independently produced RPU and mconf artifacts.
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
