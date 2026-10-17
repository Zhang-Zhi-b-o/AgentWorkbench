# Third-party components

AgentWorkbench's own code is licensed under the GNU Affero General Public License,
version 3 or later (see `LICENSE`).  It also redistributes or drives the
components below.  Nothing else is bundled: the DSH runtime, the VS Code web
server and the Zotero desktop application are installed by the user on their own
machine and are not part of this repository.

## Zotero Reader (bundled, AGPL-3.0)

`web/zotero-reader/` is a build of the official Zotero PDF reader:

- project: https://github.com/zotero/reader
- pinned commit: `4bfd82ee848e36e80aaff2a2c732615c89ad1e72`
- licence text: `web/zotero-reader/COPYING.txt`
- modification: `webpack.config.js` in the upstream build was adjusted so the
  web bundle can be emitted for this integration.  No reader logic was changed.

Corresponding source for the bundled build is the upstream repository at that
commit, plus that single build-config change.  Build instructions:
`PDFJS_CONFIG=web bash pdfjs/build` followed by
`npx webpack --config-name web`.

## PDF.js (bundled inside Zotero Reader, Apache-2.0)

`web/zotero-reader/pdf/` contains a PDF.js build shipped by the Zotero reader.
Its licence is at `web/zotero-reader/pdf/LICENSE`.  Notices for the bundled
standard fonts, character maps and wasm decoders sit next to their binaries
(`LICENSE_FOXIT`, `LICENSE_LIBERATION`, `LICENSE_JBIG2`, `LICENSE_OPENJPEG`,
`LICENSE_QCMS`).

## Components the user installs separately

| Component | Role | Licence / distribution |
|---|---|---|
| DeepSeek Harness (`@deepseek-ai/dsh`) | runs the `agents` plugin's chat UI | installed by the user with `npm`; not bundled |
| VS Code CLI (`code serve-web`) | runs the `vscode` plugin | Microsoft licence; installed by the user; not bundled |
| Zotero desktop | serves the `zotero` plugin's local API | AGPL-3.0; installed by the user; not bundled |
| Translate for Zotero | supplies the translation endpoint and key | installed by the user in Zotero; not bundled |

Earlier versions of this workbench embedded a fork of CloudCLI / claude-code-ui
as the front end.  That fork is **not** part of this repository: the shell and
the plugin pages here are original code, so no CloudCLI licence obligation
applies to this tree.
