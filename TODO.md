# TODO

- Replace the installed-package Unsloth Studio patch/rebuild flow with a cleaner integration layer. Prefer a maintained Studio source fork or formal overlay patch set over patching `site-packages` in-place.
- Collapse the current layout CSS fallbacks into one systemic Tailwind fix for arbitrary/custom-property utilities such as `w-(--sidebar-width)` and `max-w-(--thread-content-max-width)`.
- Move the voice/audio UI changes into explicit components and named classes so future Studio rebuilds do not require whack-a-mole shims.
- Keep native-audio and ASR transcript history covered by browser/build tests before removing any defensive fallbacks.
