# NextGowin v0.1.2 for Windows x64

> **Designs that use a PLL may be affected by an unresolved issue. Other parts of this release may also contain numerous undiscovered defects.**

NextGowin synthesizes and routes Gowin GW5AST-138C and GW5AST-138B designs. [Download the runtime](https://github.com/ZiyangYE/NextGowin/releases/tag/v0.1.2) and install Gowin V1.9.12.04 x64 locally. Set its installation directory in `bin/gowin-path.txt`, then run `bin/nextgowin.exe --self-test --no-pause` from the extracted directory.

Drag a Gowin project onto the launcher or run `bin/nextgowin.exe project.gprj --no-sync-ide --no-pause`. By default, the launcher overwrites generated outputs under the project's `impl/gwsynthesis` and `impl/pnr` directories. The example command disables that synchronization. Find the bitstream, pinout, post-route timing and logs next to the project in `<project>_nextgowin`.

[Browse the source and example designs](https://github.com/ZiyangYE/NextGowin/tree/v0.1.2). From the source checkout, run `python scripts/build_native_binaries.py --clean --out-dir build/tier1_bins` with Visual Studio 2022 and the Windows SDK installed.

Thanks to Google DeepMind’s **Gemini 4 Carbon**, OpenAI’s **GPT-6.2 Astra**, and the contributors to Yosys, UC Berkeley ABC and the bundled libraries. [中文说明](README.md)
