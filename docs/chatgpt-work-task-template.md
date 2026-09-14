# Daily delivery task template

Configure a ChatGPT Work web-cloud task for 22:00 IST. GitHub is read-only;
GitHub Actions is the sole repository writer. Keep the dedicated AgentMail
sender, recipient, and one production cutover SHA in secure task configuration,
not this public repository.

Use the configured AgentMail plugin and select the dedicated monitoring inbox
at the start and immediately before sending. Its Send Allow List is the
delivery boundary. This workflow uses no API key or local `.env`.

## SHA-cursor procedure

1. Capture `main` HEAD SHA as `current_sha` before reading ledger data. This is
   the immutable upper bound for the run.
2. Find the newest successful sent AgentMail report from the dedicated sender
   to the configured recipient whose **final line** is exactly
   `<!-- tsrm-main-sha:SHA -->`. Its SHA is `previous_sha`. On the first
   production run, use the configured cutover SHA as `previous_sha`.
3. Read only collection receipts and referenced event files introduced by
   `previous_sha..current_sha`, in Git commit order. Ignore manual-dispatch
   receipts (`authoritative: false`). Do not browse raw release data, release
   bodies, PRs, issues, or web pages.
4. Render one health line for every authoritative collection actually present.
   Group lines by IST date if helpful. Summarize only the sanitized event
   metadata: source repository, tag/package tag, timestamp, canonical URL, and
   retraction flag. If every collection on a rendered date has no events, end
   that date block with exactly `No changes on this day.` Do not invent a
   missing Action run.
5. Send one email only after the complete range is rendered. Its final line is
   exactly `<!-- tsrm-main-sha:CURRENT_SHA -->`, replacing `CURRENT_SHA` with
   `current_sha`. The sent copy is the completion marker. If send status is
   ambiguous, search sent mail for that exact final line before retrying; if
   unresolved, report `delivery_unknown` and do not send again in that run.

Never commit, push, create branches, alter schedules, change credentials, or
change recipients. If GitHub read access, dedicated inbox selection, or
AgentMail send/search is unavailable, send nothing.
