# Third-party notices

FormRecall's native desktop uses MyGo v0.9.1. Its Go dependencies and checksums are pinned in `go.mod` and `go.sum`. Package builds copy the license and notice files of the imported Go modules into `licenses/go`, preserving each module's relative paths. The Go distribution license is included at `licenses/go/Go-LICENSE`.

The executable's module versions can be inspected with `go version -m FormRecall.exe`. The Rust engine's dependency versions are recorded in `src/apps/engine-host/Cargo.lock`.

The OCCT 7.9.3 runtime includes `LICENSE_LGPL_21.txt` and `OCCT_LGPL_EXCEPTION.txt` under `occt-runtime/install`.

## Lucide icons

The desktop embeds SVG icons from Lucide. The complete upstream license, including the ISC terms and the MIT notice for icons derived from Feather, is preserved in `internal/workbench/icons/LICENSE` and distributed in `licenses/icons/Lucide-LICENSE`.

Copyright (c) 2026 Lucide Icons and Contributors.

Copyright (c) 2013-present Cole Bemis for the Feather-derived icons listed in that license.

## MyGo

Source repository https://github.com/egoist/mygo

MIT License

Copyright (c) 2026 MyGo contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
