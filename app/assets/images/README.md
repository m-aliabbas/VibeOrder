# App images

Generated with Codex (`codex exec … image_gen`): no text, logos or faces. The originals are in `raw/` (PNG, not
bundled). The app bundles JPEG copies made with `sips -s format jpeg -s formatOptions 78 -Z <max px>`.

| File | Used on | Prompt idea |
|---|---|---|
| `login_hero.jpg` | Sign-in brand panel; full-screen backdrop on phones | Overhead: wood-fired pizza, copper karahi, garlic naan, green chutney on dark wood, embers |
| `splash_bg.jpg` | Splash screen | Glowing brick oven mouth, flames, pizza peel, mostly dark |
| `dashboard_banner.jpg` | Dashboard greeting banner | Spice flat lay on slate, dark empty left half for text |
| `kitchen_bg.jpg` | KDS sign in (copied to `backend/media/ui/`) | Restaurant kitchen line at night, heat lamps, blurred |

Every screen draws a gradient overlay on top (`PhotoBackdrop` in `lib/widgets/visuals.dart`), and falls back to a
brand gradient if a file is missing. To replace an image, keep the same file name.
