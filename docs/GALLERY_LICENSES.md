# Gallery and Licenses

## Goal

The `gallery/` folder contains free-license images used as postcard backgrounds for random eQSL generation.

## Rules for Committing Images

- Only commit images from sources with clear reuse permission (e.g. Wikimedia Commons).
- Keep attribution data updated in `gallery/ATTRIBUTIONS.md`.
- Prefer JPG/PNG photos (avoid logos/maps unless intentional).
- Keep filenames stable and descriptive.

## Usage in Settings

Example:

```json
{
  "image_dirs": ["gallery/pieve_di_cento_landscapes"],
  "randomize_image": true
}
```

The renderer will select a random image from the configured folder (recursively).
