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
./mnc all build     # the whole chain in MNC_CHAIN order
./mnc --tui all build  # optional console with live stage summary
```

- `MncBuildPreset.yaml` beside `mnc` is user-owned workspace configuration.
  `stages.PL.jobs` limits PL concurrency without splitting `mnc all build`;
  explicit stage arguments override it. Do not overwrite the file during a
  build-script refresh.
- Each `build` command writes its transcript, stage results, and elapsed times
  under `runtime-generated/buildLog/`. `--tui` is presentation only: it must
  preserve the same log, summary, fail-fast behavior, and exit status.

The chain is `HLS PL RPU mconf yocto`, declared as `MNC_CHAIN` in
`products/msap1.conf`. Each stage also runs on its own:

```sh
./mnc HLS build
./mnc PL build
./mnc RPU build
./mnc mconf build
./mnc yocto build
./mnc deploy        # JTAG deploy using MncBuildPreset.yaml; no build report
```

- `mnc HLS build` rebuilds every Vitis HLS component under
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
- `mnc PL build` owns the whole PL flow: block design, synthesis,
  implementation, `write_bitstream`, XSA export, and SDTGen. With no option it
  runs all six in that order; `--build-bd`, `--compile-synth`,
  `--compile-impl`, `--compile-bit`, `--gen-xsa`, and `--sdtgen` select stages
  individually, and any combination still executes in that order. Each Vivado
  stage is one Tcl script in `MSAP1_PL/SourceData/Script` with its own log
  under `MSAP1_PL/vivado_gen/logs/`, so a failing stage is rerun and debugged
  on its own. `--build-bd` is required on a fresh checkout: the block
  design's output products are untracked. Vivado does not lock projects: the
  build stages refuse to run while a Vivado session of this user is open, and
  the stage script must then be sourced in that session's Tcl console
  instead. `--sdtgen` never opens the project.
- `mnc PL status`, `mnc PL summary`, and `mnc PL report` are read-only queries,
  not build stages: they are never implied by a bare `mnc PL build`, they run
  after any build stage in the same invocation, and they exit zero whenever
  the report was produced, so the verdict belongs in the output rather than
  the exit status. `--status` and `--summary` open the project read-only and
  `--report` reads only the files the stages wrote, so all three stay usable
  while a Vivado GUI holds the project.
- `mnc RPU build` consumes the raw XSA plus the installed MSAP1 OpenAMP
  contract; it must not consume mconf or invoke Lopper.
- `mnc mconf build` consumes the PL SDT artifact and renders its OpenAMP domain
  from the same contract.
- `mnc yocto build` requires matching XSA and canonical contract digests in the
  independently produced RPU and mconf artifacts.
- `mnc deploy` uses the preset's `stages.deploy` type, Xilinx hw_server IP, and
  TFTP-machine IP. Only JTAG is currently supported; it invokes the exported
  TFTP loader and intentionally does not create a build report.
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
