# BatchKit

A small Fusion 360 add-in for batch operations on bodies.

- **Batch Rename** takes a multi-selection of bodies and one base name, and names them `base 1, base 2, ...` in the order you selected, with an optional start number and zero-padding. Works on components too.
- **Batch Material** takes a multi-selection of bodies (across different components) and one physical material, and applies it to all of them at once.

## Install

1. Download `dist/BatchKit_addin.zip` and unzip it to get a `BatchKit` folder.
2. Move that folder into Fusion's add-ins directory:
   - **macOS:** `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`
   - **Windows:** `%appdata%\Autodesk\Autodesk Fusion 360\API\AddIns\`
3. In Fusion: **Utilities → ADD-INS → BatchKit → Run**.

A **BatchKit** panel appears in the Design ribbon.

## Note

Fusion can already apply one material to a multi-selection natively (ctrl-click bodies across components in the browser, then drag a material onto one of them). Batch Material is a one-click convenience for the same result; Batch Rename with auto-numbering is the part Fusion doesn't do on its own.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
