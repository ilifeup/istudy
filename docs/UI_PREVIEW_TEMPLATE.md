# UI Preview Template v1 — branch implementation

This branch introduces stable page locator identifiers for the actual iStudy React routes.

- Client IDs use `C-MODULE-01`; Server IDs use `S-MODULE-01`. Never renumber or recycle published IDs.
- The locator is visible after login. Client users/admins see a compact corner ID. Super sees a dropdown and copy button. Server Super additionally sees the source-relative path.
- The dropdown excludes detail screens that need route parameters; navigation uses existing App `go()` and does not bypass API authorization.
- No new runtime dependencies, screenshot engine, backend endpoint, database, or Mock writes.
- The `previewRegistry.ts` is the project-specific data; `PageLocator.tsx` is the reusable UI.
- Screens not yet enumerated (pre-login, dialogs, internal tabs, dynamic state) remain **not inventoried**; do not report 100% coverage.
- This is an initial implementation, **not yet locally typechecked or manually tested**. Run `npm run typecheck`, `npm run build`, and manual Client/Server role checks before release.
- Existing Playwright dependency is unchanged but unused by this module.
- Public repository does not imply authorization to reveal live credentials or user data; Mock data and diagnostics must stay synthetic/sanitized.
