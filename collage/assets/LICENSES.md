# Third-party assets in `collage/`

All fonts were fetched from the google/fonts GitHub repository (raw.githubusercontent.com/google/fonts/main/...).
Paper, torn edges, grain, tape and halftone textures are generated procedurally in `collage/imaging.py`
(no third-party texture files).

| File | Used for | Licence | Copyright |
|---|---|---|---|
| fonts/SpecialElite-Regular.ttf | typewriter strips, quotes | Apache 2.0 (`APACHE-2.0.txt`) | Astigmatic (AOETI) |
| fonts/ArchivoBlack-Regular.ttf | big grainy numerals, kicker cards | SIL OFL 1.1 (`OFL.txt`) | 2017 The Archivo Black Project Authors |
| fonts/OldStandard-Regular.ttf, OldStandard-Bold.ttf | newspaper headline / dateline | SIL OFL 1.1 | 2011 The Old Standard Project Authors |
| fonts/PlayfairDisplay.ttf (variable) | quote mark on the accent card | SIL OFL 1.1 | 2017 The Playfair Display Project Authors (RFN "Playfair Display") |
| fonts/UnifrakturMaguntia-Book.ttf | newspaper masthead (blackletter) | SIL OFL 1.1 | 2010 j. 'mach' wust (RFN UnifrakturMaguntia) |

## Models (not committed; downloaded on first use to `assets/models/`, git-ignored)

| File | Source | Licence |
|---|---|---|
| isnet-general-use.onnx (~176 MB) | https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx (DIS / IS-Net, xuebinqin/DIS) | Apache 2.0 |
| u2netp.onnx (~4.5 MB, fallback) | https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx (U^2-Net, xuebinqin/U-2-Net) | Apache 2.0 |
